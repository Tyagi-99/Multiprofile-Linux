#!/usr/bin/env python3
"""Hold profiles open and watch for drift. Duration is a parameter.

    python3 scripts/endurance.py --minutes 5 --profiles 2
    python3 scripts/endurance.py --minutes 1440 --profiles 3   # the 24h run

`docs/10_RELEASE_READINESS.md` §4 lists a 24-hour session as unverified, and
this is the instrument that closes it. It is a script rather than a test
because the suite must not take a day, and because the honest version of
"we tested long sessions" names the duration that was actually run.

What it watches, every sample:

* **RSS and open file descriptors** per browser process. A leak that matters
  at hour 18 is invisible in a ten-minute run, so the output is a series, not
  a verdict -- the slope is the point.
* **CDP responsiveness.** A browser that is alive but no longer answers
  DevTools is a profile the product can no longer steer.
* **Identity stability.** The same fields the parity harness compares, at
  every sample. A profile whose canvas hash or platform drifts mid-session
  re-identifies itself to a site without ever restarting.
* **Supervisor interference.** If the supervisor restarts a profile the pid
  changes, and that is a different session wearing the same name.

Exit status is 0 when every profile survived with a stable identity, 1 when
anything drifted, died or stopped answering.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: The fields a session must not change. Deliberately the same set the
#: identity gate compares, so "stable across a long session" and "coherent
#: across realms" cannot drift apart as definitions.
STABLE_FIELDS = ("platform", "userAgent", "uaPlatform", "cores", "memory",
                 "language", "timezone", "glRenderer", "canvasHash")

_PROBE = (
    "JSON.stringify({"
    "platform:navigator.platform,"
    "userAgent:navigator.userAgent,"
    "uaPlatform:(navigator.userAgentData?String(navigator.userAgentData.platform):null),"
    "cores:navigator.hardwareConcurrency,"
    "memory:(navigator.deviceMemory!==undefined?navigator.deviceMemory:null),"
    "language:navigator.language,"
    "timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,"
    "glRenderer:(()=>{try{const c=document.createElement('canvas').getContext('webgl');"
    "const d=c.getExtension('WEBGL_debug_renderer_info');"
    "return String(c.getParameter(d.UNMASKED_RENDERER_WEBGL));}catch(e){return null}})(),"
    "canvasHash:(()=>{try{const c=document.createElement('canvas');c.width=20;c.height=20;"
    "const x=c.getContext('2d');x.fillStyle='#f30';x.fillRect(0,0,20,20);"
    "x.fillStyle='#08f';x.font='12px serif';x.fillText('Ab',1,14);"
    "const d=x.getImageData(0,0,20,20).data;let h=0;"
    "for(let i=0;i<d.length;i++)h=(h*31+d[i])>>>0;return h;}catch(e){return null}})()"
    "})"
)


class _Origin:
    """A real origin, so the page is not about:blank for hours."""

    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>endurance</title>ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/e"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _process_stats(pid: int) -> dict[str, int | None]:
    """RSS in KiB and open file descriptors, from /proc."""
    rss = None
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1])
                break
    except OSError:
        pass
    try:
        fds = len(list(Path(f"/proc/{pid}/fd").iterdir()))
    except OSError:
        fds = None
    return {"rssKib": rss, "fds": fds}


def _probe(port: int, timeout: float = 20.0) -> dict | None:
    import websocket

    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"}
        )
        targets = json.loads(urllib.request.urlopen(request, timeout=5).read())
        target = next(t for t in targets if t.get("type") == "page")
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=timeout, suppress_origin=True)
    except Exception:  # noqa: BLE001 - an unreachable browser is the finding
        return None
    try:
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": _PROBE, "returnByValue": True},
        }))
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = json.loads(ws.recv())
            if message.get("id") == 1:
                value = ((message.get("result") or {}).get("result") or {}).get("value")
                return json.loads(value) if value else None
        return None
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001,S110 - closing a dead socket is not a failure
            pass


def run(minutes: float, count: int, interval: float) -> dict:
    import multiprofile.browser.launcher as launcher
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile, delete_profile

    origin = _Origin()
    profiles: list[dict] = []
    samples: list[dict] = []
    problems: list[str] = []
    try:
        for index in range(count):
            created = create_profile({
                "name": f"endurance-{index}-{int(time.time())}",
                "proxyType": "none", "startUrl": origin.url,
            })
            result = launcher.launch_profile(
                created["id"], url=origin.url, direct=True)
            if not result.get("ok"):
                problems.append(f"{created['id']}: launch failed: {result.get('error')}")
                continue
            profiles.append({"id": created["id"], "pid": result.get("pid")})
        if not profiles:
            return {"ok": False, "problems": problems, "samples": []}

        time.sleep(3.0)
        baseline: dict[str, dict] = {}
        deadline = time.monotonic() + minutes * 60
        sample_index = 0
        while True:
            sample_index += 1
            now = time.monotonic()
            row: dict = {"t": round(minutes * 60 - max(0.0, deadline - now), 1),
                         "profiles": {}}
            for profile in profiles:
                pid = profile["id"]
                runtime = get_runtime(pid)
                port = getattr(runtime, "cdp_port", None)
                live_pid = getattr(runtime, "pid", None)
                entry: dict = {"pid": live_pid}
                if live_pid:
                    entry.update(_process_stats(int(live_pid)))
                if live_pid != profile["pid"]:
                    problems.append(
                        f"{pid}: pid changed {profile['pid']} -> {live_pid}; the "
                        "supervisor restarted it, so this is a different session"
                    )
                    profile["pid"] = live_pid
                identity = _probe(port) if port else None
                if identity is None:
                    entry["cdp"] = "unreachable"
                    problems.append(f"{pid}: CDP did not answer at t={row['t']}s")
                else:
                    entry["cdp"] = "ok"
                    if pid not in baseline:
                        baseline[pid] = identity
                    else:
                        for field in STABLE_FIELDS:
                            if identity.get(field) != baseline[pid].get(field):
                                problems.append(
                                    f"{pid}: {field} drifted at t={row['t']}s: "
                                    f"{baseline[pid].get(field)!r} -> "
                                    f"{identity.get(field)!r}"
                                )
                row["profiles"][pid] = entry
            samples.append(row)
            print(f"  t={row['t']:>7.1f}s  " + "  ".join(
                f"{k[-6:]}:{v.get('rssKib') or '?'}KiB/{v.get('fds') or '?'}fd/{v.get('cdp')}"
                for k, v in row["profiles"].items()), flush=True)
            if time.monotonic() >= deadline:
                break
            time.sleep(min(interval, max(1.0, deadline - time.monotonic())))

        trend = {}
        for profile in profiles:
            pid = profile["id"]
            series = [s["profiles"].get(pid, {}).get("rssKib") for s in samples]
            series = [v for v in series if v]
            if len(series) >= 3:
                trend[pid] = {
                    "rssFirstKib": series[0], "rssLastKib": series[-1],
                    "rssGrowthKib": series[-1] - series[0],
                    "rssMedianKib": int(statistics.median(series)),
                    "samples": len(series),
                }
        return {"ok": not problems, "problems": problems,
                "samples": samples, "trend": trend,
                "minutes": minutes, "profileCount": len(profiles)}
    finally:
        for profile in profiles:
            try:
                launcher.stop_profile(profile["id"])
                delete_profile(profile["id"], purge_storage=True)
            except Exception as e:  # noqa: BLE001 - cleanup is best effort
                print(f"cleanup warning for {profile['id']}: {e}", file=sys.stderr)
        origin.close()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=5.0)
    parser.add_argument("--profiles", type=int, default=2)
    parser.add_argument("--interval", type=float, default=30.0,
                        help="seconds between samples")
    parser.add_argument("--out", type=Path, help="write the full series here")
    args = parser.parse_args(argv[1:])

    print(f"holding {args.profiles} profile(s) for {args.minutes} minute(s), "
          f"sampling every {args.interval}s")
    report = run(args.minutes, args.profiles, args.interval)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2))
        print(f"\nseries: {args.out}")

    print("\nRSS trend (growth over the run):")
    for pid, trend in (report.get("trend") or {}).items():
        print(f"  {pid}  {trend['rssFirstKib']} -> {trend['rssLastKib']} KiB "
              f"({trend['rssGrowthKib']:+d} KiB over {trend['samples']} samples)")
    problems = report.get("problems") or []
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for problem in problems[:20]:
            print(f"  - {problem}")
    else:
        print(f"\nno drift, no deaths, CDP answered every sample "
              f"({report.get('minutes')} minute(s))")
    print("\nNOTE: this run proves nothing about durations longer than it "
          "actually ran. A leak that matters at hour 18 is invisible in a "
          "short run; the RSS series above is the evidence, not the verdict.")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
