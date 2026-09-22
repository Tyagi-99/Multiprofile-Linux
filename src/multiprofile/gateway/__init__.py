"""Remote Network Gateway abstraction.

Separates GitHub account authentication from network routing.

GitHub integration → GitHub API, account management
Remote Gateways    → actual network egress (Azure VM, WireGuard, custom proxy)

A profile's NetworkConfig references a gateway_id from gateways.json.
The launcher resolves the gateway to a concrete NetworkRoute before launch.
"""
