"""Local proxies that let a destination say which hop reached it.

Phase 3 of the parity roadmap asks for routes proven by *measured traffic*,
not by the pre-launch health response: "a prelaunch check cannot prove that a
later disconnect, WebRTC path, DNS lookup, IPv6 connection, WebSocket, worker,
or download stays on the intended route."

The problem with proving that locally is that everything is 127.0.0.1, so a
destination cannot tell a direct connection from a proxied one. The trick
here is that ``127.0.0.0/8`` is an entire /8 on Linux: each proxy **binds its
outbound socket to a distinct loopback address**, so the destination's
``remote_addr`` names the hop that reached it.

    direct                       destination sees 127.0.0.1
    through the HTTP proxy       destination sees 127.0.0.2
    through the SOCKS5 proxy     destination sees 127.0.0.3
    through the authed proxies   127.0.0.4 / 127.0.0.5

That is a genuine destination-side observation, and it is the thing a
pre-launch probe cannot give. It is **not** a substitute for a real
third-party proxy over a real network: no NAT, no MTU, no latency, no IPv6
path, and no provider that can go down in a way a local process does not.
Those stay outstanding and are recorded as such.

No credentials belong here. The authed proxies use values generated per
run; nothing is read from a profile, an environment variable or a file, and
nothing is printed.
"""
from __future__ import annotations

import base64
import secrets
import socket
import struct
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Loopback addresses handed out to hops. 127.0.0.1 is reserved for "direct".
HOP_ADDRESSES = ("127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5", "127.0.0.6")


def loopback_alias_available(address: str) -> bool:
    """Whether this host lets us bind that loopback address.

    Checked rather than assumed: a container with a restricted loopback would
    make every assertion below vacuously pass, and a skipped test is honest
    where a passing one would not be.
    """
    try:
        with socket.socket() as probe:
            probe.bind((address, 0))
        return True
    except OSError:
        return False


@dataclass
class Destination:
    """An HTTP server that records who reached it, and how."""

    _server: ThreadingHTTPServer = field(init=False)
    requests: list[dict] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                outer.requests.append({
                    "path": self.path,
                    # The whole point: the address the *destination* observed.
                    "observedSource": self.client_address[0],
                    "host": self.headers.get("Host"),
                    "userAgent": self.headers.get("User-Agent"),
                })
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/dest"

    def sources_for(self, path_fragment: str = "") -> list[str]:
        return [r["observedSource"] for r in self.requests
                if path_fragment in r["path"]]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _Tunnel(threading.Thread):
    """Pipe two sockets until either closes."""

    def __init__(self, a: socket.socket, b: socket.socket) -> None:
        super().__init__(daemon=True)
        self._a, self._b = a, b

    def run(self) -> None:
        def pump(src: socket.socket, dst: socket.socket) -> None:
            try:
                while True:
                    chunk = src.recv(65536)
                    if not chunk:
                        break
                    dst.sendall(chunk)
            except OSError:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        forward = threading.Thread(target=pump, args=(self._a, self._b), daemon=True)
        forward.start()
        pump(self._b, self._a)
        forward.join(timeout=2)
        for s in (self._a, self._b):
            try:
                s.close()
            except OSError:
                pass


