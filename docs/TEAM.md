# Working as a team

## What this product is, architecturally

**It is a local, single-user, per-computer application.** Read this before
planning anything around it, because the shape of the product decides the
shape of the workflow.

Established by inspection, not by assumption:

* **There is no user model.** No `user_id`, no `owner_id`, no accounts, no
  roles, no permissions-per-person anywhere in the code.
* **There is no server component.** `--host` defaults to `127.0.0.1`. The
  control database is a local SQLite file. Nothing is sent anywhere; there is
  no telemetry.
* **Authentication is a single shared static bearer token**, and only for
  requests that are *not* from localhost. It exists so you can reach your own
  install over an SSH tunnel. It is not a multi-user system: one token, shared,
  rotated by restarting with a new value.
* **There is no shared scheduler.** `docs/SCALING.md` states it plainly:
  placement across machines is yours.

`clientId` on a profile is a **workspace / customer grouping inside one
install**. It looks like multi-tenancy and is not: it carries no identity and
grants nothing.

So the supported team workflow is: **one install per person, per computer.**

---

## What we are explicitly not doing

These are not pending features. They are things that would break your data or
expose your machine, and the product does not support them:

* **Do not put the live control database on shared storage.** Not NFS, not
  SMB, not Dropbox, not Google Drive, not a synced folder. SQLite's locking
  does not work correctly over them, and two machines writing one
  `control.db` corrupts it. The product has no multi-writer coordination
  because it was never designed to have more than one.
* **Do not expose the API to the network to simulate collaboration.** Binding
  `--host 0.0.0.0` hands anyone who can reach the port full control: launch
  browsers, read cookie jars, read proxy credentials. There is no TLS, no
  rate limiting and no per-user authorisation. If you need to reach your own
  install remotely, use an SSH tunnel:
  ```bash
  ssh -N -L 8765:127.0.0.1:8765 you@your-machine
  ```
* **Do not share one profile between two people at the same time.** A profile
  is one browser data directory. Two Chromiums on it concurrently is
  undefined, and the identity seed is per profile, so "the same profile in two
  places at once" is also a detectable pattern.

---

## Onboarding a teammate

### 1. Install

**Arch, Omarchy, or any distribution — rootless, no sudo:**

```bash
git clone <this repository>
cd Multiprofile-Linux
bash scripts/install-local.sh
```

That creates a virtual environment under `~/.local/lib/multiprofile`, a
`multiprofile` command in `~/.local/bin`, a desktop entry and icons. It never
writes to the data directory, so re-running it is how you upgrade.

**Debian or Ubuntu — use the same rootless script.** There is a `.deb`, and
it installs, but it is the weaker path for a teammate: it is unsigned and has
no update channel (**F12**), so upgrading means rebuilding and `dpkg -i` by
hand, and its `Recommends` names `python3-webview`, which does not appear to
be a real package on Ubuntu. An unavailable *Recommends* is skipped silently,
so the desktop window degrades to a browser tab with nothing said about it.
`install-local.sh` tests for the window and tells you. Build the `.deb` when
you want a package to hand someone, not as your own install:

```bash
./build-deb.sh
sudo dpkg -i dist/multiprofile-native_1.9.0_all.deb
sudo apt-get install -f     # only if dpkg reports missing dependencies
```

For the desktop window on Debian or Ubuntu, pywebview needs the GTK bindings,
which are distro packages and cannot come from pip:

```bash
sudo apt install python3-gi gir1.2-webkit2-4.1
```

Every path needs **Python 3.11+** and a Chromium or Chrome on `PATH`.

#### The engine, which is where Ubuntu differs

```bash
sudo pacman -S chromium      # Arch / Omarchy
sudo apt install chromium    # Debian — a real .deb, nothing further to do
```

On **Ubuntu** that same command installs the **snap**, and two things follow
from the code:

1. `shutil.which("chromium")` returns `/snap/bin/chromium` and the resolver
   accepts it — `engine.py` walks `PATH` and does not ask what kind of
   package answered.
2. The snap `home` interface does not reach hidden directories in `$HOME`.
   Profiles live under `~/.local/share/multiprofile/chrome-profiles/`, so
   every `--user-data-dir` we pass sits inside a dot-directory the confinement
   excludes.

**This is predicted from the code and from snap's confinement rules. It has
not been measured — nobody has installed on a real Ubuntu machine yet.** The
good outcome is a launch that fails naming a permission error. The bad one is
a Chromium that ignores the flag and uses its own profile directory, which
would be profiles sharing an identity: an isolation failure, and much worse
than a crash. Check which you have before trusting it — launch a profile and
confirm `~/.local/share/multiprofile/chrome-profiles/<id>/` is actually
written.

