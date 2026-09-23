"""Honest validation for device presets.

A preset is VALIDATED only when every runnable check passes and every
capability is SUPPORTED. PARTIAL/UNAVAILABLE capabilities with documented
reasons cap the result at PARTIAL. Without a real Chromium binary on the
host, the runtime check returns NOT_RUN and the preset stays PENDING —
never a faked pass.

"Real Chromium" is established by one of two evidence paths:
  1. ``--headless --dump-dom about:blank`` emits real HTML, or
  2. a live DevTools protocol session: the binary launches with
     ``--remote-debugging-port`` and ``/json/version`` returns a genuine
     Chromium/Chrome Browser version string.

Path 2 exists because some real Chromium builds (e.g. Meta's internal
``/opt/meta-chromium/chrome`` 152.x) have a fully working DevTools
protocol — real CDP emulation round-trips succeed — while ``--headless
--dump-dom`` emits nothing. Gating only on path 1 would wrongly reject a
provably real browser; gating on path 2 alone is still strict because it
requires the binary to launch and answer DevTools with a real version.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .preset import (
    ANDROID,
    DESKTOP,
    IOS,
    IPADOS,
    LINUX,
    MACOS,
    MOBILE,
    PARTIAL,
    SUPPORTED,
    TABLET,
    UNAVAILABLE,
    WINDOWS,
    DevicePreset,
)

VALIDATED = "VALIDATED"
PENDING = "PENDING"
UNSUPPORTED = "UNSUPPORTED"

PASS = "PASS"
FAIL = "FAIL"
NOT_RUN = "NOT_RUN"

_NO_CHROMIUM_REASON = "no Chromium binary on host"

_CANDIDATE_BINARIES = [
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
]
_ABSOLUTE_PATHS = [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/opt/google/chrome/chrome",
    "/opt/meta-chromium/chrome",  # Meta-internal Chromium; verified real via DevTools
    "/snap/bin/chromium",
]

_verified_binary: Optional[str] = None
_verified_evidence: Optional[str] = None


def _binary_emits_real_html(path: str, timeout: int = 20) -> Tuple[bool, str]:
    """Evidence path 1: ``--headless --dump-dom about:blank`` emits real HTML."""
    try:
        proc = subprocess.run(
            [path, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
             "--dump-dom", "about:blank"],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (proc.stdout or "").strip().lower()
        if "<html" in out and proc.returncode == 0:
            return True, f"binary {path} emitted real HTML via --headless --dump-dom about:blank"
        return False, f"binary {path} did not emit real HTML (rc={proc.returncode})"
    except Exception as e:  # noqa: BLE001
        return False, f"binary {path} failed to run: {e}"


def _binary_exposes_devtools(path: str, timeout: int = 25) -> Tuple[bool, str]:
    """Evidence path 2: the binary launches headless, exposes the DevTools
    protocol, and /json/version returns a genuine Chromium/Chrome version.

    A live DevTools endpoint that identifies itself with a real
    ``Browser`` version string (e.g. ``Chromium/152.0.7977.82``) is proof
    of a real Chromium-family binary — a stub or wrapper cannot fabricate
    this without embedding the protocol server itself.
    """
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="mpl-binary-probe-")
    proc = None
    try:
        proc = subprocess.Popen(
            [path, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
             f"--remote-debugging-port={port}", f"--user-data-dir={tmpdir}",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not _wait_devtools_alive(proc, port, timeout=timeout):
            return False, f"binary {path} did not expose DevTools on port {port}"
        info = _http_json("GET", f"http://127.0.0.1:{port}/json/version", timeout=5)
        browser = str((info or {}).get("Browser", ""))
        if "chrom" not in browser.lower():
            return False, (
                f"binary {path} exposed DevTools but Browser version "
                f"{browser!r} is not Chromium-family"
            )
        return True, (
            f"binary {path} exposed live DevTools protocol; "
            f"/json/version reports Browser={browser}"
        )
    except Exception as e:  # noqa: BLE001
        return False, f"binary {path} DevTools probe failed: {e}"
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        import shutil as _shutil

        _shutil.rmtree(tmpdir, ignore_errors=True)


def _verify_real_chromium(path: str) -> Tuple[bool, str]:
    """Run both evidence paths; EITHER counts as proof of a real Chromium."""
    ok1, ev1 = _binary_emits_real_html(path)
    if ok1:
        return True, ev1
    ok2, ev2 = _binary_exposes_devtools(path)
    if ok2:
        return True, ev2
    return False, f"{ev1}; {ev2}"


def _quick_binary_alive(path: str) -> bool:
    """Cheap liveness recheck for an already-verified binary (``--version``)."""
    try:
        proc = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=10
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0 and "chrom" in out.lower()
    except Exception:  # noqa: BLE001
        return False


def find_chromium_binary() -> Tuple[Optional[str], str]:
    """Return (path, evidence) for a REAL Chromium binary, else (None, reason).

    Positive results are cached; the cache is re-validated cheaply via
    ``--version``. Misses are re-scanned so a later install is picked up.
    """
    global _verified_binary, _verified_evidence
    if _verified_binary and os.path.exists(_verified_binary):
        if _quick_binary_alive(_verified_binary):
            return _verified_binary, _verified_evidence or ""
        _verified_binary = None
        _verified_evidence = None
    candidates: List[str] = []
    for name in _CANDIDATE_BINARIES:
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    for path in _ABSOLUTE_PATHS:
        if os.path.exists(path) and path not in candidates:
            candidates.append(path)
    if not candidates:
        return None, _NO_CHROMIUM_REASON
    errors = []
    for path in candidates:
        ok, evidence = _verify_real_chromium(path)
        if ok:
            _verified_binary = path
            _verified_evidence = evidence
            return path, evidence
        errors.append(evidence)
    return None, "; ".join(errors) if errors else _NO_CHROMIUM_REASON


# ---------------------------------------------------------------------------
# Shared headless Chromium session (one process, one target per check)
# ---------------------------------------------------------------------------

_shared: Dict[str, Any] = {}
_launch_results: Dict[Tuple, Tuple[bool, str]] = {}


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_json(method: str, url: str, timeout: int = 10) -> Any:
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _wait_devtools(port: int, timeout: int = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _http_json("GET", f"http://127.0.0.1:{port}/json/version", timeout=3)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def _wait_devtools_alive(proc: "subprocess.Popen", port: int, timeout: int = 12) -> bool:
    """Like _wait_devtools but bails out early if the probed process died.

    Used by the binary gate so a dead stub (e.g. an uninstalled snap
    wrapper) fails in ~1s instead of waiting out the whole timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            _http_json("GET", f"http://127.0.0.1:{port}/json/version", timeout=3)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def _cleanup_shared() -> None:
    proc = _shared.pop("proc", None)
    tmpdir = _shared.pop("tmpdir", None)
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    if tmpdir and os.path.isdir(tmpdir):
        import shutil as _shutil

        _shutil.rmtree(tmpdir, ignore_errors=True)


