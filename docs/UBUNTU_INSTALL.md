# Installing on Ubuntu

**Status: this has not been run on Ubuntu.** Every step below follows from
code in this repository and from how Ubuntu packages Chromium, and the two
places it is most likely to bite are called out with the command that settles
each one. Nobody has yet installed this product on an Ubuntu machine, so
treat the first install as the test it is, and tell the maintainer what
actually happened.

Verified on Arch/Omarchy with Python 3.14. See
[`INSTALLATION.md`](INSTALLATION.md) for the general picture and
[`TEAM.md`](TEAM.md) for onboarding several people.

---

## 0. Check Python first — this decides whether you can proceed at all

```bash
python3 -V
```

**Python 3.11 or newer is required**, and `scripts/install-local.sh` refuses
to continue below it rather than failing halfway. As shipped:

| Ubuntu | python3 | usable |
|---|---|---|
| 24.04 LTS and newer | 3.12+ | yes |
| 22.04 LTS | 3.10 | **no** — install a newer Python first, then `PYTHON=python3.12 bash scripts/install-local.sh` |

If `python3 -V` says 3.10 or lower, stop here and sort that out. Everything
after this assumes it does not.

## 1. System packages

```bash
sudo apt update
sudo apt install git python3-venv python3-gi
```

- **`python3-venv` is not optional on Ubuntu.** Debian splits `venv` out of
  the standard library package, and without it `python3 -m venv` fails with
  *"ensurepip is not available"* partway through the installer. This is the
  single most likely reason a first attempt fails.
- **`python3-gi`** plus a WebKit typelib is what gives you a real application
  window. Without them `multiprofile` still runs, but the console opens as a
  tab in your own browser — beside your personal tabs, which is the one place
  a profile manager should not be. The typelib package name has changed
  across releases, so find yours:

  ```bash
  apt-cache search gir1.2-webkit
  sudo apt install gir1.2-webkit2-4.1    # or whichever that lists
  ```

  The installer tests for the window at the end and tells you which of the
  two you have, so you do not have to guess.

## 2. Get the code and install

```bash
git clone <this repository>
cd Multiprofile-Linux
bash scripts/install-local.sh
```

Rootless: a virtual environment under `~/.local/lib/multiprofile`, a
`multiprofile` command in `~/.local/bin`, a desktop entry and icons. Nothing
outside `$HOME`, no `sudo`. Re-running it is how you upgrade, and it contains
no code that writes to the data directory.

**Keep the checkout.** The application itself is copied into the venv, so it
survives the clone moving — but `multiprofile-backup` is a wrapper that execs
`scripts/backup.sh` from where you cloned it. Delete the clone and your
backup command stops working.

If `~/.local/bin` is not on your `PATH`, the installer says so. Log out and
in, or add it.

## 3. The engine — the part where Ubuntu differs

Do **not** start with `sudo apt install chromium` on Ubuntu. On Debian that
is a real `.deb`; on Ubuntu it installs the **snap**, and from the code two
things follow:

1. `shutil.which("chromium")` answers `/snap/bin/chromium` and the resolver
   accepts it — `engine.py` walks `PATH` and does not ask what kind of
   package replied.
2. The snap `home` interface does not reach hidden directories in `$HOME`,
   and every profile lives under `~/.local/share/multiprofile/chrome-profiles/`.
   So every `--user-data-dir` we pass sits inside a dot-directory the
   confinement excludes.

**This is predicted, not measured.** The good outcome is a launch that fails
naming a permission error. The bad one is a Chromium that ignores the flag
and uses its own profile directory, which would mean profiles sharing one
identity — an isolation failure wearing the costume of a working install.
Section 6 has the command that tells you which you got.

### Recommended: Chrome for Testing, pinned

A plain tarball, no confinement, and the engine this project's CI proves on
every push. Unpacked extensions work on it, which branded Google Chrome 137+
cannot do at all.

```bash
sudo apt install nodejs npm
npx @puppeteer/browsers install chrome@stable
```

That prints the path it installed to. Pin it so installing another browser
later cannot silently move your profiles onto a different engine:

```bash
mkdir -p ~/.config/multiprofile
printf '{"binary": "%s"}\n' /absolute/path/to/chrome > ~/.config/multiprofile/engine.json
```

Confirm the pin took:

