"""Tests for Linux network namespace (netns) support — Workstream 5.

Status under test: EXPERIMENTAL / NOT ACTIVE (see docs/NETNS_ROADMAP.md).

These tests pin the honest behavior:
- can_use_netns() reports (False, reason) on hosts that cannot do it,
  including this sandbox (capabilities present, setns(2) blocked).
- The [netns] flag defaults to OFF and is only on via explicit opt-in.
- Privileged setup/teardown are UNREACHABLE unless the flag is on AND
  can_use_netns() passes (NotImplementedError otherwise).
- Tests that would need real privileges are skipped honestly via
  pytest.mark.skipif — never faked.

Run: PYTHONPATH=src python3 -m pytest tests/test_netns.py -v
"""
from __future__ import annotations

import pytest

from multiprofile.gateway import netns as netns_mod
from multiprofile.gateway.netns import (
    NOT_ACTIVE_MESSAGE,
    NetnsContext,
    can_use_netns,
    describe_status,
    netns_exec_argv,
    setup_profile_netns,
    teardown_profile_netns,
)


def _host_can_use_netns() -> bool:
    """Real probe, evaluated once at collection: is netns usable HERE?"""
    try:
        return can_use_netns()[0]
    except Exception:  # noqa: BLE001 - any probe failure means "not usable"
        return False


requires_netns = pytest.mark.skipif(
    not _host_can_use_netns(),
    reason=(
        "netns not usable on this host "
        "(needs CAP_SYS_ADMIN+CAP_NET_ADMIN, ip+nft/iptables, working setns(2))"
    ),
)


# ---------------------------------------------------------------------------
# can_use_netns() — honest detection
# ---------------------------------------------------------------------------

class TestCanUseNetns:
    def test_returns_tuple_bool_str(self):
        ok, reason = can_use_netns()
        assert isinstance(ok, bool)
        assert isinstance(reason, str) and reason.strip(), "reason must never be empty"

    def test_honest_negative_in_this_sandbox(self):
        # This sandbox grants the capability bits but blocks setns(2), so the
        # functional probe must fail: a (True, ...) here would be a lie.
        ok, reason = can_use_netns()
        assert ok is False
        assert reason.strip()

    def test_privilege_branch(self, monkeypatch):
        monkeypatch.setattr(netns_mod, "_check_privileges",
                            lambda: (False, "missing required capabilities: CAP_SYS_ADMIN"))
        monkeypatch.setattr(netns_mod, "_check_tools", lambda: (True, "tools ok"))
        monkeypatch.setattr(netns_mod, "_probe_setns", lambda: (True, "probe ok"))
        ok, reason = can_use_netns()
        assert ok is False
        assert "capabilit" in reason.lower()

    def test_tools_branch(self, monkeypatch):
        monkeypatch.setattr(netns_mod, "_check_privileges", lambda: (True, "caps ok"))
        monkeypatch.setattr(netns_mod, "_check_tools",
                            lambda: (False, "'ip' (iproute2) not found on PATH"))
        monkeypatch.setattr(netns_mod, "_probe_setns", lambda: (True, "probe ok"))
        ok, reason = can_use_netns()
        assert ok is False
        assert "ip" in reason

    def test_setns_probe_branch(self, monkeypatch):
        monkeypatch.setattr(netns_mod, "_check_privileges", lambda: (True, "caps ok"))
        monkeypatch.setattr(netns_mod, "_check_tools", lambda: (True, "tools ok"))
        monkeypatch.setattr(netns_mod, "_probe_setns",
                            lambda: (False, "functional setns probe failed: EPERM"))
        ok, reason = can_use_netns()
        assert ok is False
        assert "setns" in reason.lower()

    def test_all_ok_branch(self, monkeypatch):
        monkeypatch.setattr(netns_mod, "_check_privileges", lambda: (True, "caps ok"))
        monkeypatch.setattr(netns_mod, "_check_tools", lambda: (True, "tools ok"))
        monkeypatch.setattr(netns_mod, "_probe_setns", lambda: (True, "probe ok"))
        ok, reason = can_use_netns()
        assert ok is True
        assert reason.strip()

    def test_probe_cleans_up_after_itself(self):
        # The functional probe must not leak namespaces, even on failure.
        import subprocess
        before = subprocess.run(["ip", "netns", "list"], capture_output=True,
                                text=True).stdout
        can_use_netns()
        after = subprocess.run(["ip", "netns", "list"], capture_output=True,
                               text=True).stdout
        assert before == after


# ---------------------------------------------------------------------------
# Config — [netns] default OFF
# ---------------------------------------------------------------------------

class TestNetnsConfig:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("MULTIPROFILE_NETNS_ENABLED", raising=False)
        from multiprofile.config import get_settings
        assert get_settings().netns_enabled is False

    def test_explicit_opt_in(self, monkeypatch):
        monkeypatch.setenv("MULTIPROFILE_NETNS_ENABLED", "1")
        from multiprofile.config import get_settings
        assert get_settings().netns_enabled is True

    def test_truthy_values(self, monkeypatch):
        from multiprofile.config import get_settings
        for val in ("true", "yes", "on"):
            monkeypatch.setenv("MULTIPROFILE_NETNS_ENABLED", val)
            assert get_settings().netns_enabled is True

    def test_garbage_value_stays_off(self, monkeypatch):
        monkeypatch.setenv("MULTIPROFILE_NETNS_ENABLED", "maybe")
        from multiprofile.config import get_settings
        assert get_settings().netns_enabled is False


