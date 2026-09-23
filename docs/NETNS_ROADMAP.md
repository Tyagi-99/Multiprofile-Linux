# Linux Network Namespaces — Feasibility Report & Roadmap

**Workstream 5 — Multiprofile-Linux v1.7.0 upgrade**
**Date:** 2026-09-23
**Status: EXPERIMENTAL / NOT ACTIVE** — no profile traffic uses namespaces today,
and nothing in this document claims otherwise.

---

## 1. Verdict: FEASIBLE WITH CONDITIONS — do NOT enable by default in 1.7.0

The Linux mechanics are **proven working**: a process can be placed in its own
network namespace with a veth pair, addressing, default route, DNS, and NAT
egress to the internet (see §3 for the exact command transcript — HTTPS
`200` from inside the namespace in 0.2 s).

What is **not** proven is the *product* path:

1. **Privileges.** Namespace setup needs `CAP_SYS_ADMIN` + `CAP_NET_ADMIN`
   (or root). The application runs as an unprivileged desktop user. There is
   no privilege boundary yet — no setuid helper, no root daemon, no polkit
   rule. Shipping namespace creation into the app process without that
   architecture would be irresponsible.
2. **Launcher integration.** Chromium's CDP endpoint is localhost-only
   (`--remote-debugging-address=127.0.0.1`), and a network namespace has its
   *own* loopback. The host-side launcher cannot reach CDP inside the
   namespace over 127.0.0.1. Fixing this needs launcher changes
   (`--remote-debugging-pipe`, or binding CDP to the veth address with an
   explicit security review) — deliberately out of scope for this workstream,
   which was forbidden from touching the launcher.
3. **Lifecycle.** Per-profile namespaces need airtight setup/teardown
   (subnet allocation without collisions, NAT rule cleanup, a reaper for
   crashed launches). A leaked namespace is a leaked network identity.

**Recommendation:** keep `src/multiprofile/gateway/netns.py` at
`EXPERIMENTAL / NOT ACTIVE`, flag default OFF (`[netns] enabled=false`),
and treat namespaces as a future *hardened mode* — not a replacement for
the per-profile proxy layer (see §5 for the threat-model judgment).

---

## 2. What shipped in this workstream

| File | Contents |
|---|---|
| `src/multiprofile/gateway/netns.py` | **New.** Honest capability detection (`can_use_netns()`), `[netns]`-gated EXPERIMENTAL `setup_profile_netns()` / `teardown_profile_netns()` (real `ip`/`nft` commands, verified in prototype), `netns_exec_argv()` integration-point helper, `describe_status()`. Every docstring carries the `EXPERIMENTAL / NOT ACTIVE` label. Unreachable unless the flag is on **and** `can_use_netns()` passes — otherwise `NotImplementedError("netns support is NOT ACTIVE — …")`. |
| `src/multiprofile/config.py` | **Additive only.** New `Settings.netns_enabled: bool = False` (`[netns]` section, env `MULTIPROFILE_NETNS_ENABLED`, default OFF). The launcher does not consume it — no rewiring. |
| `tests/test_netns.py` | **New.** 21 tests: honest `can_use_netns()` negatives (incl. this sandbox), config default-off, gate unreachability, pure-helper assertions, and one privileged round-trip test that **skips honestly** (`pytest.mark.skipif`) where the host cannot do namespaces. |
| `docs/NETNS_ROADMAP.md` | **New.** This file. |

Not modified (hard boundary): `src/multiprofile/browser/launcher.py`,
`src/multiprofile/network.py`, `src/multiprofile/gateway/` providers.

---

## 3. Prototype evidence (`/tmp/netns_proto`, 2026-09-23)

Full transcript saved at `/tmp/netns_proto/run2.log` (ephemeral scratch —
see §8 for how to re-run). The sandbox blocks `setns(2)` (see §4), so the
prototype launches the isolated process with `unshare(CLONE_NEWNET)` and
moves the veth peer into it from the parent — the same kernel objects the
`ip netns` path uses.

Proven sequence (every step exit 0):

