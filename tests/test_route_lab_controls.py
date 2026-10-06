"""Controls for the route lab itself, so a lab fault cannot be read as a product fault.

The authenticated-proxy failure in CI was reported for weeks as
"authenticated-proxy failures", on the evidence of one line:

    proxy[connections=1, auth_failures=12, rejected=4]

That reading was wrong, and the counters are why. In ``HttpConnectProxy``
the credential check runs *before* the upstream connect, so every request
counted in ``connections`` or ``rejected`` is a request that **passed**
authentication. ``1 + 4 = 5`` successful authentications is not an
authentication failure; the failure was four upstream connects, and
``rejected`` was a bare integer that recorded no reason for any of them.

These tests need no browser, so they run in the ordinary Python job on every
supported interpreter rather than only in the live-browser job. They answer
the two questions that have to be separated before any browser result can be
interpreted:

1. Can this machine bind the loopback aliases the lab attributes hops with?
   If not, ``rejected`` will be non-zero for a reason that has nothing to do
   with the product, and these tests say so first.
2. Does the lab's 407 handler serve a correct client? If these pass and the
   browser test fails, the fixture is exonerated **in that environment**, not
   just on a developer's machine.
"""
from __future__ import annotations

import base64
import socket

import pytest
from _route_lab import Destination, HttpConnectProxy, Socks5Proxy

#: The source addresses the lab attributes hops with.
_ALIASES = ("127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5")


@pytest.mark.parametrize("alias", _ALIASES)
def test_this_machine_can_bind_the_loopback_alias(alias):
    """An environment precondition, asserted before anything is concluded from it."""
    s = socket.socket()
    try:
        try:
            s.bind((alias, 0))
        except OSError as e:
            pytest.fail(
                f"cannot bind {alias}: {e.strerror} (errno {e.errno}). The route "
                "lab attributes each hop by binding its outbound socket to one of "
                f"{_ALIASES}, so on this machine every proxied route test will "
                "report rejections for an environment reason and not a product one."
            )
    finally:
        s.close()


@pytest.fixture
def auth_lab():
    dest = Destination()
    proxy = HttpConnectProxy("127.0.0.4", username="lab-http")
    try:
        yield dest, proxy
    finally:
        proxy.stop()
        dest.close()


def _creds(proxy) -> str:
    raw = f"{proxy.username}:{proxy.password}".encode()
    return base64.b64encode(raw).decode()


def _get_through(proxy, dest, tag: str, *, auth_up_front: bool,
                 retry_same_connection: bool) -> str:
    """One absolute-form GET through the proxy. Returns the final status line."""
    def request(with_auth: bool) -> bytes:
        head = (
            f"GET http://127.0.0.1:{dest.port}/dest?{tag} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{dest.port}\r\n"
            "Connection: keep-alive\r\n"
        ).encode()
        if with_auth:
            head += f"Proxy-Authorization: Basic {_creds(proxy)}\r\n".encode()
        return head + b"\r\n"

    def status_of(sock) -> str:
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")

    sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
    try:
        sock.sendall(request(auth_up_front))
        status = status_of(sock)
        if "407" in status:
            if not retry_same_connection:
                sock.close()
                sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
            sock.sendall(request(True))
            status = status_of(sock)
        return status
    finally:
        try:
            sock.close()
        except OSError:
            pass


def test_correct_credentials_offered_up_front_are_accepted(auth_lab):
    dest, proxy = auth_lab
    status = _get_through(proxy, dest, "ctl_upfront",
                          auth_up_front=True, retry_same_connection=False)
    assert "200" in status, f"{status} reasons={proxy.rejected_reasons}"
    assert proxy.auth_failures == 0
    assert dest.sources_for("ctl_upfront") == ["127.0.0.4"]


def test_a_retry_on_the_same_connection_after_a_407_is_accepted(auth_lab):
    """What a browser actually does, and what the handler's loop exists for.

    This is the case the handler used to serve by recursing into itself once
    per challenge -- unbounded over a keep-alive connection, and discarding
    any bytes the previous read had buffered past its own request.
    """
    dest, proxy = auth_lab
    status = _get_through(proxy, dest, "ctl_same",
                          auth_up_front=False, retry_same_connection=True)
    assert "200" in status, f"{status} reasons={proxy.rejected_reasons}"
    assert proxy.auth_failures == 1, "exactly one challenge, then success"
    assert proxy.challenges_per_connection == [1]
    assert dest.sources_for("ctl_same") == ["127.0.0.4"]