# ---------------------------------------------------------------------------
# Gate — unreachable unless flag on AND host capable
# ---------------------------------------------------------------------------

class TestActivationGate:
    def test_setup_unreachable_when_flag_off(self, monkeypatch):
        monkeypatch.delenv("MULTIPROFILE_NETNS_ENABLED", raising=False)
        with pytest.raises(NotImplementedError) as exc:
            setup_profile_netns("profile-1")
        assert "NOT ACTIVE" in str(exc.value)

    def test_teardown_unreachable_when_flag_off(self, monkeypatch):
        monkeypatch.delenv("MULTIPROFILE_NETNS_ENABLED", raising=False)
        ctx = NetnsContext("p", "mp-x", "vhx", "vnx", "10.200.9.0/24",
                           "10.200.9.1", "10.200.9.2", "mpnsx", "eth0")
        with pytest.raises(NotImplementedError) as exc:
            teardown_profile_netns(ctx)
        assert "NOT ACTIVE" in str(exc.value)

    def test_setup_unreachable_when_flag_on_but_host_incapable(self, monkeypatch):
        monkeypatch.setenv("MULTIPROFILE_NETNS_ENABLED", "1")
        monkeypatch.setattr(netns_mod, "can_use_netns",
                            lambda: (False, "functional setns probe failed: EPERM"))
        with pytest.raises(NotImplementedError) as exc:
            setup_profile_netns("profile-1")
        assert "NOT ACTIVE" in str(exc.value)
        assert "setns" in str(exc.value)

    def test_not_active_message_constant(self):
        assert "NOT ACTIVE" in NOT_ACTIVE_MESSAGE
        assert "NETNS_ROADMAP" in NOT_ACTIVE_MESSAGE


# ---------------------------------------------------------------------------
# Pure helpers — real assertions, no privileges needed
# ---------------------------------------------------------------------------

class TestPureHelpers:
    def test_ns_name_is_short_and_stable(self):
        a = netns_mod._safe_ns_name("some-profile-id")
        assert a == netns_mod._safe_ns_name("some-profile-id")
        assert len(a) <= 15
        assert a.startswith("mp-")
        assert netns_mod._safe_ns_name("other") != a

    def test_allocate_subnet_in_private_range(self):
        subnet, gw, ns_ip = netns_mod._allocate_subnet("profile-1")
        assert subnet.startswith("10.200.") and subnet.endswith(".0/24")
        assert gw.startswith("10.200.") and ns_ip.startswith("10.200.")
        assert gw != ns_ip

    def test_netns_exec_argv_shape(self):
        ctx = NetnsContext("p", "mp-abc123", "vhx", "vnx", "10.200.9.0/24",
                           "10.200.9.1", "10.200.9.2", "mpnsx", "eth0")
        argv = netns_exec_argv(ctx, ["chromium", "--headless"])
        assert argv == ["ip", "netns", "exec", "mp-abc123", "chromium", "--headless"]

    def test_describe_status_never_claims_active_by_default(self, monkeypatch):
        monkeypatch.delenv("MULTIPROFILE_NETNS_ENABLED", raising=False)
        status = describe_status()
        assert status["status"] == "EXPERIMENTAL / NOT ACTIVE"
        assert status["flagEnabled"] is False
        assert status["active"] is False
        assert isinstance(status["capabilityReason"], str)

    def test_describe_status_flag_on_still_not_active_here(self, monkeypatch):
        # Even with the flag on, this sandbox cannot do setns(2): active
        # must stay False. (On a capable host this would be True.)
        monkeypatch.setenv("MULTIPROFILE_NETNS_ENABLED", "1")
        status = describe_status()
        assert status["flagEnabled"] is True
        assert status["active"] is False
        assert status["hostCapable"] is False


# ---------------------------------------------------------------------------
# Privileged round-trip — skipped honestly when the host cannot do netns.
# ---------------------------------------------------------------------------

class TestPrivilegedRoundTrip:
    @requires_netns
    def test_setup_and_teardown_round_trip(self, monkeypatch):
        # NOTE: only runs where can_use_netns() is actually True; skipped
        # (not faked) everywhere else. The flag must also be explicitly on.
        monkeypatch.setenv("MULTIPROFILE_NETNS_ENABLED", "1")
        import subprocess
        ctx = setup_profile_netns("test-profile-netns")
        try:
            assert ctx.ns_name.startswith("mp-")
            out = subprocess.run(["ip", "netns", "list"], capture_output=True,
                                 text=True, timeout=15).stdout
            assert ctx.ns_name in out
            # Egress from inside the namespace (proves the NAT path is real).
            probe = subprocess.run(
                ["ip", "netns", "exec", ctx.ns_name, "curl", "-s", "-o",
                 "/dev/null", "-w", "%{http_code}", "--max-time", "10",
                 "https://example.com"],
                capture_output=True, text=True, timeout=30)
            assert probe.stdout.strip() == "200", probe.stderr
        finally:
            teardown_profile_netns(ctx)
        out = subprocess.run(["ip", "netns", "list"], capture_output=True,
                             text=True, timeout=15).stdout
        assert ctx.ns_name not in out
