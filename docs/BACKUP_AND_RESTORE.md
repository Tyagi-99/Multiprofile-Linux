# Backup, restore, update and rollback

Everything this product knows lives in one directory on one machine. There is
no server, no account and no sync, so **a backup is the only thing standing
between you and losing a profile.** This page is the procedure, and every
command on it has been run.

Two different things get backed up, and they are not interchangeable:

| What | Covers | Use it for |
|---|---|---|
| **Whole-store backup** | every profile, the control database, cookies, extensions, settings | upgrades, machine moves, disaster recovery |
| **Per-profile backup** | one profile and its browser data | handing a profile to a teammate, undoing a change to one profile |

---

## 1. Whole-store backup

### The short version

```bash
multiprofile-backup          # take one now, verified
```

Daily, unattended:

```bash
systemctl --user enable --now multiprofile-backup.timer
systemctl --user list-timers multiprofile-backup.timer
```

`scripts/install-local.sh` installs the command and the timer but **does not
enable the timer** — writing hundreds of megabytes on a schedule is your
decision. It prints whether it is on.

**Two kinds of backup, and it tells you which it took.** `chrome-profiles/`
is written continuously by every live Chromium, so copying it under a running
profile can capture a torn cookie jar. A timer cannot stop your browsers, and
nothing in this product auto-parks an idle profile, so:

| Kind | When | Contains |
|---|---|---|
| **full** | nothing is running | everything, including cookies and sessions |
| **metadata** | a profile is running | profile definitions, **identity seeds**, proxy settings, extension assignments — *not* cookies or sessions |

A metadata backup is not a consolation prize: the seed is the identity, so a
profile restored from one presents the same browser. What it cannot give back
is logged-in sessions. Every backup carries a `MANIFEST` recording which kind
it is, so a restore is never a surprise.

Browser caches are excluded from both (the same directory names
`backup._SKIP_DIR_NAMES` excludes from a per-profile backup). They are large,
they rebuild themselves, and nobody restores for them.

**Retention:** `KEEP=7` by default; `multiprofile-backup --keep N` changes it.
Only backups this tool made are ever pruned — they are identified by their
`MANIFEST`, so anything you put in that directory by hand is left alone
whatever it is called.

**Size:** a full backup of 5 profiles is about 400 MB here, so seven of them
is roughly 2.8 GB. Check `du -sh ~/multiprofile-backups` before raising
`KEEP`, and note this grows with the number of profiles.

**It writes to `$HOME`, which is the same disk as your data.** Copy
`~/multiprofile-backups/` somewhere else, or point
`MULTIPROFILE_BACKUP_DIR` at removable or network storage.

### Doing it by hand

The data lives in `~/.local/share/multiprofile/` (or `$XDG_DATA_HOME`) and
`~/.config/multiprofile/`. `multiprofile-backup` runs exactly these steps —
they are written out here so the script is auditable, not so you have to type
them.

**Stop the application first.** SQLite is safe to copy while running only
through its own backup API, which is what the command below uses, but the
`chrome-profiles/` directories are not — a running Chromium writes to them
continuously.

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$HOME/multiprofile-backups/$STAMP"
mkdir -p "$DEST"

# The control database through SQLite's own backup API, so the copy is
# consistent even if something has it open.
sqlite3 "$HOME/.local/share/multiprofile/control.db" ".backup '$DEST/control.db'"

# Everything else.
tar -C "$HOME/.local/share" -czf "$DEST/data.tar.gz"   multiprofile
tar -C "$HOME/.config"      -czf "$DEST/config.tar.gz" multiprofile

( cd "$DEST" && sha256sum control.db data.tar.gz config.tar.gz > SHA256SUMS )
```

### Verify it — a backup you have not restored is a guess

```bash
cd "$DEST" && sha256sum -c SHA256SUMS
sqlite3 "$DEST/control.db" "PRAGMA integrity_check;"   # must print: ok
sqlite3 "$DEST/control.db" "SELECT COUNT(*) FROM profiles;"
sqlite3 "$DEST/control.db" "SELECT MAX(version) FROM schema_version;"
```

Then prove the application can actually read it, without touching your live
store, by pointing a throwaway run at a copy:

```bash
R=$(mktemp -d)
mkdir -p "$R/data" "$R/config"
tar -C "$R/data"   -xzf "$DEST/data.tar.gz"
tar -C "$R/config" -xzf "$DEST/config.tar.gz"

XDG_DATA_HOME="$R/data" XDG_CONFIG_HOME="$R/config" \
  multiprofile --server-only --port 8799
# In another terminal:
#   curl -s localhost:8799/api/profiles | python3 -m json.tool | head
```

Your profiles should be listed. That is the restore proven, not assumed.

### Restore

```bash
# Stop the application first.
mv "$HOME/.local/share/multiprofile" "$HOME/.local/share/multiprofile.before-restore"
tar -C "$HOME/.local/share" -xzf "$DEST/data.tar.gz"
tar -C "$HOME/.config"      -xzf "$DEST/config.tar.gz"
multiprofile --doctor
```

Keep `multiprofile.before-restore` until you have confirmed the restore, then
delete it yourself. Nothing deletes it for you.

---

## 2. Per-profile backup

This is the supported way to move one profile between machines or people. It
goes through the console (**Profile → Backup**) or the API:

```bash
# Create
curl -s -X POST localhost:8765/api/profiles/<id>/backup \
     -H 'Content-Type: application/json' \
     -d '{"includeUserData": true}'
