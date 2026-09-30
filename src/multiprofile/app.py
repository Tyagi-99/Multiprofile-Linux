from __future__ import annotations

import logging
import os
import threading
import time
import webbrowser

from .api.server import create_app

log = logging.getLogger("multiprofile.app")

# Persistent webview profile dir: without this the desktop window starts
# blank on a fresh/private profile. Must survive restarts.
WEBVIEW_STORAGE_PATH = os.path.expanduser("~/.local/share/multiprofile/webview")


def prepare_data_dir() -> None:
    """One-time upkeep of the operator's data, at real startup only.

    This deliberately does NOT live in create_app(). create_app is a factory
    that every test calls, and putting migrations there meant a test run
    could reach out and rewrite the operator's live files -- which is
    exactly what happened: a test whose isolation patched xdg.data_dir but
    not the name gateways.py had already bound at import ended up encrypting
    31 real gateway passwords with a key that lived in a pytest temp
    directory. Side effects belong in the entrypoint, not the factory.
    """
    try:
        from .storage.xdg import harden_data_dir, permission_faults

        fixed = harden_data_dir()
        if fixed:
            log.warning("tightened permissions on operator data: %s", ", ".join(fixed))
        # What it could not fix matters more than what it did: those files are
        # still readable by someone else. Startup says so, and /api/health
        # keeps saying so.
        stuck = permission_faults()
        if stuck:
            log.error(
                "could not make %d file(s) owner-only: %s",
                len(stuck), ", ".join(f["path"] for f in stuck),
            )
    except Exception as e:  # noqa: BLE001 - never block startup
        log.debug("permission hardening skipped: %s", e)

    try:
        from .storage.xdg import purge_legacy_extension_staging

        # Earlier versions staged a copy of the extension per profile, holding
        # the cleartext proxy password and the cookie jar at 0644. Nothing
        # writes or reads them now; the copies already on disk are removed.
        purged = purge_legacy_extension_staging()
        if purged:
            log.warning(
                "removed legacy staged extension directories (cleartext proxy "
                "password, cookie jar) for %d profile(s): %s",
                len(purged),
                ", ".join(purged[:10]) + (" …" if len(purged) > 10 else ""),
            )
    except Exception as e:  # noqa: BLE001 - never block startup
        log.debug("legacy extension purge skipped: %s", e)

    try:
        from .secrets import backend, migrate_plaintext_secrets

        moved = migrate_plaintext_secrets()
        if any(moved.values()):
            log.warning("encrypted plaintext secrets at rest: %s", moved)
        if backend() == "file":
            log.warning(
                "secrets key is a 0600 file, not the OS keychain — install the "
                "'keyring' extra so a stolen disk does not hand over the proxy "
                "passwords"
            )
    except Exception as e:  # noqa: BLE001 - never block startup
        log.warning("secret migration skipped: %s", e)


def run_server(host: str = "127.0.0.1", port: int = 8765):
    prepare_data_dir()
    app = create_app()
    # werkzeug quiet-ish
    app.run(host=host, port=port, threaded=True, use_reloader=False)


def start_background_server(host: str = "127.0.0.1", port: int = 8765) -> threading.Thread:
    t = threading.Thread(target=run_server, kwargs={"host": host, "port": port}, daemon=True)
    t.start()
    # wait briefly for bind
    time.sleep(0.4)
    return t


def _webview_icon_path() -> str | None:
    """Path to the Multiprofile window icon (installed hicolor PNG first)."""
    candidates = [
        "/usr/share/icons/hicolor/128x128/apps/multiprofile.png",
        os.path.join(os.path.dirname(__file__), "..", "static", "app_icon.png"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def open_ui(url: str = "http://127.0.0.1:8765/", title: str = "Multiprofile") -> None:
    """Prefer pywebview GTK; fall back to system browser."""
    try:
        import webview  # type: ignore

        log.info("Starting pywebview UI at %s", url)
        icon = _webview_icon_path()
        # Match the desktop file id so GNOME uses Icon=multiprofile, not a generic mark.
        os.environ.setdefault("GIO_LAUNCHED_DESKTOP_FILE", "multiprofile-native.desktop")
        try:
            import gi

            gi.require_version("Gtk", "3.0")
            from gi.repository import GLib, Gtk

            GLib.set_prgname("multiprofile-native")
            GLib.set_application_name("Multiprofile")
            if icon:
                Gtk.Window.set_default_icon_from_file(icon)
        except Exception:
            log.debug("could not set GTK app id/icon", exc_info=True)
        # This pywebview build rejects icon= on create_window. start(icon=) is what GTK applies.
        webview.create_window(title, url, width=1280, height=800)
        os.makedirs(WEBVIEW_STORAGE_PATH, exist_ok=True)
        webview.start(
            private_mode=False,
            storage_path=WEBVIEW_STORAGE_PATH,
            icon=icon,
        )
        return
    except Exception as e:  # noqa: BLE001 - broad by design; the failure is logged, not hidden
        log.warning("pywebview unavailable (%s); opening system browser", e)
        webbrowser.open(url)
        log.info("UI opened in system browser: %s", url)
        # keep process alive while server runs
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