atexit.register(_cleanup_shared)


def _get_shared_browser(binary: str) -> Tuple[subprocess.Popen, int]:
    if "proc" in _shared and _shared["proc"].poll() is None:
        return _shared["proc"], _shared["port"]
    _cleanup_shared()
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="mpl-device-validate-")
    proc = subprocess.Popen(
        [binary, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
         "--remote-allow-origins=*",
         f"--remote-debugging-port={port}", f"--user-data-dir={tmpdir}",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not _wait_devtools(port):
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"Chromium at {binary} did not expose DevTools on port {port}")
    _shared["proc"] = proc
    _shared["port"] = port
    _shared["tmpdir"] = tmpdir
    return proc, port


def _cdp_round_trip(binary: str, preset: DevicePreset, timeout: int = 25) -> Tuple[bool, str]:
    """Apply the preset's metrics via CDP and read back real values.

    PASS only when a live browser applies Emulation.setDeviceMetricsOverride
    and Runtime.evaluate reports back the emulated geometry.
    """
    cache_key = (binary, preset.viewport["width"], preset.viewport["height"],
                 preset.device_scale_factor, preset.mobile)
    if cache_key in _launch_results:
        return _launch_results[cache_key]

    def _fail(detail: str) -> Tuple[bool, str]:
        _launch_results[cache_key] = (False, detail)
        return False, detail

    try:
        _get_shared_browser(binary)
        port = _shared["port"]
    except Exception as e:  # noqa: BLE001
        return _fail(f"could not launch headless Chromium: {e}")

    target_id = None
    ws = None
    try:
        # Open a page carrying a viewport meta tag so mobile emulation reports
        # the emulated CSS viewport (about:blank has no meta tag, and mobile
        # emulation then falls back to the 980px default layout viewport).
        import urllib.parse as _urlparse

        probe_html = (
            "data:text/html,"
            + _urlparse.quote(
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                "<title>mpl-probe</title>",
                safe="",
            )
        )
        target = _http_json(
            "PUT",
            f"http://127.0.0.1:{port}/json/new?{_urlparse.quote(probe_html, safe=':/?&=%,')}",
            timeout=10,
        )
        target_id = target.get("id")
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            return _fail("DevTools target had no webSocketDebuggerUrl")

        from websocket import create_connection  # websocket-client

        ws = create_connection(ws_url, timeout=10)

        def _send(method: str, params: Dict[str, Any], cmd_id: int) -> Dict[str, Any]:
            ws.send(json.dumps({"id": cmd_id, "method": method, "params": params}))
            deadline = time.time() + 10
            while time.time() < deadline:
                msg = json.loads(ws.recv())
                if msg.get("id") == cmd_id:
                    return msg
            raise TimeoutError(f"no CDP response for {method}")

        vw = preset.viewport["width"]
        vh = preset.viewport["height"]
        r1 = _send("Emulation.setDeviceMetricsOverride", {
            "width": vw, "height": vh,
            "deviceScaleFactor": preset.device_scale_factor,
            "mobile": preset.mobile,
        }, 1)
        if "error" in r1:
            return _fail(f"Emulation.setDeviceMetricsOverride rejected: {r1['error']}")

        r2 = _send("Runtime.evaluate", {
            "expression": "JSON.stringify({w: window.innerWidth, h: window.innerHeight})",
            "returnByValue": True,
        }, 2)
        if "error" in r2:
            return _fail(f"Runtime.evaluate rejected: {r2['error']}")
        try:
            val = json.loads(r2["result"]["result"]["value"])
        except Exception as e:  # noqa: BLE001
            return _fail(f"could not parse evaluate result: {e}")

        got_w, got_h = val.get("w"), val.get("h")
        if got_w is None or abs(got_w - vw) > 2 or abs(got_h - vh) > 2:
            return _fail(
                f"round-trip mismatch: applied {vw}x{vh}, page reports {got_w}x{got_h}"
            )
        ok_detail = (
            f"real CDP round-trip via {binary}: applied {vw}x{vh}@{preset.device_scale_factor}, "
            f"page reports {got_w}x{got_h}"
        )
        _launch_results[cache_key] = (True, ok_detail)
        return True, ok_detail
    except Exception as e:  # noqa: BLE001
        return _fail(f"CDP round-trip failed: {e}")
    finally:
        try:
            if ws is not None:
                ws.close()
        except Exception:  # noqa: BLE001
            pass
        if target_id:
            try:
                _http_json("GET", f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=5)
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------

_REQUIRED_HINT_KEYS = ["brands", "full_version_list", "platform", "platform_version",
                       "architecture", "model", "mobile"]

_HINT_PLATFORM = {
    ANDROID: "Android",
    IOS: "iOS",
    IPADOS: "iOS",
    WINDOWS: "Windows",
    LINUX: "Linux",
    MACOS: "macOS",
}


def _check(name: str, status: str, detail: str) -> Dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


class DevicePresetValidator:
    """Validates a DevicePreset; see module docstring for status semantics."""

    def __init__(self) -> None:
        # Binary used by the most recent _check_runtime_launch call; recorded
        # in the validation result as binary evidence (None when no binary).
        self._last_runtime_binary: Optional[str] = None

    def validate(self, preset: DevicePreset) -> Dict[str, Any]:
        checks: List[Dict[str, str]] = [
            self._check_schema(preset),
            self._check_ua_format(preset),
            self._check_client_hints_coherence(preset),
            self._check_viewport_sane(preset),
            self._check_mobile_flag(preset),
            self._check_touch(preset),
            self._check_dpr_sane(preset),
            self._check_screen_matches_viewport(preset),
            self._check_browser_compat(preset),
            self._check_runtime_launch(preset),
            self._check_fingerprint_consistency(preset),
        ]
        statuses = [c["status"] for c in checks]
        if FAIL in statuses:
            overall = UNSUPPORTED
        elif NOT_RUN in statuses:
            overall = PENDING
        elif any(
            v.get("status") in (PARTIAL, UNAVAILABLE)
            for v in (preset.capability_matrix or {}).values()
        ):
            overall = PARTIAL
        else:
            overall = VALIDATED
        preset.validation_status = overall
        rt_check = next((c for c in checks if c["name"] == "runtime_launch"), {})
        return {
            "device_id": preset.id,
            "status": overall,
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "binary": self._last_runtime_binary,
            "binary_evidence": rt_check.get("detail", ""),
            "checks": checks,
        }

    # -- individual checks -------------------------------------------------

    def _check_schema(self, p: DevicePreset) -> Dict[str, str]:
        try:
            assert isinstance(p.id, str) and p.id, "id must be a non-empty string"
            for f in ("name", "manufacturer", "model"):
                assert isinstance(getattr(p, f), str) and getattr(p, f), f"{f} must be non-empty"
            assert p.category in (DESKTOP, MOBILE, TABLET), f"bad category {p.category}"
            assert p.platform in (WINDOWS, LINUX, MACOS, ANDROID, IOS, IPADOS), f"bad platform {p.platform}"
            assert isinstance(p.user_agent, str) and p.user_agent, "user_agent must be non-empty"
            assert isinstance(p.client_hints, dict), "client_hints must be a dict"
            for k in _REQUIRED_HINT_KEYS:
                assert k in p.client_hints, f"client_hints missing key {k}"
            for g in ("viewport", "screen"):
                d = getattr(p, g)
                assert isinstance(d, dict) and isinstance(d.get("width"), int) and isinstance(d.get("height"), int), \
                    f"{g} must be {{width:int, height:int}}"
            assert isinstance(p.device_scale_factor, (int, float)), "device_scale_factor must be numeric"
            assert isinstance(p.mobile, bool) and isinstance(p.touch, bool)
            assert isinstance(p.max_touch_points, int) and p.max_touch_points >= 0
            assert p.hardware_concurrency is None or isinstance(p.hardware_concurrency, int)
            assert p.device_memory_gb is None or isinstance(p.device_memory_gb, (int, float))
            assert isinstance(p.language_defaults, dict) and isinstance(p.media_capabilities, dict)
            assert isinstance(p.timezone_policy, str) and isinstance(p.webgl_policy, str)
            assert isinstance(p.supported_runtime, str) and p.supported_runtime
            assert isinstance(p.capability_matrix, dict) and isinstance(p.limitations, list)
            for cap, v in p.capability_matrix.items():
                assert isinstance(v, dict), f"capability {cap} must be a dict"
                assert v.get("status") in (SUPPORTED, PARTIAL, UNAVAILABLE), \
                    f"capability {cap} has bad status {v.get('status')}"
                assert isinstance(v.get("note"), str) and v["note"], f"capability {cap} needs a note"
            for lim in p.limitations:
                assert isinstance(lim, str) and lim, "limitations must be non-empty strings"
            return _check("schema", PASS, "all required fields present with sane types")
        except AssertionError as e:
            return _check("schema", FAIL, str(e))

    def _check_ua_format(self, p: DevicePreset) -> Dict[str, str]:
        ua = p.user_agent or ""
        if not ua.startswith("Mozilla/5.0"):
            return _check("ua_format", FAIL, "UA does not start with 'Mozilla/5.0'")
        if p.category == MOBILE and p.platform == ANDROID:
            ok = "Linux; Android" in ua and " Mobile " in ua and "Chrome/" in ua
            return _check("ua_format", PASS if ok else FAIL,
                          "Android mobile UA format" if ok else f"Android mobile UA malformed: {ua[:80]}")
        if p.category == MOBILE and p.platform == IOS:
            ok = "(iPhone;" in ua and "CriOS/" in ua and "Chrome/" not in ua
            return _check("ua_format", PASS if ok else FAIL,
                          "iOS-class CriOS UA format" if ok else f"iOS-class UA malformed: {ua[:80]}")
        if p.category == TABLET and p.platform == IPADOS:
            ok = "(iPad;" in ua and "CriOS/" in ua
            return _check("ua_format", PASS if ok else FAIL,
                          "iPadOS-class CriOS UA format" if ok else f"iPadOS-class UA malformed: {ua[:80]}")
        if p.category == TABLET and p.platform == ANDROID:
            ok = "Linux; Android" in ua and " Mobile " not in ua and "Chrome/" in ua
            return _check("ua_format", PASS if ok else FAIL,
                          "Android tablet UA format (no Mobile token)" if ok else f"tablet UA malformed: {ua[:80]}")
        if p.category == DESKTOP:
            token = {"Windows": "(Windows NT", "Linux": "(X11; Linux", "macOS-class": "(Macintosh;"}.get(p.platform, "")
            ok = bool(token) and token in ua and "Chrome/" in ua and " Mobile" not in ua and "Android" not in ua
            return _check("ua_format", PASS if ok else FAIL,
                          "desktop Chrome UA format" if ok else f"desktop UA malformed: {ua[:80]}")
        return _check("ua_format", FAIL, f"no UA format rule for {p.category}/{p.platform}")

    def _check_client_hints_coherence(self, p: DevicePreset) -> Dict[str, str]:
        h = p.client_hints or {}
        expected_platform = _HINT_PLATFORM.get(p.platform)
        if h.get("platform") != expected_platform:
            return _check("client_hints_coherence", FAIL,
                          f"hints platform {h.get('platform')!r} != expected {expected_platform!r}")
        expected_mobile = p.category == MOBILE
        if bool(h.get("mobile")) != expected_mobile:
            return _check("client_hints_coherence", FAIL,
                          f"hints mobile={h.get('mobile')} but category={p.category}")
        if p.category == MOBILE and not h.get("model"):
            return _check("client_hints_coherence", FAIL, "mobile hints require a non-empty model")
        if not h.get("brands") or not h.get("full_version_list"):
            return _check("client_hints_coherence", FAIL, "brands/full_version_list must be non-empty")
        return _check("client_hints_coherence", PASS,
                      f"platform={h.get('platform')}, mobile={h.get('mobile')}, model={h.get('model')!r} coherent")

    def _check_viewport_sane(self, p: DevicePreset) -> Dict[str, str]:
        w, h = p.viewport["width"], p.viewport["height"]
        if w < 200 or h < 200:
            return _check("viewport_sane", FAIL, f"viewport {w}x{h} too small")
        ratio = w / h
        if not (0.2 <= ratio <= 5.0):
            return _check("viewport_sane", FAIL, f"viewport aspect {ratio:.2f} implausible")
        return _check("viewport_sane", PASS, f"viewport {w}x{h} sane")

    def _check_mobile_flag(self, p: DevicePreset) -> Dict[str, str]:
        expected = p.category == MOBILE
        if p.mobile != expected:
            return _check("mobile_flag_matches_category", FAIL,
                          f"mobile={p.mobile} but category={p.category}")
        return _check("mobile_flag_matches_category", PASS, f"mobile={p.mobile} matches {p.category}")

    def _check_touch(self, p: DevicePreset) -> Dict[str, str]:
        needs_touch = p.category in (MOBILE, TABLET)
        if needs_touch and not (p.touch and p.max_touch_points > 0):
            return _check("touch_matches_category", FAIL,
                          f"{p.category} requires touch with max_touch_points>0")
        if not needs_touch and p.touch:
            return _check("touch_matches_category", FAIL,
                          f"{p.category} must not declare touch")
        return _check("touch_matches_category", PASS,
                      f"touch={p.touch}, max_touch_points={p.max_touch_points} matches {p.category}")

    def _check_dpr_sane(self, p: DevicePreset) -> Dict[str, str]:
        dpr = p.device_scale_factor
        if not (1.0 <= dpr <= 4.0):
            return _check("dpr_sane", FAIL, f"DPR {dpr} outside [1.0, 4.0]")
        return _check("dpr_sane", PASS, f"DPR {dpr} in [1.0, 4.0]")

    def _check_screen_matches_viewport(self, p: DevicePreset) -> Dict[str, str]:
        dpr = p.device_scale_factor
        dw = abs(p.screen["width"] - p.viewport["width"] * dpr)
        dh = abs(p.screen["height"] - p.viewport["height"] * dpr)
        if dw > 1 or dh > 1:
            return _check("screen_matches_viewport_x_dpr", FAIL,
                          f"screen {p.screen} != viewport {p.viewport} x DPR {dpr} (off by {dw:.2f}, {dh:.2f})")
        return _check("screen_matches_viewport_x_dpr", PASS,
                      f"screen == viewport x DPR within ±1 ({dw:.2f}, {dh:.2f})")

    def _check_browser_compat(self, p: DevicePreset) -> Dict[str, str]:
        if p.browser_family not in ("Chrome", "Chromium"):
            return _check("browser_compat", FAIL, f"browser_family {p.browser_family!r} not launchable here")
        if p.supported_runtime != "linux-chromium":
            return _check("browser_compat", FAIL, f"supported_runtime {p.supported_runtime!r} != 'linux-chromium'")
        if not p.browser_version_policy:
            return _check("browser_compat", FAIL, "browser_version_policy empty")
        if p.webgl_policy != "real":
            return _check("browser_compat", FAIL,
                          f"webgl_policy {p.webgl_policy!r} must be 'real' (no fabricated renderer strings)")
        return _check("browser_compat", PASS,
                      f"{p.browser_family} {p.browser_version_policy} on {p.supported_runtime}; webgl_policy=real")

    def _check_runtime_launch(self, p: DevicePreset) -> Dict[str, str]:
        binary, evidence = find_chromium_binary()
        self._last_runtime_binary = binary
        if not binary:
            return _check("runtime_launch", NOT_RUN,
                          f"{_NO_CHROMIUM_REASON} ({evidence[:120]})")
        ok, detail = _cdp_round_trip(binary, p)
        return _check("runtime_launch", PASS if ok else FAIL, detail)

    def _check_fingerprint_consistency(self, p: DevicePreset) -> Dict[str, str]:
        ua = p.user_agent or ""
        h = p.client_hints or {}
        problems = []
        if bool(h.get("mobile")) != p.mobile:
            problems.append("hints.mobile != preset.mobile")
        if p.platform == ANDROID and p.category == MOBILE and " Mobile " not in ua:
            problems.append("Android mobile UA missing 'Mobile' token")
        if p.platform == ANDROID and p.category == TABLET and " Mobile " in ua:
            problems.append("Android tablet UA must not carry the 'Mobile' token")
        if p.platform in (IOS, IPADOS) and "Chrome/" in ua:
            problems.append("iOS-class UA must use CriOS, not Chrome/")
        if p.category == DESKTOP and (" Mobile" in ua or "Android" in ua):
            problems.append("desktop UA must not contain Mobile/Android tokens")
        if p.mobile and "Mobile" not in ua:
            problems.append("mobile preset UA missing any Mobile token")
        if problems:
            return _check("fingerprint_consistency", FAIL, "; ".join(problems))
        return _check("fingerprint_consistency", PASS, "UA / Client-Hints / flags cross-check coherent")
