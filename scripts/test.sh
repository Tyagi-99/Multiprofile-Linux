#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONPATH="src:${PYTHONPATH:-}"
python3 - <<'PY'
import json
from multiprofile import __version__
assert __version__ == "1.1.0", __version__

from multiprofile.fingerprint import build_fingerprint, normalize_seed, fingerprint_report
from multiprofile.proxy_util import parse_proxy, proxy_server_arg
from multiprofile.cookies_store import import_cookies

# Fingerprint determinism
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
assert fp1["platform"] == "Win32"
assert fp1["touch"] == 0
assert normalize_seed(p) == 12345
rep = fingerprint_report(p)
assert rep["ok"] and "summary" in rep

# Mobile UA must not get Win32
pm = dict(p, userAgent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", isMobile=True, seed=99)
fpm = build_fingerprint(pm)
assert fpm["platform"] == "iPhone", fpm["platform"]
assert fpm["touch"] > 0

# Proxy parse
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
    arg = proxy_server_arg(parsed)
    assert arg
    if auth:
        assert parsed["username"] == auth[0]
        assert parsed["password"] == auth[1]

# Cookies import
chrome = [{"name": "a", "value": "1", "domain": ".example.com", "path": "/", "secure": True, "httpOnly": False, "expirationDate": 2000000000}]
assert len(import_cookies(chrome)) == 1
netscape = "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t2000000000\tb\t2\n"
assert len(import_cookies(netscape)) == 1

# Flask test client
from multiprofile.api.server import create_app
app = create_app()
c = app.test_client()
h = c.get("/api/health")
assert h.status_code == 200
hj = h.get_json()
assert hj["version"] == "1.1.0"
assert hj["static_ok"] is True
assert hj["extension_ok"] is True

r = c.get("/api/user-agents")
assert r.status_code == 200
assert isinstance(r.get_json(), list) and len(r.get_json()) >= 5
assert c.get("/api/profiles").status_code == 200

# Create + fingerprint + cookies + duplicate + v1 list
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
})
assert cr.status_code == 200, cr.data
prof = cr.get_json()["profile"]
pid = prof["id"]
assert prof.get("seed") == 777
assert prof.get("fingerprint")

fp = c.get(f"/api/profiles/{pid}/fingerprint")
assert fp.status_code == 200
assert fp.get_json()["seed"] == 777

imp = c.post(f"/api/profiles/{pid}/cookies/import", json={"cookies": chrome})
assert imp.status_code == 200
assert imp.get_json()["count"] >= 1
ex = c.get(f"/api/profiles/{pid}/cookies/export")
assert ex.status_code == 200

dup = c.post(f"/api/profiles/{pid}/duplicate")
assert dup.status_code == 200
assert dup.get_json()["profile"]["id"] != pid
assert dup.get_json()["profile"]["seed"] != 777

v1 = c.get("/api/v1/profile/list")
assert v1.status_code == 200
assert v1.get_json()["code"] == 0

# Proxy test parse path (will fail TCP on fake host — but should return structured error, not 500)
pt = c.post("/api/proxy/test", json={"customProxy": "127.0.0.1:1"})
assert pt.status_code == 200
ptj = pt.get_json()
assert "ok" in ptj and "success" in ptj

# SPA loads
idx = c.get("/")
assert idx.status_code == 200
assert b"Anti-Detect" in idx.data or b"Profile" in idx.data

print("OK: version, fingerprint determinism, proxy parse, cookies, health, SPA, v1 aliases")
PY
