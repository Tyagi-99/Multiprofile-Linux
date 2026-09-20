#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONPATH="src:${PYTHONPATH:-}"
python3 - <<'PY'
from multiprofile.api.server import create_app
app = create_app()
c = app.test_client()
assert c.get('/api/health').status_code == 200
r = c.get('/api/user-agents')
assert r.status_code == 200
assert isinstance(r.get_json(), list) and len(r.get_json()) >= 5
assert c.get('/api/profiles').status_code == 200
print('OK: health, user-agents, profiles')
PY