class _BaseProxy:
    """Common lifecycle: a listener, a bind address, a connection count."""

    def __init__(self, source_address: str, *, username: str | None = None) -> None:
        self.source_address = source_address
        self.username = username
        #: Generated per run. Never read from a profile or the environment,
        #: and never printed.
        self.password = secrets.token_urlsafe(12) if username else None
        self.connections = 0
        self.auth_failures = 0
        self.rejected = 0
        #: Hostnames the client asked the proxy to resolve, and literal
        #: addresses it resolved itself. The split is the DNS property:
        #: docs/NETWORK_ISOLATION.md §4.1 claims a proxied profile emits no
        #: DNS of its own because Chrome hands the *name* to the proxy, and
        #: this is where that is observable rather than asserted.
        self.requested_hostnames: list[str] = []
        self.requested_literals: list[str] = []
        #: Live sockets, so a proxy can be made to *vanish* rather than
        #: merely stop accepting. The difference is measurable: closing only
        #: the listener left Chromium relaying through an established tunnel,
        #: so a "dropped proxy" test passed for the wrong reason.
        self._live: list[socket.socket] = []
        self._live_lock = threading.Lock()
        self._stop = threading.Event()
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(64)
        self.port = int(self._listener.getsockname()[1])
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._guard, args=(client,), daemon=True).start()

    def _guard(self, client: socket.socket) -> None:
        try:
            self._handle(client)
        except Exception:  # noqa: BLE001 - a test proxy must not die on one bad client
            try:
                client.close()
            except OSError:
                pass

    def _handle(self, client: socket.socket) -> None:
        raise NotImplementedError

    def _track(self, *sockets: socket.socket) -> None:
        with self._live_lock:
            self._live.extend(sockets)

    def _connect_upstream(self, host: str, port: int) -> socket.socket:
        upstream = socket.socket()
        # The measurement. Binding the outbound socket is what lets the
        # destination attribute the connection to this hop.
        upstream.bind((self.source_address, 0))
        upstream.settimeout(10)
        upstream.connect((host, port))
        upstream.settimeout(None)
        return upstream

    def stop(self) -> None:
        """Stop accepting new connections. Established tunnels keep relaying.

        This is a proxy that has stopped listening, not one that has gone
        away, and the distinction turned out to matter: a mid-session test
        using this still saw traffic arrive through an open tunnel, from the
        proxy's own source address.
        """
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass

    def hard_stop(self) -> None:
        """Vanish: close the listener *and* every established tunnel.

        This is what a proxy going away mid-session actually looks like --
        the process dies, the provider drops the route, the container is
        killed. Anything the browser had open is severed.
        """
        self.stop()
        with self._live_lock:
            live, self._live = list(self._live), []
        for sock in live:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


class HttpConnectProxy(_BaseProxy):
    """An HTTP proxy that only needs to do what a browser asks of it."""

    def _note_authority(self, host: str) -> None:
        try:
            socket.inet_aton(host)
        except OSError:
            self.requested_hostnames.append(host)
        else:
            self.requested_literals.append(host)

    def _handle(self, client: socket.socket) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = client.recv(4096)
            if not chunk:
                client.close()
                return
            request += chunk
            if len(request) > 65536:
                client.close()
                return
        head, _, rest = request.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, _, target = lines[0].partition(" ")
        target = target.split(" ")[0]
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()

        if self.username:
            expected = base64.b64encode(
                f"{self.username}:{self.password}".encode()
            ).decode()
            offered = headers.get("proxy-authorization", "")
            if offered != f"Basic {expected}":
                self.auth_failures += 1
                client.sendall(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                    b'Proxy-Authenticate: Basic realm="route-lab"\r\n'
                    b"Content-Length: 0\r\n\r\n")
                # A browser retries on the same connection, so keep serving.
                return self._handle(client)

        if method.upper() == "CONNECT":
            host, _, port_text = target.rpartition(":")
            self._note_authority(host)
            try:
                upstream = self._connect_upstream(host, int(port_text or 443))
            except OSError:
                self.rejected += 1
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                client.close()
                return
            self.connections += 1
            self._track(client, upstream)
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            _Tunnel(client, upstream).start()
            return

        # A plain absolute-form request. Chromium sends these for http://.
        if "://" in target:
            without_scheme = target.split("://", 1)[1]
            authority, _, path = without_scheme.partition("/")
        else:
            authority, path = headers.get("host", ""), target.lstrip("/")
        host, _, port_text = authority.rpartition(":")
        if not host:
            host, port_text = authority, "80"
        self._note_authority(host)
        try:
            upstream = self._connect_upstream(host, int(port_text or 80))
        except OSError:
            self.rejected += 1
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            client.close()
            return
        self.connections += 1
        rebuilt = [f"{method} /{path} HTTP/1.1"]
        for name, value in headers.items():
            if name in ("proxy-authorization", "proxy-connection"):
                continue
            rebuilt.append(f"{name}: {value}")
        self._track(client, upstream)
        upstream.sendall(("\r\n".join(rebuilt) + "\r\n\r\n").encode("latin-1") + rest)
        _Tunnel(client, upstream).start()


