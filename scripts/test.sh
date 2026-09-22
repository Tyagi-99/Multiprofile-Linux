#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONPATH="src:${PYTHONPATH:-}"
export XDG_DATA_HOME="$(mktemp -d /tmp/mp-test-data-XXXXXX)"
export XDG_CACHE_HOME="$(mktemp -d /tmp/mp-test-cache-XXXXXX)"
export XDG_CONFIG_HOME="$(mktemp -d /tmp/mp-test-cfg-XXXXXX)"
unset MULTIPROFILE_API_TOKEN 2>/dev/null || true

python3 - <<'PY'
import json
import os
import zipfile
from pathlib import Path

from multiprofile import __version__
assert __version__ == "1.4.6", __version__

from multiprofile.fingerprint import build_fingerprint, normalize_seed, fingerprint_report
from multiprofile.proxy_util import parse_proxy, proxy_server_arg
from multiprofile.cookies_store import import_cookies

p = {
    "id": "profile_test",
    "seed": 12345,
    "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "isMobile": False,
    "timezone": "America/New_York",
    "language": "en-US",
    "webrtcMode": "disable_non_proxied_udp",
}
fp1 = build_fingerprint(p)
fp2 = build_fingerprint(p)
assert fp1 == fp2, "fingerprint not deterministic"
assert normalize_seed(p) == 12345
rep = fingerprint_report(p)
assert rep["ok"] and "summary" in rep

pm = dict(
    p,
    userAgent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    isMobile=True,
    seed=99,
)
fpm = build_fingerprint(pm)
assert fpm["platform"] == "iPhone", fpm["platform"]

cases = [
    ("1.2.3.4:8080", None),
    ("1.2.3.4:8080:user:pass", ("user", "pass")),
    ("user:pass@1.2.3.4:8080", ("user", "pass")),
    ("http://user:pass@1.2.3.4:8080", ("user", "pass")),
    ("socks5://1.2.3.4:1080", None),
    ("socks5h://u:p@1.2.3.4:1080", ("u", "p")),
]
for raw, auth in cases:
    parsed = parse_proxy(raw, "custom")
    assert parsed, raw
    assert parsed["host"] == "1.2.3.4"
    assert proxy_server_arg(parsed)
    if auth:
        assert parsed["username"] == auth[0]
        assert parsed["password"] == auth[1]

chrome = [{"name": "a", "value": "1", "domain": ".example.com", "path": "/", "secure": True, "httpOnly": False, "expirationDate": 2000000000}]
assert len(import_cookies(chrome)) == 1
netscape = "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t2000000000\tb\t2\n"
assert len(import_cookies(netscape)) == 1

from multiprofile.api.server import create_app
app = create_app()
c = app.test_client()
h = c.get("/api/health")
assert h.status_code == 200
hj = h.get_json()
assert hj["version"] == "1.4.6", hj
assert hj.get("static_ok") is True
assert hj.get("extension_ok") is True
assert "slots" in hj

assert c.get("/api/user-agents").status_code == 200
assert isinstance(c.get("/api/user-agents").get_json(), list)
assert c.get("/api/profiles").status_code == 200

cr = c.post("/api/profiles", json={
    "name": "Test FP",
    "userAgent": p["userAgent"],
    "userAgentId": "win_chrome",
    "isMobile": False,
    "proxyType": "none",
    "timezone": "America/New_York",
    "language": "en-US",
    "webrtcMode": "default",
    "seed": 777,
    "tags": ["lab", "qa"],
})
assert cr.status_code == 200, cr.data
body = cr.get_json()
prof = body["profile"]
pid = prof["id"]
assert prof.get("seed") == 777
assert prof.get("fingerprint")
assert "warnings" in body

fp = c.get(f"/api/profiles/{pid}/fingerprint")
assert fp.status_code == 200
assert fp.get_json()["seed"] == 777

imp = c.post(f"/api/profiles/{pid}/cookies/import", json={"cookies": chrome})
assert imp.status_code == 200
assert imp.get_json()["count"] >= 1
assert c.get(f"/api/profiles/{pid}/cookies/export").status_code == 200

dup = c.post(f"/api/profiles/{pid}/duplicate")
assert dup.status_code == 200
assert dup.get_json()["profile"]["id"] != pid

v1 = c.get("/api/v1/profile/list")
assert v1.status_code == 200 and v1.get_json()["code"] == 0

pt = c.post("/api/proxy/test", json={"customProxy": "127.0.0.1:1"})
assert pt.status_code == 200
ptj = pt.get_json()
assert "ok" in ptj and "success" in ptj

idx = c.get("/")
assert idx.status_code == 200
assert b"Anti-Detect" in idx.data or b"Profile" in idx.data

# clients
cl = c.post("/api/clients", json={"name": "Acme Labs", "color": "#abc123", "notes": "n"})
assert cl.status_code in (200, 201), cl.data
cid = cl.get_json()["client"]["id"]
assert c.get("/api/clients").status_code == 200
assert c.put(f"/api/clients/{cid}", json={"name": "Acme Labs 2"}).status_code == 200

