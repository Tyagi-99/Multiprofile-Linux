# Window Tiling via Localhost CDP — Design Spec

**Date:** 2026-09-21  
**Status:** Awaiting user approval before implementation  
**Product:** multiprofile-native (Multiprofile Linux)  
**Related release baseline:** 1.3.0 (sandbox probe, concurrency queue default 20, basic tiling attempt)  
**Scope:** Replace cascading / unreliable window placement with a deterministic equal-card grid using Chromium CDP outer bounds only.

---

## 1. Problem statement

Multi-profile launch succeeds, but Chromium windows cascade/overlap. Observed causes on the target IdeaPad (Ubuntu Wayland, `DISPLAY=:0` / XWayland):

1. Post-launch placement relied on `xdotool`/`wmctrl`, which are **not installed** → placement effectively no-ops.
2. Chromium `--window-size` controls **content** size, not outer frame → decorations overflow grid cells.
3. `--window-position` is often ignored by the window manager → default cascade wins.
4. Single early place attempt with no ready-wait, no bound re-read, no overlap/off-screen validation.
5. No user-facing **Tile Windows** reflow after settle.

**Non-goals:** fingerprint/exclusivity changes, proxy changes, profile DB redesign, dashboard redesign beyond a Tile control, xdotool/wmctrl fallbacks, Web Store extension installs.

---

## 2. Goals & non-goals

### Goals

- Deterministic **profile → slot → rectangle → native window** mapping.
- Equal-width / equal-height cards; configurable gap; zero overlap; fully inside work area.
- Outer bounds via CDP `Browser.setWindowBounds` / `Browser.getWindowBounds`.
- Localhost-only ephemeral CDP ports (`127.0.0.1`), cleaned up on profile exit.
- WorkAreaProvider with fail-loud behavior (never silent 1920×1080).
- Launch Selected = tiling **best-effort**; Tile-required / `POST /api/windows/tile` = **fail-closed** on missing work area.
- Verify actual bounds (≤4px tolerance, max 3 corrections).
- Unit geometry tests for N=1..20 across multiple resolutions; CDP smoke for 1/4/20.

### Non-goals / locked constraints

- Do **not** install or use xdotool/wmctrl as fallback.
- Do **not** bind CDP to `0.0.0.0`.
- Do **not** change fingerprint, exclusivity (`--disable-extensions-except`), proxy, profile lifecycle semantics, or dashboard chrome beyond Tile Windows.
- If CDP cannot identify/control native windows on this Chrome build in headed mode → **STOP** and document limitation (no workaround in v1).

---

## 3. Verified platform facts (pre-implementation)

| Fact | Result |
|------|--------|
| Chrome on IdeaPad | Google Chrome **151.0.7922.108** |
| Session | Wayland (`WAYLAND_DISPLAY=wayland-0`), `DISPLAY=:0` (XWayland available) |
| `Browser.setWindowBounds` | **YES** (protocol + live call) |
| `Browser.getWindowBounds` | **YES** |
| `Browser.getWindowForTarget` | **YES** |
| Localhost debugging | `http://127.0.0.1:<port>/json/version` works with `--remote-debugging-address=127.0.0.1` |
| xdotool/wmctrl on IdeaPad | Not installed (irrelevant for CDP-only v1) |

---

## 4. Final architecture

```
┌─────────────────────────────────────────────────────────────┐
│ UI / API                                                     │
│  Launch Selected (best-effort tile)                          │
│  Launch Selected + Tile (tile-required)                      │
│  POST /api/windows/tile   GET /api/windows/layout            │
│  [Tile Windows] button                                       │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│ WindowLayoutManager (orchestrator)                           │
│  - build SlotPlan                                            │
│  - apply/verify via CdpWindowController                      │
│  - validate no-overlap / in-work-area / equal-size           │
└───┬─────────────────┬───────────────────┬───────────────────┘
    │                 │                   │
    ▼                 ▼                   ▼
WorkAreaProvider   GridCalculator    CdpWindowController
 X11/XWayland       aspect-aware      port per profile
 desktop probes     priority rules    target→windowId
 fail-loud          equal cards       set/get bounds
                                      verify ≤3 retries
```

**Runtime registry** (in-memory, keyed by `profileId`):

