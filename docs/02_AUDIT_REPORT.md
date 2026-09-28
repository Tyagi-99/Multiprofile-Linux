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

## Findings

| ID | Sev | Area | Summary | Effort |
|---|---|---|---|---|
| **F1** | ✅ **Critical** | Fingerprint | UA/Client-Hints spoofing silently does nothing on the default (no-preset) path; reports success — **fixed, see below** | done |
| **F2** | ✅ **High** | Fingerprint | evasion.js applied **twice** on Chromium (extension + CDP) — **fixed** | done |
| **F3** | ⚠️ **High** | Fingerprint | Hooks are trivially detectable; `Function.prototype.toString` leaks their source | 1–3 wk |
| **F4** | ✅ **High** | Security | Cookie jar + cleartext proxy password written **0644 world-readable** — **fixed** | done |
| **F5** | ✅ **High** | Fingerprint | Every profile claims Chrome **131**; engine is Chromium **151** — **fixed** | done |
| **F6** | ⚠️ **High** | Performance | Profile store is a full read+parse per request → **3.1 s** for 25 rows at 10k | 1–2 wk |
| **F7** | ✅ Medium | Fingerprint | `evasion.js` announces itself via `console.log` on every page — **fixed** | done |
| **F8** | ✅ Medium | Security | API silently accepts and persists **unknown fields** — **fixed** | done |
| **F9** | ❌ Medium | UX | No list virtualization; 25/page pagination | 1 wk |
| **F10** | ✅ Medium | Reliability | Supervisor restart budget never bound crash loops — **fixed this session** | done |
| **F11** | 🟡 Medium | Code quality | 138 failure-swallowing handlers, deselected from lint — **identity path done**, rest is policy | 1–2 wk |
| **F12** | 🟡 Medium | Distribution | Build can't run on the dev machine; no signing, no auto-update | 2–4 wk |
| **F13** | 🟡 Medium | Reliability | Window tiling is X11-bound and fails when X tools are unreachable | 1 wk |
| **F14** | 🟡 Low | Privacy | Next.js build-time telemetry enabled by default | 5 min |
| **F15** | 🟡 Low | Build | `rewrites` in `next.config.ts` ignored under `output: export` | 15 min |
| **F16** | ✅ **High** | UX / Correctness | The wizard's **network-mode and fingerprint-strategy steps reached nothing** — found by F8; routing fixed, strategy step removed | done |
| **F17** | ✅ **Critical** | Security | An **unresolvable route launched on the host IP** and reported success — found by F16 | done |
| **F18** | ✅ **Critical** | Security | **Every profile in a proxy pool egressed through the same member** — pools never recorded a decision | done |
| **F19** | ✅ **High** | Security | The **WebRTC control was inert**: the API's vocabulary matched nothing downstream | done |
| **F20** | 🟡 Medium | Reliability | The **durable job queue has no producer** — now at least visible; moving the writer is filed | 1 wk |
| **F21** | ✅ Medium | UX / Correctness | More silent-drop contract bugs: `description`, null-clearing, `egress_ip`/`latency_ms` case — **fixed** | done |
| **F22** | ✅ **High** | Fingerprint | Timezone was **JavaScript-only**; every unhooked surface reported the host zone — **fixed** | done |

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

### F6 — ⚠️ HIGH: the profile store cannot reach 10,000 profiles

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

### F9 — ❌ MEDIUM: no list virtualization

**File:** `frontend/src/components/profiles/profiles-page.tsx`

Measured in the live console with 603 profiles: `domRows: 25`,
`hasVirtualSpacer: false`, pagination controls `Page 1` / `Next`.

The DOM stays small, so the UI itself will not jank — the brief's "60 fps at 10,000
rows" fails in the **API** (F6), not the renderer. But the brief asks for a
virtualized list, and pagination at 25/page is a materially different UX from the
competitors' infinite-scroll grids: no jump-to-row, no select-all-across-pages, and
bulk actions are bounded by the page.

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

### F13 — 🟡 MEDIUM: tiling is X11-bound and fails opaquely

**File:** [`browser/work_area.py`](../src/multiprofile/browser/work_area.py)

All three work-area detectors (`xrandr`, `_NET_WORKAREA`, `xdpyinfo`) can fail
together; the code correctly **refuses a silent 1920×1080 fallback**, which is good
engineering. But the result is that tiling — a genuinely good differentiator —
silently stops working. Your audit log shows `work_area_unavailable` on every
relaunch during the Sep 25 crash loop, while `/api/health` reported the same today
under this session's environment.

It is environment-dependent (Wayland/XWayland, missing `XAUTHORITY`), not
deterministic. Chromium is launched with `--ozone-platform=x11`, so Wayland-native
window management is not used at all. Cross-platform is new development, not a port.

---

### F14 / F15 — 🟡 LOW

- **F14:** `npm run build` prints *"Next.js now collects completely anonymous
  telemetry"*. Build-time only and not user data, but a privacy-positioned product
  should ship `NEXT_TELEMETRY_DISABLED=1` in the repo. 5 minutes.
- **F15:** `next.config.ts` declares `rewrites`, which `output: export` ignores —
  warned twice on every build. Dead config; remove it or drop the export mode.

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
