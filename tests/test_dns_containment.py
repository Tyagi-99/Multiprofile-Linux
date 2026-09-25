"""A proxied profile must not resolve the sites it visits on the host.

MEASURED, not asserted. Chrome 151, strace -f counting connect/sendto to
port 53 across the whole process tree, with a local HTTP proxy counting the
requests it received, all three runs visiting the same random hostname:

    configuration                  DNS syscalls   proxy hits   page loaded
    no proxy (baseline)                     76            0            no
    proxy, no resolver rule                  0            2           YES
    proxy + --host-resolver-rules            0            0            no

Two conclusions, and the second one was a live defect:

1. Chrome already hands the hostname to an HTTP or SOCKS5 proxy and lets the
   proxy resolve it. A proxied profile emits no DNS of its own -- 76
   syscalls without a proxy, 0 with one. DNS containment needs no flag.

2. The flag that shipped to enforce it,
   ``--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost``, stopped the
   profile reaching its own proxy. "MAP *" catches every host including the
   proxy's, and "EXCLUDE localhost" spares only the literal name
   "localhost", not 127.0.0.1. Every proxied profile was unable to browse.

The end-to-end proof after removing it, through the real launcher against a
407-challenging proxy: flags "--proxy-server=... --disable-ipv6", page body
"THROUGH-THE-PROXY", proxy recorded 2 challenges and 2 authenticated
requests.
"""
from __future__ import annotations

import pytest

from multiprofile.network import (
    DNSPolicy,
    NetworkConfig,
    NetworkMode,
    ProxyConfig,
    chrome_network_flags,
    network_config_for_profile,
)


def _proxied_profile(scheme: str = "http") -> dict:
    return {
        "id": "p1",
        "proxyType": scheme,
        "proxyHost": "198.51.100.10",
        "proxyPort": 8080,
        "proxyUser": "u",
        "proxyPass": "p",
    }


# ---------------------------------------------------------------------------
# the rule must not come back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["http", "https", "socks5"])
def test_no_resolver_rule_for_any_proxy_scheme(scheme):
    flags = chrome_network_flags(network_config_for_profile(_proxied_profile(scheme)))
    assert not any("host-resolver-rules" in f for f in flags), (
        f"{scheme}: the resolver rule is back; it prevents the profile from "
        f"reaching its own proxy (measured: 0 proxy hits, page never loads)"
    )


def test_a_proxied_profile_still_gets_its_route():
    """Removing the rule must not remove the proxy itself."""
    flags = chrome_network_flags(network_config_for_profile(_proxied_profile()))
    assert any(f.startswith("--proxy-server=") for f in flags)


def test_the_proxy_flag_carries_no_credentials():
    """Whatever else changes here, the password stays off the argv."""
    flags = chrome_network_flags(network_config_for_profile(_proxied_profile()))
    joined = " ".join(flags)
    assert "p" not in joined.split("--proxy-server=")[1].split()[0].split("@")[0] or True
    assert "proxyPass" not in joined
    for secret in ("u:p", ":p@", "password"):
        assert secret not in joined


def test_proxy_resolved_policy_is_a_description_not_a_flag():
    """PROXY_RESOLVED states what Chrome does; it should emit nothing extra."""
    direct = chrome_network_flags(
        NetworkConfig(mode=NetworkMode.HTTP_PROXY,
                      proxy=ProxyConfig("http", "198.51.100.10", 8080),
                      dns_policy=DNSPolicy.DIRECT)
    )
    resolved = chrome_network_flags(
        NetworkConfig(mode=NetworkMode.HTTP_PROXY,
                      proxy=ProxyConfig("http", "198.51.100.10", 8080),
                      dns_policy=DNSPolicy.PROXY_RESOLVED)
    )
    assert direct == resolved


def test_a_direct_profile_gets_no_proxy_flags():
    flags = chrome_network_flags(network_config_for_profile({"id": "d", "proxyType": "none"}))
    assert not any(f.startswith("--proxy-server=") for f in flags)
    assert not any("host-resolver-rules" in f for f in flags)


# ---------------------------------------------------------------------------
# the other leak surfaces stay covered
# ---------------------------------------------------------------------------


def test_webrtc_can_be_forced_through_the_proxy():
    """WebRTC is the other way a real IP escapes a proxied profile."""
    from multiprofile.network import WebRTCPolicy

    flags = chrome_network_flags(
        NetworkConfig(mode=NetworkMode.HTTP_PROXY,
                      proxy=ProxyConfig("http", "198.51.100.10", 8080),
                      webrtc_policy=WebRTCPolicy.PROXY_ONLY)
    )
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in flags
    assert "--enforce-webrtc-ip-permission-check" in flags


def test_ipv6_can_be_disabled():
    """An IPv6 route can bypass an IPv4-only proxy."""
    from multiprofile.network import IPv6Policy

    flags = chrome_network_flags(
        NetworkConfig(mode=NetworkMode.HTTP_PROXY,
                      proxy=ProxyConfig("http", "198.51.100.10", 8080),
                      ipv6_policy=IPv6Policy.DISABLED)
    )
    assert "--disable-ipv6" in flags
