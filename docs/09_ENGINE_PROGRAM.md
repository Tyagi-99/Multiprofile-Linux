# 09 — The engine program (option A): a maintained Chromium-based core

**Status: architecture agreed, first slice built and tested, the core itself
not started.** Nothing in this document claims detection parity, and the
measurements that would support such a claim are listed in §6 as the gates
they are.

The decision in [`06_ROADMAP`](06_ROADMAP.md) §1 has been made: **option A**.
This document is the architecture that follows from it, what has been built,
and what the commitment actually costs.

---

## 1. Why the cheap options were rejected

**Option C — a TLS-rewriting local proxy — is not being built.** The old
roadmap called it a shortcut on a false premise (there is no local proxy; see
§4b there), and the measurement below removes the rest of its case.

Measured 5–6 October 2026, destination-observed, product against stock
Chromium on the same host in the same session
([`transport_probe`](../src/multiprofile/diagnostics/transport_probe.py),
`tests/test_transport_identity_live.py`):

| | result |
|---|---|
| product's ClientHello vs stock Chromium's | **identical** — same ciphers in order, same extension set, same groups, same signature algorithms, same ALPN |
| Chromium's own extension order | **randomised per connection** — 16 orders from 16 connections of one browser run |
| JA3 as a comparison tool | **unusable for Chromium**: it hashes the extension list *in order* |

Two things follow. First, **the product adds no transport artefact of its
own** — the CDP overrides, the injected script and the font view do not
perturb the first flight, so there is nothing for a proxy to "fix" that the
product broke. Second, a proxy that re-originated TLS would have to *reproduce
Chromium's per-connection randomisation* as well as its cipher list, or it
would present something **more** stable than a real browser and be
distinguishable in the opposite direction. That is a worse starting position
than shipping the engine whose behaviour is already correct.

**What is still unknown, and is not a reason to build the proxy:** whether
this first flight matches Chrome on a *claimed* foreign OS. BoringSSL ships on
every platform so they are expected to be close, but expectation is not
measurement, and the reference needed is a genuine same-version Chrome on a
real Windows host (§6, gate T1).

---

## 2. What a maintained core is, concretely

It is not primarily a patch. It is the obligation to run **one known build**
and to keep knowing which build that is. Four capabilities, in order of
dependency:

```
   ┌─────────────────────────────────────────────────────────┐
   │  4. Patch set        the differences from upstream      │
   ├─────────────────────────────────────────────────────────┤
   │  3. Build pipeline   reproducible, signed, versioned    │
   ├─────────────────────────────────────────────────────────┤
   │  2. Distribution     ship it, update it, roll it back   │
   ├─────────────────────────────────────────────────────────┤
   │  1. Pin + identify   run OUR build and prove it         │  ← built
   └─────────────────────────────────────────────────────────┘
```

Layer 1 is built, because without it the layers above have nowhere to plug
in. Layer 2 is **already built for the product itself** (F12: signed `.deb`,
apt channel, deltas, rollback) and the same machinery carries an engine.
Layers 3 and 4 are the program.

---

## 3. Layer 1, built and tested

[`engine_policy.py`](../src/multiprofile/engine_policy.py),
`tests/test_engine_policy.py` (15 tests).

Before this, the product ran whatever `find_browser_binary()` turned up on
`PATH`. Three consequences, all of them things a core program cannot live
with:

* the identity a profile presents was decided by whoever last updated the
  host's package manager;
* a profile created under Chromium 151 and relaunched under 153 presented a
  different browser behind the same stored seed, and **nothing recorded it**;
* there was no configuration that said *use this build and refuse others* —
  so a managed build had nowhere to be named.

What exists now:

| capability | mechanism |
|---|---|
| **Pin** | `engine_binary` (`MULTIPROFILE_ENGINE_BINARY`). Naming a build here is the whole of "use our core" |
| **Enforce** | `engine_require_pinned`. Off by default: a product that will not launch because a pin is unset is worse than one that reports which engine it used |
| **Identify** | version *and* a content digest. A version string is not an identity — a rebuild reuses it, and two `151.0.7922.173` builds from different vendors are different software |
| **Verify** | a declared supported range and a staleness policy, with reasons |
| **Record** | every launch notes the build and its digest; a pin violation is an **identity fault**, not a note, because the operator asked for one build and got another |
| **Report** | `/api/health` → `enginePolicy` |

The supported range is chosen from measurement, not taste:

```
SUPPORTED_MAJOR_MIN = 121
```

Below 121 a GPU-less host has **no WebGL at all** without
`--enable-unsafe-swiftshader` (F46). Below 110 Chromium did not randomise
ClientHello extension order, so a profile would present a *stable* TLS
fingerprint while the rest of the fleet presents a randomised one — itself a
distinguisher.

```
STALE_AFTER_DAYS = 56
```

