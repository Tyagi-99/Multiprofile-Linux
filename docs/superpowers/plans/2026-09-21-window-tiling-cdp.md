# CDP Window Tiling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace cascading Chromium window placement with deterministic equal-card tiling via localhost-only CDP `Browser.setWindowBounds`, without changing fingerprint/exclusivity/proxy behavior.

**Architecture:** `WindowLayoutManager` is the sole owner of slot geometry. `WorkAreaProvider` detects usable desktop bounds (fail-loud). `CdpWindowController` maps profile→port→target→windowId and applies/verifies outer bounds. `launcher.py` only launches + registers runtime CDP ports. `jobs.py` only orchestrates. Explicit per-profile launch state machine; Tile API is idempotent.

**Tech Stack:** Python 3.11+, Flask API, Google Chrome 151 CDP (websockets/HTTP JSON), existing multiprofile-native package layout, `scripts/test.sh` + pytest-style unit tests under `tests/`.

**Design spec:** `docs/superpowers/specs/2026-09-21-window-tiling-cdp-design.md`

## Global Constraints

- CDP bind address MUST be `127.0.0.1` only — never `0.0.0.0`.
- Outer bounds via CDP only — no xdotool/wmctrl fallback in v1.
- Anti-detect exclusivity stays ON by default (`allow_webstore_extensions=False`).
- Do not change fingerprint, proxy, profile DB schema, or dashboard beyond Tile Windows button.
- `WindowLayoutManager` is the ONLY place that computes x/y/width/height/slot.
- Launch Selected = tiling best-effort; Tile-required / `POST /api/windows/tile` = fail-closed on missing work area.
- Verify tolerance ≤4px; max 3 correction attempts.
- Partial tile failure does NOT auto-kill the browser.
- `POST /api/windows/tile` must be idempotent (no drift on repeat).
- If CDP cannot control windows on this Chrome build → stop with `cdp_window_bounds_unsupported` (no workaround).

---

## File map

### Create

| File | Responsibility |
|------|----------------|
| `src/multiprofile/browser/runtime_state.py` | Launch state enum + in-memory registry |
| `src/multiprofile/browser/work_area.py` | `WorkArea`, `WorkAreaError`, `WorkAreaProvider` |
| `src/multiprofile/browser/grid.py` | `SlotPlan`, `GridCalculator`, validators |
| `src/multiprofile/browser/cdp_window.py` | Localhost CDP client + bound apply/verify |
| `src/multiprofile/browser/layout_manager.py` | `WindowLayoutManager` orchestrator |
| `tests/test_grid_layout.py` | Geometry unit tests N=1..20 |
| `tests/test_work_area.py` | Fail-loud / mock provider tests |
| `tests/test_runtime_state.py` | State transition tests |
| `tests/test_tile_idempotent.py` | Plan stability / no-drift tests |

### Modify

| File | Change |
|------|--------|
| `src/multiprofile/config.py` | Tile gap/margin/tolerance/retries/settle settings |
| `src/multiprofile/browser/window_layout.py` | Keep `Rect`/`WorkArea` compat shims; remove authority of place-by-pid for tiling; delegate detects to new modules |
| `src/multiprofile/browser/launcher.py` | Always allocate localhost CDP port on launch; register runtime; remove layout math / xdotool tile path; optional apply via layout manager |
| `src/multiprofile/jobs.py` | Remove `compute_layout` math; call `WindowLayoutManager` for best-effort or required tile |
| `src/multiprofile/api/server.py` | `POST /api/windows/tile`, `GET /api/windows/layout`; extend launch/job responses |
| `src/multiprofile/storage/profiles.py` | Expose richer status fields from runtime registry in list payloads (`launchState`) without schema migration |
| `src/static/app.js` / `index.html` | Tile Windows button + show Queued/launchState labels if needed |
| `scripts/test.sh` | Invoke new unit tests |
| `CHANGELOG.md` / version | Bump after feature complete (e.g. 1.4.0) — last task |

