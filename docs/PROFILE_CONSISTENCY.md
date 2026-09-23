# Multiprofile Linux — Profile Consistency Engine

Version: 1.8.0  
Status: Per-domain coherence checks (honest, bounded)

---

## 1. Purpose

The consistency engine answers one question: **does everything this profile
claims agree with everything else?** A UA that says "Pixel 9" alongside a
1920x1080 desktop viewport, or an egress country that contradicts the profile's
configured timezone, are the kind of internal contradictions that break
credibility. The engine checks six domains and reports each one independently,
so a profile can be "mostly coherent with one flagged domain" instead of a
single opaque verdict.

Endpoint: `GET /api/profiles/<id>/consistency`.

## 2. The six domains

| Domain | What it checks |
|---|---|
| `geo` | Egress IP country/timezone (from the last network diagnostic) vs the profile's configured timezone. |
| `device` | Declared device preset identity vs the fingerprint's Navigator/Screen components (UA, viewport, DPR, touch). |
| `platform` | OS platform coherence: UA platform, Client-Hints platform, and `navigator.platform`-class values agree. |
| `mobile` | Mobile/desktop class coherence: `mobile` flags, touch points, and viewport class agree (a "mobile" UA with zero touch points is flagged). |
| `browser` | Browser-family coherence: UA brand, Client-Hints brands, and the actual host browser family agree. |
| `gpu_webgl` | WebGL honesty: the reported WebGL renderer is the real host renderer (ANGLE/host GPU or SwiftShader) — never a spoofed discrete-GPU string that contradicts the launch flags. |

## 3. Status semantics

Each domain check carries `status` ∈ `PASS | WARNING | ERROR` plus a human
`message` string. The overall profile status is the worst of the six domains:

- `PASS` — all six domains coherent.
- `WARNING` — at least one domain has a soft inconsistency worth knowing about
  (e.g. egress geo unknown because no network diagnostic has run yet).
- `ERROR` — at least one domain contradicts itself (e.g. mobile UA with desktop
  geometry).

Response shape:

```json
{
  "ok": true,
  "profile_id": "…",
  "status": "WARNING",
  "checked_at": "2026-09-23T…",
  "checks": [
    {"domain": "geo", "status": "WARNING", "detail": "no egress measurement yet; run POST /network/test"},
    {"domain": "device", "status": "PASS", "detail": "…"},
    …
  ]
}
```

## 4. What it is not

- It is **not** a fingerprinting-proof score. It checks *internal* coherence of
  the profile's own declarations; it cannot verify what a remote site sees.
- It does **not** change the profile. It is read-only diagnostics.
- It does **not** claim anti-detection. Coherence is necessary hygiene, not
  invisibility.

## 5. Relationship to device presets and network diagnostics

- Profiles created with `deviceId` get their `device`/`platform`/`mobile`
  domains seeded from the preset's declared identity, so a Pixel 9 profile
  reports `PASS` on those domains out of the box.
- The `geo` domain reads the profile's **last network diagnostic**
  (`POST /api/profiles/<id>/network/test`), which is a real measurement or an
  honest `DOWN`+error — never a placeholder. See `docs/NETWORK_ISOLATION.md`.
