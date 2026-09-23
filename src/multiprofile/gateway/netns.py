"""Linux network namespace (netns) support for per-profile egress isolation.

STATUS: EXPERIMENTAL / NOT ACTIVE — see docs/NETNS_ROADMAP.md

Nothing in this module runs unless BOTH of the following hold:

1. ``[netns] enabled`` is explicitly turned on
   (``Settings.netns_enabled`` / ``MULTIPROFILE_NETNS_ENABLED=1`` — default OFF), AND
2. :func:`can_use_netns` reports ``(True, ...)`` on the current host.

Every privileged entry point calls :func:`_require_active`, which raises
``NotImplementedError("netns support is NOT ACTIVE — ...")`` otherwise.

WHAT THIS IS:
    Honest capability detection (:func:`can_use_netns`) plus EXPERIMENTAL
    setup/teardown helpers whose underlying ``ip``/``nft`` commands were
    verified working on 2026-09-23 (prototype in ``/tmp/netns_proto``,
    documented in docs/NETNS_ROADMAP.md). The helpers are unreachable in
    normal deployments: the launcher is NOT rewired to use them.

WHAT THIS IS NOT:
    - Not active. No profile traffic is placed in a namespace today.
    - Not a privilege boundary. These helpers must run with CAP_SYS_ADMIN +
      CAP_NET_ADMIN (or root). There is no setuid helper / root daemon yet —
      that architecture is an open roadmap item, NOT implemented here.
    - Not integrated with the launcher. Chromium's CDP endpoint is
      localhost-only (127.0.0.1) and a netns has its own loopback, so
      launching Chrome inside a namespace needs launcher changes that are
      deliberately out of scope (see docs/NETNS_ROADMAP.md § "Chromium fit").

NEVER claim netns isolation is active based on this module alone.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass

from ..config import get_settings

log = logging.getLogger("multiprofile.gateway.netns")

# ---------------------------------------------------------------------------
# Honest status labels — greppable, never overstated.
# ---------------------------------------------------------------------------

#: Module-wide status. Any UI/docs must surface this verbatim, not "isolated".
NETNS_STATUS = "EXPERIMENTAL / NOT ACTIVE"

#: Raised whenever a privileged entry point is called while netns is off/unusable.
NOT_ACTIVE_MESSAGE = "netns support is NOT ACTIVE — see docs/NETNS_ROADMAP.md"

# Linux capability numbers (linux/capability.h). Both are required.
_CAP_NET_ADMIN = 12
_CAP_SYS_ADMIN = 21

# Per-profile subnets are carved out of this private range. Never routable
# upstream without the NAT rule created by setup_profile_netns().
_SUBNET_BASE = "10.200.0.0/16"

# Bound shell-outs so a wedged `ip`/`nft` cannot hang the caller forever.
_CMD_TIMEOUT_S = 15


# ---------------------------------------------------------------------------
# Capability detection — REAL checks, no guessing.
# ---------------------------------------------------------------------------

def _check_privileges() -> tuple[bool, str]:
    """STATUS: active helper — pure detection, no side effects.

    Check for a Linux process with CAP_SYS_ADMIN + CAP_NET_ADMIN in its
    effective set (parsed from /proc/self/status, not from euid: euid 0
    inside a user namespace does NOT imply these capabilities).
    """
    if not sys.platform.startswith("linux"):
        return False, f"not a Linux host (platform={sys.platform})"
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            cap_eff = 0
            for line in f:
                if line.startswith("CapEff:"):
                    cap_eff = int(line.split(":", 1)[1].strip(), 16)
                    break
    except OSError as e:
        return False, f"cannot read /proc/self/status: {e}"
    missing = [
        name
        for bit, name in ((_CAP_SYS_ADMIN, "CAP_SYS_ADMIN"), (_CAP_NET_ADMIN, "CAP_NET_ADMIN"))
        if not (cap_eff >> bit) & 1
    ]
    if missing:
        return False, f"missing required capabilities: {', '.join(missing)} (euid={os.geteuid()})"
    return True, "CAP_SYS_ADMIN+CAP_NET_ADMIN present in effective set"


def _check_tools() -> tuple[bool, str]:
    """STATUS: active helper — pure detection, no side effects.

    Require ``ip`` (iproute2) plus a NAT-capable netfilter frontend
    (``nft`` preferred; ``iptables`` accepted but note that the
    iptables-nft MASQUERADE extension is missing on some kernels).
    """
    if shutil.which("ip") is None:
        return False, "'ip' (iproute2) not found on PATH"
    if shutil.which("nft") is None and shutil.which("iptables") is None:
        return False, "neither 'nft' nor 'iptables' found on PATH (NAT setup impossible)"
    tool = "nft" if shutil.which("nft") else "iptables"
    return True, f"required tools present (ip + {tool})"


def _probe_setns() -> tuple[bool, str]:
    """STATUS: active helper — functional probe with full cleanup.

    Privileges and binaries are not enough: create a throwaway namespace,
    actually enter it with ``ip netns exec`` (setns(2)), then delete it.
    Sandboxes that grant capabilities but block setns(2) fail here — that
    is the honest answer, not the capability bits.
    """
    name = f"mp-probe-{os.getpid()}"
    try:
        _run("ip", "netns", "add", name)
        _run("ip", "netns", "exec", name, "true")
    except Exception as e:  # noqa: BLE001 - any failure means "not usable"
        return False, f"functional setns probe failed: {e}"
    finally:
        # Best-effort cleanup; a failed `add` can leave the bind-mount file.
        subprocess.run(["ip", "netns", "del", name],
                       capture_output=True, timeout=_CMD_TIMEOUT_S)
    return True, "setns(2) probe OK (ip netns add/exec/del round-trip succeeded)"


def can_use_netns() -> tuple[bool, str]:
    """STATUS: active — honest capability detection, safe to call anywhere.

    Return ``(usable, reason)``. ``usable`` is True only when privileges,
    tooling, AND a functional setns(2) probe all pass. ``reason`` is a
    human-readable explanation, never empty.

    This function has no side effects on success or failure (the probe's
    throwaway namespace is deleted in a finally block).
    """
    for check in (_check_privileges, _check_tools, _probe_setns):
        ok, reason = check()
        if not ok:
            return False, reason
    return True, (
        "netns usable: CAP_SYS_ADMIN+CAP_NET_ADMIN present, ip+nft/iptables "
        "available, setns(2) probe passed"
    )


# ---------------------------------------------------------------------------
# Activation gate
# ---------------------------------------------------------------------------

def _require_active() -> None:
    """STATUS: active gate — raises unless netns is explicitly enabled AND usable.

    Every privileged entry point calls this first. Raises
    ``NotImplementedError(NOT_ACTIVE_MESSAGE)`` when the ``[netns]`` flag is
    off (the default) or :func:`can_use_netns` fails.
    """
    if not get_settings().netns_enabled:
        raise NotImplementedError(NOT_ACTIVE_MESSAGE)
    ok, reason = can_use_netns()
    if not ok:
        raise NotImplementedError(f"{NOT_ACTIVE_MESSAGE} ({reason})")


# ---------------------------------------------------------------------------
# EXPERIMENTAL setup/teardown — real commands, gated, never on by default.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NetnsContext:
    """STATUS: EXPERIMENTAL data holder — describes one profile's namespace.

    Created by :func:`setup_profile_netns`, destroyed by
    :func:`teardown_profile_netns`. All names/addresses are real values
    programmed into the kernel while the context is live.
    """
    profile_id: str
    ns_name: str        # `ip netns` name, e.g. "mp-a1b2c3"
    veth_host: str      # host-side veth interface
    veth_ns: str        # namespace-side veth interface
    subnet: str         # e.g. "10.200.7.0/24"
    gateway_ip: str     # host-side address, the namespace's default gateway
    ns_ip: str          # namespace-side address
    nft_table: str      # nftables table holding the MASQUERADE rule
    egress_if: str      # host egress interface the NAT rule targets


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    """Run a command, raising RuntimeError with stderr on failure."""
    try:
        proc = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=_CMD_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"netns command timed out: {' '.join(argv)}") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"netns command failed (rc={proc.returncode}): {' '.join(argv)}"
            + (f": {err}" if err else "")
        )
    return proc


def _safe_ns_name(profile_id: str) -> str:
    """STATUS: EXPERIMENTAL pure helper — derive a valid ≤15-char ns name."""
    digest = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:8]
    return f"mp-{digest}"


def _allocate_subnet(profile_id: str) -> tuple[str, str, str]:
    """STATUS: EXPERIMENTAL pure helper — pick a /24 inside 10.200.0.0/16.

    Returns (subnet, gateway_ip, ns_ip). Derived deterministically from the
    profile id; callers must still handle collisions (documented roadmap item).
    """
    third = (int(hashlib.sha256(profile_id.encode("utf-8")).hexdigest(), 16) % 253) + 1
    subnet = f"10.200.{third}.0/24"
    return subnet, f"10.200.{third}.1", f"10.200.{third}.2"


def _detect_egress_if() -> str:
    """STATUS: EXPERIMENTAL helper — find the host's default-route interface."""
    proc = _run("ip", "route", "show", "default")
    for line in proc.stdout.splitlines():
        parts = line.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    raise RuntimeError("no default route found: cannot determine egress interface")


