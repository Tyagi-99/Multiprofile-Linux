# 02 — Full Audit Report (Phase 1)

**Commit:** `f4b342d` + uncommitted supervisor fix from this session
**Date:** 27 September 2026
**Machine:** Arch Linux 7.1.9-arch1-2, Hyprland/Wayland, Chromium 151.0.7922.173
**Method:** static read + **live execution** — real profiles launched, real browsers
driven over CDP, real HTTP headers captured off the wire by a local echo server.

Status key: ✅ Done · 🟡 Partial · ❌ Missing · ⚠️ Broken/Risky

> **Scope note.** A previous `AUDIT.md` (commit `a79a5ca`) closed a set of
> Critical/High findings. I **re-tested those claims rather than inheriting them**;
> where they hold, they are credited in §7. The findings below are new, current,
> and reproduced on this machine today.

---

## Executive summary

The previous audit's security fixes are real and they hold. What it could not see
— because it was read-only, and because the one live check it ran happened to
exercise the working path — is that **the product's central function is broken on
its default path**.

A profile created without a device preset browses with **the host's real User-Agent
and Client Hints** while its JavaScript claims to be Windows. Measured on the wire:

```
User-Agent:        Mozilla/5.0 (X11; Linux x86_64) … Chrome/151.0.0.0
sec-ch-ua-platform: "Linux"
navigator.platform: "Win32"        ← JS says Windows
```

That is not weak spoofing. It is a **contradiction that no real browser can
produce**, and it marks the profile as automated more reliably than doing nothing
at all. The root cause is one function using a CDP helper whose own docstring
warns it must not be used that way.

Separately, the spoofing that *does* work is **detectable in a single line of
JavaScript**: `Function.prototype.toString.call(HTMLCanvasElement.prototype.toDataURL)`
returns the hook's source code, comments included.

**Verdict: the orchestration layer is close to production quality; the
anti-detection layer is not. Findings F1–F3 are ship-stoppers for the product's
own value proposition. F1 is a days-not-weeks fix; F3 is architectural.**

**Addendum (Gate 2).** The orchestration layer turned out to carry a ship-stopper
of its own, found only by following F8's trail: **F17** — a profile whose route
could not be resolved launched on the **host IP** and reported `ok: True`, with a
note reading "Network mode: DIRECT" and the wire assertion confirming the spoofed
identity. A perfect fingerprint on the operator's own IP is the one failure worse
than no spoofing at all, and it was reachable from the ordinary console: put a
profile in a proxy pool, remove the pool's members. The fail-closed guard existed
and was correct; the launcher never consulted it. Fixed, along with the routing
half of F16 that led to it. The thread that found it started as a validation
nitpick about unknown JSON keys, which is the argument for fixing the small
honest-reporting defects rather than deferring them.

---

## Deliverables

| | Document |
|---|---|
| 01 | [Project snapshot](01_PROJECT_SNAPSHOT.md) |
| 02 | This report |
| 03 | [Feature matrix](03_FEATURE_MATRIX.md) |
| 04 | [Fingerprint test results](04_FINGERPRINT_TEST_RESULTS.md) |
| 05 | [UI specification](05_UI_SPEC.md) |
| 06 | [Roadmap](06_ROADMAP.md) |
| 07 | [Security & compliance](07_SECURITY_AND_COMPLIANCE.md) |
| 08 | [Commercial architecture](08_COMMERCIAL_ARCHITECTURE.md) |

Plus a draft [Acceptable Use Policy](ACCEPTABLE_USE.md), marked as an
engineering draft rather than legal advice.

---

## Findings

| ID | Sev | Area | Summary | Effort |
|---|---|---|---|---|
| **F1** | ✅ **Critical** | Fingerprint | UA/Client-Hints spoofing silently does nothing on the default (no-preset) path; reports success — **fixed, see below** | done |
| **F2** | ✅ **High** | Fingerprint | evasion.js applied **twice** on Chromium (extension + CDP) — **fixed** | done |
| **F3** | ⚠️ **High** | Fingerprint | Hooks are trivially detectable; `Function.prototype.toString` leaks their source | 1–3 wk |
| **F4** | ✅ **High** | Security | Cookie jar + cleartext proxy password written **0644 world-readable** — **fixed** | done |
| **F5** | ✅ **High** | Fingerprint | Every profile claims Chrome **131**; engine is Chromium **151** — **fixed** | done |
| **F6** | ✅ **High** | Performance | Profile store was a full read+parse per request, and **lost concurrent writes** (3 of 20 survived) — **SQLite store** | done |
| **F7** | ✅ Medium | Fingerprint | `evasion.js` announces itself via `console.log` on every page — **fixed** | done |
| **F8** | ✅ Medium | Security | API silently accepts and persists **unknown fields** — **fixed** | done |
| **F9** | ✅ Medium | UX | Listing got **quadratically slower**; the whole store was materialised per page — **SQL paging + one-page fleet** | done |
| **F10** | ✅ Medium | Reliability | Supervisor restart budget never bound crash loops — **fixed this session** | done |
| **F11** | 🟡 Medium | Code quality | 138 failure-swallowing handlers, deselected from lint — **identity path done**, rest is policy | 1–2 wk |
| **F12** | 🟡 Medium | Distribution | Build can't run on the dev machine; no signing, no auto-update | 2–4 wk |
| **F13** | ✅ Medium | Reliability | Window tiling was X11-bound; dead on the Wayland host it is developed on | done |
| **F14** | ✅ Low | Privacy | Next.js build-time telemetry enabled by default | done |
| **F15** | ✅ Low | Build | `rewrites` in `next.config.ts` ignored under `output: export` | done |
| **F16** | ✅ **High** | UX / Correctness | The wizard's **network-mode and fingerprint-strategy steps reached nothing** — found by F8; routing fixed, strategy step removed | done |
| **F17** | ✅ **Critical** | Security | An **unresolvable route launched on the host IP** and reported success — found by F16 | done |
| **F18** | ✅ **Critical** | Security | **Every profile in a proxy pool egressed through the same member** — pools never recorded a decision | done |
| **F19** | ✅ **High** | Security | The **WebRTC control was inert**: the API's vocabulary matched nothing downstream | done |
| **F20** | 🟡 Medium | Reliability | The **durable job queue has no producer** — now at least visible; moving the writer is filed | 1 wk |
| **F21** | ✅ Medium | UX / Correctness | More silent-drop contract bugs: `description`, null-clearing, `egress_ip`/`latency_ms` case — **fixed** | done |
| **F22** | ✅ **High** | Fingerprint | Timezone was **JavaScript-only**; every unhooked surface reported the host zone — **fixed** | done |
| **F23** | ✅ **High** | Fingerprint | `enumerateDevices` **volunteered a persistent id the host withholds**, the same one for all three kinds — **fixed** | done |
| **F24** | ✅ **High** | Fingerprint | **AudioContext and ClientRects linked every profile on the machine** — identical to 12 decimal places — **fixed** | done |
| **F25** | ✅ **High** | Fingerprint | A Windows profile **enumerated this host's Linux fonts** — **per-profile fontconfig view** (residual documented) | done |
| **F26** | ✅ **High** | Fingerprint | **WebGPU contradicted the GPU the profile claims** | done |
| **F27** | ✅ **High** | Fingerprint | The **Battery API reported the host's battery** — the same curve in every profile | done |
| **F28** | ✅ Medium | Fingerprint | **Web Bluetooth missing** on a profile claiming Windows | done |
| **F29** | ✅ Medium | Fingerprint | A Windows profile with **no speech voices** | done |
| **F30** | ✅ **High** | Fingerprint | **Worker realms disagreed with their own page** | done |
| **F31** | ✅ **High** | Fingerprint | **Every profile claimed New York**, from every exit | done |
| **F32** | ✅ Medium | UX | The consistency engine had **no score** | done |
| **F33** | ✅ **High** | Performance | Starting a fleet cost **one launch after another** — 20 profiles ≈ 32 s | done |
| **F34** | ✅ Medium | UX | **No way to fill a fleet**: every create path made one profile | done |
| **F35** | ✅ **High** | Correctness | **Bulk create silently did a third of what was asked** — `count` clamped to 50, reported as success | done |
| **F36** | ✅ **High** | Data loss | **Deleting a profile destroyed its sessions irreversibly** — no trash, no restore | done |
| **F37** | ✅ **High** | Commercial | **Nothing measured what the fleet did** — no runtime, no sessions, nothing for a bill or a cap to rest on | done |
| **F38** | ✅ Medium | Commercial | Limits were **scattered, anonymous and partly fictional** — no way to ask what this install may do | done |
| **F39** | ✅ **High** | Security | **One API token**: unscoped, unrevocable, cleartext — a cron job held the credential that deletes the fleet | done |
| **F40** | ✅ Medium | Compliance | **No way to export or destroy your data** — GDPR/DPDP readiness had nothing behind it | done |
| **F41** | ✅ **Critical** | Fingerprint | **`window.__MP_PROFILE__` handed every page the whole spoofing config, seed included** — found by measuring for `docs/04` | done |
| **F42** | ✅ **High** | Fingerprint | **Every profile claimed Windows whatever you picked** — `userAgentId` reached nothing | done |

---

### F1 — ✅ CRITICAL: UA/Client-Hints spoofing does nothing on the default path — **FIXED**

> **Resolved in Gate 1.** Three causes, not one, and the third was the reason
> it survived so long:
>
> 1. **There was no User-Agent to set.** `build_fingerprint` echoed
>    `profile["userAgent"]`, which is empty on the quick-create path, so the
>    "just set `--user-agent` on every launch" fix had nothing to set. Every
>    pool in `fingerprint/_core.py` now names a default UA whose Chrome major
>    matches the client-hint brands the same pool builds.
> 2. **The override was discarded as it was applied.** `apply_ua_override` now
>    takes a **held** `PinnedCdpSession` and returns `False` without one; the
>    one-shot `_cdp_ws_send` helper is deleted, not merely documented. The
>    injector applies the same override per attached target (new tabs too) and
>    the launcher holds one session open for the browser's lifetime.
> 3. **Nothing compared the claim to the wire.** New
>    `browser/identity_assert.py` makes the browser navigate to a loopback
>    server at launch and compares the headers that arrive with the intended
>    identity. A proven mismatch **fails the launch**; an inconclusive probe is
>    recorded as unverified and never reads as a pass.
>
> Measured after the fix, no preset, no explicit UA (Chromium 141):
>
> ```
> User-Agent:         Mozilla/5.0 (Windows NT 10.0; Win64; x64) … Chrome/131.0.0.0
> sec-ch-ua:          "Google Chrome";v="131", "Chromium";v="131", "Not-A.Brand";v="99"
> sec-ch-ua-platform: "Windows"
> accept-language:    en-US,en;q=0.9
> JS:  platform=Win32  uaData=Windows  brands=131   ← agrees with the wire
> ```
>
> Found by the new assertion once the override started applying: `acceptLanguage`
> was passed as a finished header, so Chromium emitted
> `en-US,en;q=0.9;q=0.9` — a malformed header no real browser sends, and a tell
> in its own right. Fixed in the same change.
>
> **Residual (deferred at the time) — ✅ now closed in Gate 2.** A tab's *first*
> document request is issued by the browser process before the per-target
> override lands, so the hints on that one request reflected the host. Measured
> on Chromium 151, a tab opened the way an operator opens one:
>
> ```
> intended : sec-ch-ua          "Google Chrome";v="151", "Chromium";v="151", "Not-A.Brand";v="99"
>            sec-ch-ua-platform "Windows"
> actual   : sec-ch-ua          "Chromium";v="151", "Not=A?Brand";v="99"
>            sec-ch-ua-platform "Linux"
> ```
>
> **Two headers leaked, not the one recorded here.** The User-Agent was already
> right — it comes from the process flag — so what went out was a Windows UA
> beside the host's own Linux brand list, in Chromium's GREASE spelling
> (`Not=A?Brand`). That pairing is the same contradiction F1 is about, surviving
> on one request per tab.
>
> The fix arms `Fetch` on the **browser session** rather than per target. That
> distinction is the whole thing: no per-target interception can ever see this
> request, however early it is armed, because at that moment the target does not
> exist yet. Narrowed to `resourceType: Document`, so it is one pause per
> navigation rather than one per subresource. The replacement headers come from
> `low_entropy_client_hints()`, derived from the same `ua_params` the override
> uses, so the request and the page cannot drift apart. Proxy auth keeps its own
> unrestricted per-target `Fetch` — a 407 can arrive on any request, and that
> path was already proven, so it was left untouched.
>
> Release is unconditional: the rewrite is computed inside a guard and any
> failure falls back to continuing the request unmodified, because a hung tab
> would be a far worse bug than the leak. A failure there records an identity
> fault (F11) rather than vanishing.
>
> After the fix, the same measurement:
>
> ```
> sec-ch-ua          "Google Chrome";v="151", "Chromium";v="151", "Not-A.Brand";v="99"
> sec-ch-ua-platform "Windows"
> ```
>
> **Two things this fix got wrong before it was right, both worth recording.**
>
> First, it nearly disarmed the Gate 1 assertion. The launch-time identity probe
> is a document request to loopback, so the rewrite caught it too — and the
> assertion then measured headers the injector had just written. It would have
> reported *identity verified* with the UA override completely dead. F1 was a
> reporting bug before it was a spoofing bug, and this is precisely how it comes
> back. The probe path is now shared as `IDENTITY_PROBE_PATH` and exempted, so
> it observes what the browser would really send.
>
> Second, it costs **~0.5s per launch**, and the cost is not the interception.
> Measured, with the pattern deliberately matching nothing:
>
> ```
> Fetch armed, Document pattern   2.46s
> Fetch armed, matches nothing    2.45s
> Fetch never armed               1.98s
> ```
>
> Arming Fetch at browser scope drops a Chromium navigation fast-path whether or
> not anything is paused. That puts profile launch at **~2.45s against the
> brief's < 2s budget** (§4.4) — a real miss, called out rather than buried.
> `MULTIPROFILE_FIRST_REQUEST_HINTS=0` recovers 1.98s at the price of the leak.
> On by default, because an identity that contradicts itself once per tab is the
> defect this product exists to prevent, but that is a trade a founder should
> make knowingly rather than discover.
>
> Verified: `tests/test_first_request_hints_live.py` (5 live tests, including one
> that the tab still loads at all, and one that the Gate 1 launch assertion still
> passes) and `tests/test_first_request_hints.py` (9 unit guards, including that
> the probe is released untouched and that a throwing rewrite still releases the
> request); `-m network` → 24 passed.
>
> Tests: `tests/test_wire_identity.py` (43 unit/contract tests) and
> `tests/test_wire_identity_live.py` (real launch, real headers; fails on the
> pre-fix tree with `host User-Agent leaked on the wire`).

The original finding follows, unchanged.


