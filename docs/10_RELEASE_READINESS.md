# 10 — Release readiness: exactly what is needed from you

**Everything that can be built and tested without you is done and green in
CI. Nothing is published.** This document exists because "we need a signing
key and hosting" is not actionable, and the brief asked for the *exact*
resources. Each item below is something only you can supply, with what it is
for, what happens without it, and the command that consumes it.

---

## 1. The three resources

### R1 — A release signing identity

| | |
|---|---|
| **What** | An OpenPGP key pair used only for signing this product's apt repository |
| **Why only you** | It is an identity claim about who published a build. Generating it here would mean the key lived on a machine that also builds and tests, which is the arrangement [`UPDATES.md`](UPDATES.md) §4.5 explicitly warns against |
| **Without it** | Every signature in CI is from a throwaway key generated inside the container and destroyed with it. The chain is *verified* (19 offline checks) but it is verified against a key nobody should trust |
| **Consumed by** | `scripts/release.py --key-id <FPR> --gnupghome <DIR>` |
| **Create with** | [`UPDATES.md`](UPDATES.md) §4.1 — `eddsa`/`ed25519`, `Expire-Date: 2y`, passphrase, kept off the build machine |
| **Also needed** | A **revocation certificate, generated at the same time as the key** (`gpg --gen-revoke`). Needing one usually means being unable to make one |
| **Publish** | The fingerprint, somewhere that is **not** this update channel. A key fetched over the channel it authenticates proves nothing |

**Decide before generating:** who holds it, where the backup lives, and
whether a second person can sign. A one-person key with no documented
succession is an outage waiting for a holiday.

### R2 — A static HTTPS host

| | |
|---|---|
| **What** | Anywhere that serves files over HTTPS: S3 + CloudFront, a plain nginx, GitHub Pages. **No server component** — the channel is files |
| **Why only you** | It is a domain and a bill |
| **Without it** | No installed copy anywhere can receive an update. The lifecycle has only ever served from a local directory or a container |
| **Consumed by** | the `url =` in `/etc/multiprofile/update.conf`, and the apt sources line |
| **Layout** | Whatever `scripts/release.py --repo <DIR>` produced: `pool/`, `dists/stable/`, `deltas/`, `multiprofile-archive-keyring.asc` |
| **Requirement** | Keep **every previously published `.deb` in the pool**. `release.py` generates deltas from what it finds there, so pruning old packages silently turns the delta channel off |

HTTPS is not load-bearing for integrity — the signature is — but it stops a
passive observer learning which version each install runs.

### R3 — A proxy endpoint for real-network validation

| | |
|---|---|
| **What** | One HTTP(S) and one SOCKS5 endpoint on a real network, credentials or IP-authorised |
| **Why only you** | A subscription |
| **Without it** | Route testing is complete against **local** proxies (see [`NETWORK_ISOLATION.md`](NETWORK_ISOLATION.md) §7b): destination-observed egress for direct, HTTP, HTTP+auth and SOCKS5, DNS handed to the proxy, WebRTC, IPv6, downloads, and a mid-session drop. What is untested is NAT, MTU, latency, and a provider that fails in ways a local process cannot |
| **Note** | **Authenticated SOCKS5 will be refused**, deliberately — Chromium does not implement RFC 1929 and the launch is blocked with that reason (F56). Supply HTTP(S) credentials, or an IP-authorised SOCKS5 endpoint |

**Never paste credentials into a chat, an issue or a commit.** Put them in a
profile through the console, or in the environment of a single test run.

---

## 2. What is already done

| | |
|---|---|
| Deterministic `.deb` builder, runs on this machine | ✅ `scripts/build_deb.py`, compared against `dpkg-deb` on every push |
| Signed apt repository with zstd deltas | ✅ `scripts/release.py` |
| Offline chain verification | ✅ `scripts/verify_release.py`, **19 checks** |
| Update client: check / download / install / rollback | ✅ `packaging/update/multiprofile-update` |
| Automatic checking, off until a channel is configured | ✅ preserves F14: no channel, no requests |
| Full lifecycle in a clean Debian container | ✅ 13 steps, green in CI on every push |
| Delta applied, tampered index refused, unsigned refused, rollback | ✅ |
| **apt** accepts the channel, offers the candidate, upgrades, refuses unsigned | ✅ |
| Upgrade across a real code change, verified through `/api/health` | ✅ |
| Key rotation with an overlap window, and the footgun caught before it ships | ✅ `UPDATES.md` §4.6 |
| `Architecture: all`, published for amd64 and arm64 | ✅ F47 |
| Browser version policy and staleness verdict | ✅ `engine_policy.py`, `09` §3 |
| Operator diagnostics | ✅ `multiprofile --doctor`, `GET /api/doctor` |

---

## 3. The order to do it in

1. **Decide R1's custody** — holder, backup, succession. Before generating
   anything.
2. **Generate the key and its revocation certificate** ([`UPDATES.md`](UPDATES.md) §4.1).
3. **Publish the fingerprint out of band.**
4. **Stand up R2** and publish once with `release.py`, then run
   `verify_release.py` against the *live* URL.
5. **Install from it on a clean machine** and run `multiprofile --doctor`.
6. **Then** `apt update` against the live repository — the step that has only
   ever run locally.
7. R3 whenever convenient; it gates no release.

---

## 4. Still unverified after R1 and R2 — by nature, not by neglect

| | why it stays open |
|---|---|
| A year of a real server answering | Including the day it returns a 500 or a stale index |
| Unattended installing on a machine doing real work | Exercised against a throwaway channel in CI only. Off by default, and refused while any profile is running |
| An actual arm64 machine | Both indices and an arm64-configured client are tested; CI has no arm64 host |
| `.rpm`, AppImage, Windows, macOS | Not started. Real work, not a field |
| Rotation in the field | The mechanism is tested; operators re-running the keyring step is not |
| **A 24-hour session** | Not run. Crash recovery, backup/restore and schema migration are each tested; *endurance* is not, and a resource leak that matters at hour 18 would not show in a 10-minute test |

---

## 5. What not to ask for yet

A production key in GitHub Actions secrets. A release key in CI means anyone
who can push a workflow change can sign a release. The arrangement
[`UPDATES.md`](UPDATES.md) §4.5 recommends is to build in CI, publish the
unsigned artefacts, and sign on a machine a human controls.

---

*Companions:* [`UPDATES`](UPDATES.md) §4 for the commands · §5 for the
unverified list · [`09_ENGINE_PROGRAM`](09_ENGINE_PROGRAM.md) for the version
policy · [`NETWORK_ISOLATION`](NETWORK_ISOLATION.md) §7b for what route
testing covers
