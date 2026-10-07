# Installation

See [`TEAM.md`](TEAM.md) for onboarding more than one person, and
[`BACKUP_AND_RESTORE.md`](BACKUP_AND_RESTORE.md) before you create anything
you care about.

## Rootless, per-user — the recommended path on Arch and anywhere without a `.deb`

```bash
bash scripts/install-local.sh
```

Creates a virtual environment under `~/.local/lib/multiprofile`, a
`multiprofile` command in `~/.local/bin`, a desktop entry and icons. No sudo,
nothing outside `$HOME`. It is idempotent -- re-running it is how you upgrade
-- and it contains no code that writes to the data directory, so an upgrade
cannot rewrite your profiles.

Verified on Arch/Omarchy with Python 3.14. `scripts/uninstall-local.sh`
removes the application and deliberately leaves your profiles where they are.

## Verify the install before trusting it with real work

```bash
multiprofile --version
multiprofile --doctor
python3 scripts/smoke-test.py
```

`--doctor` exits non-zero when something must be fixed first.
`scripts/smoke-test.py` drives the API the console uses and runs 46 checks --
create, edit, launch, stop, restart, proxy on and off, backup, restore, and
the full extension lifecycle including update and rollback -- against a
**scratch** data directory, so it is safe on a machine that already holds real
profiles. Browser checks are skipped with a reason if no usable engine is
present.

## From source, manually

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

## Keeping the engine predictable

The launcher tries `google-chrome-stable`, `google-chrome`, `chromium`,
`chromium-browser` in that order. On a machine with more than one, pin the one
you mean so installing another browser later cannot silently change which
engine your profiles run on:

```bash
curl -s -X POST localhost:8765/api/engine/choose \
     -H 'Content-Type: application/json' \
     -d '{"binary": "/usr/bin/chromium"}'
```

The choice persists in `~/.config/multiprofile/engine.json` and
`MULTIPROFILE_ENGINE_BINARY` overrides it for one run. This matters for
extensions in particular: **branded Google Chrome 137+ cannot load unpacked
extensions at all**, so a profile with an extension assigned will refuse to
launch on it, naming the reason. Chromium and Chrome for Testing both work at
any version. The product never substitutes a different browser for you.

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