```
{ pid, cdpPort, windowId?, targetId?, slotIndex?, desiredRect?, actualRect?, tileStatus }
```

Cleared on profile stop (port dies with browser process).

Existing modules keep ownership of launch flags (fingerprint, exclusivity, proxy). Layout only adds CDP bind flags + post-launch bound control.

---

## 5. Final data flow

### 5.1 Launch Selected (best-effort tile)

```
selected profileIds (stable order)
  → launch each profile normally (existing launcher path)
  → WorkAreaProvider.detect()
       OK  → GridCalculator.plan(N, workArea)
            → for each profile: ensure CDP port recorded → map window → set/verify bounds
            → return { launched: ok, tile: { ok, results[] } }
       FAIL → browsers already/still launching; tile: { ok:false, error: work_area_unavailable, reason }
```

### 5.2 Launch Selected + Tile (tile-required)

```
WorkAreaProvider.detect() FIRST
  FAIL → abort job before launch; clear error
  OK → SlotPlan for N
  FAIL plan validation → abort before launch
  OK → launch with CDP → apply/verify each slot → batch validation
```

### 5.3 Tile Windows (`POST /api/windows/tile`)

```
running profiles (optional filter profileIds)
  → WorkAreaProvider.detect() (fail-closed)
  → SlotPlan for current count
  → for each: CDP map → set/verify
  → validation report
```

---

## 6. Final grid algorithm

### 6.1 Priority order (must not reorder)

1. ZERO OVERLAP  
2. COMPLETELY INSIDE WORK AREA  
3. EQUAL WIDTH/HEIGHT for all cards  
4. MAXIMIZE USABLE AREA  
5. REASONABLE BROWSER CARD ASPECT RATIO (~1.3–1.6 preferred, not mandatory if it breaks 1–3)  
6. MINIMIZE EMPTY CELLS  

### 6.2 Procedure

```
given N, WorkArea{x,y,w,h}, gap, margin (default gap=6, margin=0)

candidates: for cols in 1..N:
  rows = ceil(N / cols)
  score by (empty cells, cell aspect vs preferred, area utilization)
  compute cellW/cellH using integer division so that:
    total = margin*2 + gap*(cols+1) + cellW*cols  ≤ work.w
    (same for height)
  place only i in 0..N-1:
    col = i % cols; row = i // cols
    rect = { left: x+margin+gap+col*(cellW+gap),
             top:  y+margin+gap+row*(cellH+gap),
             width: cellW, height: cellH }
  reject candidate if any rect outside work area OR any pair overlaps
  never emit rects for empty cells (cols*rows > N)

pick best surviving candidate by score (rules 4–6 only among valid plans)
```

**Forbidden:** cascading offsets (`x += 30`), random WM placement as authority, unequal card sizes in one plan.

---

## 7. WorkAreaProvider design

```
WorkAreaProvider.detect() -> WorkArea | WorkAreaError

WorkArea = { x, y, w, h, source: str }

Providers (try in order):
  1. X11/XWayland: _NET_WORKAREA via xprop on $DISPLAY
  2. Available desktop geometry: xdpyinfo / gdk / similar when present
  3. Explicit env override only if set:
       MULTIPROFILE_WORKAREA=x,y,w,h  (operator-supplied — logged as source=env)

Fallback:
  DO NOT assume 1920×1080.
  If all providers fail → WorkAreaError{ code: work_area_unavailable, reasons: [...] }
```

Callers:

- Tile-required + `POST /api/windows/tile` → fail closed on `WorkAreaError`.
- Launch Selected → continue launch; attach tiling error to response.

---

## 8. CDP mapping flow

```
profileId
  → browser process (pid)
  → ephemeral cdpPort bound to 127.0.0.1
  → WebSocket: browser-level debugger URL from /json/version
  → Target.getTargets / getTargets list
  → select page target (deterministic rules below)
  → Browser.getWindowForTarget(targetId) → windowId + bounds
  → layout slot (from SlotPlan)
  → Browser.setWindowBounds(windowId, outer bounds)
  → Browser.getWindowBounds → verify
```

### 8.1 Security

- `--remote-debugging-address=127.0.0.1` only.  
- Never `0.0.0.0`.  
- Ephemeral port per profile; cleared from runtime on stop.  

### 8.2 Page target selection (when multiple `type=page`)

