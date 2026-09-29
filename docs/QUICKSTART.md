# Quickstart — your first working day

Written for the workflow this product is actually used for: **create and launch
profiles from the console, with the proxy optional per profile.** A profile
with no proxy is an ordinary supported profile, not a degraded one.

---

## 1. Start it

```bash
multiprofile
```

The desktop window opens on the console. If you would rather use your own
browser, or you are on a remote machine:

```bash
multiprofile --server-only
```

…then open <http://127.0.0.1:8765/>. Requests from localhost need no token.

## 2. Make a profile

**New profile** in the console. The only field you must decide is the
**identity** — the user-agent preset, which sets the platform, the client
hints, the screen envelope and the hardware the profile will claim. Everything
else has a working default.

Leave **Network** on **Direct Connection** if you do not have a proxy. The
profile will browse from this machine's address, and it will *say* so: the
timezone, language and position it claims are made to agree with where the
traffic actually leaves from, so there is no contradiction for a site to
notice. Add a proxy later on the same profile whenever you want one; the
identity realigns to the new exit at the next launch.

A **seed** is generated once and kept. It derives the canvas, audio, ClientRects
and hardware noise, so the profile is the same machine every time you open it
and a different machine from every other profile.

## 3. Fill a fleet

**Bulk create** makes up to the fleet capacity in one transaction — it creates
what you asked for or tells you exactly what it could not, never silently less.
**Import from a proxy list** does the same from pasted proxies, in six formats,
reporting per line.

The design target is **150 profiles stored, 20 running at once**. Both are
configurable; the second costs 130–216 MB of RAM per running profile.

## 4. Launch

Select profiles and **Launch**. A batch starts in parallel — 8 profiles in
about 3 seconds — and the windows are tiled edge to edge across the work area
your compositor reports, with a status bar or dock subtracted.

Two refusals are deliberate and worth recognising:

- **`route_unresolved`** — the profile asked for a proxy, pool member or
  gateway that could not be resolved. It will not fall back to your own IP.
- **`consistency_blocked`** — the profile carries a contradiction no real
  browser produces. The message names it. `?force=1` overrides.

## 5. The consistency score, and what it is not

The score says **how coherent the signals we can see are**. It is not a
confidence rating about being undetected, and it says nothing about surfaces
nobody has checked. A domain that cannot be evaluated is excluded from the
score rather than counted as a pass — so a profile with nothing configured
cannot outrank one with a real setup.

Read [`04_FINGERPRINT_TEST_RESULTS`](04_FINGERPRINT_TEST_RESULTS.md) for what
was measured directly, and **§8 of it** for the ceiling: the spoofing runs
above the engine, and `Function.prototype.toString` on a hooked method reveals
that. This product is built on stock Chromium and does not patch it. For
ordinary isolated browsing that is fine. Against a serious anti-bot vendor it
is not, and **no third-party checker has been run yet** — do not learn that on
an account you care about.

## 6. Things worth knowing on day one

**Deleting is undoable.** Delete moves a profile to trash with its sessions
intact; **Restore** brings it back whole. Only **Purge** destroys data, and it
says so plainly.

**Nothing will close your browsers.** Idle parking is configured but has no
scheduler behind it (**F20**), so a profile you leave open stays open.

**Cookies import and export** as JSON or Netscape, per profile.

**Backups** are per profile and optionally AES-256-GCM encrypted. The
passphrase is never stored or logged.

**Nothing leaves this machine.** No telemetry, no account, no sync. Your
proxy passwords are encrypted at rest and stripped from exports and backups.

**The API is ahead of the console** — 91 routes with named, scoped, revocable
tokens, returning a CDP endpoint that Playwright and Puppeteer attach to.
Create a token in **Settings → Tokens**; the secret is shown once and only its
SHA-256 hash is stored. See [`API.md`](API.md).

---

*If something is refused and you do not understand why, the audit log at
`~/.local/share/multiprofile/audit.jsonl` records every launch, stop and
refusal with its reason, and `/api/health` reports any file the product could
not make owner-only.*
