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


def open_ui(url: str = "http://127.0.0.1:8765/", title: str = "Multiprofile Native") -> None:
    """Prefer pywebview GTK; fall back to system browser."""
    try:
        import webview  # type: ignore

        log.info("Starting pywebview UI at %s", url)
        icon = _webview_icon_path()
        # `icon` is supported by newer pywebview; older versions raise
        # TypeError for the unknown kwarg — fall back to no window icon.
        if icon:
            try:
                webview.create_window(title, url, width=1280, height=800, icon=icon)
            except TypeError:
                webview.create_window(title, url, width=1280, height=800)
        else:
            webview.create_window(title, url, width=1280, height=800)
        os.makedirs(WEBVIEW_STORAGE_PATH, exist_ok=True)
        webview.start(private_mode=False, storage_path=WEBVIEW_STORAGE_PATH)
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