def setup_profile_netns(profile_id: str, *, egress_if: str | None = None) -> NetnsContext:
    """STATUS: EXPERIMENTAL / NOT ACTIVE — gated by :func:`_require_active`.

    Create a Linux network namespace for one profile with a veth pair,
    addressing, default route, and nftables MASQUERADE NAT to the host's
    egress interface — the exact command sequence verified on 2026-09-23
    (prototype ``/tmp/netns_proto``; see docs/NETNS_ROADMAP.md).

    Steps:
      1. ``ip netns add <ns>``
      2. ``ip link add <veth_h> type veth peer name <veth_n>``
      3. ``ip link set <veth_n> netns <ns>`` (+ ``lo`` up inside)
      4. Address + default route on both ends
      5. ``nft`` MASQUERADE rule for the profile subnet out ``egress_if``

    DNS inside the namespace uses the host's ``/etc/resolv.conf`` (the mount
    namespace is shared); a per-namespace resolver would need a private
    mount namespace — open roadmap item.

    On any failure, partial state is torn down best-effort and the original
    error is re-raised. Fail-closed: without a default route there is no
    setup at all.

    This does NOT launch Chromium — see :func:`netns_exec_argv` and the
    launcher-integration notes in docs/NETNS_ROADMAP.md.
    """
    _require_active()
    ns_name = _safe_ns_name(profile_id)
    subnet, gateway_ip, ns_ip = _allocate_subnet(profile_id)
    egress = egress_if or _detect_egress_if()
    tag = _safe_ns_name(profile_id + "-veth")[3:]  # 8 hex chars
    veth_host, veth_ns = f"vh{tag}", f"vn{tag}"
    nft_table = f"mpns{tag}"

    try:
        _run("ip", "netns", "add", ns_name)
        _run("ip", "link", "add", veth_host, "type", "veth", "peer", "name", veth_ns)
        _run("ip", "link", "set", veth_ns, "netns", ns_name)
        _run("ip", "netns", "exec", ns_name, "ip", "link", "set", "lo", "up")
        _run("ip", "addr", "add", f"{gateway_ip}/24", "dev", veth_host)
        _run("ip", "link", "set", veth_host, "up")
        _run("ip", "netns", "exec", ns_name, "ip", "link", "set", veth_ns, "up")
        _run("ip", "netns", "exec", ns_name, "ip", "addr", "add",
             f"{ns_ip}/24", "dev", veth_ns)
        _run("ip", "netns", "exec", ns_name, "ip", "route", "add",
             "default", "via", gateway_ip)
        # NAT: prefer native nftables. (The iptables-nft MASQUERADE
        # extension is absent on some kernels — verified 2026-09-23.)
        _run("sysctl", "-w", "net.ipv4.ip_forward=1")
        _run("nft", "add", "table", "ip", nft_table)
        _run("nft", "add", "chain", "ip", nft_table, "post",
             "{", "type", "nat", "hook", "postrouting", "priority", "100", ";", "}")
        _run("nft", "add", "rule", "ip", nft_table, "post",
             "ip", "saddr", subnet, "oifname", egress, "masquerade")
    except Exception:
        # Best-effort rollback of whatever was created, then re-raise.
        ctx = NetnsContext(profile_id, ns_name, veth_host, veth_ns, subnet,
                           gateway_ip, ns_ip, nft_table, egress)
        try:
            teardown_profile_netns(ctx, _skip_gate=True)
        except Exception:  # noqa: BLE001 - rollback must not mask the cause
            log.warning("netns rollback for profile %s had errors", profile_id,
                        exc_info=True)
        raise

    log.info("netns %s ready for profile %s (%s via %s)",
             ns_name, profile_id, subnet, egress)
    return NetnsContext(profile_id, ns_name, veth_host, veth_ns, subnet,
                        gateway_ip, ns_ip, nft_table, egress)


