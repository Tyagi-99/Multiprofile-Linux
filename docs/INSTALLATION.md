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

```bash
./build-deb.sh
```

Produces `dist/multiprofile-native_<version>_all.deb` and its `.sha256`.
The package is architecture-independent — it ships Python, a static
export and icons — so the same file installs on amd64 and arm64.
This runs anywhere, including on Arch: it prefers `dpkg-deb` when that exists
and falls back to [`scripts/build_deb.py`](../scripts/build_deb.py), which
writes the same archive from the standard library. CI diffs the two on every
build.

**Signing and updates are built but not published.** The repository tooling,
delta channel, `multiprofile-update` client and rollback all exist and are
tested end to end in a clean Debian container on every push — but there is no
real signing key and no host, so **a `.deb` you build today is unsigned and
has no update channel**. See [`UPDATES.md`](UPDATES.md), and **F12** in
[`02_AUDIT_REPORT`](02_AUDIT_REPORT.md).

To exercise the whole release process yourself:

```bash
bash scripts/release-lifecycle.sh
```

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
