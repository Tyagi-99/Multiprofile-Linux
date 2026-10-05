# 01 — Project Snapshot (Phase 0)

**Commit:** `f4b342d` (main, in sync with `origin/main`) + uncommitted supervisor fix from this session
**Date:** 27 September 2026
**Method:** read the files, build it, run it, launch real browser profiles, measure.
Everything below was executed on this machine unless marked **UNVERIFIED**.

> **The brief says the primary dev machine is Ubuntu. It is not.** This machine is
> **Arch Linux, kernel 7.1.9-arch1-2, Hyprland/Wayland**. That single fact changes
> two Phase 0 answers: the `.deb` could not be built here (no `dpkg-deb`), and
> the installed desktop app is **not** the `.deb` — it is an editable dev
> install. See §5 and §6.
>
> **Updated 5 October 2026 (F12).** The first half no longer holds.
> `scripts/build_deb.py` writes the three-member `ar` archive from the standard
> library, so `build-deb.sh` runs on this machine, and CI compares its output
> against `dpkg-deb` on every push. The second half still holds and matters
> more: the app you run is the working tree, so `git pull` is the update path
> and nothing about the `.deb` channel affects it.

---

## 1. The single most important architectural fact

**There is no browser engineering in this product.**

It runs **stock, system-installed Chromium** and manipulates it from outside over
the DevTools Protocol. There is no Chromium fork, no patches, no C++, no bundled
binary, no version pinning, and no engine update mechanism.

