# Browser Supervisor

Version: 1.7.0  
Module: `src/multiprofile/browser/supervisor.py`

The supervisor is a crash watchdog: a single daemon thread (`mp-supervisor`)
that keeps the *observed* browser processes in agreement with the *recorded*
profile state. It never assumes a browser is alive — every pass re-checks.

## The crash story, end to end

1. **Detection.** Every `interval_s` (default 10s, config
   `supervisor_interval_s` / `MULTIPROFILE_SUPERVISOR_INTERVAL_S`) the
   supervisor lists all profiles recorded as `running` and checks each PID
   with `pid_alive()`:
   - `os.kill(pid, 0)` — `ProcessLookupError` means dead;
     `PermissionError` means alive (a process we can't signal still exists).
   - A zombie still answers `kill(pid, 0)` but is already dead, so
     `/proc/<pid>/stat` is read and state `Z` counts as dead.
   - If `/proc` is unreadable, the check **fails closed toward alive**
     (never declares a browser dead on inconclusive evidence).
   - Live PIDs get a heartbeat write (best effort; a failed write is only
     logged, never fatal).

2. **Marking.** On a dead PID the supervisor writes an audit entry
   (`supervisor_browser_died`, with pid + cdp_port) and moves the profile
   to state `error` with the note `browser process died`. The crash is now
   visible in the API, the Jobs/Audit views, and the audit log — nothing
   about it is silent. A `profile.status` event is published on the event
   bus.

3. **Bounded restart.** Still inside the same pass, the supervisor retries
   the launch through the real launcher (`launch_profile`), at most
   `max_restart_attempts` times (default 3, config
   `supervisor_max_restart_attempts` /
   `MULTIPROFILE_SUPERVISOR_MAX_RESTARTS`). Between attempts it backs off
   exponentially (`backoff_base_s * 2^(attempt-2)`; attempt 2 waits ~2s,
   attempt 3 waits ~4s). Each attempt sets state `starting` first; a
   launcher exception is caught, logged, and counted as a failed attempt —
   **the launcher can never kill the watchdog loop**. If the supervisor
   itself is being stopped, the backoff wait aborts early.

4. **Success path.** When the launcher reports `ok`, the launcher records
   state `running` (with the new PID), and the supervisor re-tiles the
   **entire grid** via CDP (`tile_running()` — the same tiling authority
   the manual launch path uses, so geometry can't drift). The re-tile is
   audited (`supervisor_retiled`); if the re-tile itself throws, the browser
   is left alone — a tile failure never kills a healthy browser. Net
   result: `running` + new PID + full grid re-tiled.

5. **Exhaustion path.** If all attempts fail, the profile is set to `error`
   with the note `restart retries exhausted: <last_error>` and an audit
   entry (`supervisor_restart_exhausted`, with attempt count and last
   error). It stays there until an operator explicitly restarts or
   recovers the profile. **The supervisor never spins forever**: the retry
   budget is fixed per incident, and a profile already in `error` is not
   re-scanned as `running`.

## Guarantees and non-goals

- **Bounded work per pass**: at most N profiles × 3 launch attempts, with
  backoff. No unbounded loops anywhere in the module.
- **Loop survival**: every per-profile operation is wrapped; an exception
  in detection, restart, re-tile, state write, heartbeat, or audit is
  caught and logged. One bad profile cannot wedge the watchdog for the
  others.
- **No false deaths**: zombies are detected, inconclusive `/proc` reads
  fail toward alive.
- **Disabled is a no-op**: `supervisor_enabled=false`
  (`MULTIPROFILE_SUPERVISOR_ENABLED`) means `start()` logs and returns
  without spawning the thread. `create_app()` also guards the whole
  startup so a broken supervisor module can never take down the API.
- **Not a health checker**: the supervisor answers only "is the process
  alive?". Responsiveness, CDP hang, or page-level health are the job of
  `health_check` jobs and the metrics sampler, not this thread.
- **Single process**: like the event bus, the supervisor is per-process.
  Two API server processes would each supervise; don't run two.

## Configuration

| Setting | Env var | Default |
|---|---|---|
| `supervisor_enabled` | `MULTIPROFILE_SUPERVISOR_ENABLED` | `true` |
| `supervisor_interval_s` | `MULTIPROFILE_SUPERVISOR_INTERVAL_S` | `10` (clamped 2–3600) |
| `supervisor_max_restart_attempts` | `MULTIPROFILE_SUPERVISOR_MAX_RESTARTS` | `3` (clamped 0–10) |

Set `max_restart_attempts=0` to get detection + audit + `error` marking
with no automatic relaunch.
