# 07 — Security, Trust & Compliance

**Status of this document.** It describes the product **as it is**, on the
date of the last commit below, not as it is intended to become. Where a
control does not exist, it says so; where one exists but is partial, it says
what it does and does not cover. A compliance document that describes
aspirations is worse than none, because it is the document someone will rely
on when answering a customer's security questionnaire.

Scope: the Linux desktop application in this repository. There is no hosted
backend, no cloud sync and no account system — see
[`08_COMMERCIAL_ARCHITECTURE.md`](08_COMMERCIAL_ARCHITECTURE.md) for what
adding them would require.

---

## 1. Threat model

The realistic adversaries, in the order they matter:

| # | Adversary | What they want | Status |
|---|---|---|---|
| 1 | **A website the operator visits** | Detect automation; link two profiles to one machine | Partially mitigated — see §2 and F3 |
| 2 | **Another local user / a stolen laptop** | Read cookie jars, proxy credentials, profile data | Mitigated at rest; see §3 |
| 3 | **A web page open in the operator's other browser** | Drive this product's localhost API | Mitigated — see §4 |
| 4 | **A leaked automation token** | Act as the operator | Mitigated since F39 — see §4 |
| 5 | **Anyone with the operator's data directory backup** | Everything | Partially mitigated — see §3 |

Explicitly **not** in the threat model: an attacker with root on the machine,
or code already running as the operator. The product stores secrets the
operator's own processes can reach by design, and pretending otherwise would
be theatre.

---

## 2. Fingerprint integrity

The product's central claim is that each profile presents a coherent, distinct
and non-automated identity. The audit found and fixed **14 findings** in this
area (F1, F5, F7, F22–F31). Two statements are worth making plainly:

**The identity is coherent.** The contradiction that made a profile *more*
detectable than doing nothing — JavaScript claiming Windows while the wire
carried Linux — is fixed (F1), along with timezone, locale, geolocation,
fonts, WebGPU, Battery, speech voices, media devices, AudioContext,
ClientRects and worker realms.

**It is still detectable by a determined checker.** **F3 is open.** The
product drives stock Chromium over CDP, so `Runtime.enable` and the
`Function.prototype.toString` signature of injected hooks remain observable.
This is architectural, not a bug: closing it means a patched engine. Anyone
answering "is this undetectable?" should answer **no**, and should say that
the gap is a known, scoped, unstarted piece of work.

---

## 3. Data at rest

| Data | Where | Protection |
|---|---|---|
| Profile metadata | `control.db` (SQLite) | `0600`, including `-wal`/`-shm` |
| Proxy passwords | `control.db`, inside the profile document | Encrypted, `mpenc:v1:` envelope |
| Cookie jars | `~/.local/share/multiprofile/cookies/` | `0600` |
| Browser session state | `~/.local/share/multiprofile/chrome-profiles/<id>/` | Filesystem permissions only |
| API token hashes | `control.db`, `api_tokens` | SHA-256; the secret is never stored |
| Legacy API token | `$XDG_CONFIG_HOME/multiprofile/api_token` | Cleartext, operator-managed |
| Audit log | `~/.local/share/multiprofile/audit.jsonl` | `0600` |

**What is protected.** Proxy passwords are encrypted rather than stored in
clear (F4). Database files including the WAL sidecars are `0600` and a test
asserts it, because the first version of the SQLite store shipped `0644`.
Backups and account exports strip credential fields and say which ones.

**What is not.** The encryption key is derived locally and is readable by the
operator's own processes; this protects against a copied file or a shared
backup, **not** against code running as the operator. Chrome's own profile
directories are protected only by filesystem permissions — the product does
not encrypt them, and they are the largest concentration of live session
credentials on the disk. The legacy single API token remains cleartext in a
file the operator controls; tokens minted through `POST /api/tokens` are
hashed.

**Full-disk encryption is the missing layer** and is out of this product's
scope. A deployment handling valuable accounts should have it.

---

## 4. The local API

The API binds to loopback. Three controls sit in front of it:

**Loopback needs no token.** Deliberate: the console must work, and code
already running on this machine owns it anyway. The token gate applies to
non-loopback callers.

**Browser-driven attacks are closed** (verified open before the fix): `Host`
must be loopback, which defeats DNS rebinding; `Origin` must match on writes;
and a body must be declared `application/json`, which blocks CORS *simple
requests* — a `text/plain` POST that any page can send with no preflight was
landing real side effects.

**Tokens are named, scoped and revocable** since F39. Four scopes — `read`,
`launch`, `write`, `admin` — so a cron job that starts a profile does not hold
the credential that can delete the fleet. Only hashes are stored. Every
authentication failure answers an identical 401 so the endpoint is not an
oracle; a scope failure answers 403, because the credential is good and the
permission is not.

**Known gaps.** No TLS — a token on a non-loopback interface travels in
cleartext, so remote access should go through an SSH tunnel or a WireGuard
link, not across a LAN. No rate limiting or lockout on token presentation.
No per-user identities: a token is an integration, not a person.

---

## 5. Audit trail

