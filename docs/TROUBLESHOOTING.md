# Troubleshooting

**Start here:** `multiprofile --doctor`. It prints the engine, the sandbox, the
secrets backend, the identity support matrix and the fault counts, and **exits
non-zero when something must be fixed before a profile will work**. Add
`--doctor-json` for a bug report.

`GET /api/health` reports the same and more at runtime: the version, the
database, and a `permissions` block. An empty `faults` list there is a
statement — every file the product wrote landed `0600` — not an absence of
checking.

To check a whole installation rather than one symptom:

```bash
~/.local/lib/multiprofile/venv/bin/python scripts/smoke-test.py
```

49 checks against a **scratch** data directory, so it is safe to run on a
machine holding real profiles. It covers create, edit, launch, stop, restart,
proxy on and off, backup, restore and the full extension lifecycle.

### Where the logs are

| File | What it holds |
|---|---|
| `~/.local/share/multiprofile/app.log` | the application log, including tracebacks |
| `~/.local/share/multiprofile/audit.jsonl` | every launch, stop, delete, import, token mint and **refusal with its reason** (`0600`) |

For more detail, restart with `--log-level DEBUG`. Both files are under
`$XDG_DATA_HOME` if you set it.

When reporting a problem, these three are what make it diagnosable:

```bash
multiprofile --version
multiprofile --doctor-json
tail -50 ~/.local/share/multiprofile/app.log
```

### The console loads but the page is blank

An install whose static assets are missing. The page itself serves, and every
script it references 404s, so there is nothing to render. This was a real
packaging defect in `pip install` paths before the `package-data` globs were
made recursive — `static/index.html` shipped and `static/_next/` did not.

Reinstall:

```bash
bash scripts/install-local.sh
```

and confirm:

```bash
~/.local/lib/multiprofile/venv/bin/python scripts/smoke-test.py   # "every asset the console references serves"
```

### A launch was refused with `extension_runtime_unsupported`

The profile has an extension assigned and the selected browser cannot load
unpacked extensions. **Branded Google Chrome removed `--load-extension` in
137**, and no flag brings it back. The refusal names the binary and the
version it found.

Use Chromium or Chrome for Testing as the engine, and pin it so an unrelated
package install cannot change it back:

```bash
curl -s -X POST localhost:8765/api/engine/choose \
     -H 'Content-Type: application/json' \
     -d '{"binary": "/usr/bin/chromium"}'
```

The product never substitutes a different browser for you. Web Store installs
still work through the browser's own flow, per profile, from the Extensions
page.

### The application refuses to start: schema v<N> but this build understands v<M>

A newer build migrated this database and migrations are forward-only. Install
the newer version again, or restore the backup taken before that upgrade.
There is no third option — see
[`BACKUP_AND_RESTORE.md`](BACKUP_AND_RESTORE.md) §4.

### An extension was assigned but is not running

Check whether the profile was running at the time. Assignment to a running
profile is recorded as **pending restart** and applied on the next launch;
nothing is terminated underneath you. Restart that profile — and note that
**open tabs are not reopened**, though cookies, logins and extension storage
are kept.

### The console is empty, or devices do not load

The API is not running. Start it with `multiprofile --server-only` and open
<http://127.0.0.1:8765/>.

### "Chrome not found"

No Chromium or Chrome on `PATH`. `pacman -S chromium` or `apt install
chromium`. The launcher tries `google-chrome-stable`, `google-chrome`,
`chromium`, `chromium-browser` and takes the first hit.

### Windows launch on top of each other instead of tiling

The work area could not be determined, and the product refuses to invent one —
a wrong work area puts windows off-screen, which is worse than not tiling. The
error names each detector it tried. On Wayland it needs `hyprctl`, `swaymsg` or
`wlr-randr`; on X11, `xrandr`, `xprop` or `xdpyinfo`. If none of them can
answer, set the area yourself:

```bash
export MULTIPROFILE_WORKAREA=0,26,2560,1574   # x,y,width,height
```

### A launch was refused with `route_unresolved`

Deliberate, and it is the behaviour you want. The profile asked for a proxy,
pool member or gateway that could not be resolved, and launching anyway would
have put the profile on **your own IP** while reporting success — which is
exactly what **F17** was. Fix the route, or set the profile to Direct
Connection if browsing from this machine's address is what you intended.

### A launch was refused with `consistency_blocked`

The profile carries a contradiction no real browser produces — typically a
user-agent and a platform that disagree. The response lists them. `POST
/api/profiles/<id>/launch?force=1` overrides it.

### The profile's timezone is not what I set

If `geoAuto` is on, the timezone you see was only the creation default, and the
launch replaces it with one that matches where the traffic actually leaves
from: the exit IP's country through a proxy, this machine's own zone with no
proxy (**F43**). Set a timezone explicitly and it is treated as your choice and
kept — a disagreement is then reported rather than overwritten.

### A profile will not start: "Too many profiles running"

The concurrency cap, 20 by default. Stop one, or raise
`MULTIPROFILE_MAX_CONCURRENT_PROFILES`. Measured cost is 130–216 MB PSS per
running profile, so 20 is about 2.8 GB.

### A crashed profile will not delete

It should. If a profile is stuck, check `/api/health` and the audit log at
`~/.local/share/multiprofile/audit.jsonl`, which records every launch, stop and
refusal with its reason.
