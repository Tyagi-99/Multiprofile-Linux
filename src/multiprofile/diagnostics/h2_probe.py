"""What a destination sees of a browser's HTTP/2, above a completed handshake.

The companion to :mod:`transport_probe`, which parses the TLS first flight and
stops there on purpose. Its docstring states the reason and the cost:

    Observing HTTP/2 SETTINGS or HTTP/3 needs a completed handshake and
    therefore a trusted certificate; that is a separate, larger piece of work
    and is recorded as a remaining gate rather than guessed at.

This is that work for HTTP/2 -- gate **T2** in
``docs/09_ENGINE_PROGRAM.md`` §6, its h2 half. HTTP/3 remains unmeasured: it
needs a QUIC server, which is a different protocol stack rather than more of
this one.

Like the TLS probe, this module only observes. It computes no verdict, because
a single capture supports none: the h2 preface is chosen by the client before
the server has said anything, which is what makes it a fingerprint, and what
makes it meaningless without a matched reference. ``tests/
test_h2_identity_live.py`` supplies the reference by capturing stock Chromium
on the same host in the same session.

**The certificate.** A caller has to make the browser complete a handshake
with a certificate it does not inherently trust. Do not install one into the
user's trust store to achieve that: a lab measurement is not a reason to
change what a person's browser trusts. Point the engine at a wrapper that
adds ``--ignore-certificate-errors`` instead (``MULTIPROFILE_ENGINE_BINARY``),
and give the matched control the identical flag, so the thing being compared
is the browser and not the flag.

**Speculative sockets.** A headful Chromium opens connections it then
abandons. Measured on this host, the first accepted socket is routinely one
of them, and reading that as "the browser rejected our certificate" was a
wrong conclusion. Negotiating h2 is not enough to tell them apart either: a
preconnect completes the TLS handshake, ALPN included, and then sends
nothing. So a connection only counts once it has sent the client preface,
and the ones that did not are recorded rather than mistaken for the answer.
"""
from __future__ import annotations

import socket
import ssl
import struct
import time
from pathlib import Path
from typing import Any

#: The client connection preface, RFC 9113 §3.4.
PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"

FRAME_TYPES = {
    0x0: "DATA", 0x1: "HEADERS", 0x2: "PRIORITY", 0x3: "RST_STREAM",
    0x4: "SETTINGS", 0x5: "PUSH_PROMISE", 0x6: "PING", 0x7: "GOAWAY",
    0x8: "WINDOW_UPDATE", 0x9: "CONTINUATION", 0x10: "PRIORITY_UPDATE",
}

#: RFC 9113 §6.5.2, plus 0x8 (RFC 8441) and 0x9 (RFC 9218).
SETTINGS_NAMES = {
    0x1: "HEADER_TABLE_SIZE", 0x2: "ENABLE_PUSH", 0x3: "MAX_CONCURRENT_STREAMS",
    0x4: "INITIAL_WINDOW_SIZE", 0x5: "MAX_FRAME_SIZE", 0x6: "MAX_HEADER_LIST_SIZE",
    0x8: "ENABLE_CONNECT_PROTOCOL", 0x9: "NO_RFC7540_PRIORITIES",
}

#: HPACK static table index 8 is ``:status: 200``, so a minimal response needs
#: no dynamic table and no HPACK encoder.
_HPACK_STATUS_200 = b"\x88"


def frame(ftype: int, flags: int, stream: int, payload: bytes = b"") -> bytes:
    """One HTTP/2 frame: 24-bit length, type, flags, 31-bit stream id."""
    return (struct.pack(">I", len(payload))[1:] + bytes([ftype, flags])
            + struct.pack(">I", stream) + payload)