```bash
# parent: create veth pair
ip link add vh57968 type veth peer name vn57968

# child: unshare(CLONE_NEWNET)  -> fresh netns; signals parent READY

# parent: move peer into the child's netns, configure host side
ip link set vn57968 netns <child-pid>
ip addr add 10.200.1.1/24 dev vh57968
ip link set vh57968 up
sysctl -w net.ipv4.ip_forward=1
nft add table ip mpnat
nft add chain ip mpnat post '{ type nat hook postrouting priority 100 ; }'
nft add rule ip mpnat post ip saddr 10.200.1.0/24 oifname host0 masquerade

# child (inside the new netns):
ip link set lo up
ip link set vn57968 up
ip addr add 10.200.1.2/24 dev vn57968
ip route add default via 10.200.1.1 dev vn57968
getent hosts example.com        # -> 198.18.225.210  (DNS works)
curl -s -o /dev/null -w 'HTTP %{http_code} in %{time_total}s\n' \
  --max-time 15 https://example.com
# -> HTTP 200 in 0.198774s ; curl rc=0
```

Cleanup verified: `nft delete table ip mpnat`, `ip link del vh57968`;
no interfaces, tables, or namespaces leaked.

Two negative findings from the same session (both handled):

- `iptables … -j MASQUERADE` fails here: *"Extension MASQUERADE revision 0
  not supported, missing kernel module?"* — native `nft … masquerade`
  works. The module uses `nft` for NAT and documents this.
- `ip netns add` / `ip netns exec` fail here (see §4) — hence the
  `unshare`-based prototype shape.

---

## 4. Environment findings (this sandbox — 2026-09-23)

| Check | Result |
|---|---|
| uid | `0` (root inside a user namespace; host uid 131072) |
| Capabilities | `cap_sys_admin`, `cap_net_admin`, `cap_net_raw` present (effective) |
| `ip` / `nft` / `iptables` | present (`/usr/sbin/ip`, nftables v1.0.9, iptables v1.8.10 nf_tables) |
| `unshare(CLONE_NEWNET)` | **works** (exit 0) |
| `setns(2)` | **blocked — `EPERM` even into our own netns** (verified via python ctypes). Consequently `ip netns add` leaves a stale file (`setns: Operation not permitted`), `ip netns exec` / `nsenter --net` fail. |
| `ip link set <veth> netns <pid>` | works |
| nftables `masquerade` | works |
| iptables `MASQUERADE` target | **missing kernel module** |
| Host egress | `curl https://example.com` → 200 |

**Distinguish "not possible here" from "not possible in general":**
`setns(2)` being blocked is a property of *this sandbox*, not of Linux.
On a normal host with `CAP_SYS_ADMIN` the `ip netns add/exec/del`
round-trip works, and that is exactly what `can_use_netns()` probes
functionally (capability bits alone would have lied here — euid 0 with
the bits set, yet `exec` impossible).

---

## 5. Threat-model fit: is netns better than per-profile proxies?

The product already has per-profile SOCKS/HTTP proxies with fail-closed
pre-launch gates. Judged against the stated goals (**STABLE, ISOLATED,
OBSERVABLE** — explicitly *not* evasion):

- **ISOLATED — netns is meaningfully better (defense in depth).**
  Proxy isolation is *policy*: Chromium is *asked* to send everything
  through `--proxy-server`. A proxy bypass — WebRTC, DNS, QUIC/UDP,
  a Chromium proxy-handling bug, a malicious extension — exposes the host
  IP. A network namespace is *mechanism*: packets physically cannot leave
  except via the veth → NAT/proxy path. It kills entire leak classes at
  L3, including ones the proxy layer cannot see. It does not replace the
  proxy (which remains the egress *policy* authority); it makes the proxy
  unbypassable.
- **OBSERVABLE — netns is a net positive.** Per-namespace interface
  counters (`ip -s link show`) and conntrack give per-profile byte/packet
  accounting at L3, independent of proxy logs.
- **STABLE — netns is a cost.** More moving parts per launch (namespace,
  veth pair, NAT rule, subnet allocation), all requiring cleanup on both
  clean shutdown *and* crashes. A leaked namespace is a leaked identity
  and a confusing debugging surface. This is the main argument for keeping
  it opt-in and EXPERIMENTAL until the lifecycle is airtight.

**Judgment:** worth pursuing as an opt-in hardened mode for users who need
OS-level egress guarantees, layered *under* the existing proxy
fail-closed gates — not as a replacement, and not on by default until the
privilege boundary (§6.1) and launcher integration (§6.3) are solved.

---

## 6. Chromium fit — the three options assessed

The launcher (`src/multiprofile/browser/launcher.py`) spawns Chromium via
`subprocess.Popen(cmd)` and then drives it over CDP on `127.0.0.1:<port>`
(`--remote-debugging-address=127.0.0.1`, `wait_for_cdp`).

