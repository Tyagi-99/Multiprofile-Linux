# 06 — Roadmap

**Status: the first decision on this page has not been made.** Everything
after §2 branches on it, so the roadmap is written as a fork rather than as a
single line. Presenting one path as settled would be the same failure this
audit spent its length fixing — an artefact that looks complete and is not.

Where the product stands: **48 findings, 46 fixed, 2 open** — and both of the
two are open only at the part that needs something outside this repository:
F3 at its architectural ceiling, F12 at a signing key and a host. P0, P1, P2 and P3
are complete. The fingerprint vectors reachable from
JavaScript are coherent and per-profile ([`04`](04_FINGERPRINT_TEST_RESULTS.md));
the orchestration, storage, API and commercial substrate are production-shaped.

What remains is not a list of features. It is one architectural decision and
its consequences.

---

## 1. The decision: what to do about F3

**F3** — the spoofing is detectable in one line of JavaScript — is the
product's ceiling. Measured:

```js
Function.prototype.toString.call(HTMLCanvasElement.prototype.toDataURL)
// -> the hook's own source, comments included
```

Alongside it, and unreachable from JavaScript at all, sit **TLS/JA3/JA4** and
**HTTP/2 fingerprint alignment** — both entirely absent, both checked by every
serious anti-bot vendor, both discussed in [`03`](03_FEATURE_MATRIX.md) §2.

These three are one problem: **the product operates above the layer where
detection now happens.** AdsPower, GoLogin and Multilogin all ship patched
engines. No amount of work on the JavaScript surface changes the comparison.

### The three honest options

| | What it means | Effort | What you can then claim |
|---|---|---|---|
| **A — Patch the engine** | Fork Chromium; move spoofing to C++; own the build and update pipeline | **XL, 6–12 months**, plus permanent maintenance | Parity on detection |
| **B — Accept the ceiling** | Stay on stock Chromium; sell on Linux-first, price, API, transparency | **0** | Everything except "undetectable" |
| **C — Narrow the gap** | Close what is reachable without a fork: ~~`toString` masking~~ **(done)**, a TLS-rewriting proxy, HTTP/2 alignment at the proxy | **L, 2–3 months** | "Harder to detect", honestly qualified |

**B is the default and it is not a failure.** A Linux-first anti-detect
browser with no telemetry, a 91-route API, scoped tokens, a fail-closed router
and an honest consistency score is a real product for a real audience. It
simply cannot advertise on detection rate, which is what this market
advertises on.

**C is the most interesting and the least obvious.** Roughly two thirds of the
detectable surface is reachable without forking: `toString` can be masked more
completely, and TLS/HTTP/2 can be aligned in a local proxy the traffic already
passes through — the product already routes every profile through one.

**A is a company-shaping commitment**, not a project. Tracking Chromium stable
within two weeks forever is a standing obligation that does not pause.

> **This decision cannot be made by an engineer reading the code.** It is a
> question about who the product is for and what it will claim. Nothing below
> §2 can be scheduled until it is answered.

---

## 2. What to do regardless — the next 4–6 weeks

These do not depend on the F3 answer and should start now.

### 2.1 Signed builds and auto-update (**F12**) — ✅ built and tested · ⬜ not published

Every acceptance criterion on the original entry is met and **green in CI on
every push**: a signed `.deb`, a verifiable signature chain, a delta channel,
automatic update checking, and a job that builds, installs, updates across the
delta, verifies, refuses two kinds of tampered channel and rolls back in a
clean Debian container. `build-deb.sh` runs on the development machine now,
which was the blocker underneath the blocker.

**What is left is not engineering.** There is no real signing key and nothing
is published, so no install anywhere can yet receive an update.
[`UPDATES.md`](UPDATES.md) §4 is the exact procedure and §5 is what stays
unverified until it is followed — an hour of work once there is a key and a
host, and impossible before. §4.6 now covers rotating the key as well, with
the overlap window tested and the one way to get it wrong caught by
`verify_release.py` before a release ships.

*Depends on:* your signing identity and somewhere to serve static files.

### 2.2 Two low-effort findings — ✅ done

**F14** Next.js build telemetry · **F15** `rewrites` ignored under
`output: export`. Both fixed; the open count went 7 → 5, then to 4 with **F13**
and the security half of **F11**, and stayed there when **F43** was found and
fixed in the same stretch.

### 2.3 Official SDKs — M, 1–2 weeks

Thin Python and Node wrappers over the 91-route API. The highest
parity-per-effort ratio in the matrix: the API is already ahead of the UI, and
competitors' SDKs are the main way automation users evaluate a product.

*Acceptance:* create / launch / stop / list, returning a CDP endpoint
Playwright and Puppeteer can attach to; published; one worked example each.

### 2.4 Run the fingerprint checkers — S, half a day

[`04`](04_FINGERPRINT_TEST_RESULTS.md) measures every vector directly but
visits **no third-party checker**. With a proxy configured, run CreepJS,
Pixelscan, BrowserLeaks and Iphey and record the scores. This is how the F3
decision gets a number attached to it instead of an argument, and it should
happen **before** §1 is decided, not after.

### 2.5 F20 — ✅ done

Job creation moved to the durable queue, which needed the batch concept the
durable queue lacked; the legacy in-process queue is retired. Idle parking
**defaults to off**, which is the decision the finding asked for and the one
that matches what installs have actually been doing — the flag read `true` and
reached nothing. Turning it on now works. **F13** and **F11** are also done.

---