class Socks5Proxy(_BaseProxy):
    """Enough SOCKS5 for a browser: greeting, optional auth, CONNECT."""

    def _handle(self, client: socket.socket) -> None:
        greeting = client.recv(262)
        if len(greeting) < 2 or greeting[0] != 0x05:
            client.close()
            return
        methods = set(greeting[2:2 + greeting[1]])
        if self.username:
            if 0x02 not in methods:
                self.auth_failures += 1
                client.sendall(b"\x05\xff")
                client.close()
                return
            client.sendall(b"\x05\x02")
            header = client.recv(2)
            if len(header) < 2 or header[0] != 0x01:
                client.close()
                return
            user = client.recv(header[1]).decode("latin-1")
            plen = client.recv(1)
            password = client.recv(plen[0]).decode("latin-1") if plen else ""
            if user != self.username or password != self.password:
                self.auth_failures += 1
                client.sendall(b"\x01\x01")
                client.close()
                return
            client.sendall(b"\x01\x00")
        else:
            if 0x00 not in methods:
                client.sendall(b"\x05\xff")
                client.close()
                return
            client.sendall(b"\x05\x00")

        request = client.recv(4)
        if len(request) < 4 or request[1] != 0x01:  # CONNECT only
            client.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
            client.close()
            return
        atyp = request[3]
        if atyp == 0x01:
            host = socket.inet_ntoa(client.recv(4))
            self.requested_literals.append(host)
        elif atyp == 0x03:
            length = client.recv(1)[0]
            # Resolved HERE, by the proxy, which is the DNS property
            # docs/NETWORK_ISOLATION.md §4.1 describes.
            host = client.recv(length).decode("latin-1")
            self.requested_hostnames.append(host)
        elif atyp == 0x04:
            host = socket.inet_ntop(socket.AF_INET6, client.recv(16))
        else:
            client.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
            client.close()
            return
        port = struct.unpack("!H", client.recv(2))[0]
        try:
            upstream = self._connect_upstream(host, port)
        except OSError:
            self.rejected += 1
            client.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
            client.close()
            return
        self.connections += 1
        self._track(client, upstream)
        client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0")
                       + struct.pack("!H", 0))
        _Tunnel(client, upstream).start()


class TlsObserver:
    """Records the TLS ClientHello of whatever connects, then hangs up.

    No certificate, and therefore no completed handshake: the ClientHello is
    the first flight and is sent before the server says anything, which is
    both what makes it a fingerprint and what makes it observable without a
    certificate the browser would have to be made to trust.

    The page load fails, visibly, and that is fine -- the measurement is the
    first flight, not the response. Observing HTTP/2 SETTINGS or HTTP/3 needs
    a completed handshake and therefore a trusted certificate, which is a
    separate and larger piece of work.
    """

    def __init__(self) -> None:
        self.hellos: list = []
        self.raw: list[bytes] = []
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.port = int(self._listener.getsockname()[1])
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        from multiprofile.diagnostics.transport_probe import (
            ClientHelloError,
            parse_client_hello,
        )

        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            try:
                client.settimeout(5)
                buffer = b""
                # One record is enough, and a ClientHello can span TCP
                # segments, so read until the record length is satisfied.
                while len(buffer) < 5:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                if len(buffer) >= 5:
                    want = 5 + struct.unpack_from("!H", buffer, 3)[0]
                    while len(buffer) < want:
                        chunk = client.recv(8192)
                        if not chunk:
                            break
                        buffer += chunk
                if buffer:
                    self.raw.append(buffer)
                    try:
                        self.hellos.append(parse_client_hello(buffer))
                    except ClientHelloError as e:
                        self.errors.append(str(e))
            except OSError as e:
                self.errors.append(f"socket error: {e}")
            finally:
                try:
                    client.close()
                except OSError:
                    pass

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.port}/tls"

    def stop(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