# -> {"ok": true, "path": "/home/you/.local/share/multiprofile/backups/....zip", ...}

# Restore (same machine or another)
curl -s -X POST localhost:8765/api/backups/restore \
     -H 'Content-Type: application/json' \
     -d '{"backupPath": "/path/to/that.zip"}'
```

Add `"passphrase": "..."` to both to get AES-256-GCM encryption
(PBKDF2-HMAC-SHA256, 600,000 iterations). **The passphrase is not stored
anywhere.** Lose it and the backup is gone; there is no recovery path and
nobody can open it for you.

What a per-profile restore preserves, verified by
`scripts/smoke-test.py`: the profile id, its name and notes, and **its
identity seed** — so the restored profile presents the same fingerprint it
did before. A restore that generated a new seed would look like a different
device to every site the profile was logged into.

Caches (`Cache`, `Code Cache`, `GPUCache`, `Service Worker`, `ShaderCache`,
`GrShaderCache`) are deliberately excluded. They are large, they rebuild
themselves, and they are not identity.

---

## 3. Updating

```bash
# From source / rootless install:
cd /path/to/Multiprofile-Linux && git pull && bash scripts/install-local.sh

# From the .deb:
sudo dpkg -i multiprofile-native_<version>_all.deb
```

`scripts/install-local.sh` upgrades in place and **has no code that writes to
your data directory**, so an upgrade cannot rewrite your profiles. It is
idempotent; run it as often as you like.

**Take a whole-store backup before any update that changes the schema
version** (see below). Then verify:

```bash
multiprofile --version
multiprofile --doctor
python3 scripts/smoke-test.py     # 46 checks against a scratch store
```

---

## 4. Schema compatibility — read this before downgrading

The control database carries a schema version. This build is at **v8**.

**Migrations are forward-only. There is no downgrade path, and none is
planned.** Opening the store with a newer build migrates it permanently.

| Situation | What happens |
|---|---|
| Newer build, older database | migrated automatically on first open, forward only |
| Same version | opens normally |
| **Older build, newer database** | **refused**, with the version it found and the version it understands |

That refusal is deliberate and is new. Before it, an older build opened a
newer database and silently operated on the parts it recognised — at the
v7/v8 boundary that meant extension assignments existed in the database,
were invisible to the old build, and never applied, while the console still
listed them as assigned. Failing to start is the honest outcome.

```
this database is at schema v9 but this build of multiprofile only understands
v8. It was migrated by a newer version, and schema migrations are
forward-only. Install the newer version again, or restore the pre-upgrade
backup taken before that migration.
```

If you see that, you have two options and only two:

1. Reinstall the newer build. Nothing is lost.
2. Restore the whole-store backup you took before the upgrade. Anything you
   did after the upgrade is lost.

There is no third option. This is the reason step 1 says to take a backup
before updating.

Check what you are on:

```bash
sqlite3 ~/.local/share/multiprofile/control.db "SELECT MAX(version) FROM schema_version;"
```

The `schema_version` table is a ledger — one row per migration applied, with
its timestamp — so it is also the record of when your store was upgraded.

---

## 5. Extension update and rollback

Extension packages are versioned separately from your profiles.

**An update is fleet-wide.** The payload directory is shared by every profile
the extension is assigned to, so replacing it reaches all of them. The
console says so before you confirm, lists the affected profiles, and labels
the button `Update all N assigned profile(s)`. The API refuses a
`profileIds` argument on update rather than quietly applying it to everyone:

```
code: update_scope_is_fleet_wide
```

Independent per-profile extension versions are **not implemented**. If you
need one profile on an older version, that is not available today.

**Rollback** restores the previous version:

```bash
curl -s -X POST localhost:8765/api/extensions/<uuid>/rollback
```

The managed install path does not change across an update or a rollback, so
the extension keeps its ID and therefore its `chrome.storage.local` and any
granted host permissions. This is verified in a real browser by reading the
version out of the marker the extension's own content script writes into a
page — not from the database row
(`tests/test_extension_live.py::test_a_rollback_puts_the_previous_version_back_in_the_browser`).

Only **one** previous version is retained. A rollback with no retained
version is refused.

### A restart is required, and it closes your tabs

Assigning, updating, disabling or removing an extension on a **running**
profile is recorded as `pending restart` and takes effect the next time that
profile launches. Nothing is terminated underneath you.

When you do restart — from the console's `Restart N pending` button, or by
stopping and launching the profile — **open tabs are not reopened.** Cookies,
logins, extension storage and all profile data survive; the tabs themselves
do not. Finish what is open first. Tab restoration is not implemented.

---

## 6. What is not covered

* **No off-machine copy.** Backups are written to `$HOME`, the same disk as
  the data, so a disk failure takes both. Copy `~/multiprofile-backups/`
  elsewhere or set `MULTIPROFILE_BACKUP_DIR`.
* **A metadata backup cannot restore a session.** If your profiles are always
  running, every automatic backup will be the metadata kind. Stop them
  occasionally and run `multiprofile-backup` for a full one.
* **No signed update channel in practice.** The repository tooling, delta
  channel, updater and rollback all exist and are exercised end to end in a
  clean Debian container on every push, but there is no published host or
  signing key, so a `.deb` you build today is unsigned and has no update
  channel. See [`UPDATES.md`](UPDATES.md) and **F12**.