### Do not touch

- `fingerprint.py`, extension exclusivity defaults, proxy modules, profile JSON schema files, unrelated SPA chrome.

---

## State machine (locked)

Success path:

`CREATED → LAUNCHING → CDP_READY → WINDOW_FOUND → POSITIONING → VERIFIED → RUNNING`

Failure terminals (browser may still be alive unless launch itself failed):

`LAUNCH_FAILED | CDP_FAILED | WINDOW_NOT_FOUND | POSITION_FAILED | VERIFY_FAILED`

On `stop_profile`: clear registry entry; process kill as today.

---

## Data flow (locked)

```
profileIds (stable order)
  → WorkAreaProvider.detect()
  → GridCalculator.plan(N, workArea)  # ONLY place that yields rects
  → SlotPlan[{profileId, slotIndex, rect}]
  → launcher starts Chrome + localhost CDP (no rect math)
  → CdpWindowController: port → target → windowId
  → setWindowBounds(outer) → getWindowBounds → retry ≤3
  → validateNoOverlap / withinWorkArea / equalSize
  → runtime launchState updates
```

---

### Task 1: Runtime state registry

**Files:**
- Create: `src/multiprofile/browser/runtime_state.py`
- Test: `tests/test_runtime_state.py`

**Interfaces:**
- Produces:
  - `class LaunchState(str, Enum)` with values listed in Global Constraints
  - `class ProfileRuntime` dataclass: `profile_id, launch_state, pid, cdp_port, target_id, window_id, slot_index, desired_rect, actual_rect, error, updated_at`
  - `get_runtime(profile_id) -> ProfileRuntime | None`
  - `upsert_runtime(profile_id, **fields) -> ProfileRuntime`
  - `set_launch_state(profile_id, state: LaunchState, error: str | None = None) -> ProfileRuntime`
  - `clear_runtime(profile_id) -> None`
  - `list_running() -> list[ProfileRuntime]` (pid alive OR state in active set)
  - `transition(profile_id, to: LaunchState, *, error=None, **fields)` enforcing allowed edges (log + allow force on failure terminals)

- [ ] **Step 1: Write failing tests** for create→transition→clear and that unknown profile returns None

- [ ] **Step 2: Run** `PYTHONPATH=src python -m pytest tests/test_runtime_state.py -v` → expect fail (module missing)

- [ ] **Step 3: Implement** registry with threading.Lock; allowed transitions map; failure states always allowed from non-terminal

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit** `feat(runtime): profile launch state registry`

---

### Task 2: Config knobs for tiling

**Files:**
- Modify: `src/multiprofile/config.py`

**Interfaces:**
- Extend `Settings` with:
  - `window_tile_gap: int = 6`
  - `window_tile_margin: int = 0`
  - `window_tile_tolerance_px: int = 4`
  - `window_tile_max_retries: int = 3`
  - `window_tile_settle_ms: int = 200`
  - `window_tiling_enabled: bool = True` (existing)
  - env: `MULTIPROFILE_WINDOW_TILE_GAP`, `_MARGIN`, `_TOLERANCE_PX`, `_MAX_RETRIES`, `_SETTLE_MS`

- [ ] **Step 1: Add fields + env reads** (keep existing exclusivity defaults unchanged)

- [ ] **Step 2: Smoke** `PYTHONPATH=src python -c "from multiprofile.config import get_settings; s=get_settings(); assert s.window_tile_tolerance_px==4"`

- [ ] **Step 3: Commit** `feat(config): tiling tolerance/gap/retry settings`

---

### Task 3: WorkAreaProvider (fail-loud)

**Files:**
- Create: `src/multiprofile/browser/work_area.py`
- Test: `tests/test_work_area.py`
- Modify: `src/multiprofile/browser/window_layout.py` to re-export `WorkArea` from here for compat OR thin-wrap `detect_work_area` → provider