1. Prefer URL matching launch start URL (or `about:blank` immediately after start).  
2. Else prefer non-devtools, top-level page targets.  
3. Else stable sort by `targetId` ascending; pick first.  
4. **Always log** chosen `targetId`/`url`/`title` and rejected candidates.

Do **not** silently take “first page in array” without logging.

### 8.3 Outer bounds

CDP `Bounds` `{left,top,width,height,windowState:"normal"}` are the authority for **outer** window geometry. CLI `--window-position` / `--window-size` are weak hints only.

---

## 9. Launch pipeline

### Per profile (shared)

1. Existing profile resolve + exclusivity + proxy + fingerprint flags (unchanged).  
2. Allocate free TCP port on `127.0.0.1`.  
3. Add CDP flags; launch Chrome; record `{pid, cdpPort}`.  
4. Wait until CDP HTTP/WebSocket ready (timeout, e.g. 8s).  

### Tiling branch

- If work area + SlotPlan available → map window → apply/verify for assigned slot.  
- Launch Selected: tiling failures are per-profile / aggregate warnings, not launch aborts.  
- Tile-required: work area + plan must succeed **before** any launch.

---

## 10. Verification / retry pipeline

```
desired = slot.rect  # outer
for attempt in 1..3:
  Browser.setWindowBounds(windowId, desired + windowState=normal)
  sleep settle (150–300ms)
  actual = Browser.getWindowBounds(windowId)
  if max(|Δleft|,|Δtop|,|Δwidth|,|Δheight|) ≤ 4px: SUCCESS
else: tileStatus = verify_failed (browser left running)
```

After batch: `validateNoOverlap`, `validateWithinWorkArea`, `validateEqualSizes` on **actual** rects of successful tiles. Log any failures with profileId, desired, actual.

---

## 11. API design

### `POST /api/windows/tile`

Request (optional):

```json
{ "profileIds": ["p1", "p2"] }
```

Omit `profileIds` → all running profiles with a live `cdpPort`.

Response:

```json
{
  "ok": true,
  "workArea": { "x": 0, "y": 0, "w": 1920, "h": 1048, "source": "xprop:_NET_WORKAREA" },
  "grid": { "cols": 2, "rows": 2, "gap": 6, "cellW": 950, "cellH": 516 },
  "results": [
    {
      "profileId": "p1",
      "slotIndex": 0,
      "desired": { "left": 6, "top": 6, "width": 950, "height": 516 },
      "actual":  { "left": 6, "top": 6, "width": 950, "height": 516 },
      "windowId": 123,
      "targetId": "…",
      "ok": true,
      "error": null
    }
  ],
  "validation": { "valid": true, "overlaps": [], "offscreen": [], "unequalSize": false }
}
```

Work area failure → `ok: false`, `error: "work_area_unavailable"`, HTTP 4xx/5xx as appropriate — **fail closed**.

### `GET /api/windows/layout`

Read-only snapshot: work area (or error), current SlotPlan / last desired+actual bounds, tileStatus per running profile.

### UI

Single **Tile Windows** button → `POST /api/windows/tile`. No broader dashboard redesign.

### Launch response extension

Launch Selected includes:

```json
"tile": { "ok": true|false, "error": null|"work_area_unavailable"|…, "results": [ … ] }
```

---

## 12. Error handling

| Code / situation | Behavior |
|------------------|----------|
| `work_area_unavailable` | Tile-required & Tile API: fail closed. Launch Selected: launch OK, tile.ok=false |
| `cdp_unreachable` | That profile tile fails; others continue |
| `no_page_target` | Log targets; profile tile fails |
| `verify_failed` | After 3 attempts; profile remains running |
| `cdp_window_bounds_unsupported` | Hard stop for tiling features; document; no X11 tool fallback |
| Overlap/off-screen in validation | `validation.valid=false` + details; optional single corrective re-tile using same algorithm (no cascade) |
| CDP bind not localhost | Refuse launch tiling / refuse to start debugging on non-localhost |

---

## 13. Test plan

### Unit (no Chrome)

- N ∈ {1,2,3,4,5,6,8,9,10,12,15,16,20}  
- Work areas: 1366×768, 1920×1080, 2560×1440, 3440×1440, offset panel (0,32,1920,1048)  
- Assert: equal sizes, zero overlap, inside work area, no phantom slots, gap respected  
- WorkAreaProvider: success path; all-fail → explicit error (not 1920×1080)

