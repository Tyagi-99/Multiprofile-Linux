# Multiprofile Linux — Remote Gateways & Cloud Egress

Version: 1.7.0  
Status: Clean Architecture & Honest Provider Status  

---

## 1. Architectural Decoupling: Authentication vs Network Routing

A fundamental architectural principle of Multiprofile Linux is the absolute separation of **Cloud Identity / API Credentials** from **Network Egress Routing**:

```
[ GitHub Cloud Account ]                          [ Remote Gateway ]
         │                                                │
         │ (Personal Access Token)                        │ (SOCKS5 / HTTP Proxy Route)
         ▼                                                ▼
  GitHub REST API Operations                       Browser Profile Network Stack
  (Storage, Sync, Backups, Gists)                  (Chrome Egress IP Address)
```

- **GitHub Account Integration**: Strictly an authentication credential (PAT) used for authorized API interactions. **A GitHub Account is NOT a network gateway and does not alter browser egress IP.**
- **Remote Gateway Subsystem**: A dedicated routing interface (`RemoteNetworkProvider`) that directs Chromium profile socket traffic through verified external egress endpoints.

---

## 2. Provider Implementation Status

| Provider | Implementation Status | Usable for Egress Today | Description |
|---|---|---|---|
| **`custom`** | **PRODUCTION READY** | **YES** | User-configured HTTP, HTTPS, or SOCKS5 proxies. Validated via `CustomGatewayProvider`. |
| **`azure`** | **PROVIDER SCAFFOLD / NOT ACTIVE** | **MANUAL ONLY** | Scaffold interface. Automated VM creation requires external Azure provisioning. |
| **`wireguard`** | **PROVIDER SCAFFOLD / NOT ACTIVE** | **NO** | Interface stub. Requires root/`CAP_NET_ADMIN` and Linux network namespaces (`ip netns`). |

---

## 3. Azure Gateway Provider: Truthful Status & Specification

### 3.1 Honest Current Status
> **IMPLEMENTATION STATUS: PROVIDER SCAFFOLD / NOT ACTIVE**
> 
> The codebase contains `src/multiprofile/gateway/azure.py`. This provides:
> 1. A typed data model (`AzureGatewayProvider`) conforming to `RemoteNetworkProvider`.
> 2. TCP connectivity and latency health checking against manually supplied Azure VM IPs.
> 3. Route translation into a valid `NetworkRoute`.
> 
> **It does NOT provide automated cloud VM provisioning or automated proxy orchestration.** The application does not spin up Azure virtual machines, configure Dante/SOCKS5 servers, or manage Azure Virtual Networks autonomously.
> 
> Calling `POST /api/gateways/<id>/provision` returns `status: not_implemented` with actionable setup guidance.

### 3.2 Manual Azure Deployment Guide (How to Use Azure Egress Today)
To route browser profiles through Microsoft Azure IP addresses today:

1. **Provision an Azure Linux VM** (via Azure Portal or Azure CLI):
   ```bash
   az vm create \
     --resource-group rg-multiprofile \
     --name vm-egress-eastus \
     --image Ubuntu2204 \
     --size Standard_B1s \
     --admin-username azureuser \
     --generate-ssh-keys
   ```
2. **Install a SOCKS5 or HTTP Proxy** on the VM (e.g., `microsocks` or `dante-server`):
   ```bash
   sudo apt-get update && sudo apt-get install -y dante-server
   ```
   Configure `/etc/danted.conf` to listen on port `1080` with username/password authentication.
3. **Open the Inbound Port** in the Azure Network Security Group (NSG):
   ```bash
   az vm open-port --resource-group rg-multiprofile --name vm-egress-eastus --port 1080
   ```
4. **Register the Gateway in Multiprofile**:
   In the UI or via REST API:
   ```json
   POST /api/gateways
   {
     "label": "Azure East US VM",
     "providerType": "custom",
     "host": "<AZURE_VM_PUBLIC_IP>",
     "port": 1080,
     "scheme": "socks5",
     "username": "proxyuser",
     "password": "proxypassword"
   }
   ```
5. **Assign the Gateway to a Profile**:
   Set `networkConfig.mode = "REMOTE_GATEWAY"` and `networkConfig.gatewayId = "<GATEWAY_ID>"`.

### 3.3 Next Implementation Phase for Automated Azure Gateways
To transition `AzureGatewayProvider` from scaffold to fully automated:
1. Integrate the official `azure-mgmt-compute` and `azure-mgmt-network` Python SDKs.
2. Accept Azure Service Principal credentials (`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`).
3. Implement `provision()` to deploy an Azure Container Instance (ACI) running an ephemeral, authenticated SOCKS5 proxy image.
4. Implement `destroy()` to de-allocate resources when profiles close.

---

## 4. Custom Gateway Provider (Active & Verified)

`CustomGatewayProvider` is the primary and recommended gateway provider for production deployments:
- Supports SOCKS5, HTTP, and HTTPS proxies.
- Evaluates gateway health via TCP handshake and latency measurement.
- Persisted in `~/.local/share/multiprofile/gateways.json` with strict POSIX `0600` permissions.
- All REST API endpoints (`GET /api/gateways`, `GET /api/gateways/<id>`) automatically mask credentials:
  ```json
  {
    "id": "gw_54a9c1",
    "label": "Residential Proxy Pool",
    "providerType": "custom",
    "host": "proxy.example.com",
    "port": 8080,
    "username": "operator1",
    "hasPassword": true
  }
  ```
  The plain text password is never exposed in responses or logs.

---

## 5. WireGuard Provider: Architecture & Requirements

Per-profile WireGuard routing on Linux cannot be achieved simply through process environment variables. WireGuard operates at the kernel network interface level (`wg0`).

To isolate a single Chromium profile to a WireGuard tunnel without diverting the entire operating system's traffic, the system must utilize **Linux Network Namespaces (`ip netns`)**:
1. Create a namespace: `ip netns add mp-profile-1`
2. Move the WireGuard interface into the namespace: `ip link set wg0 netns mp-profile-1`
3. Configure addressing and routing inside the namespace.
4. Launch the Chromium profile inside the namespace: `ip netns exec mp-profile-1 chromium ...`

Because this requires `CAP_NET_ADMIN` or `sudo` privileges, `WireGuardGatewayProvider` remains designated as a scaffold interface pending a dedicated non-root daemon helper.

---

## 6. 1.6.0 Re-verification (2026-09-23)

Both scaffolds remain **SCAFFOLD / NOT ACTIVE** in 1.6.0 — nothing was
promoted, and no new provisioning claims are made:

- `AzureGatewayProvider.provision()` still returns
  `{"ok": false, "status": "not_implemented", …}` with manual-setup guidance.
- `WireGuardGatewayProvider.provision()` still returns
  `{"ok": false, "status": "not_implemented", …}`.
- `WireGuardGatewayProvider.health()` still returns `ok=False`,
  `status="UNKNOWN"` with the manual-setup note.
- Both statuses are pinned by automated tests
  (`tests/test_release_v16.py::test_azure_gateway_provision_still_not_implemented`,
  `test_wireguard_gateway_provision_still_not_implemented`), so a future
  implementation must update the docs and the tests together.
- New in 1.6.0: per-profile network diagnostics
  (`POST /api/profiles/<id>/network/test`, see `docs/NETWORK_ISOLATION.md`
  §7) surface gateway health honestly — `HEALTHY | DEGRADED | DOWN` with the
  real error — instead of the old silent/unknown states.