Every profile launch, stop, delete, restore, purge, import, token mint and
token revocation is recorded to `audit.jsonl` with a UTC timestamp, the
action, the profile id and a structured detail object. Token **ids** are
logged; token secrets never are.

Revoking a token keeps its row, so the trail outlives the credential.

**Gap:** the log has no integrity protection. Someone who can write the file
can edit it. Tamper-evidence (hash chaining, or shipping to an append-only
store) is a hosted-backend concern — see `08`.

---

## 6. Data rights (GDPR / India DPDP readiness)

Readiness, not certification. The two operations a data-subject request
actually needs both exist as of F40:

**Export** — `GET /api/account/export` returns one document containing
profiles, trash, workspaces, templates, measured usage, token names, the plan,
the audit log and cookie jars. It **documents its own omissions** in the
document itself: proxy passwords are absent and the field names are listed, so
the gap is discoverable before a restore rather than during one. Written to
disk the file is `0600`, because it carries cookie jars, which are live
session credentials even with passwords stripped.

**Deletion** — `POST /api/account/delete` removes browser directories, cookie
jars, the database and its WAL sidecars, the audit log and all tokens. It
requires the literal string `DELETE EVERYTHING`, not a boolean, and
`GET /api/account/deletion-preview` reports what would go without doing it. A
test asserts that a known proxy password appears in **no file** under the data
directory afterwards.

**What "readiness" does and does not mean here.** Because there is no hosted
backend, this installation is the only copy: there is no processor to instruct
and no cross-border transfer. The moment cloud sync exists, this section stops
being sufficient — a DPA, a retention schedule, a breach-notification path and
a records-of-processing register all become necessary, and none exist.

**Not implemented:** consent management, data-subject request workflow,
retention policies beyond the trash's 30 days, or an appointed DPO.

---

## 7. Acceptable use

This product exists for legitimate multi-account operation: agencies managing
client accounts, e-commerce sellers with multiple storefronts, ad operations,
QA and cross-browser testing, and privacy-conscious users separating
identities. Those are real, large and legal markets.

The same capability can be used for fraud, account-takeover, and evading bans
imposed for illegal activity. An Acceptable Use Policy that prohibits these is
a prerequisite for taking payment, for app-store distribution and for most
payment processors' onboarding. **No AUP or Terms of Service exists in this
repository.** Drafting them is a legal task, not an engineering one, and it
blocks commercialisation rather than development — see `08` §6.

**No abuse detection exists**, and on a local-only product there is little to
detect from: nothing reports home. If a hosted tier is built, volume-based
abuse signals arrive with it, and so does the obligation to act on them.

---

## 8. Supply chain and distribution

| Concern | Status |
|---|---|
| Dependency pinning | Python deps pinned; `npm` lockfile present |
| Dependency scanning | **None in CI** |
| Signed builds | **None** — F12 open |
| Auto-update | **None** — F12 open |
| Reproducible builds | Not attempted |
| SBOM | **None** |
| Crash reporting | Not wired; Chromium's own crashpad writes locally |
| Telemetry | **None.** Nothing reports to any server |

The last row is worth stating positively: this product makes **no network
requests of its own**. Traffic originates from the browsers it launches, via
the operator's configured proxies. F14 (Next.js build-time telemetry) is a
build-machine concern, not a runtime one, and remains open as a 5-minute fix.

Distribution is the weakest column here. An unsigned `.deb` with no update
channel cannot be recommended to a commercial buyer, and `build-deb.sh` cannot
be exercised on the development machine, so packaging changes ship unverified.

---

## 9. Pentest readiness

What a tester would find already documented: F3 (hook detectability), the
absence of TLS, the absence of rate limiting, unencrypted Chrome profile
directories, and the unsigned build chain.

What has been verified by testing during this audit: fail-closed proxy routing
(F17 — an unresolvable route launched on the host IP and reported success),
per-member pool assignment (F18), WebRTC leak control (F19), file permissions
including WAL sidecars, secret encryption at rest, the browser-driven API
attacks in §4, and the token scope matrix.

**Not yet done:** an external penetration test, a bug-bounty programme, or a
threat model review by anyone other than its author.

---

## 10. Summary for a security questionnaire

If asked today, the honest answers are:

- **Is data encrypted at rest?** Credentials yes; browser session directories no; full-disk encryption is assumed and not provided.
- **Is data encrypted in transit?** There is no transit — nothing leaves the machine. Remote API access has no TLS and needs a tunnel.
- **Is there an audit log?** Yes, local, not tamper-evident.
- **Is access controlled?** Yes for remote API callers, with scopes. There are no user accounts, so there is no per-person access control.
- **Can we export/delete our data?** Yes, both, with documented omissions.
- **Is it penetration tested?** No.
- **Are builds signed?** No.
- **Is it undetectable?** No. See F3.

---

*Companion documents:* [`02_AUDIT_REPORT.md`](02_AUDIT_REPORT.md) for the
finding-by-finding detail, [`08_COMMERCIAL_ARCHITECTURE.md`](08_COMMERCIAL_ARCHITECTURE.md)
for what a hosted tier would add to every section above.