c.put(f"/api/profiles/{pid}", json={"clientId": cid, "tags": ["lab", "prod"]})
assert isinstance(c.get("/api/profiles").get_json(), list)
paged = c.get("/api/profiles?tag=lab&limit=10&offset=0").get_json()
assert "items" in paged and "total" in paged

# templates
tpl = c.post("/api/templates", json={
    "name": "Win Template",
    "userAgentId": "win_chrome",
    "proxyType": "none",
    "timezone": "America/New_York",
    "tags": ["tpl"],
})
assert tpl.status_code in (200, 201), tpl.data
tid = tpl.get_json()["template"]["id"]
ft = c.post("/api/profiles/from-template", json={"templateId": tid, "clientId": cid, "count": 2})
assert ft.status_code == 200, ft.data
assert ft.get_json()["count"] == 2

# jobs
job = c.post("/api/jobs", json={"type": "bulk_stop", "profileIds": [pid]})
assert job.status_code in (200, 201), job.data
jid = job.get_json()["job"]["id"]
assert c.get(f"/api/jobs/{jid}").status_code == 200

# reap
reap = c.post("/api/maintenance/reap")
assert reap.status_code == 200
assert "reaped" in reap.get_json()

# backup
bk = c.post(f"/api/profiles/{pid}/backup", json={})
assert bk.status_code == 200, bk.data
bj = bk.get_json()
zpath = bj.get("backupPath") or bj.get("path")
assert zpath and Path(zpath).exists(), bj
assert zipfile.is_zipfile(zpath)

# token
os.environ["MULTIPROFILE_API_TOKEN"] = "test-secret-token"
app2 = create_app()
c2 = app2.test_client()
assert c2.get("/api/profiles").status_code == 401
assert c2.get("/api/health").status_code == 200
assert c2.get("/api/profiles", headers={"Authorization": "Bearer test-secret-token"}).status_code == 200
del os.environ["MULTIPROFILE_API_TOKEN"]

# delete client nulls clientId
c.delete(f"/api/clients/{cid}")
got = next(x for x in c.get("/api/profiles").get_json() if x["id"] == pid)
assert got.get("clientId") in (None, "")

d = c.delete(f"/api/profiles/{pid}?purgeStorage=1")
assert d.status_code == 200

print("OK: 1.4.6 — version, fingerprint, proxy, cookies, health, SPA, v1, clients, pagination, jobs, reap, backup, token, templates")
PY

# --- Phase 2 browser runtime (anti-detect exclusivity preserved) ---
python3 - <<'PY2'
import os, sys
sys.path.insert(0, "src")
for k in list(os.environ):
    if k.startswith("MULTIPROFILE_") or k == "MAX_CONCURRENT_PROFILES":
        # leave PATH etc; clear overrides for defaults check
        if k in ("MAX_CONCURRENT_PROFILES", "MULTIPROFILE_ALLOW_WEBSTORE_EXTENSIONS", "MULTIPROFILE_FORCE_NO_SANDBOX"):
            os.environ.pop(k, None)

from multiprofile.config import get_settings
from multiprofile.browser.manager import max_concurrent, slot_status
from multiprofile.browser.sandbox import probe_sandbox, sandbox_chrome_args
from multiprofile.browser.window_layout import compute_layout, detect_work_area
from multiprofile.browser.launcher import find_chrome

s = get_settings()
assert s.max_concurrent_profiles == 20, s
assert s.allow_webstore_extensions is False, "anti-detect exclusivity must default ON"
assert max_concurrent() == 20
st = slot_status()
assert st["max"] == 20 and st["available"] == 20, st

chrome = find_chrome()
sb = probe_sandbox(chrome)
args, sb2 = sandbox_chrome_args(chrome)
assert sb.ok == sb2.ok
if sb.ok and not sb.use_no_sandbox:
    assert "--no-sandbox" not in args, args
print("sandbox:", sb.reason, "args:", args)

area = detect_work_area()
rects = compute_layout(4, area)
assert len(rects) == 4
for i, a in enumerate(rects):
    for j, b in enumerate(rects):
        if i >= j:
            continue
        ax2, ay2 = a.x + a.w, a.y + a.h
        bx2, by2 = b.x + b.w, b.y + b.h
        overlap = not (ax2 <= b.x or bx2 <= a.x or ay2 <= b.y or by2 <= a.y)
        assert not overlap, (a, b)
print("tiling ok", area.source)

src = open("src/multiprofile/browser/launcher.py", encoding="utf-8").read()
assert "--disable-extensions-except" in src
assert "allow_webstore_extensions" in src
print("exclusivity default ON")
print("PHASE2_OK")
PY2


echo "ALL TESTS PASSED"


# CDP tiling unit tests (no Chrome required)
PYTHONPATH=src python3 -m pytest tests/test_grid_layout.py tests/test_work_area.py tests/test_runtime_state.py tests/test_tile_idempotent.py tests/test_cdp_window_smoke.py tests/test_launcher_flags.py tests/test_github_accounts.py -q

