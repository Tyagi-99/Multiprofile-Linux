# Scaling Guidance

Version: 1.7.0

How far a single Multiprofile Linux host goes, what to tune at 100 and 500
profiles, and when to stop scaling one box and add gateway hosts instead.

All numbers below are the real defaults from `src/multiprofile/config.py`;
nothing here is aspirational.

## The honest headline

A single host comfortably runs **tens** of concurrent Chromium profiles.
The binding constraints are RAM (~300–600 MB per Chromium renderer set in
practice — measure your own pages), CPU for tiling/CDP, and X11. The
control plane (SQLite, job queue, supervisor) is not the bottleneck until
far beyond that; Chromium itself is.

## Defaults that matter

| Knob | Env var | Default | Meaning |
|---|---|---|---|
| `max_concurrent_profiles` | `MAX_CONCURRENT_PROFILES` | 20 | hard cap on running profiles |
| `jobs_max_workers` | `MULTIPROFILE_JOBS_MAX_WORKERS` | 4 | job worker threads |
| `jobs_max_concurrent_launches` | `MULTIPROFILE_JOBS_MAX_LAUNCHES` | 2 | simultaneous Chromium spawns |
| `parking_enabled` | `MULTIPROFILE_PARKING_ENABLED` | true | park idle profiles |
| `parking_idle_timeout_s` | `MULTIPROFILE_PARKING_IDLE_TIMEOUT_S` | 1800 | idle seconds before parkable |
| `health_stagger` | `MULTIPROFILE_HEALTH_STAGGER` | true | jitter health checks |
| `health_max_concurrent` | `MULTIPROFILE_HEALTH_MAX_CONCURRENT` | 4 | health checks in flight per wave |
| `health_jitter_s` | `MULTIPROFILE_HEALTH_JITTER_S` | 30 | max random delay per profile |
| `metrics_cache_ttl_s` | `MULTIPROFILE_METRICS_CACHE_TTL_S` | 5 | `/proc` sample cache (never > 5s) |
| `supervisor_interval_s` | `MULTIPROFILE_SUPERVISOR_INTERVAL_S` | 10 | watchdog pass interval |
| `supervisor_max_restart_attempts` | `MULTIPROFILE_SUPERVISOR_MAX_RESTARTS` | 3 | bounded restarts per incident |

## At ~100 profiles (dozens running)

- **Leave the cap alone** unless you've measured RAM: 20 concurrent is the
  sane default. Raising it is a RAM decision, not a config decision.
- **Idle parking is your friend.** With the default 30-minute idle timeout,
  profiles nobody is using get gracefully stopped (data kept, state
  `parked`) and stop consuming RAM. Lower the timeout (e.g. 600s) if your
  workload is bursty.
- **Health checks stay staggered.** 100 profiles × jitter 30s ÷ 4
  concurrent = the fleet is sampled in waves, not all at once. Don't turn
  `health_stagger` off at this size.
- **Cursor pagination does the heavy lifting in the UI.** List endpoints
  return pages (`{"items", "next_cursor", "limit", "total"}`); the console
  never materializes 100 profiles at once. `total` is the filtered count —
  free, because the endpoints already hold the list in memory.
- **SQLite WAL is fine here.** The control DB sees a few writes per
  second (heartbeats, job transitions, audit). WAL mode handles the app's
  own threads comfortably; `busy_timeout=5000` absorbs transient
  contention.

## At ~500 profiles (fleet territory)

- **This is no longer one box's job for *running* browsers.** 500 stored
  profiles are fine; 500 concurrent Chromiums are not — that's a RAM and
  X11 story no config knob fixes. Keep `max_concurrent_profiles` where
  your RAM measurements say, and let parking keep the hot set small.
- **Raise `health_jitter_s`** (e.g. 120–300s) so 500 health checks spread
  over minutes, and consider raising `health_max_concurrent` only if the
  sampler host has CPU headroom — each check is a `/proc` read plus a PID
  probe, cheap but not free.
- **Job queue depth**: `jobs_max_workers=4` with 2 concurrent launches
  means a bulk launch of 100 profiles drains at ~2 parallel spawns.
  Chromium spawn is the cost (seconds each); raising launch concurrency
  past 2–4 mostly contends on disk/CPU during startup. Measure before
  raising.
- **Supervisor interval**: at 500 profiles a 10s pass does 500 PID checks
  (each a `kill(pid, 0)` + occasional `/proc` read) — trivial. Leave it.
- **SQLite WAL limits**: still fine for control-plane metadata at this
  scale *on one host*. It is not a multi-host store and not a time-series
  DB — don't point two API servers at one `control.db`, and don't use it
  for high-frequency metrics history (the 5s `/proc` cache is point-in-
  time, not history).
- **Audit JSONL**: append-only and unbounded per file. At 500 profiles
  with chatty jobs, rotate or ship the log — the app won't do it for you.

## When to add gateway hosts

Add a second host (or more) when any of these is true:

1. **RAM is the wall.** You need more concurrent browsers than one box's
   RAM allows. Split profiles across hosts; each host runs its own
   control plane (there is no shared scheduler — placement is yours).
2. **Egress diversity is the goal.** Per-profile `custom` proxies already
   give per-profile egress on one box; gateway hosts (Azure/WireGuard
   scaffolds are **NOT ACTIVE** — see `docs/REMOTE_GATEWAYS.md`) would be
   the answer once those providers are real. Today, the production path
   is manual proxy routing per profile.
3. **Blast radius.** One host's Chromium fleet is one kernel's fate. If an
   operator error or OOM-killer event must not take all 500 profiles,
   shard them.

What you do *not* need a second host for: the API, the control DB, the
job queue, or the supervisor — none of them are the bottleneck at these
scales. Scale the browsers; the control plane follows.