**Files:** [`cdp_identity.py:190-249`](../src/multiprofile/browser/cdp_identity.py#L190),
[`cdp_identity.py:333-369`](../src/multiprofile/browser/cdp_identity.py#L333),
[`launcher.py:774-781`](../src/multiprofile/browser/launcher.py#L774)

`apply_ua_override()` sends `Network.setUserAgentOverride` through `_cdp_ws_send()`.
That helper's **own docstring** says:

> *"One-shot semantics: connects, sends, reads the response, closes. NOTE: because
> the session closes immediately, session-scoped overrides
> (`Network.setUserAgentOverride`, …) do **NOT** persist — use
> `open_pinned_cdp_session` when the effect must outlive the call."*

And `launcher.py:786`, **ten lines below the call site**, states the same rule again.
The code documents the constraint twice and violates it.

`apply_ua_override()` returns `True` on ACK, so the launcher appends
`"CDP UA/client-hints override applied"` and the operator sees success.

**Proven by measurement.** Called the function directly against a live profile,
then captured the headers it actually sent to a local server:

```
apply_ua_override() returned: True          ← reports success

User-Agent:         Mozilla/5.0 (X11; Linux x86_64) … Chrome/151.0.0.0
sec-ch-ua:          "Chromium";v="151", "Not=A?Brand";v="99"
sec-ch-ua-platform: "Linux"

VERDICT: DISCARDED when the one-shot session closed
```

At launch it frequently does not even ACK — three of four launches logged
`CDP UA/client-hints override FAILED (no CDP response)`, because `_pick_page_ws_url`
finds no content page yet on an `about:blank` launch.

**Why this is worse than no spoofing.** The JS layer *does* apply, so the profile
presents:

| Surface | Value | Source |
|---|---|---|
| `User-Agent` header | Linux, Chrome 151 | real host |
| `sec-ch-ua-platform` | `"Linux"` | real host |
| `navigator.platform` | `Win32` | injected |
| `navigator.userAgentData.platform` | `Windows` | injected |
| `navigator.userAgentData.brands` | Chrome **131** | injected |

Any server that compares its own request headers with JS-reported values catches
this **100% of the time**, with no fingerprinting library required. A plain
Chromium would have looked like an ordinary Linux user.

**The preset path is fine** — and this is what makes the bug easy to miss. With a
valid `deviceId`, the launcher sets `--user-agent` (a persistent process flag) and
holds a pinned CDP session open. Verified consistent:

```
User-Agent:         Mozilla/5.0 (Windows NT 10.0; Win64; x64) … Chrome/131.0.0.0
sec-ch-ua:          "Chromium";v="131", "Google Chrome";v="131", …
sec-ch-ua-platform: "Windows"
JS:  platform=Win32  uaData=Windows  brands=131  screen=1366x768
```

So the defect is precisely: **no device preset → no persistent UA mechanism at all.**
The quick-create "New Profile" flow produces exactly such profiles.

**Fix:** set `--user-agent` on every launch (not just the preset path), and move
`Network.setUserAgentOverride` onto a held `PinnedCdpSession`. Make
`apply_ua_override` return `False` unless it holds an open session. Add a
launch-time assertion comparing wire headers against the intended identity — this
class of bug must fail loudly, never silently report success.

---

### F2 — ✅ HIGH: evasion.js is applied twice on Chromium — **FIXED**

> **Resolved in Gate 1**, by the preferred route: `--load-extension`,
> `--disable-extensions-except` and the whole `_prepare_extension` staging step
> are gone. CDP injection is the only route; it works on both engines and runs
> before page script. Exclusivity now means plain `--disable-extensions`, since
> there is no longer an extension of ours to make an exception for.
>
> **Correction to the measurement below.** `toString(toDataURL).match(/copy =
> null/g).length == 2` is **not** a layer count: that wrapper's source contains
> `let copy = null;` and `catch(e) { copy = null; }`, so one layer counts 2.
> `evasion.js` also already carried an `alreadyShielded` guard that stopped the
> canvas hook stacking. The conclusion still held, and the honest measurement is
> the script's own executions per document, on a real `http://` page (content
> scripts do not run on `data:` or `about:`):
>
> ```
> before:  [Evasion] evasion.js active on: http://127.0.0.1:40679/hooks
>          [Evasion] evasion.js active on: http://127.0.0.1:40679/hooks
> after:   [Evasion] evasion.js active on: http://127.0.0.1:44223/hooks
> ```
>
> and the extension target list: `chrome-extension://…/background.js` before,
> empty after.
>
> Relying on per-hook `alreadyShielded` guards to survive a second execution of
> the whole script is not a design — the WebGL, navigator and screen hooks have
> no such guard. One route is.
>
> Two things the staging step did had to be carried over or they would have been
> lost silently: the `__PROFILE_TIMEZONE__` / `__PROFILE_ALLOCATED_IP__` /
> `__PROFILE_SEED__` token substitutions, and the explicit `webrtcMode`.
> `build_identity_script` now takes a `replacements` mapping and the launcher
> passes all three; `tests/test_evasion_single_hook.py` guards it.
>
> Also fixed as a consequence: `health.extension_loaded` tested for the
> per-profile staging copy, which would have read False on every healthy profile
> once the copy was gone. It now reports the injector's own `inject_ok` — whether
> the script actually reached the browser — which is what the field was always
> meant to mean.
>
> Tests: `tests/test_evasion_single_hook.py` (static route guards + two live
> tests; both live ones fail on the pre-fix tree).

The original finding follows, unchanged.

### F2 (original) — evasion.js is applied twice on Chromium

**Files:** [`launcher.py:654-658`](../src/multiprofile/browser/launcher.py#L654),
[`launcher.py:371-417`](../src/multiprofile/browser/launcher.py#L371)

The CDP injection rewrite was motivated by Google Chrome refusing `--load-extension`.
But the launcher **still stages the extension and still passes the flags**, and
**Chromium honours them**. On this machine both routes run.

Observed in the live target list:

```
background_page   chrome-extension://nkeimhogjdpnpccoofpliimaahmaaome/background.html
```

and measured in the page:

```js
Function.prototype.toString.call(HTMLCanvasElement.prototype.toDataURL)
  .match(/copy = null/g).length   →   2      // wrapped twice
```

**Impact:** canvas/WebGL noise applied twice, so the rendered fingerprint is not
the one the config specifies and is not reproducible across engines; two hook
layers double the F3 artifact surface; and the behaviour silently differs between
Google Chrome and Chromium, which is exactly the kind of environment-dependent
divergence that makes fingerprint bugs unreproducible.

**Fix:** pick one route. Drop `--load-extension`/`--disable-extensions-except` and
the `_prepare_extension` staging (which also removes F4), or drop CDP injection.
CDP is the better choice — it works on both browsers and runs before page script.
Add a test asserting exactly one hook layer.

---

### F3 — ⚠️ HIGH: the spoofing is detectable in one line

**File:** [`evasion.js`](../src/extension/evasion.js) (`makeNative`, line ~16)

`makeNative()` assigns an **own** `toString` to each hooked function. That defeats
`fn.toString()` but not `Function.prototype.toString.call(fn)`, which is the check
every serious detector actually uses. Measured live:

| Probe | Result |
|---|---|
| `Function.prototype.toString.call(HTMLCanvasElement.prototype.toDataURL)` | `function toDataURL() { let copy = null; try { copy = n…` |
| `Function.prototype.toString.call(WebGLRenderingContext.prototype.getParameter)` | `function getParameter(param) { // UNMASKED_VENDOR_WEBGL if…` |
| `Object.prototype.hasOwnProperty.call(toDataURL,'toString')` | `true` — native functions never have this |
| `Object.getOwnPropertyDescriptor(navigator,'platform')` | `get,set,enumerable,configurable` — native lives on `Navigator.prototype` |
| `Object.getOwnPropertyDescriptor(navigator,'hardwareConcurrency')` | present as own property |

The WebGL hook **leaks its own source comment** (`// UNMASKED_VENDOR_WEBGL`) to any
page that asks. This is not a subtle statistical signal; it is the spoofing code
handing over its implementation.

**Credit where due:** worker-realm coverage is genuinely good. A `Blob` worker
reported the same `platform` and `hardwareConcurrency` as the main realm — many
competitors leak here.

**Fix:** short term, install hooks via `Proxy` with a faithful `get` trap, keep
descriptors on the prototype rather than the instance, and never leave own
`toString` properties. Long term this is the **architectural ceiling**: JS-level
spoofing is always inspectable by the page. Closing it properly means patching
Chromium — a multi-quarter programme and the actual moat competitors have built.
This should be an explicit, costed strategic decision, not a backlog item.

---

### F4 — ✅ HIGH: cookie jar and proxy password written world-readable — **FIXED**

> **Resolved in Gate 1**, at the root as this finding recommends: removing the
> staging step (F2) removes both writes. But that only stops NEW ones. The
> copies already in a user's data directory — including on the machine this
> audit ran on — were still there, still 0644, still holding a password the
> keychain exists to protect.
>
> New `storage/xdg.purge_legacy_extension_staging()` **deletes** those
> directories: at startup for every profile, and at launch for the profile
> being launched, so a long-running process still cleans as it goes. Deleted
> rather than hardened, deliberately: nothing reads them any more, and both
> files are copies — the canonical cookie jar is `cookies/<id>.json` and the
> canonical password is in the keychain — so a 0600 plaintext secret would be a
> plaintext secret kept for no reason. A directory that cannot be removed has
> its two secret files tightened instead and the next sweep retries.
>
> `tests/test_file_permissions.py` also gains a guard that `launcher.py`
> contains exactly one `write_text` (the temp file it renames over Chrome's own
> `Preferences`), so a third umask-decided write cannot appear unnoticed —
> which is how this one arrived after the sweep that fixed H1.

The original finding follows, unchanged.

### F4 (original) — cookie jar and proxy password written world-readable

**File:** [`launcher.py:403,415`](../src/multiprofile/browser/launcher.py#L403)

```python
(dest / "cookies.json").write_text(json.dumps(cookies, indent=2), …)   # full cookie jar
(dest / "proxy.json").write_text(json.dumps(proxy_data, indent=2), …)  # cleartext user+password
```

Both use bare `write_text`, so the umask decides. Observed on disk (`umask 0022`):

```
-rw-r--r--  cookies.json     ← 0644, world-readable
-rw-r--r--  evasion.js       ← 0644, contains the full fingerprint config
```

Every other writer in the codebase goes through `secure_write_json()` (0600) —
`profiles.py`, `clients.py`, `templates.py`, `cookies_store.py`, `xdg.py`. **This
one function bypasses it**, and in doing so also **undoes the keychain
encryption**: `proxy_data["password"]` is the decrypted secret, written to a
world-readable file.

This is the same class as the previously-fixed H1, in a writer that sweep missed.
`proxy.json` was not reproduced live (no proxy credentials in this environment);
it is the adjacent line to the `cookies.json` write that **was** observed at 0644.

**Fix:** route both through `secure_write_json`. Better: delete the staging path
entirely as part of F2, which removes this finding at the root.

---

### F5 — ✅ HIGH: every profile claims Chrome 131; the engine is Chromium 151 — **FIXED**

> **Resolved in Gate 2.** New `src/multiprofile/engine.py` is the one place that
> answers *what version may we claim*: it asks the installed binary once per
> process, caches the answer, and offers the rewrites that bring a UA string and
> a client-hints brand list into line with it. Everything that used to hardcode
> 131 now goes through it — the five UA pools, all seven device presets, the
> `/api/user-agents` picker, and the `_extract_ua_metadata` fallback in
> `cdp_identity`.
>
> Measured live against Playwright's Chromium **141.0.7390.37**, a profile with
> no preset and no explicit UA:
>
> ```
> engine        : 141.0.7390.37  (source=binary)
> --user-agent  : … Chrome/141.0.0.0 Safari/537.36        (was Chrome/131.0.0.0)
> sec-ch-ua     : "Google Chrome";v="141", "Chromium";v="141", "Not-A.Brand";v="99"
> fullVersionList: 141.0.7390.37                          (was 131.0.0.0)
> picker        : "Windows 11 — Chrome 141"               (was "… Chrome 131")
> preset win11  : … Chrome/141.0.0.0, browser_version_resolved=141
> ```
>
> Four rules, each with a test in `tests/test_engine_version.py` (31 tests):
>
> 1. **A pool default and a preset track the binary.** Their 131 literals are
>    now templates, rewritten at build/launch time. `browser_version_policy`
>    moved from `"pinned:131"` to `"match-engine"` on all seven presets —
>    `pinned` is the policy that let the drift happen silently.
> 2. **An explicit operator UA is never renumbered.** They may be matching a
>    fingerprint bought elsewhere, and silently moving it would break the
>    pairing they came for. The disagreement is reported in the launch notes
>    instead. Crucially the *hints follow the UA*: with an explicit UA beside a
>    preset, neither is aligned, so the two can never claim different browsers.
> 3. **An unknown engine invents nothing.** No binary on PATH, a binary that
>    will not answer, `MULTIPROFILE_CLAIM_ENGINE_VERSION=0` — all yield the
>    literals plus a record that says `source: "unavailable"`, and the launch
>    note reads `claimed Chrome version 131 is UNVERIFIED`. A wrong guess and a
>    stale literal are both lies; only one of them is also unfalsifiable. Same
>    rule the launch-time identity assertion follows.
> 4. **The two version claims are made differently, because real Chrome makes
>    them differently.** The UA string carries the reduced `<major>.0.0.0` form;
>    `Sec-CH-UA-Full-Version-List` carries the real build. The old code put the
>    reduced form in both, and no Chrome release has ever had a patch version of
>    `0.0` — that pairing was a tell on its own.
> 5. **Which form of the version a UA token gets is per token.** UA reduction is
>    a Blink change, and Chrome for iOS is WebKit under Apple's engine rule — it
>    never adopted the frozen form. A real Chrome for iOS sends its whole build
>    (`CriOS/151.0.7922.173`) where desktop and Android send `<major>.0.0.0`, and
>    the presets were written that way. Rule 4 was first applied to every token
>    alike, which minted `CriOS/151.0.0.0` on all ten iOS/iPadOS presets — the
>    same `0.0` patch tell rule 4 exists to remove, reintroduced one line over.
>    `_UA_FULL_VERSION_TOKENS` (`CriOS`, `EdgiOS`) now takes the full build.
>
> Two bugs found while fixing it:
>
> - `_chrome_version()` matched only `Chrome/`. The iPhone pool's UA says
>   `CriOS/`, so that pool **always** fell back to the 131 literal regardless of
>   its own UA string — and would now have disagreed with its realigned UA. The
>   parser matches every Chromium version token (`Chrome`, `Chromium`, `CriOS`,
>   `Edg`, `EdgA`, `EdgiOS`, `HeadlessChrome`).
> - `GET /api/devices/<id>` served the raw catalogue, so the device page and the
>   wire described two different browsers. It now serves `preset_view()`, the
>   same rewrite the launch path uses, plus `browser_version_resolved`.
>
> **It also narrows the F1 residual.** That residual — a new tab's first
> document request leaves before its per-target override lands — used to leak
> `sec-ch-ua: "Chromium";v="141"` while the profile claimed 131, so the request
> disagreed with the identity on *both* version and platform. Measured now, the
> same request leaks only `sec-ch-ua-platform: "Linux"`; the version component
> matches. Still a leak, still open, but one signal narrower.
>
> Not closed by this: the brief's "≤2 weeks behind stable" is still unreachable
> on a stock distro engine — the claim is now capped at the user's own update
> cadence, which is the honest half of §9.2. `MULTIPROFILE_ENGINE_VERSION` lets
> an operator pin a version explicitly when they know better.
>
> Verified: `pytest tests/ -q` → 1214 passed (was 1183); the same suite with
> `MULTIPROFILE_ENGINE_VERSION=151.0.7654.32` → 1214 passed, so alignment being
> *active* breaks nothing; live browser suite `-m network` → 19 passed on
> Chromium 141. `ruff check src/ tests/` clean.
>
> **Re-verified on the real engine.** The numbers above were taken on
> Playwright's Chromium 141 in a container with no system browser. Re-run against
> the dev machine's own **Chromium 151.0.7922.173 (Arch Linux)**:
>
> ```
> detect_engine_version() → full=151.0.7922.173 major=151 uaVersion=151.0.0.0
>                           source=binary  binary=/usr/bin/chromium
> ```
>
> The Arch banner (`Chromium 151.0.7922.173 Arch Linux`) parses, as do the
> `snap` and `Google Chrome` forms; a 3-component banner fills the patch with
> `0`, and unparseable text yields `None` rather than a guess. Launched live,
> both a no-preset profile and an `iphone-16` profile reported
> `identity_ok=True`, reason *verified on the wire* — so the launch-time
> assertion, which is strict enough to abort a launch, holds with a 151 claim:
>
> ```
> desktop : user-agent … Chrome/151.0.0.0 Safari/537.36
>           sec-ch-ua "Google Chrome";v="151", "Chromium";v="151", "Not-A.Brand";v="99"
>           sec-ch-ua-platform "Windows"   sec-ch-ua-mobile ?0
> iphone  : user-agent … CriOS/151.0.7922.173 Mobile/15E148 Safari/604.1
>           sec-ch-ua-platform "iOS"       sec-ch-ua-mobile ?1
> ```
>
> `pytest tests/ -q` → **1228 passed, 2 skipped** on Chromium 151;
> `-m network` → 19 passed; `ruff check src/ tests/` clean.
> `tests/test_netns.py::test_probe_cleans_up_after_itself`, which fails in the
> container for a missing `ip` binary, passes here.

The original finding follows, unchanged.

### F5 (original) — every profile claims Chrome 131; the engine is Chromium 151

**Files:** [`_core.py:185,193`](../src/multiprofile/fingerprint/_core.py#L185),
[`api/user_agents.py`](../src/multiprofile/api/user_agents.py),
`devices/presets_android.py:106` (`browser_version_policy="pinned:131"`)

The claimed version is hardcoded in the UA pool and defaults. Nothing compares it
to the installed binary — `validator.py:602` checks only that
`browser_version_policy` is *non-empty*.

**~20 stable releases of drift.** Even when the identity is internally consistent
(preset path), a page can detect the lie by probing engine features that shipped
after 131, or CSS/JS behaviour that differs between 131 and 151. Version cohort is
also a strong fingerprinting signal on its own: Chrome 131 had negligible real
market share by this date.

The brief's §5.1 requires tracking latest stable within **≤2 weeks**. That is
impossible without either bundling an engine or deriving the claimed version from
the installed one.

**Fix (tactical):** derive the claimed major from `chromium --version` at launch and
rewrite the UA/brands to match. **Fix (strategic):** ship a pinned engine.

---

### F6 (original) — ⚠️ HIGH: the profile store cannot reach 10,000 profiles

> Superseded. Fixed below: the store moved to SQLite. Note that the headline
> number here is the brief's 10,000; the product's confirmed design size is
> 150 stored with at most 20 running, and the fix was driven by a lost-update
> bug that was live at any size, not by the row count.

**File:** [`storage/profiles.py:126`](../src/multiprofile/storage/profiles.py#L126) (`load_profiles`)

Every API call reads, parses and normalises the entire `profiles.json`. Measured,
first page of **25 rows**:

| Profiles | Store size | `GET /api/profiles?limit=25` |
|---|---|---|
| 1,000 | 2.1 MB | 40 ms |
| 2,500 | 5.4 MB | 207 ms |
| 5,000 | 10.7 MB | 795 ms |
| **10,000** | **21.4 MB** | **3,115 ms** |

Growth is **superlinear** — 10× the data costs 78× the time. Pagination gives no
relief: the first 25 rows cost the same as all 10,000. Writes are equally O(n)
(whole file rewritten per create), so bulk-creating 10,000 profiles is O(n²).

Against the brief's budgets (<100 ms interaction; 1→10,000 bulk create; 10k list at
60 fps) this **fails by ~30×**. `control.db` (SQLite, WAL, already present and 94%
covered) is the obvious destination.

**Do not build billing or entitlements on this data layer** — that code would be
written twice.

---

### F7 — ✅ MEDIUM: the evasion script announces itself — **FIXED**

> **Resolved in Gate 1**, by the flag rather than the delete key. The line now
> runs only when `MULTIPROFILE_EVASION_DEBUG=1` puts `debug: true` in the
> injected config, and it sits *after* the config is parsed so it can be turned
> on at all. Default off; when it is on the launch notes say so loudly.
>
> Kept rather than deleted for one reason: the number of times that line appears
> for a single document is the only honest way to count how many times the
> script ran, and that is the measurement that caught F2. Deleting it would have
> removed the instrument along with the leak. Verified live — 0 console messages
> with the flag off, 1 per document with it on:
>
> ```
> MULTIPROFILE_EVASION_DEBUG=0   console/log messages seen: 0
> MULTIPROFILE_EVASION_DEBUG=1   [Evasion] evasion.js active on: http://127.0.0.1:37721/quiet
> ```
>
> Note the file already carried the comment *"No console.log — logging extension
> activity is a detection signal"* twelve lines below the console.log. A comment
> is not a guard; `tests/test_evasion_is_quiet.py` is — it counts the console
> calls in the script with comments stripped, and asserts the one that remains
> is inside the flag.

The original finding follows, unchanged.

### F7 (original) — the evasion script announces itself

**File:** [`evasion.js:13`](../src/extension/evasion.js#L13)

```js
console.log("[Evasion] evasion.js active on:", location.href);
```

Fires on every page, in every frame. Low exploitability (a page cannot read past
console output), but it is a plain-text confession visible in any DevTools session,
screen-share, demo or support screenshot — and trivially harvested if a page
installs a `console` proxy before it runs in the extension route. Delete it or put
it behind a debug flag.

---

### F8 — ✅ MEDIUM: the API persists unknown fields — **FIXED**

> **Resolved in Gate 2.** A field registry in
> [`api/validation.py`](../src/multiprofile/api/validation.py) now decides what a
> profile payload may contain, and `validate_profile_payload` refuses anything
> outside it *before* the write. The audit's own repro and the typo that cost it
> a test:
>
> ```
> POST /api/profiles {"name":"junk-field-test","totallyBogusKey":"pwned"}
> → 400 {"code":"invalid_input","field":"totallyBogusKey","message":"unknown field"}
>
> POST /api/profiles {"name":"typo","devicePresetId":"iphone-16"}
> → 400 … "message":"unknown field; did you mean 'deviceId'?"
> ```
>
> Nothing unknown reaches `profiles.json` — the refusal happens before
> `create_profile`, and there is a test for that specifically, because a 400 that
> still persisted the row would be worse than the original bug.
>
> **"Unknown" turned out to be three situations, not one**, and collapsing them
> would have broken the product:
>
> 1. **Honoured** — validated as before, plus `status`, which
>    `frontend/src/lib/mutations.ts` has always sent on the park/unpark path with
>    nothing validating it. It is now an enum
>    (`idle, running, queued, failed, parked`).
> 2. **Server-owned** — accepted and dropped. There is no `GET
>    /api/profiles/<id>`, so the only way to edit a profile you have just read is
>    to take a row from `GET /api/profiles` and `PUT` it back — and that row is
>    enriched with runtime truth the server injected (`launchState`, `cdpPort`,
>    `proxyOk`, `egressIp`, `lastLaunch`, …). Refusing those keys would have
>    turned the obvious client into a 400, and only *after* a launch populated
>    them. A test asserts every key the list endpoint emits is accepted on
>    update, so the registry cannot drift from the enrichment — this was found
>    the hard way, twice, while writing the fix.
> 3. **Recognised and honoured by nothing** — accepted, reported in the
>    (previously unused) `warnings[]`, and dropped. This tier exists only because
>    our own wizard populates it; it is a defect list, and F16 is the defect.
>
> ```
> POST /api/profiles {… "fingerprintStrategy":"stable","networkMode":"gateway","gatewayId":"gw_1"}
> → 200 warnings: [
>     "ignored 'fingerprintStrategy': no backend code reads it; …",
>     "ignored 'gatewayId': read only for a proxy pool's members; …",
>     "ignored 'networkMode': the route is read from networkConfig.mode (upper-case: …)"
>   ]
>   stored: none of the three
> ```
>
> Dropping rather than persisting is the point: a setting that cannot take effect
> but reads back from the API as if it had is what let F16 survive unnoticed.
>
> Verified: 17 new tests in `tests/test_api_unknown_fields.py`;
> `pytest tests/ -q` → **1245 passed, 2 skipped** on Chromium 151. No existing
> test needed changing, so nothing in the suite relied on unknown keys being
> accepted. `ruff check src/ tests/` clean.

The original finding follows, unchanged.

### F8 (original) — the API persists unknown fields

**File:** [`api/validation.py:245`](../src/multiprofile/api/validation.py#L245)

Validation rejects unknown *values* (a bad `deviceId` is refused) but not unknown
*keys*. Verified:

```
POST /api/profiles {"name":"junk-field-test","totallyBogusKey":"pwned"}
→ HTTP 200 | stored keys include bogus: True | warnings: []
```

The junk key is written into `profiles.json` and returned on every subsequent read.

This cost me a test during this audit: I sent `devicePresetId` (which does not
exist — the field is `deviceId`), the API accepted it without complaint, and the
preset silently never applied. **A typo in an API field is indistinguishable from a
working request** — for operators scripting against the local API, that is a real
support burden and a schema-drift vector.

**Fix:** reject unknown keys, or accept them with a `warnings[]` entry. The
response already carries a `warnings` array that is currently unused here.

---

### F9 — ✅ MEDIUM: listing profiles got quadratically slower — **FIXED**

**Files:** [`storage/db.py`](../src/multiprofile/storage/db.py),
[`storage/profiles.py`](../src/multiprofile/storage/profiles.py),
[`api/server.py`](../src/multiprofile/api/server.py)

Originally filed as "no list virtualization", measured in the live console
with 603 profiles: `domRows: 25`, `hasVirtualSpacer: false`. That reading was
right about the renderer and right that the real failure was in the API — and
it survived the storage move in F6, because F6 made the *store* fast while
this path still materialised everything repeatedly.

The list endpoint collected every matching profile into Python and sliced the
page out of it. The collector walked in chunks of 500, and the function it
called re-read and re-decorated the whole table on **every chunk** — so
serving 25 rows out of 10,000 profiles materialised the store twenty times:

| | 2,000 | 10,000 |
|---|---|---|
| first page (25 rows) | 246 ms | **5,006 ms** |
| filter by tag | 118 ms | 2,628 ms |

Five times the data for twenty times the work.

Filtering, ordering and the keyset cut now happen in SQL. Warm, at 10,000
profiles:

| | before | after |
|---|---|---|
| no filter | 4,561 ms | **0.5 ms** |
| tag filter | 1,568 ms | 24.5 ms |

(The "before" figures understate it: that loop was measured already calling
the new SQL-backed inner function, so the original was slower still.)

**The predicates mirror the Python ones exactly**, not approximately. A
cheaper `data_json LIKE '%term%'` would have matched field *names* and any
other string in the document, so the `total` returned beside the page would
have disagreed with the page — a filter that quietly means something different
at scale is worse than a slow one. A parametrised test runs both
implementations over the same corpus and asserts identical id sets and counts.
Search terms are escaped, so a profile named `100%` is found rather than
matching everything.

**Status stays a Python filter, deliberately.** A profile's status is not
stored — it is synthesised on read from the runtime registry — so it cannot be
a `WHERE` clause without the database disagreeing with the live process table.
Asking for one falls back to scanning the matching set, which is acceptable
because status narrows to what is *running*, bounded by the browser slot
limit. The cost is stated in the docstring rather than hidden.

**Select-all across a filter** is now possible: `GET /api/profiles/ids`
resolves a filter to its ids. Selection was bounded by the page, so a bulk
action on a filtered set of 400 meant paging through it sixteen times. It is
capped at 5,000 and refuses an oversized selection **with the count**, rather
than truncating — truncating is how a "delete all" quietly deletes some.

`_all_filtered_profiles` was deleted rather than left unused, since it encoded
the pattern being fixed.

**The UI half, re-scoped.** The product's stated design size is **150
profiles**. At that size most of what this finding asked for solves a problem
that does not exist, so it was not built:

- **No virtualization.** 150 rows of a plain table is not a rendering problem;
  the original measurement found no jank at 25 and the cost is linear. A
  windowed list would be machinery with no user benefit until the target moves.
- **No cross-page selection.** The page now holds 200, so the whole fleet is
  one page: "select all" already means all of them, and no bulk action is
  bounded by a page boundary that no longer exists.

What was built instead:

- The page size is 200, which it could not have been while every row carried
  its fingerprint.
- **A list row no longer carries its generated fingerprint** — 2,114 bytes of
  a 2,795-byte row, which the table never read. A row is 663 bytes now. At 150
  profiles the whole fleet is **one page, 85 KB, 3.9 ms**, with no pagination
  control in sight.
- The bulk bar states the scope of a selection (`N selected of M matching —
  this page only`) whenever a second page does exist. At 150 it never fires;
  if the target moves, the operator sees the gap instead of assuming they
  selected everything.

**A bug this uncovered, which bites at exactly 150.** There was no
`GET /api/profiles/<id>`. The console's detail page fetched
`/api/profiles?limit=500` and searched the page — but the list clamped `limit`
to 100, so **the console could not open any profile past the first hundred**:

```
asked for 500, got 100 of total 150
profile #10  findable: True
profile #149 findable: False
```

Nothing reported the truncation, which is why it survived: the request
succeeded, the page rendered, and the answer was "not found" for a profile
sitting in the store. There is now a real detail endpoint — one indexed row,
0.6 ms, fingerprint included — and the console uses it.

The `/api/profiles/ids` endpoint and the SQL paging remain in place. They cost
nothing at 150 and are the part that would have been hard if the target moves.

33 tests added.

---

### F10 — ✅ MEDIUM: supervisor crash-loop — fixed this session

**File:** [`browser/supervisor.py`](../src/multiprofile/browser/supervisor.py)

`max_restart_attempts` lived in a local loop variable, bounding only consecutive
*launch failures*. A browser that launched successfully then died on the next tick
got a fresh budget every time. Your own log shows it:
`relaunch … succeeded (attempt 1)` repeating every ~11 s.

Fixed before this audit began: the budget is now spent per **crash episode**, a
restart counts as recovery only after `restart_stability_s` (default 60 s), and a
crash-looping profile is parked in `error` after 3 restarts. Measured: 10 unbounded
relaunches before, 3 and parked after. Two regression tests added.
Uncommitted in the working tree.

---

### F6 — ✅ HIGH: the profile store lost concurrent writes — **FIXED**

**Files:** [`storage/db.py`](../src/multiprofile/storage/db.py),
[`storage/profiles.py`](../src/multiprofile/storage/profiles.py),
[`storage/notes.py`](../src/multiprofile/storage/notes.py),
[`storage/clients.py`](../src/multiprofile/storage/clients.py)

Profiles lived in a single `profiles.json`. Every mutation read the whole
list, changed one entry and wrote the whole list back — and the lock was
released between the read and the write. Two requests editing **different**
profiles therefore raced, and the later writer wrote back a snapshot taken
before the earlier one's change.

Measured under the threaded Flask server the product actually runs
(`app.run(threaded=True)`), 20 threads each updating a different profile:

```
writes that survived: 3/20
```

All twenty returned success to their callers. Nothing logged, nothing failed,
seventeen edits gone. A per-row `UPDATE` cannot lose a write to a different
row, so the fix is structural rather than a bigger lock:

```
after: 20/20
```

`notes.py` and `clients.py` had the same pattern — appending one note rewrote
every profile — and now write one row too.

**Cost at scale.** The old store parsed the entire file on every read and
rewrote it on every edit. At 2000 profiles (a 4.5 MB file):

| operation | profiles.json | SQLite |
|---|---|---|
| `get_profile` | 24.1 ms | 0.074 ms |
| `update_profile` | 46.1 ms | 0.154 ms |
| `load_profiles` (all) | — | 40 ms |

Every single-field edit wrote 4.5 MB to disk.

**Schema.** `id`, `name`, `client_id`, `created_at`, `updated_at` are columns;
the rest of the profile stays in `data_json`. A profile has ~40 fields and
gains more each release, so a column per field would mean a migration per
feature. The extracted five are the ones something *other than the profile*
needs — billing aggregates by client, the console sorts by name and date.

**Migration** imports an existing `profiles.json` on first read, into an empty
table only, and **renames rather than deletes** it: at that moment the file is
the only copy of the operator's profiles, and a migration that removes its own
input has no way back if the import is wrong. Malformed entries are dropped,
proxy passwords are wrapped, and a second run is a no-op.

**Two regressions I introduced and the tests caught:**

- **The database was world-readable.** `profiles.json` was 0600; SQLite creates
  its own file, so the process umask decided and `control.db` landed **0644** —
  along with `-wal` and `-shm`. The password stays encrypted, but every
  profile's proxy host, username, client and notes became readable by any local
  user. `harden_data_dir()` already knew about the file but only ran at
  startup, which left a window that now spanned the whole profile store. The
  mode is set at creation.
- **`get_profile` became O(n).** Each accessor called `load_profiles()` just to
  ensure migration had run, so every indexed lookup first read and decorated
  every row — removing most of what the move was for. Replaced with a
  one-time guard keyed by database path.

A third issue was latent rather than introduced: the cached connection was
kept for the life of the process regardless of where `db_path()` pointed, so
any caller that repointed `XDG_DATA_HOME` silently kept using the previous
directory. Survivable when this database held only runtime rows; not once it
held the profiles. It now reopens when the path changes.

**A test that existed and did not catch this.**
`test_concurrent_writes_do_not_interleave_into_a_broken_file` ran 20
concurrent creates and asserted the names it found were unique — but a lost
create removes a name without duplicating one, so it passed throughout. It now
asserts the count, which is the assertion that would have failed.

23 tests added, plus 10 retargeted from the file to the database: permissions,
secrets-at-rest and the resilience properties all still hold, of the new store.

---

### F42 — ✅ HIGH: picking an identity did nothing; every profile claimed Windows — **FIXED**

**Files:** [`storage/profiles.py`](../src/multiprofile/storage/profiles.py),
[`api/user_agents.py`](../src/multiprofile/api/user_agents.py),
[`api/validation.py`](../src/multiprofile/api/validation.py),
[`api/server.py`](../src/multiprofile/api/server.py)

Also found while measuring for `docs/04`: a macOS profile came back claiming
Windows. Creating one profile per catalogue id:

```
win_chrome     -> fp.platform='Win32'   ch_platform='Windows'
mac_chrome     -> fp.platform='Win32'   ch_platform='Windows'
linux_chrome   -> fp.platform='Win32'   ch_platform='Windows'
pixel_9        -> fp.platform='Win32'   ch_platform='Windows'
iphone_15      -> fp.platform='Win32'   ch_platform='Windows'
```

`GET /api/user-agents` offers ten identities across Windows, macOS, Linux,
Android, iOS and iPadOS. `userAgentId` was accepted by the API, validated,
aliased from `uaId`, stored on the profile and carried by templates — and read
by nothing that builds a fingerprint, which derives everything from
`userAgent` and `isMobile`. **F16** and **F19** again: a control present in the
vocabulary and absent from the path.

**The fleet-level cost is worse than the per-profile one.** An operator who
cannot make a macOS profile notices eventually. A fleet where *every* profile
claims Windows regardless of selection shares a value across all of them,
which is a linkage signal in its own right — the same failure as **F31**,
where every profile announced New York from every exit.

**Three fixes.**

*The id resolves.* `_resolve_user_agent_id` turns a catalogue id into its
engine-aligned UA and `isMobile`, which is all the fingerprint pools need.
Measured after, a `mac_chrome` profile reports `MacIntel`, `macOS`,
`ANGLE (Apple, Apple M3, OpenGL ES 3.0)` and macOS speech voices (Alex,
Samantha, Daniel, Fred) — the per-OS machinery worked all along and was never
told which OS to use.

*An unknown id is refused.* It used to be stored and ignored, so a typo
produced a profile claiming something nobody picked. Now a 400 naming the
valid ids.

*The alias is applied before validation, and on both paths.* `uaId` was copied
into `userAgentId` **after** `validate_profile_payload` had run, so the alias
was a way around every check the real field had; and it ran only on create, so
`uaId` on an update was accepted and silently dropped — **F8** and **F21**'s
shape. One `_apply_aliases` helper now runs first on create and update.

**A rule the tests pin down.** A UA the operator *typed* wins over the picker:
they may be matching a fingerprint bought elsewhere. A UA the *catalogue*
produced does not, because otherwise the picker works exactly once per profile
and then jams on its own previous answer — which is what the first version of
this fix did, caught by the update-path test before it was committed.

36 tests added.

---

### F41 — ✅ CRITICAL: every page could read the spoofing configuration — **FIXED**

**Files:** [`browser/cdp_inject.py`](../src/multiprofile/browser/cdp_inject.py)

Found while building the measurement harness for
[`04_FINGERPRINT_TEST_RESULTS`](04_FINGERPRINT_TEST_RESULTS.md) — a probe that
listed globals matching `/^__MP|multiprofile|evasion/i` came back with two.

`build_identity_script` emitted its configuration at top level:

```js
var __MP_INLINE_CFG__ = {...};
window.__MP_PROFILE__ = __MP_INLINE_CFG__;
(function(){ /* evasion.js */ })();
```

Both are properties of the global object. Measured on a live profile:

```
window.__MP_PROFILE__  ->  25 keys, 1977 bytes
architecture, bitness, ch_brands, ch_full_version_list, ch_platform,
clientHints, cores, engineVersion, gpu, isMobile, language, languages,
mediaDeviceGroupIds, mediaDeviceIds, memory, model, platform,
platformVersion, screen, seed, timezone, touch, userAgent, webrtcMode,
webrtcPolicy

seed: 1479877959
```

**Why this is critical rather than merely embarrassing.** Two separate
failures, and the second is the serious one.

*It names the product in one property read.* This is **F7**'s finding — the
evasion script announcing itself — one layer down and far louder. F7 was a
`console.log`; this is a structured object.

*It hands over the seed.* The seed derives every noise value the script
produces, so a site that reads it can undo the canvas, audio and ClientRects
noise. Worse, the seed is itself a **stable, unique, cross-site identifier** —
more reliable than any fingerprint, because it is an exact integer rather than
a statistical signature, and it is constant for the life of the profile. A
profile that leaks its seed is more trackable than one with no spoofing at
all, which would at least be anonymous among every other Linux Chromium.
`mediaDeviceIds` leaks alongside it, undoing part of **F23**.

**The fix is one wrapper.** `evasion.js` is already an IIFE, so enclosing the
pair in one more function makes the config a closure variable — reachable by
the code that needs it, unreachable from the page. Nothing else consumed the
globals; the only other references are inside `evasion.js` itself, which now
resolves them through scope.

Measured after: `window.__MP_PROFILE__` is `undefined`, `window.__MP_INLINE_CFG__`
is `undefined`, no global matches the pattern, and the identity is unchanged
(`navigator.platform === "Win32"`, Windows UA, worker realms still agreeing).

**One existing test asserted the broken shape** — `test_cdp_inject.py` required
the script to start with a top-level `var` and to set `window.__MP_PROFILE__`.
It encoded the vulnerability as a requirement, so it was rewritten to assert
the stronger invariant instead, and the reason is recorded in its docstring.

7 tests: 3 in `test_cdp_inject.py` pinning the generated shape, 4 in the new
`test_config_leak_live.py` asserting what a real page can actually see.

**How it survived this long.** Every previous check asked whether a *spoofed
value* was correct. None asked what else was lying around in the global scope
next to it — which is the same blind spot that made F3 worth writing down:
the spoofing itself is a surface.

---

### F40 — ✅ MEDIUM: no way to take your data out, or to destroy it — **FIXED**

**Files:** [`account.py`](../src/multiprofile/account.py) (new),
[`api/server.py`](../src/multiprofile/api/server.py)

The brief asks for "data deletion and export" under GDPR / India DPDP
readiness. `backup.py` exported **one profile**; nothing could answer "give me
everything you hold" or "delete everything you hold", and nothing could say
what either answer would contain.

Both matter locally before a regulator is involved. An export is how an
operator moves to a new machine or keeps a copy that outlives a disk; a
deletion is how they hand a laptop back.

**`GET /api/account/export`** returns one document: profiles, trash,
workspaces, templates, measured usage, token names, the plan, the audit log
and the cookie jars. Sections are collected independently — one unreadable
section must not cost the operator the other eight — and a failure is recorded
**in** the document rather than raised, because an export that silently omits
a section is the thing to avoid and one that says "audit: could not be read"
is useful.

**It documents its own gaps.** Proxy passwords are not in it, and the field
names are in the document rather than only in the source: an export whose
omissions are undocumented is worse than one with no omissions, because the
hole is found at restore time. Decrypting credentials into a plain file that
then gets emailed to a new laptop would undo **F4**, where exactly this class
of secret was found world-readable. Token hashes do not travel either.
Written to a file, the export is `0600`, because it carries cookie jars and
those are live session credentials even with the passwords stripped.

**`POST /api/account/delete`** removes the Chrome profile directories, the
cookie jars, the database and its WAL sidecars, the audit log and the tokens —
not merely the rows that make them visible. A delete that unlinks an index and
leaves the data is the failure this audit kept finding, one layer down; a test
asserts the proxy password appears in **no file** anywhere under the data
directory afterwards. The database is closed before its files are unlinked,
since removing a file from under an open SQLite handle leaves the next write
recreating a partial database that looks like a working one — and the app is
asserted to still function afterwards, as an empty install rather than a
broken one.

It requires the exact string `DELETE EVERYTHING` rather than a boolean: a flag
is too easy to send by accident from a script that meant something else, and
there is nothing behind this one. **`GET /api/account/deletion-preview`** says
what would go without doing it, because nobody should have to run an
irreversible operation to learn what it covers.

26 tests added.

---

### F39 — ✅ HIGH: one API token, unscoped, unrevocable, in cleartext — **FIXED**

**Files:** [`api_tokens.py`](../src/multiprofile/api_tokens.py) (new),
[`api/auth.py`](../src/multiprofile/api/auth.py),
[`storage/db.py`](../src/multiprofile/storage/db.py),
[`api/server.py`](../src/multiprofile/api/server.py)

The product had exactly one API token, stored in cleartext in
`$XDG_CONFIG_HOME/multiprofile/api_token`. `auth.py`'s own docstring already
listed the weakness — "a single static bearer token, not per-user
credentials" — and three consequences follow from it, all real rather than
theoretical:

*No least privilege.* A cron job that starts a profile each morning held the
credential that can delete the fleet, read every cookie jar and rewrite every
proxy. The blast radius of a leaked automation token was the entire product.

*No revocation.* Rotating after a leak meant changing the one token and
breaking every other integration at the same moment — which in practice means
it does not get rotated.

*Cleartext at rest.* Whoever could read the file had a working credential.

**What replaced it.** An `api_tokens` table (schema v6) of named tokens, each
with scopes, a creation time, a last-used time and a revocation time. Tokens
are `mpt_<id>_<secret>`; the id is not secret and exists so verification is
one indexed lookup rather than a comparison against every row, which would
make request cost scale with how many tokens the operator has.

**Only the hash is stored.** SHA-256, deliberately not bcrypt or argon2: these
are 256 bits from `secrets`, not human-chosen passwords. A slow KDF exists to
make guessing a low-entropy secret expensive and there is nothing low-entropy
here to guess; it would cost every request measurable time and buy nothing.
A test asserts the secret does not appear anywhere in `control.db` or its WAL
sidecars.

**Four scopes**, coarse on purpose: `read`, `launch`, `write`, `admin`. A
permission model with thirty verbs reads as rigorous and gets chosen wrongly,
because nobody can hold thirty verbs in their head while deciding what a cron
job needs. `write` and `launch` imply `read` — a token that may change things
may look at what it changed — and the implication is stored rather than
resolved at check time, so reading a row answers what a token can do without
also having to know the rules. Managing tokens needs `admin` **even to list
them**: which integrations exist, when they last ran and what they may do is
reconnaissance.

**Two distinctions the responses are careful about.** Every authentication
failure — malformed, unknown, revoked, wrong secret — answers 401 with an
identical body, so the endpoint is not an oracle telling an attacker which
part of a guess was right. A *scope* failure answers **403**, because the
credential is good and the permission is not; a 401 there would send someone
to replace a token that was never wrong.

The secret is returned once, by the call that mints it, and there is
deliberately no way to recover it: a store that can show you a token later is
a store that can leak one later. Revocation keeps the row, so the trail
survives the credential.

**The legacy token still works**, carries every scope, and is *not* copied
into the new store. It may already be wired into the operator's scripts;
narrowing it would silently break a live integration, and silently duplicating
a credential into a database is not a migration anyone asked for.

Unchanged and deliberate: **loopback still needs no token**. The console must
work, and code already running on this machine owns it anyway. Scopes
therefore bite exactly where automation connects from, which is where they
matter.

49 tests added.

---

### F38 — ✅ MEDIUM: the limits were scattered, anonymous and partly fictional — **FIXED**

**Files:** [`plans.py`](../src/multiprofile/plans.py) (new),
[`config.py`](../src/multiprofile/config.py),
[`api/server.py`](../src/multiprofile/api/server.py)

The limits existed; they were just unreachable. `MAX_PROFILES` lived in the
profile store, `MAX_CONCURRENT_PROFILES` in the browser manager, and neither
was visible from the other or from the console. An operator who hit one got a
refusal naming a number they had never chosen and could not find without
reading two modules and knowing which environment variables the process
happened to start with.

A named set of limits — a plan — makes that one request instead of an
archaeology exercise, and it is the same object a hosted product would sell.
`GET /api/plan` returns the plan, every limit, and current usage against it.

**The part worth being pedantic about.** Every limit reports whether anything
**enforces** it. Two do (`maxProfiles`, `maxConcurrent`); three are
declarative (`maxWorkspaces`, `maxApiTokens`, `includedRuntimeHours`) and say
so. A plan listing five limits of which two are real, without distinguishing
them, would be exactly the failure this audit kept finding — **F1**'s control
that reported success while doing nothing, **F19**'s API vocabulary matching
nothing downstream — wearing commercial dress. The tests assert `enforced`
against the store and the browser manager rather than against the plan's own
claim.

**Two deliberate non-features.**

*A plan authorises nothing.* This module reports and applies limits; it does
not check a licence, phone home, or decide whether anyone paid. The local
build runs `self-hosted`, deliberately the most generous plan, because a tool
someone runs on their own machine has no business refusing to work.
Enforcement that means anything commercially belongs on a server that issues
the plan, and that server does not exist — see `docs/08`.

*Environment variables still win.* `MAX_PROFILES=400` overrides the plan.
That is not a hole in a security control, because there is no security control
here; it is the acknowledgement that on a local install the operator owns the
machine. The report labels each value `plan` or `env`, because "150 because
the plan says so" and "150 because `MAX_PROFILES` says so" send someone to
different places to change it.

`self-hosted` is pinned to **150 stored / 20 concurrent** — the measured
operating size from F33 and F35 — and a test asserts the two stay equal, since
if they drift apart one of them is lying to the operator. An unrecognised plan
name falls back to the default and reports the fallback rather than silently
changing the limits.

18 tests added.

---

### F37 — ✅ HIGH: nothing measured what the fleet did — **FIXED**

**Files:** [`usage.py`](../src/multiprofile/usage.py) (new),
[`storage/db.py`](../src/multiprofile/storage/db.py),
[`api/server.py`](../src/multiprofile/api/server.py)

`storage/db.py` made `client_id` a column with the comment that "billing has
to aggregate by it". There was nothing to aggregate. The product recorded no
runtime, no session history and no launch count, so every commercial question
it might be asked — what a seat is worth, whether a cap is generous or mean,
which workspace is expensive, what a fair-use limit should be — was a guess,
and would have stayed a guess however the pricing page was written.

This is also an operator-facing gap before it is a commercial one: "which of
my profiles is actually being used?" had no answer.

**What is metered.** Seconds spent in the `RUNNING` state. A launch is a count
and trivial to record, but it is not what a fleet costs: twenty profiles
started and closed immediately and twenty left open overnight are the same
number of launches and very different amounts of memory, proxy bandwidth and
wear.

**Where it is metered.** Inside `db.set_profile_state`, the transition the
launcher, the supervisor, the job handlers and the API all already route
through. Metering from those four callers would mean four places that must
each remember, and the one that forgets produces a bill that is quietly short
— the identical shape to **F1** (a control that reported success while sitting
off the path that mattered) and **F19** (an API vocabulary matching nothing
downstream). The meter is deliberately outside the state transaction, so a
metering failure can never roll back a state change: the record of what the
browser is doing outranks the record of what it cost.

**Three properties it is built to have.**

*It cannot double-bill.* A repeated or forced transition to `RUNNING` returns
the existing open session rather than opening a second one.

*It rounds against its author.* A session abandoned by a crash is closed at
the profile's **last heartbeat**, never at the time the process next starts —
the machine may have been off for three days. A clock that runs backwards
floors at zero rather than producing negative time. A meter that can be shown
to round in its author's favour is worth nothing to the person being billed.

*It attributes to who owned the profile then.* `client_id` is snapshotted when
the session opens rather than joined at read time, because a profile can be
moved between workspaces and a join would silently rewrite last month's
numbers every time one changed hands. Usage for a **deleted** profile keeps
counting toward the total and loses only its name: dropping the row would make
the totals stop adding up, which would quietly discount whatever was deleted.

`GET /api/usage` gives totals by profile and by workspace over a day / week /
month / all window; `GET /api/usage/sessions` gives the rows behind them,
because a total nobody can drill into is a number to be taken on trust, which
is what a bill must not be. Nothing is backfilled — inventing history for a
table a bill would be drawn from is the one thing metering must not do — so an
upgraded install starts measuring at its first launch.

33 tests added. One found a real defect while being written: the stale-
heartbeat guard compared **whole seconds**, so a heartbeat recorded 300 ms
after a session opened read as older than the session and was discarded.

---

### F36 — ✅ HIGH: deleting a profile destroyed the asset, irreversibly — **FIXED**

**Files:** [`storage/db.py`](../src/multiprofile/storage/db.py),
[`storage/profiles.py`](../src/multiprofile/storage/profiles.py),
[`api/server.py`](../src/multiprofile/api/server.py),
[`config.py`](../src/multiprofile/config.py),
[`frontend/src/components/trash/trash-page.tsx`](../frontend/src/components/trash/trash-page.tsx) (new)

`DELETE /api/profiles/<id>` removed the row and then `rmtree`'d
`chrome-profiles/<id>`. That directory holds the cookies, the logins and
whatever warmed-up session state the profile had accumulated. In an
anti-detect browser it **is** the asset: it cannot be recreated by making a new
profile with the same settings, because the value of the old one was that some
site had come to trust it. A confirmation dialog was the only thing between a
misclick and losing it — and the console offers the same button for a
multi-select, so one misclick could take a fleet.

Every comparable product (AdsPower, GoLogin, Multilogin) has a trash with
restore. The audit brief asks for one under "Trash with restore".

**What was built.** A `profiles_trash` table (schema v3) with the same shape as
`profiles` plus `deleted_at`. Deleting moves the row between the two tables in
a single transaction — as two statements outside one, a profile ends up in
neither table or in both. The profile's directory and cookie jar are left
exactly where they are, so a restore is a move of one row rather than a
recovery.

| Route | Does |
|---|---|
| `DELETE /api/profiles/<id>` | Moves to the trash. Keeps every file. |
| `DELETE /api/profiles/<id>?permanent=1` | The old behaviour, when asked for by name. |
| `GET /api/trash` | Trashed profiles, newest first, with retained bytes. |
| `POST /api/profiles/<id>/restore` | Back into the fleet. |
| `DELETE /api/trash/<id>` | Permanent, files included. |
| `POST /api/trash/empty` | The same, for all of them. |

**Four decisions worth stating.**

*A restore is a create.* It is checked against the fleet capacity from F35 in
the same transaction, and answers 409 if there is no room — silently exceeding
the cap to honour a restore would make the cap meaningless.

*Trashed profiles do not count against the cap.* If they did, a full fleet
could never be trimmed: deleting would not free a slot and creating would stay
refused for ever.

*A running profile cannot be trashed* (409, "stop the profile before deleting
it"): Chrome is writing into the directory the trash is supposed to preserve,
so trashing it would capture a torn copy. The guard checks the **process**,
not the record — `isRunning` is derived from a status and pid that nothing
updates when Chrome dies unexpectedly, so trusting it would leave a crashed
profile undeletable until something reaped it, which is a worse failure than
the one the guard prevents.

*Retention is reconciled on access, not on a timer.* Trashed profiles are
purged after `MULTIPROFILE_TRASH_RETENTION_DAYS` (default 30, `0` disables it),
checked when the trash is read. A background thread performing irreversible
deletions is a worse thing to own than a slightly late purge.

**Console.** A Trash page under System: retained disk per row and in total,
because the trash deliberately does *not* free space and an operator deciding
whether to empty it should be told what they get back. Restore asks for
nothing — it is reversible; the two permanent actions are the only ones styled
destructive and both confirm with a modal naming the count and the bytes. The
delete dialogs on the Profiles page said "Browser storage will be purged. This
cannot be undone", which is no longer true, and now say where the profile is
going; the toast carries an *Open trash* action.

Verified against a running console with four trashed profiles (841 MB): restore
returned one to the fleet with its directory intact, a permanent delete removed
610 MB from disk, and the profile came back with its `seed` and `fingerprint`
unchanged — a restored profile that gets a new identity is just a fresh create
wearing the old name.

**Correction made during the build.** The first version measured the retained
directory while *rendering* the trash — a full tree walk per row on every page
load. This host's own Chromium profile holds **3,239 files**, so 150 trashed
profiles cost roughly **2 s of `stat()` calls to display a column**: the same
shape as F9, introduced while fixing F36. Nothing writes to a trashed profile,
so the size is measured once, when it is trashed, and stored in
`profiles_trash.disk_bytes` (schema v4, with an in-place `ALTER` for a database
that already has the v3 table). Measured after: the listing no longer touches
the disk at all.

23 tests added.

---

### F35 — ✅ HIGH: bulk create silently did a third of what was asked — **FIXED**

**Files:** [`api/server.py`](../src/multiprofile/api/server.py),
[`storage/profiles.py`](../src/multiprofile/storage/profiles.py),
[`storage/db.py`](../src/multiprofile/storage/db.py),
[`config.py`](../src/multiprofile/config.py),
[`profile_import.py`](../src/multiprofile/profile_import.py)

`POST /api/profiles/from-template` clamped its `count`:

```python
count = max(1, min(int(payload.get("count") or 1), 50))
```

Measured against an empty store before the fix:

```
asked for 150 -> status=200 ok=True count=50
count="abc"   -> status=200 count=1
count=-5      -> status=200 count=1
```

Asking for a fleet of 150 returned HTTP 200, `ok: true`, and fifty profiles.
Nothing in the response mentioned the other hundred. A `count` that was not a
number silently became 1, which is the same failure in a smaller disguise: the
operator gets a plausible answer to a question they did not ask.

The number 50 corresponded to nothing — not the concurrency limit, not a
storage limit, not a measured cost. Filling a fleet from a template is the one
flow this endpoint exists for, so silently doing a third of it is the worst
available answer; refusing would at least have been visible.

**Three changes.**

*A count is validated, not reinterpreted.* Non-integers, zero and negatives are
400s naming the field. An absurd count (`1e9`) is rejected as input rather than
attempted.

*The ceiling is the fleet's own capacity.* `config.max_profiles` (default 150,
`MAX_PROFILES` to change it) is the number of profiles that may be **stored** —
a different limit from `max_concurrent_profiles`, which is how many may run.
A batch that will not fit is refused with **409** and the numbers needed to act
on it, because "fleet is full" alone does not tell an operator whether to
delete two profiles or fifty:

```json
{"error": {"code": "fleet_full", "stored": 1, "capacity": 150,
           "free": 149, "requested": 150,
           "message": "fleet is full: 1 of 150 stored, room for 149 more, 150 requested"}}
```

*The batch is one transaction.* The old loop called `create_profile` once per
profile, so a failure at row 80 of 150 left 79 stored with no indication of
where it stopped. `db.insert_profiles` builds the batch and writes it in a
single `BEGIN IMMEDIATE`, checking capacity **inside** the transaction — a
check in the caller would let two concurrent batches each see room for 80 and
both commit. Measured after: 149 profiles created in 0.01 s, and a refused
batch stores nothing.

The cap is enforced in the store rather than in the endpoint, because a limit
that a loop of single `POST /api/profiles` calls can walk past is not a limit.
`GET /api/profiles/capacity` reports `{stored, capacity, free}` so the console
can say "room for 12 more" while a number is being chosen rather than after it
has been submitted. Import (F34) shares the ceiling: rows beyond the free space
are reported by line as `fleet_full` instead of each one costing its own
rejected transaction.

**What the cap deliberately does not block.** A restore from backup writes
"the set is now this" and is *not* checked against the capacity. Refusing to
restore someone's own backup because of a soft product limit would be the worse
failure, so a restore of 200 profiles into a cap of 150 succeeds; the fleet is
then over its limit and *creating* is refused until it is trimmed. Verified:
`free` clamps at 0 rather than going negative, and the refusal names the real
numbers ("20 of 5 stored").

**Scope note.** 150 is this product's measured operating size, not the brief's
10,000 — see the design target recorded with F33. It is one environment
variable, and the paging, the SQL predicates and the id-resolution endpoint
built for F9 are all size-independent, so moving it is configuration rather
than rework.

18 tests added. Two existing tests were retargeted, neither weakened:
`test_resilience` patched `db.put_profile`, which creation no longer calls, and
`test_profile_import::test_one_request_is_bounded` measured the `MAX_ROWS`
bound, which the default fleet cap would otherwise have shadowed.

---

### F34 — ✅ MEDIUM: no way to fill a fleet — **FIXED**

**Files:** [`profile_import.py`](../src/multiprofile/profile_import.py) (new),
[`api/server.py`](../src/multiprofile/api/server.py)

The brief asks for "create / clone / bulk create / import / export". The
product had create, clone, duplicate and from-template — every one of which
makes a **single** profile. An operator arriving with a block of proxies from
a provider, which is how a fleet is actually filled, had to type each one in.

`POST /api/profiles/import` takes a pasted list and creates one profile per
proxy. Parsing is not reimplemented: `proxy_util.parse_proxy` already accepts
every shape a provider sends — `ip:port`, `ip:port:user:pass`,
`user:pass@host:port`, and the `http://` / `socks5://` / `socks5h://` URL
forms — and it is the same function the launcher routes through, so an
imported profile cannot parse differently from one typed by hand. It had **no
direct test coverage**; it does now.

**Partial success by design.** A bad line does not reject the batch: someone
pasting 150 proxies usually has one typo, and failing all 150 for it means
finding the typo by bisection. Every row is reported by the input line it came
from, so a caller fixes two lines rather than re-pasting everything. A partial
import returns **207**, not 200 — a flat 200 would let a client treat
"created 0 of 150" as success.

**Duplicates are skipped, not merged.** Pasting the same block twice is a far
more common accident than wanting two profiles on one exit, and the costs are
asymmetric: a skipped row is a line in a report, a duplicated fleet is 150
profiles to delete by hand. The same host and port with a *different* username
is not treated as a duplicate, because rotating providers hand out one endpoint
with many sessions. A proxy an existing profile already uses is warned about
and still created — refusing it would make re-importing after a partial failure
impossible.

**A secret leak caught during the work.** The first version stored the pasted
line in `customProxy` as "what the operator typed", which wrote the proxy
password to disk **in clear** beside the encrypted copy of the same password:
`_encrypt_profile_secrets` wraps `proxyPass` and nothing else. `backup.py`
already treats `customProxy` as a secret field and strips it, which was the
hint. Redacting it was not an option either — `NetworkConfig.from_profile`
prefers `customProxy` over the split fields, so a redacted string would be what
the launch actually routed on and the credentials would be lost. The field is
left empty and the split fields carry everything, with the password encrypted
at rest. A test asserts the password does not appear in the database files.

**A correctness bug caught the same way.** A bare `ip:port:user:pass` has no
scheme, and `parse_proxy` defaults to HTTP — so importing a SOCKS5 list
produced a fleet that silently spoke the wrong protocol to every proxy in it.
The batch takes a `proxyType`, applied only to rows that carry no scheme of
their own.

One request is bounded at 500 rows. Not a fleet limit — the operator decides
how many profiles they keep — but a bound on a single call, reported rather
than silently truncated.

31 tests added.

---

**Addendum — the console half.** Both fleet-filling paths were endpoints with
no screen, so the only way to make a fleet from the console was still to click
"New profile" once per profile. A **Bulk create** dialog on the Profiles page
now covers both: N copies of a template, or a pasted proxy list. It shows the
fleet's remaining room *before* a number is chosen rather than after it is
submitted, disables the button when the batch will not fit, and keeps itself
open to show the per-line report when anything failed or was skipped — a clean
run has nothing to say and closes. Two shape bugs were caught building it:
`GET /api/templates` answers with a bare array rather than an envelope, and
`count` must be sent as a number, since the server validates the type now
instead of reinterpreting it (F35).

---

### F33 — ✅ HIGH: starting a fleet cost one launch after another — **FIXED**

**Files:** [`jobs/legacy.py`](../src/multiprofile/jobs/legacy.py),
[`browser/launcher.py`](../src/multiprofile/browser/launcher.py),
[`browser/cdp_window.py`](../src/multiprofile/browser/cdp_window.py),
[`browser/cdp_inject.py`](../src/multiprofile/browser/cdp_inject.py)

Raised by the operating target: **up to 150 profiles stored, a maximum of 20
running simultaneously**, with the operator choosing which 4 / 6 / 8 / … / 20
to start. Nothing was reported as slow; this is what measurement found at that
size.

Bulk launch was a plain `for` loop, so starting N profiles cost N launches end
to end. Measured per launch:

| phase | time |
|---|---|
| Chrome spawn → debugging port bound | 0.51 s |
| identity assertion (the F1 guard) | 0.50 s |
| `wait_for_cdp` | 0.18 s |
| identity injection | 0.13 s |
| fontconfig (F25) | 0.06 s |
| **total** | **~1.6 s** |

Almost all of it is *waiting*, so it overlaps. Measured with 8 profiles:

```
sequential   ~13 s
parallel      2.9 s   (0.36 s per profile)
```

At the maximum of 20 that is the difference between about half a minute of
progress bar and a few seconds.

**A fixed sleep went first.** The launch did `time.sleep(1.2)` before checking
whether Chrome had died — 1.2 s of every launch, to answer a question that is
usually settled in a fraction of it: a browser that dies on a bad flag or a
locked profile does so almost immediately, and one that lives has bound its
port. It now polls for either answer, with the same timeout as a bound, so a
slow-but-healthy start is treated exactly as before. Launch: 1.9 s → 1.6 s.

**Port allocation had to be made safe first.** `allocate_localhost_port` binds
port 0, reads the number, closes the socket, and Chrome binds it later — a
window in which the port is free. Sequentially that window is small enough not
to matter. In parallel it is not: two launches were handed the same number, and
the second browser failed to bind its debugging port, surfacing as a launch
that came up with no CDP at all. Ports handed out are now remembered, which
removes the half of the race we cause ourselves; the external half is noted
rather than claimed fixed.

The first version of that had a defect worth recording, because an existing
test caught it rather than a person: the release function was written but
**never called from `src/`**, so ports accumulated for the life of the process,
and the only thing bounding them was a backstop that pruned by probing each one
with a TCP connect. That connect lives in `cdp_window.py`, where
`test_cdp_origin_lockdown` requires every CDP socket to suppress its `Origin`
header — and it counted the probe as an un-suppressed CDP client. The test was
right that the file had grown a socket it should not have. Wiring the release
into every teardown path removed the reason the probe existed, so it was
deleted and the test passes unmodified.

**A bug the parallel run exposed.** `Emulation.setLocaleOverride` is per
*browser context* and refuses to be set twice — "Another locale override is
already in effect". It was being sent for every attached target, so every
target after the first got an error back, and on a failed re-apply the
profile's Intl would have fallen back to the host: the exact leak F31 closed.
It was invisible until several targets started attaching at once and it
appeared in the log. Sent once per browser now, with a test.

**Concurrency default left alone, and it is the right one.** The cap stays at
20, which is the confirmed design maximum. Measured on this host, a running
profile costs **130–216 MB** (PSS, shared pages counted once), so 20 concurrent
is ~2.8 GB of this 13 GB machine — comfortable. For contrast, running all 150
at once would be ~21 GB, beyond this hardware regardless of software; storing
150 and running at most 20 of them is what the product is built for.
`MAX_CONCURRENT_PROFILES` still raises it (hard maximum 64) for an operator who
knows their own hardware.

14 tests added.

---

### F32 — ✅ MEDIUM: the consistency engine had no score — **FIXED**

**File:** [`consistency.py`](../src/multiprofile/consistency.py)

The engine already reported PASS/WARNING/ERROR per domain but gave no single
number, so an operator could not rank profiles or notice one getting worse
without reading six messages. A `score` out of 100 is now part of every report,
along with a `score_detail` naming which domains counted, which were skipped
and which were penalised — a low score is always traceable to a check rather
than appearing as a bare number.

Two rules keep it from being a false comfort, and both are asserted:

- **A domain that could not be evaluated is excluded from the denominator**,
  not scored as a pass or a fail. Scoring it as a pass would let a profile with
  nothing configured outrank one with a real setup; scoring it as a fail would
  punish an operator for a check they never asked for.
- **Any ERROR caps the total at 40.** One flat contradiction is not something
  four passes elsewhere should be able to average away.

It is deliberately *not* a claim about being undetected — it says how coherent
the signals we can see are, and nothing about surfaces nobody has checked. That
distinction is written into the module docstring so the number is not read as
something it is not.

A `webgpu` domain was added at the same time, cross-checking the claimed WebGL
renderer against the WebGPU adapter vendor, so the F26 contradiction is caught
before a launch rather than after.

16 tests added.

---

### F31 — ✅ HIGH: every profile claimed New York, from every proxy — **FIXED**

**Files:** [`geo.py`](../src/multiprofile/geo.py) (new),
[`storage/profiles.py`](../src/multiprofile/storage/profiles.py),
[`browser/launcher.py`](../src/multiprofile/browser/launcher.py),
[`browser/cdp_inject.py`](../src/multiprofile/browser/cdp_inject.py)

Three surfaces that have to name one place were set by three different things,
or not at all. Measured with a profile routed through a Brazilian exit:

```
navigator.language           en-US        Intl locale   en-US
timezone                     America/New_York
(1234.56).toLocaleString()   1,234.56
getCurrentPosition()         the host's real surroundings
Accept-Language on the wire  en-US,en;q=0.9
```

`create_profile` stamped `America/New_York` and `en-US` on **every** profile at
creation, before anything knew where its traffic would leave from. So a fleet on
Brazilian, German and Indian exits all claimed the same city — a contradiction
inside each profile, and a value all of them shared.

The geolocation half is the worst of the three: nothing set it, so
`getCurrentPosition` resolved against the host. That is the only surface in the
identity that leaks a physical fact about **the person** rather than the
computer, and it was identical for every profile on the machine.

**The rule** is that the exit IP is the authority — everything else is a claim,
and the address the packets actually came from is not. But a value the operator
*chose* is theirs, so only the creation default is replaced. A `geoAuto` flag
records which it was; a deliberate US persona on a German exit is reported as a
conflict rather than silently rewritten.

Language and position are set at the engine where an anchor exists, the same
move as F22: `--lang` / `--accept-lang` for the first request (before any CDP
session exists), `Emulation.setLocaleOverride` for Intl, and
`Emulation.setGeolocationOverride` for position. Coordinates are the country's
principal city with a per-profile offset of a few kilometres — a fixed point
would be a constant shared by every profile in the country, the same linking
mistake F24 found — and the reported accuracy is wide, because a desktop
answering with a metre-accurate position is claiming a GPS it does not have.

Measured after:

| exit | navigator.language | timezone | number | date | position |
|---|---|---|---|---|---|
| BR | `pt-BR,pt,en` | America/Sao_Paulo | `1.234,56` | `14/01/2024` | São Paulo |
| DE | `de-DE,de,en` | Europe/Berlin | `1.234,56` | `15.1.2024` | Berlin |

with `Accept-Language` on the wire matching in both.

**An instructive intermediate failure.** A first version aligned only the CDP
overrides and not the fingerprint `evasion.js` reads, so the page answered
`pt-BR` to Intl and `en-US` to `navigator.language` **in the same document** —
worse than the leak it replaced. The place is now decided once, before anything
derived from it is built, and passed down rather than recomputed.

A stale-data bug was also caught by a test asserting the two country tables
agree: Vietnam was mapped to `Asia/Bangkok`, which the consistency engine reads
as Thailand, so every Vietnamese exit would have aligned to a timezone the
consistency check then flagged as a conflict.

28 tests added (20 pure, 8 live).

---

### F30 — ✅ HIGH: worker realms disagreed with their own page — **FIXED**

**Files:** [`browser/cdp_inject.py`](../src/multiprofile/browser/cdp_inject.py),
[`extension/evasion.js`](../src/extension/evasion.js)

Worker coverage was two partial mechanisms in `evasion.js`, and it leaked
through the gaps in both.

**A service worker got nothing.** It is registered by URL and fetched by the
browser, so the `Worker` constructor wrapper could not reach it:

```
top document     platform: Win32          hardwareConcurrency: 8
service worker   platform: Linux x86_64   hardwareConcurrency: 16
```

The User-Agent was right in both (a process-wide flag) and so was the timezone
(set on the engine, F22). Only the JavaScript-spoofed values were missing — and
they were missing in **the one realm that outlives the page**. A service worker
persists across navigations and can be messaged by any page in scope, so a
script reading the host's real platform and core count through one gets a
stable cross-profile identifier for free.

**A module worker hung.** It has no `importScripts()`; the wrapper called it
anyway inside a `try/catch`, so the prelude loaded, the real script silently
never did, and the worker answered nothing:

```
bare Chromium 151   classic: Linux x86_64/16   module: Linux x86_64/16
profile (wrapper)   classic: Win32/12          module: TIMEOUT
```

That breaks any site whose bundler emits a module worker, and a browser where
module workers hang is not the Chrome the User-Agent claims.

**A worker loaded from a URL got nothing** from the second mechanism, which
hooked `URL.createObjectURL` and only ever saw blobs — while also being a
broad, observable change to an API that has nothing to do with workers.

**And where that mechanism did apply, the realms still disagreed.** It spoofed
the WebGL strings but never added the canvas noise:

```
page / shared / service   cv: 2097776059
dedicated worker          cv: 2367204759
```

A page could compare its own OffscreenCanvas render against a worker's and see
the difference — the surface meant to make realms agree was what made them
differ.

**The fix** is one mechanism: the injector evaluates a worker identity script
over CDP while the worker is frozen by `waitForDebuggerOnStart`, which lands
ahead of the worker's first line. Both `evasion.js` mechanisms were removed
rather than extended, because having two is what made the canvas noise
inconsistent in the first place.

Dedicated workers are children of a page target and never reach the browser
session's auto-attach, so auto-attach is also armed on each page session —
filtered to `type: worker`, because attaching shared and service workers from
both sessions paused them twice and stopped service workers activating at all.

Measured after, all six realms agreeing on every field:

| | platform | cores | tz | GPU | canvas |
|---|---|---|---|---|---|
| top document | Win32 | 4 | America/New_York | NVIDIA | 2825665054 |
| worker (blob) | Win32 | 4 | America/New_York | NVIDIA | 2825665054 |
| worker (URL) | Win32 | 4 | America/New_York | NVIDIA | 2825665054 |
| worker (module) | Win32 | 4 | America/New_York | NVIDIA | 2825665054 |
| shared worker | Win32 | 4 | America/New_York | NVIDIA | 2825665054 |
| service worker | Win32 | 4 | America/New_York | NVIDIA | 2825665054 |

Two bugs of my own were caught while building this and are worth recording,
since both were the same shape as the finding: the page/iframe dedupe returned
early **without releasing a target waiting for the debugger**, which froze
pages once duplicates became common; and the worker path had no dedupe at all,
so a twice-attached worker had the canvas noise applied twice and hashed
differently from its page.

31 live tests added.

---

### F29 — ✅ MEDIUM: a Windows profile with no speech voices — **FIXED**

**File:** [`extension/evasion.js`](../src/extension/evasion.js)

`speechSynthesis.getVoices()` returned an empty array, in a profile and in
stock Chromium 151 alike — normal for a Linux box with no speech-dispatcher,
impossible for the Windows machine the profile claims to be. Windows ships
Microsoft David and Zira with the OS, so every Chrome on Windows enumerates at
least those. An empty voice list on a `Win32` navigator is a platform
contradiction and one of the cheapest checks a probe can run: one property
access, no permission, no user gesture.

Voices are built against the real `SpeechSynthesisVoice` prototype so
`instanceof` holds. The core set is deliberately **not** randomised per
profile — nearly every Windows install has exactly David and Zira, so identical
lists are what real machines look like, and noise would make profiles less
plausible rather than more. Only the optional language packs vary.

Caught while verifying: the first version marked every local voice whose
language matched as the default, so **three voices claimed to be the default at
once** — a more specific anomaly than the empty list it replaced. Exactly one is
now flagged, and a test holds it.

8 live tests added.

---

### F28 — ✅ MEDIUM: Web Bluetooth missing on a Windows claim — **FIXED**

**File:** [`browser/launcher.py`](../src/multiprofile/browser/launcher.py)

This host's Chromium build ships Web Bluetooth disabled while upstream Chrome
on Windows and macOS does not:

```
default                          navigator.bluetooth  undefined
                                 Bluetooth            undefined
                                 BluetoothDevice      undefined
--enable-features=WebBluetooth   all three present
```

A profile whose User-Agent says Windows and whose navigator has no Bluetooth at
all is describing a browser that does not exist. Turning the genuine interface
family back on beats synthesising it: a fake `navigator.bluetooth` without the
matching constructors trades one contradiction for a more detailed one.

**A hazard this exposed:** Chrome honours the **last** `--enable-features` on
the command line rather than the union of them. Appending a second one would
have silently dropped the `Vulkan` flag F26 relies on — a failure that looks
like it works. One place now composes the list, the GPU args no longer carry a
feature flag of their own, and a test asserts the launch emits exactly one.

6 tests added.

---

### F27 — ✅ HIGH: the Battery API reported the host's battery — **FIXED**

**File:** [`extension/evasion.js`](../src/extension/evasion.js)

`navigator.getBattery()` was untouched, so it answered with this machine's real
state. Every profile returned:

```
charging=false  level=0.28  chargingTime=Infinity  dischargingTime=5545
```

Three things are wrong with that at once:

- It is the host's state, so it is identical across profiles — and unlike a
  static fingerprint it **moves, in lockstep**. Two "different computers" whose
  batteries drain together are one computer, and a drifting shared value links
  them more strongly than a shared constant would.
- It contradicts the claimed machine: these profiles present as desktops with a
  discrete GPU, and no desktop discharges.
- `dischargingTime` is a live counter, so the value is close to unique at any
  instant.

A desktop without a battery reports exactly what the spec describes — charging
true, level 1, chargingTime 0, dischargingTime Infinity — so for a desktop
profile **the honest answer is also the constant one**, and the moving link
disappears rather than being replaced by a different lie. Mobile profiles do
have a battery, so those get a per-profile deterministic level instead.

The real `BatteryManager`'s getters are shadowed rather than a stand-in
returned, so `instanceof` and the change events keep working.

5 live tests added.

---

### F26 — ✅ HIGH: WebGPU contradicted the GPU the profile claims — **FIXED**

**Files:** [`fingerprint/_core.py`](../src/multiprofile/fingerprint/_core.py),
[`browser/launcher.py`](../src/multiprofile/browser/launcher.py),
[`extension/evasion.js`](../src/extension/evasion.js)

WebGL and WebGPU describe the same card in different vocabularies and a page can
read both. One profile said both of these at once:

```
WebGL   ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ...)
WebGPU  requestAdapter() -> null
```

No Windows machine with an RTX 4060 has no WebGPU adapter. **The null was ours,
not the host's.** Window placement needs `--ozone-platform=x11`, and that leaves
Dawn without an adapter:

```
x11                            requestAdapter() -> null
wayland                        {vendor: intel, architecture: gen-12lp}
x11 --enable-features=Vulkan   {vendor: intel, architecture: gen-12lp}
```

Vulkan buys the adapter back without giving up the window control.
`MULTIPROFILE_WEBGPU=0` opts out for a host whose Vulkan stack misbehaves.

And where an adapter *was* returned it described the host truthfully and
identically for every profile — `intel` / `gen-12lp` on this laptop — so the
surface linked every profile on the machine to every other as well as
contradicting the WebGL claim.

The adapter identity is now derived from the renderer string the profile
already claims, so the two vocabularies cannot drift apart: an RTX 4060 is
`nvidia`/`ada`, a GTX 1660 is `nvidia`/`turing`. An unrecognised card claims
nothing rather than inventing an identity that would disagree.

Only the four identity fields are replaced; the adapter is the real one, so
limits, features and `requestDevice()` keep working. A synthesised adapter that
failed on first use would be a louder tell than the one it replaced, and a live
test asserts a device can still be created.

Also closed an iframe gap this found: `applyAudioSpoofs` and `applyRectSpoofs`
were added after the iframe realm hook was written and were never wired into
it, so an audio or rect fingerprint taken inside a frame returned the host's
while the same read in the top document returned the profile's.

18 tests added.

---

### F25 — ✅ HIGH: a Windows profile enumerated this host's Linux fonts — **FIXED (residual documented)**

**Files:** [`browser/fontconfig.py`](../src/multiprofile/browser/fontconfig.py) (new),
[`browser/launcher.py`](../src/multiprofile/browser/launcher.py),
[`extension/evasion.js`](../src/extension/evasion.js)

Font enumeration was unaddressed. Measured on this host, a profile reporting
`navigator.platform === 'Win32'` and `sec-ch-ua-platform: "Windows"`:

| | |
|---|---|
| Windows fonts found | **0** — no Calibri, Segoe UI, Tahoma, Consolas |
| Linux fonts found | **13** — including `C059`, `P052`, `Z003`, `D050000L`, `Standard Symbols PS` |

Those five are the URW base-35 faces ghostscript installs. They are on no
Windows machine. A missing Calibri is weak evidence about an unusual install;
those five names are proof that a self-declared Windows box is a Linux one.

The existing hook made this worse rather than better. `evasion.js` picked one
or two names from a hardcoded eight-font pool and forced `measureText` to fall
back for them — so a "Windows" profile hid *Georgia* while announcing
`D050000L`.

**Why this was not fixed in JavaScript.** Measured here, canvas and DOM font
detection agree exactly, so hooking only `measureText` would have introduced a
*new* contradiction. The DOM half cannot be moved honestly: reporting a width
the engine did not lay out desynchronises measurement from rendering on any
page that uses the font for real.

Chromium resolves local fonts through fontconfig, which honours
`FONTCONFIG_FILE`. Measured against stock Chromium 151:

```
DEFAULT     [C059, P052, Z003, D050000L, Standard Symbols PS, Nimbus Roman,
             Nimbus Sans, Liberation Sans, Noto Sans, Arial, Times New Roman]
RESTRICTED  [Liberation Sans, Noto Sans]
```

So each launch now writes a per-profile fontconfig and points the child at it.
Every surface agrees for free — canvas, DOM, `document.fonts`, workers, and
what is actually painted — because none of them is lying. Same move as F22:
anchor at the engine where an anchor exists. The superseded `evasion.js`
section was removed, not extended.

Measured after:

| | before | after |
|---|---|---|
| ghostscript faces | 5 | **0** |
| distribution faces (DejaVu, Nimbus, Cantarell, …) | 8 | **0** |
| core Windows families | 0 | **3** (Arial, Times New Roman, Courier New) |
| canvas/DOM disagreements | 0 | **0** |

Cost: 53 ms per launch. Claims are honest — a name is claimed only where a
metric-compatible file can back it (Liberation for the core three), and the 13
that cannot be backed on this host are left absent and recorded as an identity
fault rather than faked with the wrong metrics.

**Residual, not closed.** The backer's own name stays enumerable. fontconfig
renaming governs *matching*, but Chrome reads a face's names from the font
file's internal `name` table:

```
fc-list   Courier New      | Courier New | CourierNewPSMT
Chrome    Liberation Mono  | LiberationMono
```

So the three Liberation names remain visible. This is a strict improvement —
absence of Calibri is weak, `D050000L` was proof — but it is not closure.

**Decision needed.** Closing it requires the claimed names to be genuine in
the file, which is packaging, not code:

1. **Ship metric-compatible fonts** (`carlito`, `caladea`, `gelasio`, and the
   Chrome OS core set `arimo`/`tinos`/`cousine`). This raises the credible
   Windows claim from 3 families to ~8 and is legally clean, since those
   fonts are redistributable under their own names. It adds package
   dependencies, which cannot be verified here because `build-deb.sh` does
   not run on this machine.
2. **Rewrite the font binary's `name` table** so the file itself says Arial.
   This closes the residual completely but puts a third party's trademark
   into a file this product ships. Aliasing Arial to Liberation Sans is what
   every Linux distribution already does in `30-metric-aliases.conf`;
   asserting a file *is* Arial is a different act and needs legal review.

Option 1 is the recommendation. Neither was taken here.

31 tests added (25 policy, 6 live). Suite: 1423 passed, 5 skipped.

---

### F24 — ✅ HIGH: AudioContext and ClientRects linked every profile — **FIXED**

**File:** [`extension/evasion.js`](../src/extension/evasion.js)

Both surfaces were entirely unhooked. `evasion.js` opens by claiming
*"Canvas & AudioContext noise injection"*; `AudioContext` appeared in that
header comment and **nowhere else in the file**. `getClientRects`,
`getBoundingClientRect` and `DOMRect` had zero references anywhere in the repo.

Both are computed by the host — the audio stack and the rendering stack — so
every profile returned the same answer. Measured, two profiles with different
seeds, claiming a GTX 1660 and an RTX 3060 respectively:

```
seed 111   audio 172.6716509661   rect 10.093750,20.187500,30.296875,40.390625
seed 222   audio 172.6716509661   rect 10.093750,20.187500,30.296875,40.390625
```

Identical to twelve decimal places, while canvas and WebGL differed per profile.

**This is worse than either profile being identified alone: it links them to
each other.** Two "different computers" that share an audio fingerprint and
sub-pixel text metrics exactly are one computer, and cross-profile linkage is
the specific failure an anti-detect product exists to prevent. A site does not
need to know *who* a profile is to know it is the same operator as the last
fifty.

> **Resolved.** Both surfaces get per-profile deterministic noise, seeded the
> same way canvas already is. The properties that matter are not "is it noisy"
> but:
>
> - **stable** — the noise is a pure function of `(seed, index, original
>   value)`, so a rect re-read gives the same answer. Drag-and-drop, sticky
>   headers and hit-testing all read a rect more than once; a value that
>   drifted would break real sites *and* be a louder tell than the shared one
>   it replaced.
> - **reproducible** — a freshly rendered `OfflineAudioContext` agrees with the
>   first, the way a real machine does. Per-render randomness would defeat
>   linkage and be obvious to any site that renders twice.
> - **small** — ~1e-7 audio amplitude (inaudible, no effect on playback) and
>   ~1e-3 of a pixel on rects, which is below Chrome's own 1/64 px layout
>   quantisation and disturbs no layout logic.
>
> ```
> seed 111   audio 172.6716513248   rect 10.093502,20.187662,30.297356,40.390277
> seed 222   audio 172.6716503918   rect 10.094142,20.187091,30.296750,40.390467
> ```
>
> Verified: 7 live tests in `tests/test_audio_rects_live.py`, which assert the
> two profiles differ, that each is stable across reads, and that the audio
> value is reproducible across renders — the third being the one that is easy
> to get wrong.

**WebGPU: unverified here, not fixed.** `navigator.gpu` still has zero
references, so an adapter would report the host. On this machine
`requestAdapter()` returns **no adapter** under the launch configuration, so the
leak could not be reproduced and has not been fixed on guesswork. It needs
re-measuring on a desktop session with GPU acceleration before it is worth
hooking — noted so the gap is not mistaken for coverage.

---

### F23 — ✅ HIGH: the media-device spoofing was the leak — **FIXED**

**Files:** [`extension/evasion.js`](../src/extension/evasion.js), [`fingerprint/_core.py`](../src/multiprofile/fingerprint/_core.py), [`fingerprint/media.py`](../src/multiprofile/fingerprint/media.py)

`enumerateDevices` was rewritten unconditionally: every device got a stable
64-char id derived from the profile seed, whatever the browser had actually
returned. Measured against stock Chromium 151 on the same loopback page, no
media permission granted:

```
BARE:  audioinput / videoinput / audiooutput   deviceId=''           label=''  groupId=''
OURS:  all three                               deviceId='a2118a3d…'  label=''  groupId=''
```

Chrome **withholds** device ids until permission is granted. Two consequences,
and both make a profile easier to identify than leaving the surface untouched:

1. **A state no real browser produces.** An id beside an empty label and an
   empty groupId does not happen: without permission Chrome gives you nothing,
   with permission it gives you all three. The combination is the tell.
2. **A volunteered cross-site identifier.** The id is stable for the life of
   the profile, so any site could read it without asking for anything — while
   the host's own behaviour leaked precisely nothing here. The spoofing was the
   only thing leaking.

And the three ids were **identical** — all from `_rng(seed, 20)` — so a
microphone, a camera and a speaker shared one identifier, which no browser has
ever done.

> **Resolved.** The hook now substitutes only what the browser is already
> exposing: a real id is replaced with a seeded one, an empty id stays empty.
> Chrome's reserved `default` and `communications` ids are passed through
> untouched, because renaming them breaks `getUserMedia` and is a tell in
> itself. The three kinds get distinct ids (salts 20/21/22), and `groupIds` are
> now derived too — the audio pair sharing one group and the camera holding its
> own, which is the shape of an ordinary machine.
>
> After the fix, the profile matches the bare browser exactly:
> `deviceId=''  label=''  groupId=''`.
>
> **Still open: labels under permission.** Once a site is granted camera or
> microphone access, `label` still passes through as the host's real hardware
> name. Fixing that needs a per-platform device catalogue rather than a filter —
> "Default - MacBook Pro Microphone" on a Windows profile would simply be a
> different contradiction — so it is left as P1 work rather than patched badly.
>
> Verified: 7 tests in `tests/test_media_devices.py`, including a live one that
> asserts a profile volunteers no id where the host would withhold it.

---

### F22 — ✅ HIGH: the timezone was a set of patched methods, not the engine's — **FIXED**

**Files:** [`browser/cdp_inject.py`](../src/multiprofile/browser/cdp_inject.py), [`browser/launcher.py`](../src/multiprofile/browser/launcher.py)

A profile's timezone existed only as JavaScript. `evasion.js` hooks
`Intl.DateTimeFormat`, `resolvedOptions`, `Date.prototype.getTimezoneOffset`,
`toString` and `toTimeString` — and nothing else. Every surface it did not think
of reported the host's zone. There was no `Emulation.setTimezoneOverride` and no
`TZ` in the browser's environment; `launcher.py` set only `OZONE_PLATFORM`.

Measured on this machine (host `Asia/Kolkata`, profile `America/New_York`),
inside a single document:

```
Date.prototype.toString        04:58:55 GMT-0400 (Eastern Daylight Time)
Date.prototype.toLocaleString  9/28/2026, 2:28:55 PM
```

Nine and a half hours apart — exactly Kolkata (+5:30) against New York (−4:00).
A page needed **one** unhooked method to catch the profile contradicting itself,
and `toLocaleString` is not an obscure one. `toLocaleTimeString` and
`toLocaleDateString` were equally open.

This is the F1 shape again in a different vector: not weak spoofing, but an
*internal contradiction* that marks the profile more clearly than doing nothing.
And it is the vector where a contradiction matters most, because IP↔timezone
agreement is the first thing a fraud system checks.

> **Resolved.** The timezone is set on the engine, not on methods:
> `Emulation.setTimezoneOverride` per attached target, plus `TZ` in the browser
> process's environment so the process itself — not only its renderers — agrees.
> Every surface then matches by construction, including the ones nobody has
> thought of yet. The JS hooks stay as a fallback for when the override cannot
> be applied, and they compute from the same zone, so the two cannot disagree.
>
> Patching one method at a time cannot win this game: the set of surfaces that
> expose local time is open-ended, and each new one is a silent regression. That
> is the argument for the engine-level anchor wherever an engine-level anchor
> exists — the same reasoning behind F5's version alignment.
>
> After the fix, same document:
>
> ```
> toString       05:00:34 GMT-0400 (Eastern Daylight Time)
> toLocaleString 9/28/2026, 5:00:34 AM
> worker         America/New_York, offset 240
> ```
>
> `InjectionStatus` gained `timezoneApplied`, so the console can say whether the
> engine took it rather than assuming.
>
> Verified: 7 live tests in `tests/test_timezone_live.py`. They assert that the
> surfaces **agree with each other** rather than asserting exact strings, because
> the defect was never what any one of them said.

**Still open (5.4 item 14): the timezone does not follow the proxy.** The egress
country and zone are measured (`network.py`, `_ECHO_GEO_URL`) and
`consistency.py` warns when they disagree with the profile — but nothing ever
writes `profile["timezone"]`, so the operator is told about a mismatch they have
to fix by hand. `DevicePreset.timezone_policy = "auto"` reaches
`MobileProfileConfig.timezone` as the literal string `"auto"` and is never
emitted. Auto-alignment is a feature, and it needs a decision about whether it
overwrites an operator's explicit choice.

---

### F20 — 🟡 MEDIUM: the durable job queue has no producer — **VISIBLE, WRITER FILED**

**Files:** [`api/server.py`](../src/multiprofile/api/server.py), [`jobs/`](../src/multiprofile/jobs/)

Two job generations coexist, and the migration between them was never finished.
`POST /api/jobs` validates against the **legacy** enum
(`launch_profile | stop_profile | bulk_launch | bulk_stop`) and writes to the
in-process queue. The durable SQLite queue starts its workers at boot and
`recover()` re-queues anything a crash left running — but the only `enqueue()`
call site in `src/` is inside `parking.py`, whose own callers
(`schedule_health_checks`, `park_profile`) have no caller in the application.

So roughly 1,500 lines — `parking.py`, and the `park` / `restart` /
`consistency_check` handlers — are code and config that nothing reaches, while
workers spin against an empty table. The console's "Park" button does
`stop` + `PUT status=parked` instead.

> **Half resolved.** `GET /api/jobs` now lists **both** queues. Durable rows are
> normalised into the shape the console already reads — the two were never given
> a common one (legacy: `type`, epoch `createdAt`, string id; the jobs table:
> `job_type`, ISO `created_at`, integer id) — and durable ids are prefixed
> `durable:` so the two can never collide. Either queue failing is logged and
> skipped rather than blanking the page.
>
> This matters even before the writer moves: parking *can* create a durable job
> and crash recovery *does* re-queue into that table, and until now no client
> could see either.
>
> **Filed, not done: moving the writer.** `POST /api/jobs` still targets legacy,
> deliberately. A bulk API type maps to N durable jobs, cancel semantics differ
> between the two, and the console's jobs page changes shape — that is a feature
> with a migration, not an audit fix, and it should land with the decision about
> whether idle-parking is on by default. Until it does, the second queue stays a
> recovery mechanism with no ordinary producer.
>
> Verified: 7 tests in `tests/test_jobs_listing.py`, including that one queue
> failing does not hide the other, and that `POST` still targets legacy.

---

### F11 — 🟡 MEDIUM: 138 failure-swallowing handlers — **IDENTITY PATH DONE**

`pyproject.toml` deliberately deselects `BLE001` and `S110`, documenting why.
Current count: **103 blind-except + 35 try-except-pass**.

The rationale (a launch path must never crash a browser) is legitimate. The cost is
that this product's value *is* surfacing failures, and F1 is precisely a failure
that was swallowed and reported as success. The exception policy and the
silent-failure bug are the same problem seen twice.

**Fix:** not a blanket cleanup. Classify: handlers on the identity/security path
must record a structured failure that reaches `/api/health` and the launch notes;
cosmetic paths can keep `pass` with a comment.

> **Resolved for the identity path.** New
> [`browser/identity_faults.py`](../src/multiprofile/browser/identity_faults.py) is
> the sink: a bounded, thread-safe registry of `IdentityFault` records, each a
> stable `code`, a one-line `detail`, and the `where` that swallowed it — because
> the whole problem is that the failure used to be invisible from outside that
> function. The shape follows `engine.py`'s `source: "unavailable"` record, which
> was already doing this for one case; this generalises it rather than redoing it.
>
> A fault reaches the operator in both places the finding asks for:
>
> - **`/api/health`** gains an `identity` block — `faults`, `faultCount`,
>   `byCode`, `truncated`. It is always present, because `faults: []` is a
>   statement ("nothing has been swallowed since this process started") and a
>   block that appeared only on trouble could not be used to confirm the
>   absence of it.
> - **The launch notes.** `launch_profile` clears the profile's faults at the
>   start, so a launch reports its own and not the last one's, and sweeps in any
>   fault raised elsewhere on the identity path before the wire assertion runs.
>
> Recording is `log.warning`, not `log.debug`: an identity step that did not
> happen is not a detail. And `record()` cannot raise, whatever it is handed —
> it is called from inside `except` blocks, and a recorder that could throw would
> turn a swallowed failure into a crash, which is worse than the bug it reports
> and is how this policy would get reverted.
>
> Call sites converted, chosen for consequence rather than count:
>
> | Code | Was | Consequence it was hiding |
> |---|---|---|
> | `preset_unknown` / `preset_unreadable` / `preset_unserializable` | `return None` | a profile naming a `deviceId` built **without that device** — F8's story in code |
> | `preset_config_unreadable` | `log.debug` | falls back to the pool UA, so the profile does not claim the device it was given |
> | `preset_hints_unreadable` | `log.debug` | UA from the preset, hints from the fingerprint — the exact UA/hints disagreement F5 exists to prevent |
> | `inject_connect_failed` | warning only | no hooks, no proxy auth, no cookies reached the browser |
> | `targets_unenumerable` | `log.debug` | already-open pages went uninjected, i.e. browsing as the host |
> | `cdp_session_unavailable`, `device_emulation_incomplete`, `ua_override_failed`, `identity_session_error` | note only | now also visible process-wide, not just in one profile's notes |
>
> Left alone deliberately: `PinnedCdpSession.close()`'s `pass` (a socket that
> will not close changes no identity), retry loops that record on final failure,
> and everything off the identity path. F11 remains open for those; what is
> closed is the part where a profile's identity could be quietly wrong.
>
> Verified: 18 tests in `tests/test_identity_faults.py`, covering the record, the
> bound, per-profile scoping, both sinks, and the real call sites. One of them
> pins a bug found while writing this: the note sweep first skipped every fault
> whose `where` began with `browser.launcher`, on the assumption that the
> launcher notes its own — but the two faults in `_resolve_wire_identity` are
> launcher-scope and have no notes list to write to, so that rule hid them
> completely. The sweep now skips only the exact scope the note-writing helper
> stamps.

---

### F12 — 🟡 MEDIUM: distribution is not production-ready

No signing, no auto-update, no crash reporting, Debian-only, and `build-deb.sh`
**cannot run on the primary dev machine** (exit 127, no `dpkg-deb`). The packaging
path is exercised only in CI. For a paid desktop app in a category where users are
security-sensitive, unsigned unverifiable binaries are a hard blocker.

---

### F13 — ✅ MEDIUM: tiling was X11-bound and failed opaquely — **FIXED**

**File:** [`browser/work_area.py`](../src/multiprofile/browser/work_area.py)

All three work-area detectors were X11 tools — `xrandr`, `_NET_WORKAREA` via
`xprop`, and `xdpyinfo`. The code correctly **refused a silent 1920×1080
fallback**, which was good engineering, but the consequence was that tiling —
a genuinely good differentiator — simply stopped working with no explanation
beyond "set an environment variable".

**Reproduced on this project's own development host** (Arch, Hyprland,
Wayland):

```
xrandr_primary: unavailable
_NET_WORKAREA:  unavailable
xdpyinfo:       unavailable
no reliable work area (set MULTIPROFILE_WORKAREA=x,y,w,h)
```

`xrandr` and `xdpyinfo` were **not installed at all**, so XWayland was never
going to help. Tiling was dead on the machine the product is developed on.

**Three Wayland detectors**, tried before the X11 chain when the session is
Wayland — the compositor's own answer beats XWayland's view of it, and on a
host with no X11 utilities it is the only answer there is:

| Detector | Reports |
|---|---|
| `hyprctl monitors -j` | geometry, `scale`, and `reserved` as [left, top, right, bottom] |
| `swaymsg -t get_outputs` | a logical `rect`; sway does not expose reserved edges |
| `wlr-randr --json` | geometry and scale for generic wlroots compositors |

Two details that would otherwise be wrong. **`reserved` is subtracted** — the
Wayland equivalent of `_NET_WORKAREA`, without which every tiled window sits
under the status bar. And **scale is converted to logical pixels**, because
windows are positioned in logical coordinates and ignoring a 2× display would
place every window in the top-left quadrant.

Measured after, on the same host: `{x: 0, y: 26, w: 2560, h: 1574,
source: "hyprctl"}` — the full panel minus its 26-pixel top bar.

**The failure now explains itself.** A Wayland session with none of the three
tools installed says so, and names them, instead of only naming the remedy:
"set this variable" does not tell a Wayland user that the three things that
failed were X11 utilities they do not have and do not want. Nothing invents a
size — a wrong work area tiles windows off-screen, which is worse than not
tiling.

26 tests added. Three existing tests in `test_work_area.py` began failing on a
Wayland host, because they patch the X11 detectors and assume nothing else can
answer. They were **environment-dependent all along** and passed only by
accident of where they ran; they now pin `XDG_SESSION_TYPE=x11` so they test
the chain they were always about. No assertion changed.

---

### F14 / F15 — ✅ LOW: a noisy build that phoned home — **FIXED**

**Files:** [`frontend/next.config.ts`](../frontend/next.config.ts),
[`frontend/package.json`](../frontend/package.json)

- **F14:** `npm run build` printed *"Next.js now collects completely anonymous
  telemetry"*. Build-time only and not user data, but a product whose own
  selling point is that it makes **no network requests of its own** should not
  phone home to build itself. `NEXT_TELEMETRY_DISABLED=1` is now set in the
  `build`, `dev`, `export` and `publish` scripts — in the repository rather
  than via `next telemetry disable`, which writes to each developer's global
  config and so does not travel with the code.

- **F15:** `next.config.ts` declared `rewrites`, which `output: "export"`
  ignores, so every production build printed two warnings saying the rewrite
  would not apply. Both were true, and a build that always prints warnings
  trains whoever runs it to skim past them — which is how a real warning gets
  missed.

  The rewrite could not simply be deleted: it proxies `/api/*` to Flask on
  `127.0.0.1:8765` and is what makes `npm run dev` talk to real data. The
  config is now a function of the build phase, so dev declares the rewrite and
  the export build declares `output: "export"`, and neither declares the other.

Verified: `npm run publish` completes with **no warnings**, and `next dev`
boots and answers `/api/health` with a 502-class error from the unreachable
Flask upstream rather than a 404 — which is the proof the rewrite is still
live, since without it that path would not route anywhere at all.

---

---

### F18 — ✅ CRITICAL: every profile in a pool used the same proxy — **FIXED**

**File:** [`network.py:_network_config_from_pool`](../src/multiprofile/network.py)

Found by re-reading the pool path after F17, and measured before the fix:

```
5 profiles in a 3-member STICKY pool:   all five  → 10.0.0.1
assignments recorded:                    {}
3 profiles in a 2-member ROTATE pool:   all three → 10.1.0.1   (cursor: 0)
```

`choose_member` is deliberately **pure** — "no disk write, no network" — so that
reading a profile's route stays a read. The writer, `pools.assign()`, was written
for the launch path and then never called by it: the only route in was
`POST /api/pools/<id>/assign`, which no UI code and no launch touches.

So the pool never learned anything. `assignments` stayed `{}` forever and `cursor`
never moved, which meant the "least loaded" tie-break scored **every** member at
zero and picked the first one. Consequences, all of them silent:

- every profile in a pool egressed through **one member**;
- `strategy: "rotate"` never rotated;
- a profile did not keep its IP across launches (sticky was a no-op);
- `pool_usage()` and the pools UI's `profileCount` always read 0.

This is worse than a missing feature. An operator who deliberately spreads 50
profiles across a pool to keep them apart got 50 profiles sharing one IP — which
**correlates them**, the single outcome a proxy pool exists to prevent. It is the
same shape as F17, except F17 needed a broken pool to trigger and **this was the
happy path**.

> **Resolved.** `network_config_for_profile` takes `commit: bool = False`, and
> `launch_profile` is the only caller that passes `True` — a launch commits the
> decision, a look never does. That distinction is load-bearing: the function is
> also called by the health poller, the diagnostics route and
> `/api/profiles/<id>/network-health`, and a rotate pool that advanced once per
> *read* would hand a profile a different egress every time anyone opened its
> page. `pools.assign()` was already correct and is now simply called.
>
> ```
> STICKY, 6 profiles:  10.0.0.1 10.0.0.2 10.0.0.3 10.0.0.1 10.0.0.2 10.0.0.3
> same profiles again: identical (stickiness holds across launches)
> plain reads:         assignments unchanged
> ROTATE, 4 launches:  10.1.0.1 10.1.0.2 10.1.0.1 10.1.0.2
> reads in between:    do not advance the cursor
> ```
>
> Verified: 11 tests in `tests/test_pool_assignment.py`, including that an empty
> pool still blocks rather than going direct (F17's rule) and that a disabled
> member is never assigned.

---

### F19 — ✅ HIGH: the WebRTC control did nothing — **FIXED**

**File:** [`api/validation.py`](../src/multiprofile/api/validation.py), [`network.py:_map_webrtc`](../src/multiprofile/network.py)

Two vocabularies in one codebase, with nothing translating. `WebRTCPolicy`,
`_map_webrtc`, `fingerprint/_core.py` and `evasion.js` all speak
`default | disable | disable_non_proxied_udp`. The API validator alone accepted
`default | disabled | proxy`. Measured:

```
disabled  -> default      proxy -> default
disable   -> disable      disable_non_proxied_udp -> disable_non_proxied_udp
```

Both non-default values a client could send fell through to `DEFAULT`: **no Chrome
flags, no JS hardening**, and a privacy control that reported itself set while
doing nothing. For an anti-detect product WebRTC leak protection is a headline
feature, so this is a claim the product did not honour.

F8 made it worse rather than better: tightening field validation turned the two
words that *do* work into 400s, so the setting was not merely inert — it was
**unreachable**. A good fix (a field registry) hardened a broken contract in
place, which is the risk of validating a vocabulary without checking who consumes
it.

> **Resolved.** One vocabulary, `normalize_webrtc_mode()` in `network.py` (which
> owns `WebRTCPolicy` and therefore owns the words). The console's spellings stay
> as aliases — `disabled → disable`, `proxy → disable_non_proxied_udp` — so
> profiles **already stored** with them start being honoured instead of quietly
> staying open. Unknown values normalise to `default`, the only safe direction: a
> typo must never silently weaken a profile.
>
> ```
> spelling                   -> canonical                -> chrome flags
> 'disabled'                 -> disable                  -> 3
> 'proxy'                    -> disable_non_proxied_udp  -> 2
> 'nonsense'                 -> default                  -> 0
> and the JS half: 'disabled' -> fingerprint webrtcMode='disable'
> ```
>
> Verified: 28 tests in `tests/test_webrtc_mode.py`. One asserts the two halves
> **cannot disagree** — flags present iff the injected script is hardened — because
> fixing one side and not the other is exactly how this survived.

---

### F17 — ✅ CRITICAL: an unresolvable route launched on the host IP — **FIXED**

**File:** [`browser/launcher.py:712`](../src/multiprofile/browser/launcher.py#L712)

Found while fixing F16, and the more serious of the two by a wide margin.

`NetworkConfig` records a route that was required but could not be resolved as
`blocked_reason`, on a **DIRECT-looking** config — `mode=DIRECT`, `proxy=None`.
That shape is deliberate and the code says why: `needs_proxy_check()` is False
when `proxy` is None, so a proxy mode carrying no proxy would sail through the
gate. `check_proxy_before_launch` honours `blocked_reason` and checks it *first*,
with a comment noting that DIRECT-looking configs reach it too.

The launcher then called that gate like this:

```python
if settings.proxy_check_enabled and net.needs_proxy_check():
    allowed, block_reason, proxy_health = check_proxy_before_launch(net, …)
```

`needs_proxy_check()` is False for every blocked config, so **the only code that
reads `blocked_reason` sat behind a condition a blocked config can never
satisfy** — and `proxy_check_enabled` could switch it off for everything.
Measured, a profile assigned to a pool with no enabled member:

```
resolved : mode=DIRECT blocked_reason="Pool 'dead-pool' has no enabled member (pool is empty)"
LAUNCH ok : True | error: None
proxy flag: (NONE — browsing from the host IP)
notes     : "Network mode: DIRECT"
            "identity verified on the wire: sec-ch-ua … Windows …"
```

The browser launched on the host IP, the launch was reported healthy, the note
presented DIRECT as the intended mode, and the wire assertion confirmed the
spoofed identity — which is exactly the combination that makes it invisible. For
this product that is the worst possible failure: the fingerprint is perfect and
the IP is the operator's own. `run_diagnostics` had the matching fail-closed
branch and could report the truth, but nothing consulted it at launch either.

The pool code was right all along. Its docstring states that if a pool cannot
supply a member the result is blocked, "never DIRECT — falling back to the host
IP is the one outcome a proxy pool must never produce." The guard was correct and
unreachable.

> **Resolved.** `blocked_reason` is now checked unconditionally, before the probe
> branch and outside `proxy_check_enabled` — disabling the proxy *probe* is a
> choice about network calls at launch, not consent to browse from the host IP
> when a route was demanded. The launch returns
> `errors: ["route_unresolved"]`, records `ProfileState.ERROR`, audits
> `launch_blocked_route_unresolved`, and releases the concurrency slot it had
> already taken (a leaked slot would burn the launch budget on launches that
> never happened, and there is a test for that specifically).
>
> ```
> LAUNCH ok : False
> error     : Pool 'dead-pool' has no enabled member (pool is empty)
> errors    : ['route_unresolved']
> with MULTIPROFILE_PROXY_CHECK_ENABLED=0 -> still refused
> ```
>
> Verified: `tests/test_route_fail_closed.py`, which asserts the measurement
> above and the probe-disabled case, and checks that the ordinary dead-proxy
> health path still reports normally rather than being shadowed by the new check.

---

### F16 — 🟡 HIGH: two wizard steps configure nothing — **ROUTING FIXED**

**Files:** [`frontend/src/components/wizard/wizard-dialog.tsx:106`](../frontend/src/components/wizard/wizard-dialog.tsx#L106),
[`network.py:551`](../src/multiprofile/network.py#L551)

Found while fixing F8, which is also why it went unnoticed: the API accepted these
keys, persisted them, and returned them on every subsequent read, so from the
frontend's side the settings looked applied.

The wizard's create payload sends three keys that **no backend code reads**:

| Key sent | What the backend actually reads | Status |
|---|---|---|
| `fingerprintStrategy` (`stable` / `preset` / `custom`) | nothing — zero references in `src/` | dead |
| `networkMode` (`direct` / `gateway` / …) | nested `networkConfig.mode`, upper-case enum (`DIRECT`, `REMOTE_GATEWAY`, …) | dead |
| `gatewayId` | only `storage/pools.py` for a **pool member**; a profile routes via `poolId` or `networkConfig` | dead |

`fingerprintStrategy` is a whole wizard step with three selectable cards
(`wizard-dialog.tsx:592`) and a line in the confirmation summary (`:791`). The
operator picks a fingerprint strategy, the summary confirms it, and nothing
downstream has ever consulted it — the fingerprint is derived from the profile
seed and its device preset either way.

The network step is worse than dead for one of its branches. The proxy branches
happen to work because the wizard *also* sends the legacy flat `proxyHost` /
`proxyPort` / `proxyType` fields, which `NetworkConfig.from_legacy_profile` does
read. `direct` and `gateway` send only `networkMode` (+ `gatewayId`), so they fall
through to legacy inference on empty proxy fields. Measured:

```
wizard: gateway  -> mode=DIRECT  blocked=None
wizard: direct   -> mode=DIRECT  blocked=None
wizard: socks5   -> mode=SOCKS5  blocked=None
```

**A profile the operator created as "gateway" browses from the host IP, and
nothing says so.** `blocked_reason` is `None`, so it is not reported as degraded
anywhere — it is indistinguishable from a profile deliberately set to direct.

`network_config_for_profile` does guard this case, and its docstring is explicit
that "falling back to the host IP is the one outcome a proxy pool must never
produce" — but that guard is on the `poolId` path, and the wizard's gateway branch
never sets `poolId`. The safeguard is real and the wizard walks around it.

For a product whose entire value is that a profile's route is what you told it to
be, this is the same class of defect as F1, and the same severity: a setting
reported as applied that silently is not. It is listed HIGH for that reason, not
for the dead fingerprint step beside it.

> **Routing resolved.** The console's own route vocabulary is now translated
> where every other route is resolved, in `network_config_for_profile`:
>
> - `_requested_network_mode` reads the operator's request in whatever spelling
>   it arrives — the wizard's lower-case `networkMode`, the nested upper-case
>   `networkConfig.mode`, or a bare `gatewayId`. The *absence* of a preference is
>   kept distinct from a request for `direct`, because only the second is a
>   statement, and only an unmet statement may block.
> - `_network_config_from_gateway` resolves a profile's gateway to an endpoint
>   from `gateways.json`, mirroring `_network_config_from_pool` including
>   `ProxyFallback.BLOCK` and its refusal to fall back. It reads the gateway with
>   `safe=False`, since a masked password would reach Chrome as `***`. A gateway
>   that is missing, unnamed or endpoint-less blocks.
> - `_refuse_silent_direct` generalises the rule to every route: **if the profile
>   asked for something other than DIRECT and DIRECT is what came back, the
>   request did not resolve.** That also closes a case nobody had filed —
>   `networkMode: "socks5"` with empty proxy fields was equally silent.
>
> `networkMode` and `gatewayId` moved from `PROFILE_FIELDS_UNIMPLEMENTED` into the
> honoured tier, with `networkMode` validated against the console's vocabulary and
> `gatewayId` required when the mode is a gateway — refused at the door rather
> than at launch:
>
> ```
> POST /api/profiles {"name":"wiz","networkMode":"gateway"}
> → 400 field=gatewayId "required when networkMode is a gateway: name the gateway…"
>
> POST /api/profiles {"name":"wiz","networkMode":"gateway","gatewayId":"gw_7391ddcf"}
> → 200 warnings: []
>   resolved -> REMOTE_GATEWAY | proxy: socks5://10.0.0.9:1080
>   launch   -> refused: pre-launch health check failed (gateway unreachable),
>               "Launch blocked to prevent host IP exposure"
> ```
>
> No frontend change was needed: the wizard's `NetMode` vocabulary
> (`direct | http | https | socks5 | gateway`) is accepted in full, so every
> branch it can produce now either resolves or is refused, and none of them can
> reach a silent DIRECT. Verified in `tests/test_route_fail_closed.py`.
>
> This fix is only load-bearing because **F17** was fixed with it: blocking a
> route means nothing while the launcher never reads `blocked_reason`.

> **`fingerprintStrategy` — ✅ resolved by removal.** The fingerprint is derived
> from the profile seed and its device preset, so `stable` was the only one of the
> selector's three options that ever meant anything; `preset` and `custom` were a
> promise the product could not keep. Rather than leave a control that implied
> three, the wizard step was deleted — the payload key, the three-card selector
> (`wizard-dialog.tsx`), and the "Strategy" line in the confirmation summary.
>
> `PROFILE_FIELDS_UNIMPLEMENTED` is now **empty**, which is its goal state: it
> held three keys when F8 landed, `networkMode` and `gatewayId` left when the
> route was wired up, and this was the last. A key in that tier is an admission
> that the console sends something nothing honours, so a test asserts it stays
> empty and reads any new entry as a defect report.
>
> One consequence worth stating: a client still sending `fingerprintStrategy` now
> gets a **400** rather than a 200 with a warning, because the key is simply
> unknown and F8's rule applies. That is the intended behaviour — it is the
> difference between being told and being ignored — but it is a contract change
> for anything scripted against the old spelling.

---

## 4.1 Code quality & architecture

| Item | Status | Note |
|---|---|---|
| Separation of concerns | ✅ | clean api/browser/storage/fingerprint split |
| Error handling | 🟡 | F11 — 138 swallowing handlers. F1 was the consequence; the identity path now asserts instead of swallowing, and what it cannot assert it **records** as a fault reaching `/api/health` and the launch notes |
| Logging | 🟡 | works; no structure, no rotation, no crash reporting |
| Crash recovery | ✅ | `db.recover()` at startup; supervisor watchdog (F10 fixed) |
| Hardcoded secrets | ✅ | scan clean |
| Dead code | ✅ | F2 fixed — the extension staging path is deleted, not just documented |
| TODOs/FIXMEs | ✅ | **zero** |
| Type safety | 🟡 | frontend TS strict & clean; Python has annotations but **no type checker in CI** |
| Tests | ✅ | 558 functions, 1123 passing, 72% coverage |
| CI | ✅ | lint + 3-version matrix + coverage gate + isolated live-browser job |

## 4.2 Security

| Item | Status | Note |
|---|---|---|
| Profile data at rest | 🟡 | proxy passwords AES + OS keychain; **rest plaintext by design**. F4 broke even that and is fixed — the cleartext copy is deleted, not hardened |
| IPC | ✅ | N/A — no bridge; pywebview has no `js_api`. HTTP API is the whole surface |
| Local API exposure | ✅ | localhost-bound; Host + Origin + Content-Type guards **verified live** |
| CSRF / DNS rebinding | ✅ | 4/4 attack shapes blocked (§7) |
| CDP exposure | ✅ | origin lockdown **verified** — my own connection was refused 403 |
| Auth model | 🟡 | one shared static token; **no user model, no roles, no sessions** |
| Auto-updater integrity | ❌ | no updater at all |
| Input validation | 🟡 | F8 — unknown fields persisted |
| Supply chain | ✅ | 2 runtime deps; 0 vulns both ecosystems |

## 4.3 Fingerprint consistency (live results)

Tested: real Chromium 151, profile `os=windows`, `tz=America/New_York`, `en-US`.

| Vector | No preset (default) | With `deviceId` | Notes |
|---|---|---|---|
| `User-Agent` header | ✅ Windows/131 (was ⚠️ real host) | ✅ Windows/131 | F1 fixed |
| `sec-ch-ua*` headers | ✅ consistent (was ⚠️ real host) | ✅ consistent | F1 fixed; first request of a NEW tab still leaks the platform — Gate 2 |
| `navigator.platform` | ✅ Win32 — agrees with UA | ✅ Win32 | |
| `userAgentData` | ✅ Windows/131 — agrees with UA | ✅ consistent | |
| Screen / DPR | ✅ 1366×768 | ✅ 1366×768 | host is 2560×1440 |
| `hardwareConcurrency` | ✅ spoofed | ✅ | host has 12 |
| `deviceMemory` | ✅ spoofed | ✅ | |
| Timezone | ✅ America/New_York | ✅ | |
| `languages` | ✅ en-US,en | ✅ | |
| `navigator.webdriver` | ✅ false | ✅ | |
| Worker realm | ✅ **consistent** | ✅ | platform+cores match main realm |
| Canvas / WebGL | ✅ hooked once (F2 fixed) | ✅ hooked once | still detectable — F3 |
| Engine version truth | ⚠️ claims 131, is 151 | ⚠️ | F5 |
| **Spoofing artifacts** | ⚠️ **source leaks via `Function.prototype.toString`** | ⚠️ | F3 |
| Audio | ❌ not injected | ❌ | honestly declared in the manifest |
| ClientRects · Battery · Geolocation · DNT · speechSynthesis | ❌ | ❌ | confirmed absent |

**Bottom line (at audit time):** the default path failed the most basic
cross-surface check, and the preset path was internally coherent and then failed the
artifact check. **After Gate 1** both paths pass the cross-surface check and the
launch refuses to hand over a profile that does not. Both still fail the artifact
check — that is F3, and it is a strategy decision, not a bug fix.

## 4.4 Performance

See `01_PROJECT_SNAPSHOT.md` §10. Cold start (138 ms) and launch (1.22 s) beat
budget. Profile list at 10k (**3,115 ms** vs <100 ms) misses by ~30×. RAM ~275 MB
PSS per idle profile caps a 13 GB machine near 20 concurrent profiles — which is
exactly `max_concurrent_profiles`, so the default is honest.

## 4.5 UX / UI

Captured live at 603 profiles. The console is **genuinely good** — arguably the
strongest part of the product. Dark theme, coherent spacing, real data, sensible
IA (Workspace / Operations / System), command palette (`⌘K`), live SSE activity
feed, resource panel, honest health chip ("Healthy · 603 profiles · Chrome OK").
It is competitive with AdsPower/GoLogin on visual quality today.

Gaps: no virtualization (F9) · no folders/tree · no saved views or column
customization · no shift-range selection · English only · light theme exists but
dark is the shipped default · no empty-state coaching on the Profiles table.

---

## 7. Re-tested previous-audit claims — these hold ✅

Verified live today, not inherited:

```
CSRF text/plain + hostile Origin    → 403 BLOCKED
CSRF JSON + hostile Origin          → 403 BLOCKED
DNS rebinding (foreign Host)        → 403 BLOCKED
Missing Content-Type                → 403 BLOCKED
Legitimate local client (no Origin) → 200 ALLOWED
GET /api/health, foreign Host       → 403 BLOCKED
CDP hostile origin                  → 403 Forbidden (my own connection refused)
Secrets backend                     → keyring (OS keychain), /api/health honest
profiles.json                       → 0600
web_accessible_resources            → absent from manifest (C1 stays fixed)
Dependencies                        → pip-audit clean, npm audit 0
LICENSE                             → MIT present
```

Also genuinely well built: fail-closed proxy policy · `ProxyConfig.masked_str()`
keeping credentials out of logs · the consistency engine (512 lines, 90% covered) ·
the state machine refusing invalid transitions · honest `PARTIAL` reporting in
`audio.py`/`battery.py` · work-area code refusing a fabricated fallback · **zero
TODOs**. The culture of documenting one's own weaknesses is real and worth keeping.

---

## 8. Recommended order (for Phase 4 approval — not started)

**Gate 1 — make the product do what it claims (3–5 days). ✅ DONE.** F1 → F2 → F4 → F7,
in that order, one commit each. Every profile now carries a User-Agent, the
UA/Client-Hints override travels on a session held for the browser's lifetime, the
launch is verified against the real wire and refused on a proven mismatch, there is
one script-injection route, the cleartext proxy password is gone from disk
(including copies already written), and `evasion.js` is silent unless asked.

**Gate 2 — make it honest and hold the line (1–2 weeks). F5 ✅ DONE. F8 ✅ DONE.**
F5 (derive
version from the real binary — done: `engine.py`, see the ✅ block above) · F8
(field registry at the API boundary — done; it surfaced **F16**, whose fix
surfaced **F17**, a Critical host-IP leak — both routing fixes are done, and
`fingerprintStrategy` is the one piece of F16 left, awaiting a product
decision) ·
F11 on the identity path only (done: `identity_faults.py`) · the residual leak F1
left behind — **done**: `Fetch` is now armed on the browser session, not per
target, because the request that leaked is formed before any target exists. It
turned out to leak the brand list as well as the platform; see the F1 block.

*(The launch-time identity assertion originally listed here shipped in Gate 1 —
without it, F1's fix could not be shown to work at all.)*

**Gate 3 — make it scale (3–4 weeks).** F6 (profiles → SQLite) then F9. Billing and
entitlements must come after, not before.

**Gate 4 — strategic decision required, not an implementation task.** F3 sets the
detectability ceiling. JS-level spoofing on stock Chromium can be *tidied* but never
made undetectable. Bundling and patching an engine is the competitors' actual moat
and is a multi-quarter, specialist-hire programme. **This is a founder/investor
decision about what the product is**, and the roadmap in `06_ROADMAP.md` cannot be
costed until it is made.

---

## 9. Three things I would push back on

**1. The brief assumes an Ubuntu dev machine and a `.deb` install. Neither is true
here.** This is Arch; the app runs from an editable venv install; the `.deb` cannot
be built locally. Every "never break the existing install" instruction therefore
protects a *dev* install, not the shipped artifact — and the shipped artifact is
tested by nobody outside CI.

**2. "Track latest stable within ≤2 weeks" and "stock Chromium" are incompatible.**
You cannot pin a claimed version to real-world stable while running whatever the
distro ships. Pick one: bundle an engine (expensive, correct) or derive the claim
from the installed binary (cheap, honest, caps you at the user's update cadence).

**3. Do not run the Phase 2 feature matrix against this fingerprint layer.** The
brief asks for WebGPU, JA3/JA4, TLS alignment, media devices, a real-device
fingerprint database. Adding vectors on top of a layer that leaks its own source
code in one line, and that doesn't spoof the UA at all on the default path, adds
surface without adding stealth. Fix F1–F3 first; then the matrix is worth building.
*(F1 and F2 are now fixed. F3 remains, and it is the one that still makes this point.)*

---

## 10. Deliverable status

| # | Document | Status |
|---|---|---|
| 01 | `01_PROJECT_SNAPSHOT.md` | ✅ delivered |
| 02 | `02_AUDIT_REPORT.md` | ✅ this document |
| 03 | `03_FEATURE_MATRIX.md` | ⏸ not started — needs competitor research (AdsPower/GoLogin/Multilogin claims must be sourced, not recalled) |
| 04 | `04_FINGERPRINT_TEST_RESULTS.md` | ⏸ partial — §4.3 holds direct wire/JS evidence; public checkers (CreepJS, Pixelscan, IPHey, BrowserScan, Whoer) **not run** |
| 05–07 | UI spec · roadmap · architecture | ⏸ not started |
| 08 | Executive summary | ⏸ not started |

**Phase 1 stopped here as instructed.** Gate 1 (F1 → F2 → F4 → F7) was then
approved and implemented — one commit per finding, each with its own live
measurement; see the ✅ blocks above and `CHANGELOG.md`.

**Gate 2 is now substantially done:** F5, F8, F11 (identity path), F16 (routing),
F17, and the two findings F16/F17 turned up on the way — F18 and F19. What remains
in Gate 2 is the F1 residual (a new tab's first document request) and F16's
`fingerprintStrategy`, which needs a product decision rather than an
implementation. **Gate 3 is not started.**

Documents 03 and 05–08 are still not started, and §04 is still partial — the public
checkers (CreepJS, Pixelscan, IPHey, BrowserScan, Whoer) have still never been run,
which is the largest single hole in the evidence base for a fingerprinting product.