**Interfaces:**
- Produces:
  - `@dataclass WorkArea: x:int,y:int,w:int,h:int,source:str`
  - `class WorkAreaError(Exception): code="work_area_unavailable"; reasons:list[str]`
  - `class WorkAreaProvider:`
    - `detect(self) -> WorkArea` raises `WorkAreaError`
    - providers: `_from_net_workarea()`, `_from_xdpyinfo()`, `_from_env_override()` (`MULTIPROFILE_WORKAREA=x,y,w,h`)
  - **Never** return hardcoded 1920×1080

- [ ] **Step 1: Tests** — mock all providers fail → `WorkAreaError`; env override succeeds; never equals silent 1920×1080 default without source=env

- [ ] **Step 2: Implement providers**

- [ ] **Step 3: Tests pass**

- [ ] **Step 4: Commit** `feat(workarea): fail-loud WorkAreaProvider`

---

### Task 4: GridCalculator (sole geometry owner)

**Files:**
- Create: `src/multiprofile/browser/grid.py`
- Test: `tests/test_grid_layout.py`, `tests/test_tile_idempotent.py`

**Interfaces:**
- Produces:
  - `@dataclass Rect: x:int,y:int,w:int,h:int` (or import shared Rect)
  - `@dataclass Slot: profile_id:str, index:int, rect:Rect`
  - `@dataclass SlotPlan: work_area:WorkArea, cols:int, rows:int, gap:int, slots:list[Slot]`
  - `class GridCalculator:`
    - `plan(self, profile_ids: list[str], work_area: WorkArea, *, gap:int|None=None, margin:int|None=None) -> SlotPlan`
  - `validate_no_overlap(rects) -> {valid, overlaps}`
  - `validate_within_work_area(rects, work_area) -> {valid, offscreen}`
  - `validate_equal_sizes(rects) -> bool`
- Priority order hard-coded as in spec §2 mods
- Empty cells emit **no** rect
- Same inputs → identical SlotPlan (idempotent)

- [ ] **Step 1: Write geometry tests** for N in `{1,2,3,4,5,6,8,9,10,12,15,16,20}` × work areas `{1366x768,1920x1080,2560x1440,3440x1440,(0,32,1920,1048)}` asserting equal size, no overlap, inside area, no phantoms, gap

- [ ] **Step 2: Write idempotence test** — call `plan` thrice; assert rects identical

- [ ] **Step 3: Implement GridCalculator**

- [ ] **Step 4: All grid tests pass**

- [ ] **Step 5: Commit** `feat(grid): deterministic equal-card SlotPlan calculator`

---

### Task 5: CdpWindowController

**Files:**
- Create: `src/multiprofile/browser/cdp_window.py`
- Optional gated test later

**Interfaces:**
- Produces:
  - `allocate_localhost_port() -> int` (bind `127.0.0.1:0`)
  - `wait_cdp_ready(port, timeout_s=8) -> dict` (`/json/version`)
  - `class CdpWindowController:`
    - `connect(port:int)`
    - `pick_page_target(start_url:str|None) -> target_id` (deterministic + logs rejects)
    - `get_window_id(target_id) -> int` via `Browser.getWindowForTarget`
    - `set_outer_bounds(window_id, rect:Rect) -> None` via `Browser.setWindowBounds` with `windowState=normal`
    - `get_outer_bounds(window_id) -> Rect`
    - `apply_and_verify(window_id, desired:Rect, *, tolerance, retries, settle_ms) -> {ok, actual, attempts, error}`
- Uses stdlib `urllib` + `websocket-client` **or** existing deps; if adding dependency, prefer stdlib HTTP + `websocket-client` only if already present — check `pyproject.toml`. If no websocket lib, implement minimal WebSocket handshake for CDP (or add `websocket-client` as dependency in this task with pin). Prefer adding `websocket-client` to `pyproject.toml` if needed.
- Reject connections to non-127.0.0.1 URLs

- [ ] **Step 1: Check deps**; add `websocket-client` only if required

- [ ] **Step 2: Implement controller** with structured logging of target choice