### CDP integration (gated env)

- Chrome 151 headed/smoke: 1, 4, 20 profiles  
- Localhost-only port check  
- Multi page-target selection logging  
- Verify retry path  
- Cleanup on stop  

### Manual (IdeaPad)

- 4 / 9 / 20 visual card grids  
- Tile after closing 2 windows  
- Exclusivity still on  
- No non-localhost CDP  

---

## 14. Files / modules

### Create

| Path | Role |
|------|------|
| `src/multiprofile/browser/work_area.py` | WorkAreaProvider + providers + fail-loud error |
| `src/multiprofile/browser/grid.py` | GridCalculator / SlotPlan / validators |
| `src/multiprofile/browser/cdp_window.py` | Localhost CDP client: targets, get/set bounds, verify |
| `src/multiprofile/browser/layout_manager.py` | WindowLayoutManager orchestrator |
| `tests/test_grid_layout.py` | Geometry unit tests |
| `tests/test_work_area.py` | Provider fail-loud / mock tests |
| `tests/test_cdp_window_smoke.py` | Optional gated CDP smoke |

### Modify

| Path | Change |
|------|--------|
| `src/multiprofile/browser/window_layout.py` | Thin re-exports or deprecate in favor of new modules (keep import compatibility if needed) |
| `src/multiprofile/browser/launcher.py` | Ephemeral localhost CDP port; register runtime; call layout best-effort/required; **no** xdotool path for v1 tiling |
| `src/multiprofile/jobs.py` | SlotPlan before/after launch per mode; pass slot rects; aggregate tile results |
| `src/multiprofile/api/server.py` | `POST /api/windows/tile`, `GET /api/windows/layout`; health may expose workArea source |
| `src/multiprofile/config.py` | gap/margin/tolerance/settle/retry settings (defaults) |
| `src/static/app.js` + `index.html` | Tile Windows button only |
| `scripts/test.sh` / tests | Wire new unit tests |
| `CHANGELOG.md` / version | After implementation approval (not in this doc phase) |

### Do not modify (unless a compile/link requires a trivial hook)

- Fingerprint generation / exclusivity flag defaults  
- Proxy subsystem  
- Profile database schema (runtime CDP fields stay in-memory unless already persisted runtime store is the natural place — prefer in-memory registry)  
- Extension bundle  

---

## 15. Remaining technical risks

1. **Wayland vs XWayland:** Headed Chrome may be XWayland on IdeaPad; CDP bounds usually work, but if a future Chrome is Wayland-native and CDP window bounds no-op → hard stop (`cdp_window_bounds_unsupported`), no wmctrl fallback.  
2. **Decoration / DPI:** get vs set may differ by a few pixels; 4px tolerance + 3 retries mitigate; large persistent deltas must be logged.  
3. **Multi-target noise:** Extensions/background pages already appear in target lists — selection rules + logging required.  
4. **Port exhaustion / races:** Ephemeral ports must be allocated carefully under parallel launch.  
5. **Restore/maximize:** Chrome may apply `windowState` maximized; always force `normal` before set bounds.  
6. **Session restore geometry:** May fight first apply; verify loop is mandatory.  
7. **Multi-monitor:** v1 uses primary work area from provider; explicit monitor selection is future work (can be env override).  

---

## 16. Implementation gate

**This document must be approved by the user before:**

- writing an implementation plan, or  
- modifying product code for tiling.

Approved design sections: §1 Architecture, §2 Grid+CDP mapping (with mods), §3 Apply/verify+API (with launch modes), §4 Tests+acceptance.

---

## 17. Summary checklist (locked)

- [x] Approach A: localhost CDP + outer `Browser.setWindowBounds`  
- [x] No xdotool/wmctrl fallback  
- [x] WorkAreaProvider fail-loud  
- [x] Priority rules 1–6  
- [x] Deterministic profile→port→target→windowId→slot  
- [x] Launch Selected best-effort vs tile-required fail-closed  
- [x] Verify ≤4px / max 3 retries  
- [x] Tile API + layout GET + Tile Windows button  
- [x] Geometry tests 1–20 + resolutions  
- [x] Fingerprint/exclusivity/proxy untouched  
