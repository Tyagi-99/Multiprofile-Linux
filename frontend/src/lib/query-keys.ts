/**
 * TanStack Query key strategy.
 *
 * One factory per domain. SSE events invalidate ONLY the keys they affect —
 * never the whole cache. See useSSE() in lib/sse.ts for the event→key map.
 */

export const profilesKeys = {
  all: ["profiles"] as const,
  lists: () => [...profilesKeys.all, "list"] as const,
  list: (filters: { q?: string; status?: string; clientId?: string; cursor?: string | null; limit?: number } = {}) =>
    [...profilesKeys.lists(), filters] as const,
  details: () => [...profilesKeys.all, "detail"] as const,
  detail: (id: string) => [...profilesKeys.details(), id] as const,
  health: (id: string) => [...profilesKeys.all, id, "health"] as const,
  fingerprint: (id: string) => [...profilesKeys.all, id, "fingerprint"] as const,
  capacity: () => [...profilesKeys.all, "capacity"] as const,
};

export const devicesKeys = {
  all: ["devices"] as const,
  list: () => [...devicesKeys.all, "list"] as const,
  detail: (id: string) => [...devicesKeys.all, id] as const,
};

export const jobsKeys = {
  all: ["jobs"] as const,
  lists: () => [...jobsKeys.all, "list"] as const,
  list: (filters: { status?: string; limit?: number } = {}) =>
    [...jobsKeys.lists(), filters] as const,
  detail: (id: string) => [...jobsKeys.all, id] as const,
};

export const networkKeys = {
  all: ["network"] as const,
  proxies: () => [...networkKeys.all, "proxies"] as const,
  gateways: () => [...networkKeys.all, "gateways"] as const,
};

export const auditKeys = {
  all: ["audit"] as const,
  list: (filters: { limit?: number } = {}) => [...auditKeys.all, filters] as const,
};

export const trashKeys = {
  all: ["trash"] as const,
  list: () => [...trashKeys.all, "list"] as const,
};

export const usageKeys = {
  all: ["usage"] as const,
  summary: (period: string) => [...usageKeys.all, "summary", period] as const,
  sessions: (period: string) => [...usageKeys.all, "sessions", period] as const,
  plan: ["plan"] as const,
};

export const systemKeys = {
  health: ["system", "health"] as const,
  settings: ["system", "settings"] as const,
  metricsSummary: ["system", "metrics-summary"] as const,
};
