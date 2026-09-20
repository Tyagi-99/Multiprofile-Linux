# Architecture (1.2.0)

```
Desktop/Browser UI (static SPA)
        |  HTTP (+ optional Bearer token from localStorage mp_api_token)
        v
Flask API (threaded)
  ├─ profiles / clients / templates JSON (XDG data)
  ├─ jobs queue (in-process) → concurrency gate (MAX_CONCURRENT_PROFILES, default 3)
  ├─ orphan reaper thread (15s) + POST /api/maintenance/reap
  ├─ backup/restore (stdlib zip + sha256; no encryption in MVP)
  ├─ audit.jsonl (append-only)
  └─ Chromium launcher + per-profile extension copy (+ optional CDP port)
```

No Wine. No WebView2. Linux UI via pywebview GTK or system browser fallback.
Cloud proxy / updater remain stubs.
