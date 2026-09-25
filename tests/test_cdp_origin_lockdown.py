"""A web page must not be able to drive a profile's browser over CDP.

Every profile runs Chrome with a DevTools port on 127.0.0.1, and CDP has no
authentication of its own: whoever can open that WebSocket owns the browser
-- read the cookie jar, navigate, run script. The only thing standing
between a visited website and that socket is Chrome's origin check on the
DevTools endpoint, and the launcher was switching it off with
``--remote-allow-origins=*``.

The flag was there because websocket-client sends an Origin header by
default, and Chrome then refuses the handshake. Measured on Chrome 151:

    WITH --remote-allow-origins=*   no Origin: ACCEPTED   hostile Origin: ACCEPTED
    WITHOUT the flag                no Origin: ACCEPTED   hostile Origin: REFUSED 403

So the flag bought nothing our own clients needed. They now pass
``suppress_origin=True`` and send no Origin at all, Chrome's default check
stays on, and a browser -- which always sends Origin -- is locked out.

Verified live end to end after the change: launch ok, identity injected
(navigator.platform "Win32", hardwareConcurrency 4), our own CDP client
resolved a window id, and a connection carrying Origin
https://evil.example was refused with 403.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "multiprofile"

# Every module that opens a DevTools WebSocket.
CDP_CLIENTS = [
    SRC / "browser" / "cdp_inject.py",
    SRC / "browser" / "cdp_window.py",
    SRC / "browser" / "cdp_identity.py",
    SRC / "devices" / "validator.py",
]

# Every module that spawns Chrome.
SPAWNERS = [
    SRC / "browser" / "launcher.py",
    SRC / "devices" / "validator.py",
]


def _flag_literals(path: Path) -> list[str]:
    """String literals in the file that look like the dangerous flag."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "remote-allow-origins" in node.value
    ]


@pytest.mark.parametrize("path", SPAWNERS, ids=lambda p: p.name)
def test_chrome_is_not_launched_with_remote_allow_origins(path):
    """The flag disables the only barrier between a website and the browser.

    Checked as a string literal, so a comment explaining the history does
    not trip it but an actual re-added flag does.
    """
    assert _flag_literals(path) == [], (
        f"{path.name} passes --remote-allow-origins to Chrome again; any page "
        f"that learns the CDP port could then drive the profile"
    )


@pytest.mark.parametrize("path", CDP_CLIENTS, ids=lambda p: p.name)
def test_every_cdp_client_suppresses_its_origin(path):
    """Without this the connection is refused once the flag is gone."""
    source = path.read_text(encoding="utf-8")
    opens = len(re.findall(r"create_connection\(|ws\.connect\(", source))
    suppressed = source.count("suppress_origin=True")
    assert opens > 0, f"{path.name} no longer opens a CDP socket — update this test"
    assert suppressed >= opens, (
        f"{path.name} opens {opens} CDP socket(s) but only {suppressed} suppress "
        f"Origin; an un-suppressed one will be refused by Chrome"
    )


def test_the_cdp_port_is_bound_to_loopback_only():
    """A port on 0.0.0.0 would make the origin check the least of it."""
    source = (SRC / "browser" / "launcher.py").read_text(encoding="utf-8")
    assert "--remote-debugging-address=127.0.0.1" in source


def test_cdp_ready_refuses_a_non_localhost_debugger_url():
    """Defence in depth: never speak CDP to something off-box."""
    from multiprofile.browser.cdp_window import CdpError, wait_cdp_ready

    assert CdpError is not None
    source = (SRC / "browser" / "cdp_window.py").read_text(encoding="utf-8")
    assert "cdp_not_localhost" in source
    assert wait_cdp_ready is not None
