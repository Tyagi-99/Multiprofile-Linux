"""Test isolation: no test may read or write the operator's real store.

There was no conftest at all. ``xdg._xdg`` resolves every data path as
``os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"`` and then
**creates the directory**, so a test that did not redirect XDG itself operated
on ``~/.local/share/multiprofile`` -- the operator's live profiles, cookies and
control database. Seventeen tests across five files did exactly that. The
observable damage on this machine was a real control.db migrated v7 -> v8 by a
test run and an ``extensions/`` directory conjured into the live store by a
bare path resolve.

It also explains the long-standing "migration flake". ``db._schema_ready`` is a
module global cleared only when the resolved path *string* changes, and
``db.close()`` -- which clears it -- was called by nothing. So two tests that
resolved to the same path (which only happened via the unisolated home
fallback) shared one connection, and the second one's schema setup was skipped
because the first had already set the flag. Whether such a pair landed next to
each other depended on ``pytest-randomly``'s seed, which is what made it look
like a flake rather than an ordering bug.

Two layers, because isolation that can be silently lost is not isolation:

1. ``_isolate_xdg`` redirects every XDG base to a per-test temporary directory.
   A test that sets its own still wins -- monkeypatch applies after this.
2. ``_no_real_store`` fails the test if a path under the real home is resolved
   anyway. Layer 1 is the fix; layer 2 is what stops the fix being undone by a
   future test that reaches for ``Path.home()`` directly.

Set ``MP_ALLOW_REAL_STORE=1`` to work against the live store on purpose. The
acceptance harness does that deliberately; nothing under ``tests/`` should.
"""
from __future__ import annotations

import os
import pathlib
import shutil
from pathlib import Path

import pytest

#: Resolved once, before any test can redirect ``HOME``.
_REAL_HOME = Path.home()

_FORBIDDEN = (
    _REAL_HOME / ".local/share/multiprofile",
    _REAL_HOME / ".config/multiprofile",
    _REAL_HOME / ".cache/multiprofile",
    _REAL_HOME / ".local/state/multiprofile",
)


#: Every base the application resolves data through.
_XDG_BASES = (
    ("XDG_DATA_HOME", "data"),
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_CACHE_HOME", "cache"),
    ("XDG_STATE_HOME", "state"),
)


def _allowed() -> bool:
    return os.environ.get("MP_ALLOW_REAL_STORE") == "1"


def pytest_configure(config):
    """Redirect the XDG bases before collection, not just before each test.

    Fixtures are too late for this. Module-level code runs at **collection**,
    and several live modules decide their skip gate there by calling
    ``engine_choice.resolve()`` -- which read the operator's real
    ``~/.config/multiprofile/engine.json``. Harmless in itself (it is a read),
    but it meant a developer's engine pin silently decided which tests ran,
    and the autouse fixtures below could not see it to stop it.

    Set as real environment variables rather than through monkeypatch, because
    there is no test in scope yet to tear down against. The per-test fixture
    still points each test at its own directory; this only moves the floor.
    """
    if _allowed():
        return
    import tempfile

    base = pathlib.Path(tempfile.mkdtemp(prefix="mp-test-xdg-"))
    config._mp_xdg_base = base
    for env, sub in _XDG_BASES:
        if os.environ.get(env):
            continue
        target = base / sub
        target.mkdir(parents=True, exist_ok=True)
        os.environ[env] = str(target)


def pytest_unconfigure(config):
    base = getattr(config, "_mp_xdg_base", None)
    if base is not None:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_xdg(tmp_path_factory, monkeypatch):
    """Point every XDG base at a per-test temporary directory."""
    if _allowed():
        yield
        return
    base = tmp_path_factory.mktemp("xdg")
    for env, sub in _XDG_BASES:
        target = base / sub
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(env, str(target))
    yield


@pytest.fixture(autouse=True)
def _no_real_store(monkeypatch, request):
    """Fail loudly rather than touch the live store."""
    if _allowed():
        yield
        return

    from multiprofile.storage import xdg

    real_xdg = xdg._xdg

    def guarded(env: str, default: str) -> Path:
        base = os.environ.get(env) or str(Path.home() / default)
        resolved = Path(base) / xdg.APP
        if resolved in _FORBIDDEN:
            raise AssertionError(
                f"{request.node.nodeid} resolved the operator's real store "
                f"({resolved}); {env} was not redirected. See tests/conftest.py."
            )
        return real_xdg(env, default)

    monkeypatch.setattr(xdg, "_xdg", guarded)
    yield


@pytest.fixture(autouse=True)
def _close_db_between_tests():
    """Drop the process-wide connection and its schema flag after every test.

    ``db._schema_ready`` survives the database file being replaced or deleted
    as long as the path string is unchanged, so without this a later test can
    inherit a connection to a file that no longer exists and skip its own
    schema setup.
    """
    yield
    try:
        from multiprofile.storage import db

        db.close()
    except Exception:  # noqa: BLE001 - teardown must not mask a test failure
        pass