Two Chrome stable cycles. A build further behind is one an operator should be
deciding about, not discovering. This is also Phase 5's security-update
process, expressed as a verdict rather than a wiki page: `supported` /
`unverified` (in range, too old, or will not report a version) /
`unsupported` (out of range, or a violated pin).

On this host today: `supported`, Chromium 151.0.7922.173, `system` origin, 46
days old.

---

## 4. Layers 3 and 4 — the program, sized honestly

| phase | work | effort |
|---|---|---|
| **P1** Build pipeline | `depot_tools`, a pinned upstream tag, a reproducible release config, ~2–4 h of build per revision on adequate hardware, artefact signing | 1–2 months to stand up |
| **P2** Distribution | reuse F12's signed channel for an engine payload; engine and product versions become a *matrix* to test, not two independent numbers | 2–4 weeks |
| **P3** First patch set | §5 | 1–2 months |
| **P4** Continuing | **track upstream stable within two weeks, forever** | ~0.3–0.5 FTE, permanent |

**P4 is the commitment, and it does not pause.** Chrome ships stable roughly
every four weeks with security fixes inside that. Every one of those is a
rebase of the patch set, a rebuild, a re-run of every gate in this repository,
and a channel push. Missing it means shipping a browser with known public
vulnerabilities to people running valuable accounts in it — which is a worse
outcome than any detection problem this program is meant to solve.

**The honest framing:** this is a standing obligation of roughly half an
engineer, permanently, before any detection benefit is measured. It is a
company-shaping decision, which is why
[`06_ROADMAP`](06_ROADMAP.md) §1 insisted an engineer reading the code could
not make it.

---

## 5. The patch surface, derived from measurement

Only things *measured* on this host are listed. Anything else would be
speculation dressed as a plan.

| # | what | why a core is needed | evidence |
|---|---|---|---|
| **E1** | Remove the CDP/`Runtime.enable` artefacts | The identity is installed over DevTools. The attachment itself is observable, and no amount of JavaScript hides the mechanism that injects the JavaScript | F3's stated ceiling |
| **E2** | Ship the font files the claimed OS has | `font_claim_unbacked` names 12 absent Windows faces; `font_backer_name_visible` names 3 Linux faces that cannot be hidden because Chrome reads the file's internal `name` table and fontconfig cannot rewrite it. A core ships its own fonts and writes its own name tables | F25 residual, `04` §11 |
| **E3** | Native `screen`/`device-width` coherence | Currently `Emulation.setDeviceMetricsOverride` with a zeroed viewport. It works and is measured, but it is an override a core would not need | F54 |
| **E4** | Worker identity without per-target overrides | `userAgentData` now comes from `Network.setUserAgentOverride` per worker target. Correct, and still a DevTools override | F53 |
| **E5** | Decide whether to touch TLS **at all** | §1: the transport is already correct *for Linux*. This is a patch only if gate T1 shows a real cross-OS difference | T1 |

**E5 is the point of §1.** A core makes transport changes *possible*; it does
not make them *necessary*. That is decided by a measurement nobody has taken
yet.

---

## 6. Gates — what must be measured before any parity claim

None of these is passed. They are listed so that "we have a core" is never
mistaken for "we match the competition".

| gate | what it needs | status |
|---|---|---|
| **T1** Cross-OS transport reference | A genuine same-version Chrome on a real Windows host, captured through the same destination and route | **not started** — needs a Windows machine or VM |
| **T2** HTTP/2 + HTTP/3 behaviour | A completed handshake, so a trusted certificate; then SETTINGS, frame order, priority and QUIC transport parameters | **not started** |
| **T3** Font coherence on a claimed OS | The claimed OS's font files, with name tables, and a probe that reads metadata rather than widths | **not started** (E2) |
| **T4** Third-party checker delta | Re-run the 5 October captures against a core build and publish the difference | **not started** |
| **T5** Account-site acceptance | The only gate that measures the thing customers care about, and the hardest to run ethically and legally | **not started** |

**A `supported` engine verdict is a supply-chain property, not a fingerprint
property.** It means the build is one this product vouches for and the
operator can be told which. It says nothing about detection.

---

## 7. What a user can rely on today

* The engine that ran a profile is **identified and recorded**, so an
  identity is reproducible against a known build.
* An engine outside the supported range, or more than two Chrome cycles old,
  is **reported** rather than discovered.
* A pinned build can be **required**, which is the switch a managed core is
  turned on with.
* The product's transport is **measurably identical to stock Chromium** on
  the same host, so it adds no artefact of its own.

**Not** that cross-OS claims are undetectable, that the transport matches
Chrome on the claimed OS, or that a maintained core exists.

---

*Companions:* [`06_ROADMAP`](06_ROADMAP.md) §1 and §4b ·
[`03_FEATURE_MATRIX`](03_FEATURE_MATRIX.md) §3b ·
[`04_FINGERPRINT_TEST_RESULTS`](04_FINGERPRINT_TEST_RESULTS.md) §11 ·
[`UPDATES`](UPDATES.md) for the channel an engine payload would reuse
