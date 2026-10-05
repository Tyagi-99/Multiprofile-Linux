#!/usr/bin/env python3
"""Compare a Multiprofile profile against stock Chromium on the same host.

    python3 scripts/identity_parity.py --out /tmp/parity.json
    python3 scripts/identity_parity.py --profile profile_abc123 --out /tmp/p.json
    python3 scripts/identity_parity.py --no-control        # product only

Why both sides in one run: a difference is only attributable to the product
if the control used the same engine build, the same display, the same session
and the same collector. Running the control yesterday, or on another machine,
measures the environment.

The report is **redacted by construction** -- no IP addresses, no cookies, no
credentials, no raw fingerprint hashes -- so it can be pasted into an issue.
See ``multiprofile.diagnostics.identity_probe``.

Exit status: 0 when no contradictions were found, 1 when any were, 2 when the
harness itself could not measure. Divergences from the control never fail the
run on their own: spoofing a platform is a divergence and it is the product
working.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from multiprofile.diagnostics.identity_probe import (  # noqa: E402
    SERVICE_WORKER_SOURCE,
    WORKER_SOURCE,
    Manifest,
    collect_manifest,
    compare_manifests,
    redact,
)


class ProbeServer:
    """Serves the probe page, the URL worker, the service worker -- and
    records the request headers of the first document request.

    The headers matter on their own: a new tab's first document request is
    formed by the browser process before any renderer override lands, which
    is the residual F1 this records rather than assumes.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                if self.path.startswith("/probe-worker.js"):
                    body, ctype = WORKER_SOURCE, "text/javascript"
                elif self.path.startswith("/probe-sw.js"):
                    body, ctype = SERVICE_WORKER_SOURCE, "text/javascript"
                else:
                    body, ctype = (
                        b"<!doctype html><html><head><title>parity probe</title>"
                        b"</head><body>probe</body></html>", "text/html")
                    outer.requests.append({
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                    })
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/probe"

    def first_document_headers(self) -> dict[str, Any]:
        """Only the identity-bearing headers, and only the first request.

        Cookies are dropped rather than redacted: there is no version of a
        cookie value that belongs in a shareable report.
        """
        keep = (
            "user-agent", "accept-language", "accept", "accept-encoding",
            "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
            "sec-ch-ua-platform-version", "sec-ch-ua-arch", "sec-ch-ua-model",
            "sec-ch-ua-bitness", "sec-ch-ua-full-version",
            "sec-ch-ua-full-version-list", "sec-ch-ua-wow64",
            "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest",
            "upgrade-insecure-requests", "dnt",
        )
        for request in self.requests:
            headers = request["headers"]
            if "user-agent" not in headers:
                continue
            return redact({k: headers[k] for k in keep if k in headers})
        return {}

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _environment(engine_detail: str | None = None) -> dict[str, Any]:
    """Everything needed to say what was measured and where."""
    return {
        "host": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "displayServer": (
                "wayland" if os.environ.get("WAYLAND_DISPLAY")
                else "x11" if os.environ.get("DISPLAY") else "none"
            ),
            "desktop": os.environ.get("XDG_CURRENT_DESKTOP") or None,
            "cores": os.cpu_count(),
        },
        "engine": engine_detail,
        "collectedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _engine_detail() -> str | None:
    try:
        from multiprofile.engine import describe_engine

        return str(describe_engine().get("detail"))
    except Exception:  # noqa: BLE001 - reported as unknown rather than fatal
        return None


def run_product(url: str, profile_id: str | None) -> tuple[Manifest, str | None]:
    """Launch (or reuse) a product profile and collect its manifest."""
    import multiprofile.browser.launcher as launcher
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile, get_profile

    created = False
    if profile_id:
        if get_profile(profile_id) is None:
            raise SystemExit(f"no such profile: {profile_id}")
    else:
        profile_id = create_profile({
            "name": f"parity-probe-{int(time.time())}",
            "proxyType": "none",
            "startUrl": url,
        })["id"]
        created = True

    result = launcher.launch_profile(profile_id, url=url, direct=True)
    if not result.get("ok"):
        raise SystemExit(f"launch failed: {result.get('error')}")
    time.sleep(2.5)
    port = get_runtime(profile_id).cdp_port
    manifest = collect_manifest(
        port, label="multiprofile",
        environment=_environment(_engine_detail()),
    )
    manifest.environment["profile"] = {
        "id": profile_id,
        "createdByHarness": created,
        "devicePreset": (get_profile(profile_id) or {}).get("devicePresetId"),
        "route": "direct",
        "proxy": None,
    }
    return manifest, profile_id