```bash
multiprofile --doctor | grep engine
```

It should name your pinned binary and report `supported`.

### If you already have Google Chrome installed

`google-chrome-stable` is **first** in `BROWSER_BINARIES`, so it wins the
`PATH` search. Branded Chrome 137+ cannot load unpacked extensions, so a
profile with an extension assigned will refuse to launch and say why.
Pinning as above is what prevents that.

## 4. Verify before trusting it with real work

```bash
multiprofile --version
multiprofile --doctor
~/.local/lib/multiprofile/venv/bin/python scripts/smoke-test.py
```

`--doctor` exits non-zero when something must be fixed first.

Run the smoke test with **the interpreter behind the install**, as above, not
the system `python3`: it talks CDP and needs `websocket-client`, which the
app's environment has and a bare system Python generally does not. With the
wrong interpreter it refuses to start and says so.

It runs 49 checks — create, edit, launch, stop, restart, proxy on and off,
backup, restore, and the whole extension lifecycle — against a **scratch**
data directory, so it is safe on a machine that already holds real profiles.
Expect `49 passed, 0 failed, 0 skipped`. Checks needing a browser are skipped
with a reason on a headless box, and then it is not evidence about launching
anything.

## 5. Turn on backups

```bash
systemctl --user enable --now multiprofile-backup.timer   # daily, verified
multiprofile-backup                                       # or one right now
```

Each backup is checksummed and its database integrity-checked before it
counts as one; seven are kept. A profile that is running when the timer fires
gets a *metadata* backup — definitions, identity seeds, proxy and extension
settings, but not cookies or sessions — and the `MANIFEST` records that. Stop
your profiles now and then so a full one gets taken.

Budget roughly 3 GB for seven backups of a small fleet. They land in `$HOME`,
on the same disk as the data, so copy them elsewhere or set
`MULTIPROFILE_BACKUP_DIR`.

**On a machine you do not stay logged into**, a user timer only runs while
you have a session. Allow it to run anyway:

```bash
loginctl enable-linger "$USER"
```

## 6. Settle the snap question, if you chose snap Chromium anyway

Launch one profile, then look at whether its directory was actually written:

```bash
multiprofile --server-only &
sleep 3
curl -s localhost:8765/api/profiles -X POST \
     -H 'Content-Type: application/json' -d '{"name":"snaptest"}'
```

Launch `snaptest` from the console, then:

```bash
ls -la ~/.local/share/multiprofile/chrome-profiles/
```

A directory for that profile, with a `user-data/Default/` inside it, means
the confinement is not in the way. **No directory, or an empty one, while the
browser is plainly running, means Chromium ignored `--user-data-dir` — stop
and switch to Chrome for Testing before creating anything else.** Two
profiles sharing a directory share an identity.

## 7. Start it

```bash
multiprofile                   # the desktop window
multiprofile --server-only     # API + console on http://127.0.0.1:8765/
```

Or launch **Multiprofile** from the application menu.

Everything is local: `--host` defaults to `127.0.0.1`, there is no account,
no telemetry and no server component. Your data lives in
`~/.local/share/multiprofile/`.

## 8. Know these before your first session

- **Use the `linux` identity.** It is the supported one. A profile claiming
  Windows or macOS on a Linux host is detectable by fonts alone — measured,
  and recorded as gate T3 in [`09_ENGINE_PROGRAM`](09_ENGINE_PROGRAM.md) §6b.
  New profiles default to the host's platform.
- **No third-party fingerprint checker has ever been run against this
  product** (gate T4). Internal consistency is measured; detection is not.
  Fine for isolated browsing and separate logins; do not learn its limits on
  an account you cannot afford to lose.
- **A controlled restart does not restore open tabs.**
- **Extensions installed from the Web Store inside a profile** are
  browser-side, not fleet-managed: no bulk assign, update or rollback.
- **Nothing auto-parks your browsers.** Idle parking defaults off; a profile
  you leave open stays open.

## 9. Uninstall

```bash
bash scripts/uninstall-local.sh
```

Removes the application and deliberately leaves your profiles where they are.

---

## If something fails

Collect this and send it to the maintainer — it is the whole picture and
contains no secrets:

```bash
multiprofile --doctor-json
python3 -V
cat /etc/os-release | head -2
command -v chromium google-chrome google-chrome-stable
```

See also [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).
