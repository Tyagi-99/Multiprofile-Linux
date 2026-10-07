#!/usr/bin/env python3
"""Can the resolved engine actually start headful and serve CDP?

Run before a live-browser test suite. The point is to fail at the engine,
with the engine's own stderr, rather than let an engine that cannot start
cascade into hundreds of unreadable test errors -- which is exactly what
happened the first time this job was pointed at a different browser: 29
failures and 192 errors, none of which named the cause.

Two properties are checked, because they fail for different reasons:

  1. The capability gate accepts it (it can load unpacked extensions).
  2. It starts **headful** and answers CDP.

Headful matters. The product launches with --ozone-platform=x11 because
window tiling is a real feature and CDP can only set bounds on a window that
exists, so "it runs headless" would not be evidence about anything this
product does.

CDP is the success signal rather than --dump-dom. --dump-dom is a headless
facility: asked for it headful, Chrome opens a window and never exits, and
the check times out having proved nothing. That is not hypothetical -- it is
what the first version of this script did.

Exit 0 and print what it found, or exit 1 naming the fault.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

READY_TIMEOUT_S = float(os.environ.get("MP_PREFLIGHT_TIMEOUT", "45"))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    sys.path.insert(0, "src")
    from multiprofile.engine_choice import resolve

    cap = resolve()
    print(f"resolved: binary={cap.binary} brand={cap.brand} "
          f"version={cap.version} unpacked={cap.unpacked} origin={cap.origin}",
          flush=True)
    if not cap.binary:
        print("FAIL: no engine binary resolved at all", file=sys.stderr)
        return 1
    if not cap.unpacked:
        print(f"FAIL: this engine cannot load unpacked extensions: {cap.reason}",
              file=sys.stderr)
        if cap.remediation:
            print(cap.remediation, file=sys.stderr)
        return 1

    port = free_port()
    # Not TemporaryDirectory. Chrome's child processes keep writing to the
    # user-data directory for a moment after the browser process exits, so
    # the context manager's rmtree raced them and the preflight died with
    #     OSError: [Errno 39] Directory not empty: 'Default'
    # *after* printing "headful CDP ok" -- reporting failure for an engine it
    # had just proven healthy. A cleanup race must never be able to do that,
    # so the directory is removed best-effort at the end instead.
    data_dir = tempfile.mkdtemp(prefix="mp-preflight-")
    try:
        proc = subprocess.Popen(
            [cap.binary,
             f"--user-data-dir={data_dir}",
             "--ozone-platform=x11",
             "--no-first-run",
             "--no-default-browser-check",
             "--disable-search-engine-choice-screen",
             f"--remote-debugging-port={port}",
             "about:blank"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.time() + READY_TIMEOUT_S
            version = None
            while time.time() < deadline:
                if proc.poll() is not None:
                    out, err = proc.communicate()
                    print(f"FAIL: the engine exited rc={proc.returncode} before "
                          f"serving CDP\nstdout={out[-2000:]}\nstderr={err[-3000:]}",
                          file=sys.stderr)
                    return 1
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                        version = json.loads(r.read())
                    break
                except (urllib.error.URLError, OSError, ValueError):
                    time.sleep(0.5)
            if version is None:
                proc.terminate()
                try:
                    out, err = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, err = proc.communicate(timeout=10)
                print(f"FAIL: the engine started but did not answer CDP within "
                      f"{READY_TIMEOUT_S:.0f}s\nstdout={out[-2000:]}\n"
                      f"stderr={err[-3000:]}", file=sys.stderr)
                return 1
            print(f"headful CDP ok: {version.get('Browser')} "
                  f"webkit={version.get('WebKit-Version')}", flush=True)
            return 0
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
    finally:
        # Best-effort: whether the scratch directory could be removed says
        # nothing about the engine, which is the only thing this script is
        # here to report on.
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