def run_control(url: str) -> Manifest:
    """Stock Chromium, throwaway profile, same host, same session.

    Deliberately minimal flags: a control carrying the product's flags is not
    a control. Only the remote-debugging port, a scratch user-data-dir and
    first-run suppression, plus the software-GL flags when the host has no
    graphics device -- without those a GPU-less host has no WebGL at all and
    the comparison loses the whole GL surface (F46).
    """
    from multiprofile.browser.launcher import find_chrome

    binary = find_chrome()
    port = _free_port()
    user_data = Path(tempfile.mkdtemp(prefix="parity-control-"))
    flags = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        url,
    ]
    try:
        from multiprofile.browser.launcher import _probe_gpu_available

        if not _probe_gpu_available():
            flags[-1:-1] = ["--disable-gpu", "--use-gl=swiftshader",
                            "--enable-unsafe-swiftshader"]
    except Exception as e:  # noqa: BLE001 - the control still runs without it
        print(f"control: could not probe for a GPU ({e}); "
              "WebGL may be absent on this host", file=sys.stderr)

    process = subprocess.Popen(
        flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        time.sleep(3.0)
        manifest = collect_manifest(
            port, label="stock-chromium",
            environment=_environment(_engine_detail()),
        )
        manifest.environment["profile"] = {
            "id": None, "createdByHarness": True, "devicePreset": None,
            "route": "direct", "proxy": None,
            "note": "stock Chromium, scratch user-data-dir, no product flags",
        }
        return manifest
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(user_data, ignore_errors=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", help="existing profile id; one is created if omitted")
    parser.add_argument("--out", type=Path, help="write the full report here")
    parser.add_argument("--no-control", action="store_true",
                        help="skip the stock Chromium control (weaker evidence)")
    parser.add_argument("--keep-profile", action="store_true",
                        help="do not delete a profile this harness created")
    args = parser.parse_args(argv[1:])

    server = ProbeServer()
    product: Manifest | None = None
    control: Manifest | None = None
    created_id: str | None = None
    try:
        print(f"probe origin: {server.url}")
        print("collecting: multiprofile ...")
        product, profile_id = run_product(server.url, args.profile)
        if args.profile is None:
            created_id = profile_id
        product.headers = server.first_document_headers()

        if not args.no_control:
            server.requests.clear()
            print("collecting: stock chromium control ...")
            control = run_control(server.url)
            control.headers = server.first_document_headers()
    finally:
        try:
            import multiprofile.browser.launcher as launcher

            if product is not None:
                launcher.stop_profile(
                    (product.environment.get("profile") or {}).get("id") or ""
                )
            if created_id and not args.keep_profile:
                from multiprofile.storage.profiles import delete_profile

                delete_profile(created_id, purge_storage=True)
        except Exception as e:  # noqa: BLE001 - reported, never fatal to the report
            print(f"cleanup warning: {e}", file=sys.stderr)
        server.close()

    if product is None or not product.observations:
        print("FAILED: the product manifest is empty", file=sys.stderr)
        for error in (product.errors if product else []):
            print(f"  - {error}", file=sys.stderr)
        return 2

    findings = compare_manifests(product, control)
    report = {
        "product": product.to_dict(),
        "control": control.to_dict() if control else None,
        "findings": [f.to_dict() for f in findings],
    }
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"\nreport: {args.out}")

    contradictions = [f for f in findings if f.kind == "contradiction"]
    divergences = [f for f in findings if f.kind != "contradiction"]

    print(f"\ncontradictions (no real browser produces these): {len(contradictions)}")
    for finding in contradictions:
        print(f"  FAIL  {finding.surface}: {finding.detail}")
        print(f"          got {finding.product!r} / expected {finding.control!r}")
    print(f"\ndivergences from the control (for a human to judge): {len(divergences)}")
    for finding in divergences:
        print(f"  {finding.severity.upper():5} {finding.surface}: {finding.detail}")

    if control is None:
        print("\nNOTE: no control was collected, so nothing here is attributable "
              "to the product rather than to this host.")
    return 1 if contradictions else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