def test_a_retry_on_a_new_connection_after_a_407_is_accepted(auth_lab):
    dest, proxy = auth_lab
    status = _get_through(proxy, dest, "ctl_new",
                          auth_up_front=False, retry_same_connection=False)
    assert "200" in status, f"{status} reasons={proxy.rejected_reasons}"
    assert dest.sources_for("ctl_new") == ["127.0.0.4"]


def test_wrong_credentials_are_refused_and_the_client_is_eventually_dropped(auth_lab):
    """The challenge loop is bounded, and says so when it fires."""
    dest, proxy = auth_lab
    sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
    bad = base64.b64encode(b"lab-http:wrong").decode()
    req = (
        f"GET http://127.0.0.1:{dest.port}/dest?ctl_bad HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{dest.port}\r\n"
        f"Proxy-Authorization: Basic {bad}\r\n"
        "Connection: keep-alive\r\n\r\n"
    ).encode()
    try:
        seen = 0
        for _ in range(proxy.MAX_AUTH_CHALLENGES + 2):
            try:
                sock.sendall(req)
                buf = sock.recv(4096)
            except OSError:
                break
            if not buf:
                break
            seen += buf.count(b"407")
        assert seen >= 1, "the proxy must challenge a client with wrong credentials"
        assert proxy.auth_failures <= proxy.MAX_AUTH_CHALLENGES, (
            "the challenge loop is bounded, so a client offering bad credentials "
            "forever cannot hold the handler open forever"
        )
        assert dest.sources_for("ctl_bad") == [], (
            "a request that never authenticated must not reach the destination"
        )
    finally:
        try:
            sock.close()
        except OSError:
            pass


def test_a_rejection_records_why(auth_lab):
    """``rejected`` without a reason is what made the CI failure unreadable."""
    dest, proxy = auth_lab
    dest.close()  # nothing is listening now, so the upstream connect must fail
    status = _get_through(proxy, dest, "ctl_rej",
                          auth_up_front=True, retry_same_connection=False)
    assert "502" in status, status
    assert proxy.rejected == 1
    assert proxy.rejected_reasons, "a rejection must say why"
    reason = proxy.rejected_reasons[0]
    assert "connect" in reason.lower() or "refused" in reason.lower(), reason


def test_the_unauthenticated_proxy_is_the_control_for_all_of_this():
    """The matched control: same code path, no credentials configured."""
    dest = Destination()
    proxy = HttpConnectProxy("127.0.0.2")
    try:
        status = _get_through(proxy, dest, "ctl_noauth",
                              auth_up_front=False, retry_same_connection=False)
        assert "200" in status, f"{status} reasons={proxy.rejected_reasons}"
        assert proxy.auth_failures == 0
        assert proxy.challenges_per_connection == []
        assert dest.sources_for("ctl_noauth") == ["127.0.0.2"]
    finally:
        proxy.stop()
        dest.close()


def test_socks5_authentication_is_accepted_by_the_lab():
    """Chromium cannot use an authenticated SOCKS5 proxy; the lab can serve one.

    Worth separating, because "the launch is refused" is a product decision and
    "the lab cannot do it either" would be a fixture limitation. It is the
    former.
    """
    dest = Destination()
    proxy = Socks5Proxy("127.0.0.5", username="lab-socks")
    try:
        sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
        try:
            sock.sendall(b"\x05\x01\x02")
            assert sock.recv(2) == b"\x05\x02"
            user = proxy.username.encode()
            password = (proxy.password or "").encode()
            sock.sendall(
                bytes([0x01, len(user)]) + user + bytes([len(password)]) + password
            )
            assert sock.recv(2) == b"\x01\x00", "the lab rejected its own credentials"
            sock.sendall(
                b"\x05\x01\x00\x01"
                + socket.inet_aton("127.0.0.1")
                + dest.port.to_bytes(2, "big")
            )
            reply = sock.recv(10)
            assert reply[1] == 0x00, (
                f"SOCKS5 CONNECT failed: reply={reply!r} "
                f"reasons={proxy.rejected_reasons}"
            )
        finally:
            sock.close()
    finally:
        proxy.stop()
        dest.close()