### (a) Launch Chromium via `ip netns exec`
The documented path (`docs/REMOTE_GATEWAYS.md` §5). Works on capable hosts;
**does not work in this sandbox** (setns blocked). One integration blocker
that is independent of privileges: **the namespace has its own loopback**,
so the host launcher's CDP dial to `127.0.0.1:<debug_port>` will not reach
Chromium. Options: `--remote-debugging-pipe` (CDP over stdio — cleanest,
namespace-agnostic, but the launcher's CDP client code is TCP-based today)
or binding the debug port to the veth address (weakens the
"CDP is always localhost-only" invariant — needs explicit security review).
Either way: launcher changes, out of scope here.

### (b) Privileged helper with file capabilities, launcher stays unprivileged
The cleanest *production* shape long-term: a tiny helper (or root daemon)
owns namespace/veth/NAT lifecycle; the launcher talks to it over a socket
and spawns Chromium via `nsenter`/`ip netns exec`. But a helper bearing
`CAP_SYS_ADMIN`+`CAP_NET_ADMIN` is effectively root-equivalent — it must be
a minimal, audited, purpose-built binary, not a shell-out wrapper. This is
a packaging + security-design workstream of its own (systemd unit or
setcap'd helper, strict argv validation). Not started.

### (c) Per-namespace proxy routing instead of NAT
Run a tiny forwarding proxy on the host end of the veth pair; Chromium
keeps its existing `--proxy-server` flag pointed at it. The namespace then
provides *egress-shape* isolation (no direct route anywhere; only the proxy
port is reachable) while the product's proxy health-checking, auth, and
fail-closed machinery stays exactly as-is. **Best composition with the
current architecture** — but note it reintroduces policy-vs-mechanism:
if Chromium bypasses `--proxy-server`, the namespace still has no direct
egress, so the bypass fails closed (connection refused) rather than
leaking. Recommended as the first integration target *after* (a)/(b)
foundations land.

---

## 7. Blockers (exact)

1. **Privileges** — `CAP_SYS_ADMIN` + `CAP_NET_ADMIN` (or root) at setup
   time; no helper/daemon architecture exists yet.
2. **Tooling** — `ip` (iproute2) + `nft` (preferred; iptables-nft
   MASQUERADE missing on some kernels — use native nftables).
3. **`setns(2)` availability** — blocked in this sandbox; `can_use_netns()`
   probes it functionally and reports honestly.
4. **Launcher integration** — CDP-over-127.0.0.1 vs. per-namespace
   loopback (§6a); needs launcher changes this workstream was forbidden
   to make.
5. **Lifecycle** — subnet allocation/collision, NAT cleanup, crash reaper.
   The module's teardown is best-effort and idempotent, but there is no
   supervisor integration yet.
6. **DNS inside the namespace** — inherits the host's `/etc/resolv.conf`
   (shared mount namespace). A per-namespace resolver needs a private
   mount namespace — open item.

---

## 8. What "done" would look like

- [ ] Privilege architecture decided and implemented: minimal audited
      helper or root daemon owning netns/veth/NAT lifecycle (not the app
      process shelling out as root).
- [ ] Launcher integration: Chromium spawned into the namespace with a
      working CDP channel (`--remote-debugging-pipe` preferred), keeping
      the localhost-only CDP invariant or an explicitly reviewed exception.
- [ ] Subnet allocator with collision detection + persistent registry of
      live namespaces per profile.
- [ ] Crash reaper: supervisor integration that tears down namespaces for
      profiles that died uncleanly.
- [ ] `POST /api/profiles/<id>/network/test` extended to report netns
      state honestly (`namespace: ACTIVE|INACTIVE`, never implying
      isolation when it isn't).
- [ ] Docs updated to promote the status label, tests updated alongside
      (same discipline as the 1.6.0 gateway scaffold pins).
- [ ] `[netns] enabled` remains default OFF; enabling requires explicit
      operator action on a capable host.

## 9. Reproducing the prototype

The prototype lives in `/tmp/netns_proto/` (**ephemeral** — not part of
the repo, may not survive a reboot):

```bash
sudo python3 /tmp/netns_proto/netns_prototype.py   # full veth+NAT+curl round-trip
# transcript of the verified run: /tmp/netns_proto/run2.log
```

It needs root-equivalent capabilities and a kernel with nftables NAT.
Where `setns(2)` works, the equivalent standard-tooling sequence is
`ip netns add` / `ip link set … netns` / `ip netns exec` / `nft … masquerade`
— the exact commands `gateway/netns.py` issues.
