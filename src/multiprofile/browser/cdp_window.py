"""Localhost-only Chromium CDP outer-window bounds control."""
from __future__ import annotations

import json
import logging
import socket
import time
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .grid import Rect

log = logging.getLogger("multiprofile.browser.cdp_window")

try:
    import websocket
except ImportError:  # pragma: no cover
    websocket = None  # type: ignore


class CdpError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def allocate_localhost_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_cdp_ready(port: int, timeout_s: float = 8.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.monotonic() + timeout_s
    last = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            ws = data.get("webSocketDebuggerUrl") or ""
            host = urlparse(ws).hostname if ws else "127.0.0.1"
            if host not in {"127.0.0.1", "localhost"}:
                raise CdpError("cdp_not_localhost", f"CDP host not localhost: {host}")
            return data
        except CdpError:
            raise
        except Exception as e:
            last = str(e)
            time.sleep(0.15)
    raise CdpError("cdp_unreachable", f"CDP not ready on 127.0.0.1:{port}: {last}")


@dataclass
class PageTarget:
    target_id: str
    title: str
    url: str
    type: str = "page"


class CdpWindowController:
    def __init__(self, port: int):
        if websocket is None:
            raise CdpError("cdp_dependency_missing", "websocket-client is required")
        self.port = int(port)
        self._ws = None
        self._id = 0

    def connect(self, timeout_s: float = 8.0) -> None:
        info = wait_cdp_ready(self.port, timeout_s=timeout_s)
        path = urlparse(info.get("webSocketDebuggerUrl") or "").path
        ws_url = f"ws://127.0.0.1:{self.port}{path}"
        # No Origin header: keeps Chrome's default DevTools origin check on.
        self._ws = websocket.create_connection(ws_url, timeout=timeout_s, suppress_origin=True)
        self._ws.settimeout(timeout_s)

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def __enter__(self) -> "CdpWindowController":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _call(self, method: str, params: dict | None = None) -> dict[str, Any]:
        if self._ws is None:
            raise CdpError("cdp_unreachable", "not connected")
        self._id += 1
        mid = self._id
        self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self._ws.recv())
            if msg.get("id") != mid:
                continue
            if "error" in msg:
                err = msg["error"]
                text = err.get("message") if isinstance(err, dict) else str(err)
                raise CdpError("cdp_rpc_error", f"{method}: {text}")
            return msg.get("result") or {}

    def list_targets(self) -> list[PageTarget]:
        try:
            result = self._call("Target.getTargets")
            raw = result.get("targetInfos") or []
            return [
                PageTarget(
                    target_id=t.get("targetId") or "",
                    title=t.get("title") or "",
                    url=t.get("url") or "",
                    type=t.get("type") or "",
                )
                for t in raw
                if t.get("targetId")
            ]
        except CdpError:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2) as resp:
                items = json.loads(resp.read().decode("utf-8"))
            return [
                PageTarget(
                    target_id=t.get("id") or "",
                    title=t.get("title") or "",
                    url=t.get("url") or "",
                    type=t.get("type") or "page",
                )
                for t in items
                if t.get("id")
            ]

    def pick_page_target(self, start_url: str | None = None) -> PageTarget:
        targets = self.list_targets()
        pages = [
            t
            for t in targets
            if t.type == "page"
            and not (t.url or "").startswith("devtools://")
            and "chrome-extension://" not in (t.url or "")
        ]
        rejected = [t for t in targets if t not in pages]
        if not pages:
            raise CdpError("no_page_target", f"no page targets among {len(targets)} targets")

        chosen = None
        if start_url:
            for t in pages:
                if start_url in (t.url or ""):
                    chosen = t
                    break
        if chosen is None:
            for t in pages:
                u = t.url or ""
                if u in {"about:blank", "chrome://newtab/", "chrome://new-tab-page/"} or u.startswith(
                    "chrome://newtab"
                ):
                    chosen = t
                    break
        if chosen is None:
            chosen = sorted(pages, key=lambda t: t.target_id)[0]

        log.info(
            "selected target=%s url=%s rejected=%s",
            chosen.target_id,
            chosen.url,
            [(t.type, t.target_id[:8], (t.url or "")[:50]) for t in rejected[:10]],
        )
        return chosen

    def list_page_windows(self) -> list[tuple[str, int, str]]:
        """``(target_id, window_id, url)`` for every real page target.

        Same filter as :meth:`pick_page_target` -- devtools and extension
        targets are not windows the operator sees. A target whose window
        cannot be resolved is skipped rather than guessed at.
        """
        pairs: list[tuple[str, int, str]] = []
        for t in self.list_targets():
            if t.type != "page":
                continue
            url = t.url or ""
            if url.startswith("devtools://") or "chrome-extension://" in url:
                continue
            try:
                window_id, _ = self.get_window_id_for_target(t.target_id)
            except CdpError as e:
                log.debug("no window for target %s: %s", t.target_id[:8], e)
                continue
            pairs.append((t.target_id, window_id, url))
        return pairs

    def close_target(self, target_id: str) -> bool:
        """Close one page target. Closing a window's last tab closes it."""
        try:
            result = self._call("Target.closeTarget", {"targetId": target_id})
        except CdpError as e:
            log.warning("close target %s failed: %s", target_id[:8], e)
            return False
        # Newer Chrome returns {} on success; older returns {"success": bool}.
        return bool(result.get("success", True))

    def get_window_id_for_target(self, target_id: str) -> tuple[int, Rect | None]:
        try:
            result = self._call("Browser.getWindowForTarget", {"targetId": target_id})
        except CdpError as e:
            raise CdpError("cdp_window_bounds_unsupported", str(e)) from e
        wid = result.get("windowId")
        if wid is None:
            raise CdpError("window_not_found", f"no windowId for target {target_id}")
        b = result.get("bounds") or {}
        rect = None
        if b:
            rect = Rect(int(b.get("left") or 0), int(b.get("top") or 0), int(b.get("width") or 0), int(b.get("height") or 0))
        return int(wid), rect

    def set_window_bounds(self, window_id: int, rect: Rect) -> None:
        self._call(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "left": int(rect.x),
                    "top": int(rect.y),
                    "width": int(rect.w),
                    "height": int(rect.h),
                    "windowState": "normal",
                },
            },
        )

    def get_window_bounds(self, window_id: int) -> Rect:
        result = self._call("Browser.getWindowBounds", {"windowId": window_id})
        b = result.get("bounds") or {}
        return Rect(int(b.get("left") or 0), int(b.get("top") or 0), int(b.get("width") or 0), int(b.get("height") or 0))

    def apply_and_verify(
        self,
        window_id: int,
        desired: Rect,
        *,
        tolerance: int = 4,
        retries: int = 3,
        settle_ms: int = 200,
    ) -> dict[str, Any]:
        last = None
        for attempt in range(1, retries + 1):
            try:
                self.set_window_bounds(window_id, desired)
            except CdpError as e:
                return {
                    "ok": False,
                    "attempts": attempt,
                    "error": e.code,
                    "message": str(e),
                    "desired": desired.to_dict(),
                    "actual": None,
                }
            time.sleep(max(0, settle_ms) / 1000.0)
            try:
                actual = self.get_window_bounds(window_id)
            except CdpError as e:
                return {
                    "ok": False,
                    "attempts": attempt,
                    "error": e.code,
                    "message": str(e),
                    "desired": desired.to_dict(),
                    "actual": None,
                }
            last = actual
            if (
                abs(actual.x - desired.x) <= tolerance
                and abs(actual.y - desired.y) <= tolerance
                and abs(actual.w - desired.w) <= tolerance
                and abs(actual.h - desired.h) <= tolerance
            ):
                return {
                    "ok": True,
                    "attempts": attempt,
                    "desired": desired.to_dict(),
                    "actual": actual.to_dict(),
                    "error": None,
                    "message": None,
                }
        return {
            "ok": False,
            "attempts": retries,
            "error": "verify_failed",
            "message": f"bounds not within {tolerance}px after {retries} attempts",
            "desired": desired.to_dict(),
            "actual": last.to_dict() if last else None,
        }


def wait_for_cdp(port: int, timeout_s: float = 8.0):
    return wait_cdp_ready(port, timeout_s=timeout_s)
