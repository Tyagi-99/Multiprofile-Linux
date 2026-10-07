#!/usr/bin/env python3
"""Smoke-test an installation by driving the API the console uses.

Run this after installing, on any machine, before trusting it with real work.
It exercises what an operator actually does -- create, edit, launch, stop,
restart, proxy on and off, backup, restore, and the whole extension lifecycle
-- and prints a pass/fail line for each check.

Two properties make it safe on a machine that already holds real profiles:

  * It runs against a scratch XDG_DATA_HOME (--data-dir, default under the
    system temporary directory). The operator's own store is never opened.
  * It starts its own server on a free port, so a server already running is
    not disturbed.

By default it tests whatever `multiprofile` is on PATH. --binary points it at
another copy, which is how you compare a .deb install against a from-source
install on the same machine.

Browser checks are skipped with a reason when no usable engine is present, so
this is still useful on a headless box -- but then it is not evidence about
launching anything. Exit status is 0 only if every check that ran passed.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "extensions" / "mp-test-ext"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    outcome = PASS if ok else FAIL
    results.append((name, outcome, detail))
    print(f"[{outcome}] {name}" + (f"  -- {detail}" if detail and not ok else ""),
          flush=True)
    return ok


def skip(name: str, why: str) -> None:
    results.append((name, SKIP, why))
    print(f"[{SKIP}] {name}  -- {why}", flush=True)


def note(text: str) -> None:
    print(f"       {text}", flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class App:
    """A server process on a scratch data directory."""

    def __init__(self, binary: str, data_home: str, port: int | None = None):
        self.binary = binary
        self.data_home = data_home
        self.port = port or free_port()
        self.proc: subprocess.Popen | None = None
        self.log_path = Path(data_home) / "smoke-server.log"

    def start(self, timeout: float = 90.0) -> App:
        env = dict(os.environ)
        env["XDG_DATA_HOME"] = self.data_home
        env["XDG_CONFIG_HOME"] = self.data_home + "-config"
        env["XDG_STATE_HOME"] = self.data_home + "-state"
        env["XDG_CACHE_HOME"] = self.data_home + "-cache"
        for suffix in ("", "-config", "-state", "-cache"):
            os.makedirs(self.data_home + suffix, exist_ok=True)
        self._log = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [self.binary, "--server-only", "--port", str(self.port)],
            env=env, stdout=self._log, stderr=self._log, start_new_session=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.get("/api/health")
                return self
            except Exception:  # noqa: BLE001 - any failure here means "not up yet"
                if self.proc.poll() is not None:
                    raise RuntimeError(
                        f"the server exited rc={self.proc.returncode}\n"
                        + self.log_path.read_text()[-3000:]) from None
                time.sleep(0.4)
        raise RuntimeError(f"the server never became healthy within {timeout:.0f}s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                self.proc.wait(timeout=10)
        try:
            self._log.close()
        except Exception:  # noqa: BLE001,S110 - teardown; nothing left to report to
            pass

    def _req(self, method, path, body=None, raw_body=None, content_type=None,
             extra_headers=None, timeout=180):
        headers = {"Accept": "application/json"}
        data = None
        if raw_body is not None:
            data = raw_body
            headers["Content-Type"] = content_type or "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers,
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                return r.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except Exception:  # noqa: BLE001 - a non-JSON error body is still evidence
                return e.code, {"raw": payload[:800].decode("utf-8", "replace")}

    def get(self, p, **kw):
        return self._req("GET", p, **kw)

    def post(self, p, body=None, **kw):
        if "raw_body" in kw:
            return self._req("POST", p, **kw)
        return self._req("POST", p, body if body is not None else {}, **kw)

    def put(self, p, body, **kw):
        return self._req("PUT", p, body, **kw)

    def delete(self, p, body=None, **kw):
        return self._req("DELETE", p, body, **kw)


# ---------------------------------------------------------------------------
# Reading the browser's own answer
# ---------------------------------------------------------------------------

IDENTITY_JS = """(() => {
  const c = document.createElement('canvas');
  c.width = 220; c.height = 60;
  const x = c.getContext('2d');
  x.textBaseline = 'top'; x.font = '15px Arial';
  x.fillStyle = '#f60'; x.fillRect(0, 0, 110, 25);
  x.fillStyle = '#069'; x.fillText('mp-smoke-probe', 2, 2);
  const png = c.toDataURL();
  let h = 0;
  for (let i = 0; i < png.length; i++) { h = ((h << 5) - h + png.charCodeAt(i)) | 0; }
  return JSON.stringify({
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    canvas: String(h),
    ua: navigator.userAgent,
    platform: navigator.platform,
    cores: navigator.hardwareConcurrency,
    leak: typeof window.__MP_PROFILE__,
    screen: screen.width + 'x' + screen.height,
  });
})()"""

#: Chromium's ICU canonicalises some zone names, so what a page reports is not
#: always the string in /etc/localtime. Measured on an Arch host: a stock
#: Chromium with no product involvement reports Asia/Calcutta for a host zone
#: file of Asia/Kolkata. Agreeing with the real browser is correct behaviour,
#: so the comparison is canonical rather than literal.
_TZ_CANON = {
    "Asia/Kolkata": "Asia/Calcutta",
    "Europe/Kyiv": "Europe/Kiev",
    "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "Asia/Ho_Chi_Minh": "Asia/Saigon",
}


def canon_tz(zone: str) -> str:
    return _TZ_CANON.get(zone, zone)


def host_timezone() -> str:
    try:
        return os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]
    except Exception:  # noqa: BLE001 - absence is reported, not guessed
        return ""


def cdp_identity(port: int, timeout: float = 30.0) -> dict:
    import websocket

    targets = json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{port}/json", timeout=timeout).read())
    pages = [t for t in targets
             if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        raise RuntimeError(f"no page target; saw {[t.get('type') for t in targets]}")
    # suppress_origin: Chromium 111+ rejects a WebSocket Origin it did not expect.
    ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"],
                                     timeout=timeout, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": IDENTITY_JS, "returnByValue": True,
            "awaitPromise": True}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                res = msg.get("result", {})
                if "exceptionDetails" in res:
                    raise RuntimeError(str(res["exceptionDetails"])[:400])
                return json.loads(res.get("result", {}).get("value"))
        raise RuntimeError("Runtime.evaluate timed out")
    finally:
        ws.close()


def build_ext_zip(version: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(FIXTURE):
            for f in sorted(files):
                full = Path(root) / f
                data = full.read_bytes()
                if version and f == "manifest.json":
                    manifest = json.loads(data)
                    manifest["version"] = version
                    data = json.dumps(manifest).encode()
                zf.writestr(str(full.relative_to(FIXTURE)), data)
    return buf.getvalue()


def multipart(field: str, filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = "----multiprofile-smoke-boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: application/zip\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def check_install(app: App, binary: str) -> dict:
    out = subprocess.run([binary, "--version"], capture_output=True, text=True,
                         timeout=60)
    record("the installed copy reports a version",
           out.returncode == 0 and out.stdout.strip().startswith("multiprofile"),
           f"rc={out.returncode} stdout={out.stdout.strip()!r}")
    note(out.stdout.strip())

    status, health = app.get("/api/health")
    record("the API answers /api/health", status == 200, f"status={status}")
    record("health reports ok", bool((health or {}).get("ok")),
           json.dumps(health)[:300])
    perms = (health or {}).get("permissions") or {}
    record("no permission faults", perms.get("faults") == [],
           json.dumps(perms)[:300])
    engine = (health or {}).get("engine") or {}
    note(f"engine: {engine.get('detail') or engine.get('binary')}")
    note(f"data dir: {(health or {}).get('data_dir')}")
    return health or {}


def check_profiles(app: App, browser_ok: bool) -> App:
    ids = []
    for n in range(1, 4):
        status, body = app.post("/api/profiles", {"name": f"smoke-{n}"})
        pid = ((body or {}).get("profile") or {}).get("id")
        record(f"create a profile (smoke-{n})", status == 200 and bool(pid),
               f"status={status} body={json.dumps(body)[:250]}")
        if pid:
            ids.append(pid)
    if len(ids) != 3:
        record("three profiles created", False, f"got {ids}")
        return app

    status, body = app.put(f"/api/profiles/{ids[0]}", {"name": "smoke-renamed"})
    status2, after = app.get(f"/api/profiles/{ids[0]}")
    name_now = ((after or {}).get("profile") or after or {}).get("name")
    record("edit a profile", status in (200, 204) and name_now == "smoke-renamed",
           f"status={status} name={name_now!r}")

    if not browser_ok:
        for nm in ("launch every profile", "each profile has its own fingerprint",
                   "the identity claims this machine's timezone",
                   "no identity global leaks into the page",
                   "stop every profile", "relaunch after stop",
                   "the identity is identical across a restart",
                   "a profile launches after its proxy is removed"):
            skip(nm, "no usable browser engine on this machine")
    else:
        _check_launches(app, ids)

    _check_proxy_config(app, ids[0], browser_ok)
    return _check_persistence(app, ids)


def _check_launches(app: App, ids: list[str]) -> None:
    ports: dict[str, int] = {}
    for pid in ids:
        status, body = app.post(f"/api/profiles/{pid}/launch")
        port = (body or {}).get("cdpPort") or (body or {}).get("debugPort")
        if status == 200 and port:
            ports[pid] = port
        else:
            record(f"launch {pid}", False,
                   f"status={status} body={json.dumps(body)[:400]}")
    record("launch every profile", len(ports) == len(ids),
           f"{len(ports)}/{len(ids)} launched")
    if len(ports) != len(ids):
        return
    time.sleep(4)

    idents: dict[str, dict] = {}
    for pid, port in ports.items():
        try:
            idents[pid] = cdp_identity(port)
        except Exception as e:  # noqa: BLE001 - reported as the failure it is
            record(f"read the identity of {pid} over CDP", False, repr(e)[:300])
    if len(idents) != len(ids):
        for pid in ids:
            app.post(f"/api/profiles/{pid}/stop")
        return

    for pid, ident in idents.items():
        note(f"{pid}: tz={ident['tz']} platform={ident['platform']} "
             f"screen={ident['screen']} cores={ident['cores']} "
             f"canvas={ident['canvas']}")

    canvases = [i["canvas"] for i in idents.values()]
    record("each profile has its own fingerprint",
           len(set(canvases)) == len(canvases), f"canvas hashes={canvases}")

    host = host_timezone()
    if host:
        record("the identity claims this machine's timezone",
               all(canon_tz(i["tz"]) == canon_tz(host) for i in idents.values()),
               f"host={host} pages={[i['tz'] for i in idents.values()]}")
    else:
        skip("the identity claims this machine's timezone",
             "could not read the host zone from /etc/localtime")

    record("no identity global leaks into the page",
           all(i["leak"] == "undefined" for i in idents.values()),
           str([i["leak"] for i in idents.values()]))

    stopped = 0
    for pid in ids:
        status, body = app.post(f"/api/profiles/{pid}/stop")
        if status == 200:
            stopped += 1
    record("stop every profile", stopped == len(ids), f"{stopped}/{len(ids)}")
    time.sleep(3)

    target = ids[0]
    before = idents[target]
    status, body = app.post(f"/api/profiles/{target}/launch")
    port = (body or {}).get("cdpPort") or (body or {}).get("debugPort")
    record("relaunch after stop", status == 200 and bool(port),
           f"status={status} body={json.dumps(body)[:300]}")
    if port:
        time.sleep(4)
        try:
            after = cdp_identity(port)
            drift = {k: (before[k], after[k]) for k in
                     ("tz", "canvas", "ua", "platform", "screen", "cores")
                     if before[k] != after[k]}
            record("the identity is identical across a restart", not drift,
                   f"changed={drift}")
        except Exception as e:  # noqa: BLE001
            record("the identity is identical across a restart", False, repr(e)[:300])
        app.post(f"/api/profiles/{target}/stop")
        time.sleep(2)


def _check_proxy_config(app: App, pid: str, browser_ok: bool) -> None:
    status, _ = app.put(f"/api/profiles/{pid}", {
        "proxyType": "http", "proxyHost": "127.0.0.1", "proxyPort": 9})
    record("configure a proxy on a profile", status in (200, 204), f"status={status}")
    status, body = app.get(f"/api/profiles/{pid}")
    prof = (body or {}).get("profile") or body or {}
    record("the proxy is persisted",
           prof.get("proxyType") == "http" and str(prof.get("proxyPort")) == "9",
           f"type={prof.get('proxyType')} port={prof.get('proxyPort')}")
    status, _ = app.put(f"/api/profiles/{pid}", {"proxyType": "none"})
    record("remove the proxy again", status in (200, 204), f"status={status}")
    if not browser_ok:
        return
    status, body = app.post(f"/api/profiles/{pid}/launch")
    port = (body or {}).get("cdpPort") or (body or {}).get("debugPort")
    record("a profile launches after its proxy is removed",
           status == 200 and bool(port),
           f"status={status} body={json.dumps(body)[:300]}")
    if port:
        time.sleep(2)
        app.post(f"/api/profiles/{pid}/stop")


def _check_persistence(app: App, ids: list[str]) -> App:
    """Restart the whole application and return the new live server.

    Persistence is the claim the rest of the product rests on, so it is tested
    by actually stopping the process rather than by re-reading the store.
    """
    status, listing = app.get("/api/profiles")
    before = {r["id"]: r.get("name") for r in (listing or {}).get("items", [])}
    record("every created profile is listed", set(ids) <= set(before),
           f"listed={sorted(before)}")

    data_home, port, binary = app.data_home, app.port, app.binary
    app.stop()
    restarted = App(binary, data_home, port).start()
    status, listing = restarted.get("/api/profiles")
    after = {r["id"]: r.get("name") for r in (listing or {}).get("items", [])}
    record("profiles survive an application restart", after == before,
           f"before={sorted(before)} after={sorted(after)}")
    status, health = restarted.get("/api/health")
    record("health is ok after an application restart",
           bool((health or {}).get("ok")), json.dumps(health)[:200])
    return restarted


def check_backup_restore(app: App) -> None:
    status, body = app.post("/api/profiles", {"name": "backup-me",
                                              "notes": "keepsake"})
    prof = (body or {}).get("profile") or {}
    pid, seed = prof.get("id"), prof.get("seed")
    if not pid:
        record("create a profile to back up", False, json.dumps(body)[:300])
        return
    record("create a profile to back up", True)

    status, body = app.post(f"/api/profiles/{pid}/backup", {"includeUserData": True})
    path = (body or {}).get("path") or (body or {}).get("backupPath")
    record("create a per-profile backup", status == 200 and bool(path),
           f"status={status} body={json.dumps(body)[:300]}")
    if not path:
        return
    record("the backup file exists on disk", os.path.isfile(path), path)
    record("the backup response carries no passphrase", "passphrase" not in body,
           str(list(body)))

    status, _ = app.delete(f"/api/profiles/{pid}")
    record("delete the profile", status in (200, 204), f"status={status}")
    status, _ = app.get(f"/api/profiles/{pid}")
    record("the profile is really gone", status == 404, f"status={status}")

    status, body = app.post("/api/backups/restore", {"backupPath": path})
    record("restore from the backup", status == 200 and bool((body or {}).get("ok")),
           f"status={status} body={json.dumps(body)[:300]}")
    status, body = app.get(f"/api/profiles/{pid}")
    prof = (body or {}).get("profile") or body or {}
    record("the restored profile keeps its name and notes",
           prof.get("name") == "backup-me" and prof.get("notes") == "keepsake",
           f"name={prof.get('name')!r} notes={prof.get('notes')!r}")
    record("the restored profile keeps its identity seed", prof.get("seed") == seed,
           f"before={seed} after={prof.get('seed')}")


def check_extensions(app: App, data_home: str) -> None:
    status, body = app.get("/api/extensions/capability")
    cap = (body or {}).get("capability") or {}
    unpacked = cap.get("unpackedLoading")
    record("the capability endpoint reports this engine", status == 200 and bool(cap),
           f"status={status} body={json.dumps(body)[:300]}")
    note(f"engine: {cap.get('brand')} {cap.get('version')} "
         f"unpackedLoading={unpacked} crxSideload={cap.get('crxSideload')}")
    if not unpacked:
        for nm in ("permissions are shown before anything is installed",
                   "import an extension", "bulk assign to selected profiles",
                   "repeated assignment is idempotent",
                   "disable an assignment without removing it",
                   "update the extension", "a per-profile update is refused",
                   "roll the extension back",
                   "removing one profile leaves the others assigned"):
            skip(nm, f"this engine cannot load unpacked extensions: {cap.get('reason')}")
        return

    zip_path = os.path.join(data_home, "smoke-ext.zip")
    with open(zip_path, "wb") as fh:
        fh.write(build_ext_zip())
    status, prev = app.post("/api/extensions/preview", {"zipPath": zip_path})
    manifest = (prev or {}).get("manifest") or {}
    record("permissions are shown before anything is installed",
           status == 200 and manifest.get("permissions") is not None,
           f"status={status} body={json.dumps(prev)[:300]}")
    note(f"permissions: {manifest.get('permissions')} "
         f"host: {manifest.get('hostPermissions')}")

    payload, ctype = multipart("file", "smoke-ext.zip", build_ext_zip())
    status, body = app.post("/api/extensions/upload", raw_body=payload,
                            content_type=ctype,
                            extra_headers={"X-Multiprofile-Upload": "1"})
    ext = (body or {}).get("extension") or {}
    ext_id = ext.get("id")
    record("import an extension", status == 201 and bool(ext_id),
           f"status={status} body={json.dumps(body)[:300]}")
    if not ext_id:
        return

    status, body = app.get(f"/api/extensions/{ext_id}")
    record("a freshly imported extension is not installed anywhere",
           body["extension"]["assignments"] == [],
           f"assignments={body['extension']['assignments']}")

    pids = []
    for n in range(4):
        status, body = app.post("/api/profiles", {"name": f"ext-{n}",
                                                  "tags": ["smoke-fleet"]})
        pids.append(((body or {}).get("profile") or {})["id"])
    chosen = pids[:3]

    status, body = app.post(f"/api/extensions/{ext_id}/assign",
                            {"profileIds": chosen})
    per_profile = (body or {}).get("results") or (body or {}).get("profiles")
    record("bulk assign to selected profiles", status in (200, 207),
           f"status={status} body={json.dumps(body)[:300]}")
    record("the bulk result records a per-profile outcome", bool(per_profile),
           json.dumps(body)[:300])

    status, body = app.get(f"/api/extensions/{ext_id}")
    states = {a["profileId"]: a.get("state")
              for a in body["extension"]["assignments"]}
    record("exactly the selected profiles are assigned",
           set(states) == set(chosen), f"states={states}")
    record("an unselected profile is left alone",
           bool(states) and pids[3] not in states, f"states={states}")

    app.post(f"/api/extensions/{ext_id}/assign", {"profileIds": chosen})
    status, body = app.get(f"/api/extensions/{ext_id}")
    count = len(body["extension"]["assignments"])
    record("repeated assignment is idempotent", count == 3, f"assignments={count}")

    status, _ = app.post(f"/api/extensions/{ext_id}/enabled",
                         {"profileId": chosen[0], "enabled": False})
    status2, body = app.get(f"/api/extensions/{ext_id}")
    row = next((a for a in body["extension"]["assignments"]
                if a["profileId"] == chosen[0]), None)
    record("disable an assignment without removing it",
           status == 200 and row is not None and row.get("enabled") is False,
           f"status={status} row={json.dumps(row)[:200]}")

    v2 = os.path.join(data_home, "smoke-ext-v2.zip")
    with open(v2, "wb") as fh:
        fh.write(build_ext_zip("4.2.0"))
    status, body = app.post(f"/api/extensions/{ext_id}/update", {"zipPath": v2})
    status2, body2 = app.get(f"/api/extensions/{ext_id}")
    record("update the extension",
           status == 200 and body2["extension"]["version"] == "4.2.0",
           f"status={status} version={body2['extension']['version']}")

    # The scope guarantee: a selected-profile update must refuse rather than
    # silently change the whole fleet.
    status, body = app.post(f"/api/extensions/{ext_id}/update",
                            {"profileIds": chosen[:1], "zipPath": v2})
    record("a per-profile update is refused rather than silently fleet-wide",
           status in (400, 409) and "fleet" in json.dumps(body).lower(),
           f"status={status} body={json.dumps(body)[:300]}")

    status, body = app.post(f"/api/extensions/{ext_id}/rollback")
    status2, body2 = app.get(f"/api/extensions/{ext_id}")
    record("roll the extension back",
           status == 200 and body2["extension"]["version"] == "1.0.0",
           f"status={status} version={body2['extension']['version']}")

    status, _ = app.post(f"/api/extensions/{ext_id}/unassign",
                         {"profileId": chosen[2]})
    status2, body = app.get(f"/api/extensions/{ext_id}")
    left = {a["profileId"] for a in body["extension"]["assignments"]}
    record("removing one profile leaves the others assigned",
           status == 200 and left == set(chosen[:2]), f"left={left}")
    payload_path = body["extension"].get("payloadPath")
    if payload_path:
        record("the shared payload is not deleted while another profile uses it",
               os.path.isfile(os.path.join(payload_path, "manifest.json")),
               payload_path)


def browser_available(health: dict) -> bool:
    return bool(health.get("chrome_ok"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", default=shutil.which("multiprofile"),
                        help="the multiprofile to test (default: the one on PATH)")
    parser.add_argument("--data-dir", default=None,
                        help="scratch data directory (default: a temporary one)")
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the scratch data directory")
    args = parser.parse_args()

    if not args.binary:
        print("No `multiprofile` on PATH. Install it, or pass --binary.",
              file=sys.stderr)
        return 2
    if not FIXTURE.is_dir():
        print(f"The extension fixture is missing: {FIXTURE}\n"
              "Run this from a checkout -- it needs tests/fixtures/extensions.",
              file=sys.stderr)
        return 2

    data_dir = args.data_dir or tempfile.mkdtemp(prefix="multiprofile-smoke-")
    print(f"binary:   {args.binary}")
    print(f"scratch:  {data_dir}")
    print("Your own profiles are NOT touched: this runs against the scratch "
          "directory above.\n", flush=True)

    app = App(args.binary, data_dir)
    try:
        app.start()
        health = check_install(app, args.binary)
        browser = browser_available(health)
        if not browser:
            print("\nNo usable browser engine; browser checks will be skipped.\n",
                  flush=True)
        print("\n-- profiles " + "-" * 56, flush=True)
        # check_profiles restarts the application, so it hands back the server
        # the remaining checks must use.
        app = check_profiles(app, browser)
        print("\n-- backup and restore " + "-" * 46, flush=True)
        check_backup_restore(app)
        print("\n-- extensions " + "-" * 54, flush=True)
        check_extensions(app, data_dir)
    finally:
        app.stop()
        if not args.keep:
            for suffix in ("", "-config", "-state", "-cache"):
                shutil.rmtree(data_dir + suffix, ignore_errors=True)

    passed = [r for r in results if r[1] == PASS]
    failed = [r for r in results if r[1] == FAIL]
    skipped = [r for r in results if r[1] == SKIP]

    print("\n" + "=" * 72)
    for name, outcome, detail in results:
        line = f"{outcome}  {name}"
        if detail and outcome != PASS:
            line += f"\n      {detail}"
        print(line)
    print("=" * 72)
    print(f"{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")
    if failed:
        print("\nThis installation is NOT ready for daily use. The failing "
              "checks are listed above.")
    elif skipped:
        print("\nEvery check that could run passed. The skipped ones need a "
              "browser this machine does not have, so this is not yet "
              "evidence about launching profiles.")
    else:
        print("\nEvery check passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