def teardown_profile_netns(ctx: NetnsContext, *, _skip_gate: bool = False) -> None:
    """STATUS: EXPERIMENTAL / NOT ACTIVE — gated by :func:`_require_active`.

    Remove everything :func:`setup_profile_netns` created: the nftables NAT
    table, the veth pair (deleting the host end removes the peer), and the
    named namespace. Best-effort and idempotent — missing pieces are
    ignored so a half-torn-down namespace can be retried.

    ``_skip_gate`` is for internal rollback only; external callers must go
    through the gate.
    """
    if not _skip_gate:
        _require_active()
    for argv in (
        ["nft", "delete", "table", "ip", ctx.nft_table],
        ["ip", "link", "del", ctx.veth_host],
        ["ip", "netns", "del", ctx.ns_name],
    ):
        subprocess.run(argv, capture_output=True, timeout=_CMD_TIMEOUT_S)
    log.info("netns %s torn down for profile %s", ctx.ns_name, ctx.profile_id)


def netns_exec_argv(ctx: NetnsContext, argv: list[str]) -> list[str]:
    """STATUS: EXPERIMENTAL — pure argv constructor, no side effects.

    Return the command that would run ``argv`` inside the profile's
    namespace (``ip netns exec <ns> ...``). This is the documented
    launcher-integration point; the launcher itself is intentionally NOT
    modified by this workstream (see docs/NETNS_ROADMAP.md § "Chromium fit").

    NOTE: a process in the namespace gets the namespace's OWN loopback, so
    Chromium's localhost-only CDP endpoint (127.0.0.1) is NOT reachable
    from the host launcher. Wiring this up needs launcher changes that do
    not exist yet — do not treat this argv as a working launch path.
    """
    return ["ip", "netns", "exec", ctx.ns_name, *argv]


def describe_status() -> dict[str, object]:
    """STATUS: active — report the honest module status for UIs/diagnostics.

    Never claims isolation is active. ``active`` is True only when the flag
    is on AND :func:`can_use_netns` passes.
    """
    ok, reason = can_use_netns()
    enabled = get_settings().netns_enabled
    return {
        "status": NETNS_STATUS,
        "flagEnabled": enabled,
        "hostCapable": ok,
        "capabilityReason": reason,
        "active": bool(enabled and ok),
    }


def _local_tcp_probe(host: str, port: int, timeout: float = 3.0) -> bool:
    """STATUS: active helper — tiny TCP dial used by future health checks."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