| | |
|---|---|
| Engine | **Stock Chromium 151.0.7922.173 (Arch Linux build)** — whatever `PATH` offers |
| Discovery | [`launcher.py:249`](../src/multiprofile/browser/launcher.py#L249) — tries `google-chrome-stable`, `google-chrome`, `chromium`, `chromium-browser`, first hit wins |
| Control | CDP over WebSocket, `127.0.0.1:<random port>` |
| Spoofing | **JavaScript injected into the page**, plus CDP `Network.setUserAgentOverride` / `Emulation.*` |
| Not present | Chromium fork · engine patches · per-profile engine version · bundled engine · engine auto-update |

This places Multiprofile in a **different and weaker architectural class** than
Multilogin (patched Mimic/Stealthfox) or AdsPower (patched SunBrowser). Everything
spoofed here is spoofed *above* the engine, in a place the page can inspect, and §4
of the audit shows that this is currently detectable in one line of JavaScript.

One injection route, since audit F2 was fixed:

1. **CDP** — `Page.addScriptToEvaluateOnNewDocument` + `Target.setAutoAttach`
   ([`cdp_inject.py`](../src/multiprofile/browser/cdp_inject.py)). Works on both engines and
   runs before any page script.

Two routes used to exist simultaneously: the launcher also staged an MV3 extension to disk
and passed `--load-extension`/`--disable-extensions-except`. Google Chrome ignores those
flags; **Chromium honours them**, so on Chromium `evasion.js` ran twice per document
(measured: two `[Evasion] evasion.js active on: <url>` lines for one document, one after the
fix). The flags and the staging step are gone. `src/extension/` remains as the *source* of
`evasion.js`, read by the injector — it is not loaded into any browser.

---

## 2. Tech stack (verified)

| Layer | What it is | Evidence |
|---|---|---|
| Backend | Python ≥3.11, Flask 3.1.3, single process, `127.0.0.1:8765` | `pyproject.toml`, `app.py` |
| Desktop shell | pywebview 6.2.1 (GTK/WebKit). No JS↔Python bridge — the UI talks HTTP | `app.py`, `pip list` |
| Frontend | **Next.js 16.3.6** static export, React 19.1.1, Tailwind 4.1.12, TypeScript 5.9.2 | `frontend/package.json` |
| Data plane | Stock Chromium child processes, one `--user-data-dir` each | `launcher.py` |
| Control plane | SQLite (WAL) `control.db` — runtime state + durable job queue | `storage/db.py` |
| Profile store | Single `profiles.json` array | `storage/profiles.py` |
| Secrets | `keyring` 25.7.0 → OS keychain; AES via `cryptography` 50.0.1 | `secrets.py`, `/api/health` |
| Test runtime | CPython **3.14.4** (this venv) | `.venv/bin/python` |
| Shipped runtime | Whatever `python3` the distro ships | `packaging/debian/control` |

**Process model:** one Flask process + N Chromium processes. No IPC bridge —
**the local HTTP API is the entire attack surface.**

---

## 3. Repository map

```
src/multiprofile/      17,631 LoC Python, 89 modules — the application
  api/                 Flask routes (server.py is 1,320 lines), auth, validation, SSE
  browser/             launch, CDP inject/identity, supervisor, grid/tiling, sandbox
  devices/             33 device presets + a validator that scores them
  fingerprint/         per-vector config generators (canvas, webgl, fonts, …)
  gateway/             WireGuard / Azure / netns egress providers (netns is OFF)
  jobs/                durable queue, workers, handlers
  storage/             profiles.json, SQLite control DB, pools, templates, XDG paths
  consistency.py       cross-vector coherence engine (IP↔TZ↔lang↔UA↔device)
src/extension/          1,307 LoC JS — MV3 manifest + evasion.js (1,150 lines)
src/static/            built frontend, served by Flask
frontend/              10,704 LoC TS/TSX — Next.js console (source)
tests/                  8,843 LoC, 37 files, 558 test functions
packaging/debian/      control, postinst, changelog, .desktop, icons
docs/                  14 documents (this one included)
build-deb.sh           hand-rolled .deb builder
```

---

## 4. How profiles are stored

Root: `~/.local/share/multiprofile/` (XDG, `storage/xdg.py`)

| Path | Contents | Permissions | Encrypted |
|---|---|---|---|
| `profiles.json` | every profile: name, tags, fingerprint, proxy ref | `0600` ✅ | ❌ plaintext (documented choice) |
| `control.db` | runtime state, job queue (SQLite WAL) | `0600` ✅ | ❌ |
| `audit.jsonl` | audit trail | `0600` ✅ | ❌ |
| `chrome-profiles/<id>/user-data/` | the real Chrome profile — cookies, localStorage, history | Chrome's own `0700`/`0600` | Chrome's own |
| `chrome-profiles/<id>/extension/` | staged extension + **`cookies.json`** + **`proxy.json`** | **`0644` world-readable** ⚠️ | ❌ **cleartext proxy password** |
| `cookies/` | canonical cookie store | `0600` ✅ | ❌ |

**Encryption at rest is scoped to proxy passwords only**, held under a key in the
OS keychain (`/api/health` reports `backend: keyring`). Everything else is
deliberately readable so the store stays diffable.

The `0644` row is audit finding **F4** — that one writer bypasses both the
permission hardening and the keychain encryption applied everywhere else.

---

## 5. Packaging and distribution

| | |
|---|---|
| Format | `.deb` only, from a hand-rolled `build-deb.sh` (no `debhelper`, no `dpkg-buildpackage`) |
| Targets | Linux/Debian only. **No Windows, no macOS, no `.rpm`, no AppImage, no Flatpak** |
| Signing | **None** |
| Auto-update | **None** |
| Telemetry / crash reporting | **None** in the app (but see F14: Next.js build-time telemetry is on) |

**`build-deb.sh` cannot run on this machine.** Verified: exit **127**,
`dpkg-deb: command not found`. It fails loudly and leaves the tree clean — correct
behaviour, but it means the packaging path is **never exercised on the primary dev
machine**. Only CI (`ubuntu-latest`) can build it.

---

## 6. How the app is actually installed here

Not the `.deb` — a **development install**:

```
~/.local/share/applications/multiprofile.desktop   → Exec=~/.local/bin/multiprofile
~/.local/bin/multiprofile                          → exec .venv/bin/multiprofile
.venv/bin/multiprofile                             → multiprofile.__main__:main
.venv/.../__editable__.multiprofile_native-1.9.0.pth → points at this working tree
```

Consequence: **source edits take effect on the next launch, with no reinstall.**
Launched via `gtk-launch multiprofile.desktop` under a systemd user scope.

---

## 7. Build & run commands that actually work

```bash
# dependencies (this venv already has them)
.venv/bin/pip install -e ".[all]"
.venv/bin/pip install pytest pytest-cov ruff        # dev group, was NOT installed

# tests — 1123 passed, 2 skipped, ~58s
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q

# lint — clean on the enforced rule set
.venv/bin/python -m ruff check src/ tests/

# frontend — 0 vulns, typecheck clean, lint clean, builds
cd frontend && npm ci && npm run typecheck && npm run lint && npm run build

# run headless / with window
.venv/bin/python -m multiprofile --server-only --port 8765
.venv/bin/python -m multiprofile

# package — FAILS on Arch (needs dpkg-deb)
bash build-deb.sh
```

---

## 8. Dependency health

| Check | Result |
|---|---|
| `pip-audit` (full venv) | **No known vulnerabilities** ✅ |
| `npm audit` (154 pkgs) | **0 vulnerabilities** ✅ |
| `npm run typecheck` | clean ✅ |
| `npm run lint` | clean ✅ |
| `ruff check src/ tests/` | clean on enforced set ✅ |
| Runtime deps | 2 only: `flask`, `websocket-client` — unusually lean ✅ |
| Abandoned packages | none found |

The H3 dependency finding from the previous audit is genuinely closed.

---

## 9. Size, tests, coverage

| Metric | Value |
|---|---|
| Python (src) | 17,631 LoC, 89 modules |
| Tests | 8,843 LoC, 37 files, **558 test functions**, 24 classes |
| Frontend | 10,704 LoC TS/TSX |
| Extension JS | 1,307 LoC (`evasion.js` = 1,150) |
| **Test result** | **1123 passed, 2 skipped** (~58s) |
| **Coverage** | **72%** (8,660 statements, 2,406 uncovered) |
| `TODO`/`FIXME`/`HACK` | **0** — genuinely clean |
| Files > 500 lines | 9 (`api/server.py` 1,320 · `launcher.py` 974 · `network.py` 914) |

**Weakest coverage:** `proxy_util.py` 26% · `window_layout.py` 28% ·
`health.py` 30% · `cdp_window.py` 31% · `app.py` 37% · `jobs/legacy.py` 44% ·
`__main__.py` 0% · `logging_setup.py` 0%.

**Lint caveat:** `pyproject.toml` deliberately deselects the failure-swallowing
rules. With them on: **103 blind-except + 35 try-except-pass = 138 findings**.
This is documented, not hidden — but see audit F11.

---

## 10. Measured performance (this machine)

| Metric | Measured | Brief's budget | |
|---|---|---|---|
| API cold start | **138 ms** (137/140/139) | < 1.5 s | ✅ |
| Profile launch (API round trip) | **1.22 s** | < 2 s | ✅ |
| RAM per idle profile (PSS) | **~275 MB** (11–12 processes) | — | ⚠️ 20 profiles ≈ 5.5 GB |
| RAM per idle profile (RSS) | ~1.25 GB (inflated by shared pages) | — | |
| Disk per profile | ~2.7 KB record + Chrome user-data dir | — | |
| Profile list, first page of 25 | 40 ms @1k · 207 ms @2.5k · **795 ms @5k** · **3,115 ms @10k** | < 100 ms | ❌ |

The last row is the headline performance defect (audit **F6**): latency grows
**superlinearly** — 10× the data costs 78× the time — because the whole store is
read, parsed and normalised on every request. Pagination does not help; the first
page of 25 costs the same as all 10,000.

---

## 11. What Phase 0 could not verify

| Item | Status | Why |
|---|---|---|
| `.deb` install/upgrade/removal | **UNVERIFIED** | no `dpkg` on Arch; needs a Debian/Ubuntu box or CI artifact |
| Windows / macOS | **N/A** | no code exists; tiling is X11-bound |
| Public fingerprint checkers (CreepJS, Pixelscan, IPHey, BrowserScan, Whoer) | **NOT RUN** | replaced with direct wire-header + JS-realm probes, which are more reproducible and are what §4 of the audit reports. These sites still need a run for `docs/04`. |
| Proxy-mode behaviour end to end | **UNVERIFIED** | no proxy credentials available in this environment |
| UI at 10k profiles, 60 fps | partially | backend measured; frame-rate not instrumented |

---

## 12. Snapshot verdict

A **well-engineered orchestration layer around an unmodified browser.** The
control plane (state machine, durable jobs, supervisor, fail-closed proxy policy,
consistency engine) is careful, tested and honestly documented. The security
regressions from the previous audit are genuinely closed — I re-tested the CSRF
and DNS-rebinding guards live and all four attack shapes are blocked.

The gap is not the plumbing. It is that **the product's core claim — that a
profile presents a coherent, non-host identity — does not hold on the default
path**, and the architecture (JS-level spoofing on stock Chromium) sets a low
ceiling on how well it ever can. That is the subject of `02_AUDIT_REPORT.md`.