- [ ] **Step 3: Manual smoke on IdeaPad** (headed optional): launch chrome with `--remote-debugging-address=127.0.0.1 --remote-debugging-port=PORT`, set bounds, get bounds — document result in commit message. If fails → mark blocker `cdp_window_bounds_unsupported` and STOP per spec

- [ ] **Step 4: Commit** `feat(cdp): localhost window bounds controller`

---

### Task 6: WindowLayoutManager (orchestrator)

**Files:**
- Create: `src/multiprofile/browser/layout_manager.py`

**Interfaces:**
- Consumes: WorkAreaProvider, GridCalculator, CdpWindowController, runtime_state
- Produces:
  - `class WindowLayoutManager:`
    - `build_plan(profile_ids) -> SlotPlan` (raises WorkAreaError)
    - `tile_running(profile_ids: list[str]|None=None) -> dict`  # API shape from spec
    - `apply_plan(plan: SlotPlan) -> dict`  # uses runtime cdp ports; updates launch states
    - `best_effort_tile_after_launch(profile_ids) -> dict`  # catches WorkAreaError → ok:false
- **Idempotent:** `tile_running` always recomputes plan from current work area + current profile id order (sorted stable by input order); never offsets from previous actuals
- On CDP/window/position/verify failure: set failure launchState; **do not kill browser**

- [ ] **Step 1: Implement manager**

- [ ] **Step 2: Unit-test apply_plan with mocked CdpWindowController** (fake set/get returns desired)

- [ ] **Step 3: Commit** `feat(layout): WindowLayoutManager orchestrator`

---

### Task 7: Wire launcher (no layout math)

**Files:**
- Modify: `src/multiprofile/browser/launcher.py`
- Modify: `src/multiprofile/browser/window_layout.py` (deprecate place_window_by_pid for tiling path; keep function but unused by launcher)

**Changes:**
- On `launch_profile`:
  1. `set_launch_state(CREATED/LAUNCHING)`
  2. Always allocate localhost CDP port (not only when `cdp=True`); store in runtime; still honor explicit `cdp` for exposing port in API response
  3. Flags: `--remote-debugging-address=127.0.0.1 --remote-debugging-port=P`
  4. Remove calls to `place_window_by_pid` / grid math
  5. After process start: wait CDP ready → `CDP_READY` or `CDP_FAILED`
  6. Do **not** compute rects here; accept optional `window_rect` only if passed from manager (manager may call apply separately). Preferred: launcher returns `{ok,pid,cdpPort}`; manager applies bounds afterward
  7. On success path without tile: state `RUNNING` after CDP_READY (or leave at CDP_READY for manager to continue)
- `stop_profile`: `clear_runtime(profile_id)` after kill

- [ ] **Step 1: Refactor launch_profile per above**

- [ ] **Step 2: Ensure exclusivity flags unchanged**

- [ ] **Step 3: Commit** `refactor(launcher): localhost CDP registry; remove x11 tile path`

---

### Task 8: Wire jobs (orchestration only)

**Files:**
- Modify: `src/multiprofile/jobs.py`

**Changes:**
- Remove imports of `compute_layout` / local rect math
- For `launch_profile` / `bulk_launch`:
  - Optional job field `tileRequired: bool` (default false)
  - If `tileRequired`: `build_plan` first; on WorkAreaError fail job before launches
  - Launch all profileIds via `launch_profile` without rects
  - Then `WindowLayoutManager.apply_plan` or `best_effort_tile_after_launch`
  - Attach `tile` object to job results
- Preserve concurrency slot acquire/release behavior

- [ ] **Step 1: Implement job branching**

- [ ] **Step 2: Commit** `refactor(jobs): delegate tiling to WindowLayoutManager`

---

### Task 9: API + UI

**Files:**
- Modify: `src/multiprofile/api/server.py`
- Modify: `src/multiprofile/storage/profiles.py` (merge `launchState` into list/get)
- Modify: `src/static/index.html`, `src/static/app.js`

