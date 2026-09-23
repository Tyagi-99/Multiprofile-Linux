/**
 * Shared TanStack Query hooks for server state used across the shell
 * (sidebar badges, topbar health, command palette, dialogs).
 *
 * Server state lives here; UI-only state lives in zustand stores.
 * See query-keys.ts for the key factory and sse.ts for realtime invalidation.
 */

import { useQuery } from "@tanstack/react-query";
import { get } from "./api";
import { auditKeys, devicesKeys, jobsKeys, profilesKeys, systemKeys } from "./query-keys";
import type {
  AuditPage,
  DeviceSummary,
  HealthResponse,
  JobsPage,
  MetricsSummary,
  Profile,
  ProfilesPage,
} from "./types";

export function useSystemHealth() {
  return useQuery({
    queryKey: systemKeys.health,
    queryFn: () => get<HealthResponse>("/api/health"),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}

export function useProfilesSearch(limit = 100) {
  return useQuery({
    queryKey: profilesKeys.list({ limit }),
    queryFn: () => get<ProfilesPage>("/api/profiles", { limit }),
    staleTime: 15_000,
    retry: 1,
  });
}

export function useActiveJobs() {
  return useQuery({
    queryKey: jobsKeys.list({ limit: 25 }),
    queryFn: () => get<JobsPage>("/api/jobs", { limit: 25 }),
    staleTime: 10_000,
    retry: 1,
  });
}

export function useDevices() {
  return useQuery({
    queryKey: devicesKeys.list(),
    queryFn: () => get<{ devices: DeviceSummary[] }>("/api/devices"),
    staleTime: 60_000,
    retry: 1,
  });
}

/** Total count for a launch-state filter (limit=1, read `total`). */
export function useProfilesCount(status?: string) {
  return useQuery({
    queryKey: profilesKeys.list({ status, limit: 1 }),
    queryFn: () =>
      get<ProfilesPage>("/api/profiles", { limit: 1, ...(status ? { status } : {}) }),
    select: (page) => page.total ?? page.items.length,
    staleTime: 15_000,
    retry: 1,
  });
}

/** Active jobs: queued/pending/running/in_progress. */
export function useActiveJobsCount() {
  return useQuery({
    queryKey: jobsKeys.list({ limit: 100 }),
    queryFn: () => get<JobsPage>("/api/jobs", { limit: 100 }),
    select: (page) =>
      (page.jobs ?? page.items ?? []).filter((j) =>
        ["queued", "pending", "running", "in_progress"].includes(
          String(j.status ?? "").toLowerCase(),
        ),
      ).length,
    staleTime: 10_000,
    retry: 1,
  });
}

export function useRecentAudit(limit = 8) {
  return useQuery({
    queryKey: auditKeys.list({ limit }),
    queryFn: () => get<AuditPage>("/api/audit", { limit }),
    staleTime: 15_000,
    retry: 1,
  });
}

/** Honest point-in-time resource measurements; UNAVAILABLE, never fabricated. */
export function useMetricsSummary() {
  return useQuery({
    queryKey: systemKeys.metricsSummary,
    queryFn: () => get<MetricsSummary>("/api/metrics/summary"),
    staleTime: 10_000,
    refetchInterval: 15_000,
    retry: 1,
  });
}

export function useProfileDetail(id: string) {
  return useQuery({
    queryKey: profilesKeys.detail(id),
    queryFn: async () => {
      // No GET /api/profiles/<id> endpoint; find in the list like the old UI.
      const page = await get<ProfilesPage>("/api/profiles", { limit: 500 });
      return page.items.find((p) => p.id === id) ?? null;
    },
    staleTime: 10_000,
    retry: 1,
  });
}

export interface ProfileHealth {
  overallOk?: boolean;
  status?: string;
  network_mode?: string;
  proxy_ok?: boolean | null;
  egress_ip?: string | null;
  fingerprint_loaded?: boolean;
  extension_loaded?: boolean | null;
  launch_state?: string;
  process_alive?: boolean;
  pid?: number | null;
  uptime_s?: number | null;
  cdp_alive?: boolean;
  cdp_port?: number | null;
  last_launch?: number | null;
  last_error?: string | null;
  checked_at?: number | null;
  user_data_dir?: string | null;
  storage_accessible?: boolean;
  [key: string]: unknown;
}

export function useProfileHealth(id: string) {
  return useQuery({
    queryKey: profilesKeys.health(id),
    queryFn: () => get<ProfileHealth>(`/api/profiles/${encodeURIComponent(id)}/health`),
    staleTime: 10_000,
    retry: 1,
  });
}

export interface ConsistencyCheck {
  domain: string;
  status: string;
  expected?: unknown;
  actual?: unknown;
  message?: string | null;
}

export function useProfileConsistency(id: string) {
  return useQuery({
    queryKey: [...profilesKeys.all, id, "consistency"] as const,
    queryFn: async () => {
      try {
        return await get<{ status: string; checks: ConsistencyCheck[] }>(
          `/api/profiles/${encodeURIComponent(id)}/consistency`,
        );
      } catch {
        return null; // endpoint optional; tolerate 404
      }
    },
    staleTime: 30_000,
    retry: false,
  });
}

export interface CookieItem {
  name?: string;
  value?: string;
  domain?: string;
  path?: string;
  expires?: string | number;
  expirationDate?: number;
  secure?: boolean;
  httpOnly?: boolean;
  httponly?: boolean;
}

export function useProfileCookies(id: string) {
  return useQuery({
    queryKey: [...profilesKeys.all, id, "cookies"] as const,
    queryFn: async () => {
      const data = await get<CookieItem[] | { cookies: CookieItem[] }>(
        `/api/profiles/${encodeURIComponent(id)}/cookies`,
      );
      return Array.isArray(data) ? data : data.cookies ?? [];
    },
    staleTime: 15_000,
    retry: 1,
  });
}

export interface NetworkHealth {
  mode?: string;
  status?: string;
  ok?: boolean;
  message?: string | null;
  proxyHealth?: {
    egress_ip?: string | null;
    egressIp?: string | null;
    latency_ms?: number | null;
  } | null;
}

export function useProfileNetworkHealth(id: string) {
  return useQuery({
    queryKey: [...profilesKeys.all, id, "network-health"] as const,
    queryFn: () => get<NetworkHealth>(`/api/profiles/${encodeURIComponent(id)}/network-health`),
    staleTime: 15_000,
    retry: 1,
  });
}

export function useProfileAudit(id: string, limit = 200) {
  return useQuery({
    queryKey: [...auditKeys.list({ limit }), "profile", id] as const,
    queryFn: () => get<AuditPage>("/api/audit", { limit }),
    select: (page) =>
      (page.items ?? []).filter(
        (e) => e.profileId === id || (e.detail != null && JSON.stringify(e.detail).includes(id)),
      ),
    staleTime: 15_000,
    retry: 1,
  });
}

export interface FpComponent {
  name?: string;
  status?: string;
  note?: string;
  expected?: unknown;
  runtime?: unknown;
}

export interface FingerprintReport {
  fingerprint?: Record<string, unknown>;
  warnings?: string[];
  components?: FpComponent[];
  summary?: string;
  generated_at?: string;
}

export function useFingerprint(id: string) {
  return useQuery({
    queryKey: [...profilesKeys.all, id, "fingerprint"] as const,
    queryFn: () => get<FingerprintReport>(`/api/profiles/${encodeURIComponent(id)}/fingerprint`),
    staleTime: 30_000,
    retry: 1,
  });
}

export interface Gateway {
  id: string;
  name?: string;
  providerType?: string;
  provider?: string;
  config?: Record<string, unknown>;
  [key: string]: unknown;
}

export function useGateways() {
  return useQuery({
    queryKey: ["gateways", "list"],
    queryFn: () => get<{ gateways?: Gateway[]; items?: Gateway[] }>("/api/gateways"),
    select: (d) => d.gateways ?? d.items ?? [],
    staleTime: 15_000,
    retry: 1,
  });
}

export function useGatewayHealth(id: string | null) {
  return useQuery({
    queryKey: ["gateways", id, "health"],
    queryFn: async () => {
      try {
        const d = await get<{ ok?: boolean; health?: { ok?: boolean } }>(
          `/api/gateways/${encodeURIComponent(id!)}/health`,
        );
        return { ok: true, healthy: !!(d.ok || d.health?.ok) };
      } catch {
        return { ok: false, healthy: false };
      }
    },
    enabled: !!id,
    staleTime: 30_000,
    retry: false,
  });
}

export interface DeviceCapability {
  status?: string;
  note?: string;
}

export function useDeviceDetail(id: string | null) {
  return useQuery({
    queryKey: [...devicesKeys.all, id, "detail"],
    queryFn: () =>
      get<{ capability_matrix?: Record<string, DeviceCapability> }>(
        `/api/devices/${encodeURIComponent(id!)}`,
      ),
    enabled: !!id,
    staleTime: 60_000,
    retry: 1,
  });
}

export interface ClientSummary {
  id: string;
  name?: string;
  description?: string;
}

export function useClients() {
  return useQuery({
    queryKey: ["groups", "clients"],
    queryFn: () => get<{ clients?: ClientSummary[]; items?: ClientSummary[] }>("/api/clients"),
    select: (d) => d.clients ?? d.items ?? [],
    staleTime: 30_000,
    retry: 1,
  });
}