**The way around the question entirely** is to pin Chrome for Testing. It is
a plain tarball with no confinement, it is the engine CI proves against on
every push, and the capability gate accepts it for unpacked extensions:

```bash
npx @puppeteer/browsers install chrome@stable
curl -s -X POST localhost:8765/api/engine/choose \
     -H 'Content-Type: application/json' \
     -d '{"binary": "/absolute/path/to/chrome"}'
```

**Pin on any machine that also has Google Chrome.** `google-chrome-stable` is
first in `BROWSER_BINARIES`, and Chrome from Google's own `.deb` is common on
Ubuntu desktops. Branded Chrome 137+ cannot load unpacked extensions at all,
so a profile with an extension assigned will refuse to launch, naming the
reason. Pinning is what stops a browser installed months later from changing
which engine your profiles run on.

### 2. Verify before trusting it with real work

```bash
multiprofile --version
multiprofile --doctor
python3 scripts/smoke-test.py
```

`--doctor` exits non-zero if something must be fixed first. `smoke-test.py`
runs 46 checks — create, edit, launch, stop, restart, proxy on and off,
backup, restore, and the whole extension lifecycle — against a **scratch**
data directory, so it is safe on a machine that already holds real profiles.
Expect `46 passed, 0 failed, 0 skipped`. Checks that need a browser are
skipped with a reason on a headless box, and then it is not evidence about
launching.

### 3. Start it

```bash
multiprofile                   # the desktop window
multiprofile --server-only     # API + console on http://127.0.0.1:8765/
```

Or launch **Multiprofile** from the application menu.

### 4. Turn on backups before you create anything you care about

`~/.local/share/multiprofile/` is yours, it is local, and nobody else can see
it — which also means nobody else has a copy of it.

```bash
systemctl --user enable --now multiprofile-backup.timer   # daily, verified
multiprofile-backup                                       # or one right now
```

Each backup is checksummed and its database is integrity-checked before it
counts as one. Seven are kept. If a profile is running when the timer fires
it takes a *metadata* backup — definitions, identity seeds, proxy and
extension settings, but not cookies or sessions — and records that in the
backup's `MANIFEST`. Stop your profiles now and then so a full one gets
taken.

Backups land in `$HOME`, the same disk as the data. Copy them elsewhere, or
set `MULTIPROFILE_BACKUP_DIR`. See
[`BACKUP_AND_RESTORE.md`](BACKUP_AND_RESTORE.md).

---

## Dividing work across people

Since every install is independent, coordination is a human process. What the
product supports:

**Who owns which accounts.** Keep this list outside the product — a shared
document. The product has no concept of ownership, so nothing stops two
people creating a profile for the same account, and nothing warns you.

**Handing a profile over.** Use a per-profile backup, which preserves the
identity seed, cookies and extension storage, so the receiving machine
presents the same browser:

```bash
# Sender
curl -s -X POST localhost:8765/api/profiles/<id>/backup \
     -H 'Content-Type: application/json' \
     -d '{"includeUserData": true, "passphrase": "agreed-out-of-band"}'

# Transfer the .zip however you transfer sensitive files, then on the receiver
curl -s -X POST localhost:8765/api/backups/restore \
     -H 'Content-Type: application/json' \
     -d '{"backupPath": "/path/to/that.zip", "passphrase": "agreed-out-of-band"}'
```

Encrypt it. That file contains cookies and session tokens; it is as sensitive
as the accounts it logs into. **Hand the profile over, do not clone it** —
two people using one identity from two IP addresses, concurrently, is one of
the easier things for a site to notice.

**Sharing proxies.** Proxy configuration is per profile and stored per
install. There is no shared proxy pool across machines. Each person enters
the same upstream details on their own machine.

**Sharing an extension.** Each person imports the same `.zip` on their own
install. Extension state (`chrome.storage.local`) is per profile and per
machine and is not synchronised. If the extension has its own cloud account,
**that data is not isolated by this product at all** — it is synchronised by
the extension's vendor, under whatever account you sign into, and this
product has no visibility into or control over it.

---

## Two limitations worth knowing on day one

**Nothing parks your browsers.** `parking_enabled` defaults on with a
30-minute idle timeout, but nothing enqueues the job, so a profile you leave
open stays open and holding RAM. Close what you are not using. 20 concurrent
profiles is the configured cap and RAM is the real wall.

**The engine ceiling is unmeasured against commercial anti-bot vendors.** The
product is sound for keeping ordinary sessions separate and consistent, which
is what the test suite measures. It has **not** been measured against a
serious anti-bot vendor, and no claim is made there. Do not learn that on a
live account that matters.