**API:**
- `POST /api/windows/tile` body optional `{profileIds?: string[]}` → `WindowLayoutManager.tile_running` (fail-closed)
- `GET /api/windows/layout` → work area + plan snapshot + per-profile runtime tile fields
- Launch endpoints include `tile` best-effort summary when tiling enabled
- Jobs create accepts `tileRequired`

**UI:**
- Button **Tile Windows** → `POST /api/windows/tile`
- Show `launchState` on row if easy (optional minimal)

- [ ] **Step 1: Add routes**

- [ ] **Step 2: Add button + handler**

- [ ] **Step 3: Commit** `feat(api/ui): tile windows endpoint and button`

---

### Task 10: Test harness + package verification

**Files:**
- Modify: `scripts/test.sh`
- Run grid/work_area/runtime tests in CI script
- Manual checklist in CHANGELOG

- [ ] **Step 1: Extend `scripts/test.sh`** to run `python -m pytest tests/test_grid_layout.py tests/test_work_area.py tests/test_runtime_state.py tests/test_tile_idempotent.py -q`

- [ ] **Step 2: Run full `bash scripts/test.sh`**

- [ ] **Step 3: On IdeaPad** — install/run, Launch Selected 4/9/20, Tile twice (idempotent), close 2 + Tile

- [ ] **Step 4: Bump version to 1.4.0**, changelog, build deb, commit+tag when manual pass

- [ ] **Step 5: Commit** `test/release: tiling 1.4.0`

---

## Dependencies

- Chrome/Chromium with CDP Browser domain (verified 151 on IdeaPad)
- Optional new pip dep: `websocket-client` (only if required)
- System: `xprop`/`xdpyinfo` recommended for WorkAreaProvider (env override as escape hatch)

## Migration / compatibility

- Old `window_layout.place_window_by_pid` remains but unused by launch path (no breakage if external imports)
- Profiles JSON on disk unchanged; launchState is runtime-only
- Existing jobs without `tileRequired` keep best-effort behavior
- API additive only

## Rollback strategy

1. Revert commits for tasks 7–10 to restore prior launcher tile hints
2. Or feature-flag: `MULTIPROFILE_WINDOW_TILING=0` skips manager apply (already in settings)
3. Deb downgrade to 1.3.0 package

## Build / test commands

```bash
cd /path/to/multiprofile-native
PYTHONPATH=src python -m pytest tests/test_grid_layout.py tests/test_work_area.py tests/test_runtime_state.py tests/test_tile_idempotent.py -v
bash scripts/test.sh
bash build-deb.sh   # after version bump
```

## Acceptance criteria

- [ ] Geometry tests green for listed N × resolutions
- [ ] WorkArea all-fail never returns silent 1920×1080
- [ ] Launch Selected with bad work area still launches browsers; `tile.ok=false`
- [ ] Tile-required / POST tile fail closed without work area
- [ ] CDP bound only on 127.0.0.1
- [ ] Exclusivity still default ON
- [ ] Repeat Tile → same grid (no cascade drift)
- [ ] Partial CDP failure leaves browser alive; launchState reflects failure
- [ ] Manual 4/9/20 look like equal cards; Tile after close reflows
- [ ] No fingerprint/proxy code changes in diff

## Spec coverage self-check

| Spec item | Task |
|-----------|------|
| Localhost CDP + setWindowBounds | 5–7 |
| WorkAreaProvider fail-loud | 3 |
| Grid priorities + no phantoms | 4 |
| Deterministic target mapping | 5 |
| Launch best-effort vs required | 8–9 |
| Verify ≤4px / 3 retries | 5–6 |
| Tile API + GET layout + button | 9 |
| State machine | 1,7 |
| No auto-kill on tile fail | 6–7 |
| Idempotent tile | 4,6,9 |
| Tests 1–20 + resolutions | 4,10 |
| No xdotool fallback | 7 (removed) |
| Fingerprint untouched | Global + diff review in 10 |

