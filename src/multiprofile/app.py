from __future__ import annotations
import logging
import os
import threading
import time
import webbrowser
from typing import Optional

from .api.server import create_app

log = logging.getLogger("multiprofile.app")

# Persistent webview profile dir: without this the desktop window starts
# blank on a fresh/private profile. Must survive restarts.
WEBVIEW_STORAGE_PATH = os.path.expanduser("~/.local/share/multiprofile/webview")


def run_server(host: str = "127.0.0.1", port: int = 8765):
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
    except Exception as e:
        log.warning("pywebview unavailable (%s); opening system browser", e)
        webbrowser.open(url)
        log.info("UI opened in system browser: %s", url)
        # keep process alive while server runs
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
