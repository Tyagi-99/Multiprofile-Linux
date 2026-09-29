# Installation

## From source — the only path verified on this machine

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
multiprofile
```

`multiprofile` opens the desktop window. `multiprofile --server-only` runs the
API and console at <http://127.0.0.1:8765/> with no window, which is what you
want when driving it from a script or over SSH.

Requires **Python 3.11+** and a Chromium or Chrome on `PATH`. The launcher tries
`google-chrome-stable`, `google-chrome`, `chromium`, `chromium-browser` in that
order and uses the first it finds. On Arch: `pacman -S chromium`. On Debian or
Ubuntu: `apt install chromium`.

Optional extras, all included by `[all]`:

| Extra | Gives you | Without it |
|---|---|---|
| `ui` | the pywebview desktop window | use `--server-only` and a browser |
| `encrypted-backup` | AES-256-GCM encrypted backups | plaintext `.zip` backups only |
| `keychain` | the secrets master key in the OS keyring | a `0600` key file, and the app says so rather than pretending |

## The `.deb`

`./build-deb.sh` produces `dist/multiprofile-native_<version>_amd64/`.

**It is unsigned and there is no update channel** — see **F12** in
[`02_AUDIT_REPORT`](02_AUDIT_REPORT.md). It also **cannot be built or exercised
on the development machine**, which is Arch and has no `dpkg-deb`, so packaging
changes here ship unverified. Install it only if you are prepared to verify it
yourself.

## Where your data lives

Everything is under `~/.local/share/multiprofile/` (or `$XDG_DATA_HOME`):

```
control.db           profiles, runtime state, jobs, usage, tokens  (0600)
chrome-profiles/     one --user-data-dir per profile
cookies/             exported cookie jars                          (0600)
audit.jsonl          what happened, and when                       (0600)
```

Nothing is sent anywhere. There is no telemetry, no account, and no server
component — the product is entirely local, which is why `--host` defaults to
`127.0.0.1`.