def observe_once(
    port: int,
    cert: str | Path,
    key: str | Path,
    *,
    timeout: float = 60.0,
    host: str = "127.0.0.1",
) -> dict[str, Any]:
    """Terminate one h2 connection on ``port`` and report what the client sent.

    Returns fields, never a judgement: the client's SETTINGS, the order of its
    frame types, its connection-level window update, and which priority scheme
    it used -- RFC 7540 PRIORITY frames, RFC 9218 PRIORITY_UPDATE, or neither.
    ``error`` is set and the rest left empty when no h2 connection completed.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    ctx.set_alpn_protocols(["h2", "http/1.1"])

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)

    out: dict[str, Any] = {
        "alpn": None, "tls_version": None, "cipher": None, "preface_ok": None,
        "settings": {}, "frame_order": [], "window_update": None,
        "priority_scheme": None, "frames": 0, "connections": 0,
        "discarded": [], "error": None,
    }

    tls = None
    buf = b""
    accept_until = time.time() + timeout
    while time.time() < accept_until:
        try:
            srv.settimeout(max(1.0, accept_until - time.time()))
            conn, _ = srv.accept()
        except socket.timeout:
            break
        out["connections"] += 1
        try:
            candidate = ctx.wrap_socket(conn, server_side=True)
        except Exception as e:  # noqa: BLE001 - recorded, then try the next socket
            out["discarded"].append(f"handshake: {type(e).__name__}")
            try:
                conn.close()
            except Exception:  # noqa: BLE001,S110 - already failing
                pass
            continue
        if candidate.selected_alpn_protocol() != "h2":
            out["discarded"].append(f"alpn={candidate.selected_alpn_protocol()!r}")
            candidate.close()
            continue
        # The preface decides. A preconnect gets this far -- handshake done,
        # h2 agreed -- and then never speaks.
        candidate.settimeout(min(8.0, max(2.0, accept_until - time.time())))
        pending = b""
        try:
            while len(pending) < len(PREFACE):
                chunk = candidate.recv(65536)
                if not chunk:
                    raise ConnectionError("closed before the preface")
                pending += chunk
        except Exception as e:  # noqa: BLE001 - recorded, then try the next socket
            out["discarded"].append(f"no preface: {type(e).__name__}")
            try:
                candidate.close()
            except Exception:  # noqa: BLE001,S110 - already failing
                pass
            continue
        out["preface_ok"] = pending[:len(PREFACE)] == PREFACE
        buf = pending[len(PREFACE):]
        tls = candidate
        break

    if tls is None:
        out["error"] = (
            f"no h2 connection completed within {timeout:.0f}s; "
            f"{out['connections']} connection(s), discarded={out['discarded']}"
        )
        srv.close()
        return out

    out["alpn"] = tls.selected_alpn_protocol()
    out["tls_version"] = tls.version()
    out["cipher"] = tls.cipher()[0] if tls.cipher() else None
    tls.settimeout(10.0)

    def _need(n: int) -> bytes:
        nonlocal buf
        while len(buf) < n:
            chunk = tls.recv(65536)
            if not chunk:
                raise ConnectionError("client closed")
            buf += chunk
        head, buf = buf[:n], buf[n:]
        return head

    try:
        tls.sendall(frame(0x4, 0, 0))                     # our SETTINGS
        answered = False
        read_until = time.time() + 20
        while time.time() < read_until:
            header = _need(9)
            length = int.from_bytes(header[0:3], "big")
            ftype, flags = header[3], header[4]
            stream = int.from_bytes(header[5:9], "big") & 0x7FFFFFFF
            payload = _need(length) if length else b""
            out["frames"] += 1
            out["frame_order"].append(FRAME_TYPES.get(ftype, f"UNKNOWN_0x{ftype:x}"))

            if ftype == 0x4 and not flags & 0x1:
                for i in range(0, len(payload) - 5, 6):
                    ident, value = struct.unpack(">HI", payload[i:i + 6])
                    out["settings"][SETTINGS_NAMES.get(ident, f"0x{ident:x}")] = value
                tls.sendall(frame(0x4, 0x1, 0))           # SETTINGS ACK
            elif ftype == 0x8 and stream == 0:
                out["window_update"] = int.from_bytes(payload[:4], "big") & 0x7FFFFFFF
            elif ftype == 0x2:
                out["priority_scheme"] = "RFC 7540 PRIORITY frames"
            elif ftype == 0x10:
                out["priority_scheme"] = "RFC 9218 PRIORITY_UPDATE"
            elif ftype == 0x1 and not answered:
                tls.sendall(frame(0x1, 0x4, stream, _HPACK_STATUS_200))
                tls.sendall(frame(0x0, 0x1, stream, b"ok"))
                answered = True
    except (ConnectionError, socket.timeout, OSError) as e:
        out["ended"] = type(e).__name__
    except Exception as e:  # noqa: BLE001 - a probe reports its own failure
        out["error"] = repr(e)
    finally:
        if out["settings"].get("NO_RFC7540_PRIORITIES") and not out["priority_scheme"]:
            out["priority_scheme"] = "RFC 9218, declared in SETTINGS with no frame sent"
        for sock in (tls, srv):
            try:
                sock.close()
            except Exception:  # noqa: BLE001,S110 - teardown
                pass
    return out


#: The fields a parity comparison may use. Everything else in the dict is
#: either about our own server or about this particular run.
COMPARABLE = ("settings", "window_update", "priority_scheme")


def differences(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Field-by-field disagreement between two observations, or ``{}``."""
    out: dict[str, Any] = {}
    for key in COMPARABLE:
        if a.get(key) != b.get(key):
            out[key] = {"a": a.get(key), "b": b.get(key)}
    return out