## 3. If the answer is **B — accept the ceiling**

Sell what is genuinely good. 3–4 months.

| Task | Effort | Why |
|---|---|---|
| §2 in full | 6 wk | Table stakes |
| Cookie robot / warm-up | M | Most-requested absent feature; aged profiles are the asset |
| Headless mode exposed | S | Engine supports it; not surfaced |
| Scheduler | M | Ordinary product surface |
| Proxy rotation, sticky sessions | M | Pools are assignment-sticky; rotation is not built |
| Custom fields, saved views, inline edit | M | [`05`](05_UI_SPEC.md) §9 gaps |
| Accessibility audit | S | Claimed nowhere; should be earned before it is |

**Positioning:** "The anti-detect browser for people who run Linux and read
the source." No telemetry, a real API, an honest consistency score, and
documentation that says what does not work. That last one is a genuine
differentiator in this market.

---

## 4. If the answer is **C — narrow the gap**

The pragmatic middle. 2–4 months, and it front-loads the measurable wins.

| Task | Effort | Closes |
|---|---|---|
| ~~Complete `toString` masking~~ | ✅ done | Most of the F3 surface reachable from JS |
| ~~Audit every hook for prototype tampering~~ | ✅ done | Found the own-`prototype` tell on 22 hooks, and F44 |
| **TLS/JA3 alignment in the local proxy** | **L** | The largest non-engine gap |
| HTTP/2 SETTINGS and frame-order alignment | M | Same category |
| Re-run the checkers, publish the delta | S | Proves it worked |

The insight is that **the traffic already passes through a local proxy the
product controls**, so the TLS ClientHello and the HTTP/2 preface can be
rewritten there without touching Chromium. That is real work, not a trick, but
it is months rather than years, and it closes the vector a serious anti-bot
vendor actually checks first.

**Honest ceiling:** `Runtime.enable` and CDP-attachment artefacts remain.
C gets most of the way, not all of it, and the claim must stay "harder to
detect" rather than "undetectable".

---

## 5. If the answer is **A — patch the engine**

| Phase | Effort |
|---|---|
| Fork, build pipeline, reproducible builds | 2 mo |
| Move canvas/WebGL/audio/rects noise to C++ | 2 mo |
| TLS + HTTP/2 in the network stack | 1.5 mo |
| Font enumeration below Skia (closes F25 properly) | 1 mo |
| Per-profile engine version selection | 1 mo |
| Rebase discipline: stable within ≤ 2 weeks, forever | **ongoing** |

**Do not start A before validating two things** ([`08`](08_COMMERCIAL_ARCHITECTURE.md)):
that a payment processor will accept this product category, and that there is
demand at a price. A is only rational if the product is going to be sold, and
both of those can invalidate it in a fortnight of conversation.

---

## 6. Commercial, if it is being sold

Specified in [`08`](08_COMMERCIAL_ARCHITECTURE.md); the local substrate (usage
metering, plans, scoped tokens, data rights) is **built and tested**. The
hosted layer — identity, entitlement, billing, admin — is **16–23
engineer-weeks** and is not started.

Its first task is not engineering: **validate payment-processor onboarding**.
Stripe and Razorpay underwrite this category case by case, and a decline
changes the distribution model entirely.

---

## 7. Sequencing, independent of the fork

```
DONE     ├─ F11, F12 (built), F13, F14, F15, F20, F43–F48
         └─ F3's JavaScript surface

NOW      ├─ Publish the channel                  ← needs a key + a host
         └─ Run the checkers                     ← gives §1 a number

DECIDE   ├─ F3: A, B or C                        ← blocks everything below
         └─ Validate payment processors          ← blocks all of §6

THEN     ├─ SDKs
         ├─ The chosen §3 / §4 / §5 path
         └─ §6 if selling
```

Two things are worth doing before the decision because they *inform* it:
running the checkers turns F3 from an argument into a score, and the processor
conversation determines whether A is rational at all.

---

## 8. What would change this roadmap

- **Checker scores come back better than expected.** B becomes clearly right and A is off the table.
- **Payment processors decline the category.** §6 collapses; a one-time licence through a reseller needs almost none of it.
- **The scale target moves past 150/20.** Mostly configuration — the SQL paging and id resolution are size-independent — plus list virtualization. Weeks, not months.
- **A second maintainer appears.** A becomes conceivable. With one maintainer it is an unbounded standing obligation.

---

## 9. The one-paragraph version

The product is in good shape below the fingerprint layer: 46 of 48 findings
fixed, the orchestration and storage sound, the API ahead of the UI, and a
commercial substrate that is honest about what it measures. It has one
structural problem — it operates above the layer where detection now happens —
and that problem has three legitimate answers with very different price tags.
**Start F12 today, run the checkers this week, and then decide F3 with a
number in hand.** Everything else follows from that decision, and nothing else
should be scheduled before it.

---

*Companions:* [`02_AUDIT_REPORT`](02_AUDIT_REPORT.md) ·
[`03_FEATURE_MATRIX`](03_FEATURE_MATRIX.md) ·
[`04_FINGERPRINT_TEST_RESULTS`](04_FINGERPRINT_TEST_RESULTS.md) ·
[`05_UI_SPEC`](05_UI_SPEC.md) ·
[`07_SECURITY_AND_COMPLIANCE`](07_SECURITY_AND_COMPLIANCE.md) ·
[`08_COMMERCIAL_ARCHITECTURE`](08_COMMERCIAL_ARCHITECTURE.md)
