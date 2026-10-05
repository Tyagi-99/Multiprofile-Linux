# Troubleshooting

**Start here:** `GET /api/health` reports the version, the database, and a
`permissions` block. An empty `faults` list there is a statement — every file
the product wrote landed `0600` — not an absence of checking.

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
