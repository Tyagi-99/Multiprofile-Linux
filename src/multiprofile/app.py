from __future__ import annotations
import logging
import threading
import time
import webbrowser
from typing import Optional

from .api.server import create_app

log = logging.getLogger("multiprofile.app")


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


def open_ui(url: str = "http://127.0.0.1:8765/", title: str = "Multiprofile Native") -> None:
    """Prefer pywebview GTK; fall back to system browser."""
    try:
        import webview  # type: ignore

        log.info("Starting pywebview UI at %s", url)
        webview.create_window(title, url, width=1280, height=800)
        webview.start()
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
