/**
 * Domain types mirroring the Flask API response shapes.
 * These are handwritten against src/multiprofile/api/server.py — no codegen,
 * so if a route changes, update here and the compiler will flag call sites.
 */

export type LaunchState =
  | "idle"
  | "starting"
  | "running"
  | "stopping"
  | "parked"
  | "error";

export interface Profile {
  id: string;
  name: string;
  clientId?: string | null;
  deviceId?: string | null;
  userAgent?: string | null;
  startUrl?: string | null;
  proxyId?: string | null;
  tags?: string[];
  notes?: string | null;
  color?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  /** Runtime state injected by the API (_with_launch_state). Null = never launched. */
  launchState: LaunchState | null;
  cdpPort?: number | null;
  [key: string]: unknown;
}

export interface ProfilesPage {
  items: Profile[];
  next_cursor: string | null;
  limit: number;
  total?: number;
}

export interface DeviceSummary {
  id: string;
  label?: string;
  platform?: string;
  status?: string;
  [key: string]: unknown;
}

export interface JobSummary {
  id: string;
  job_type?: string;
  profile_id?: string | null;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  detail?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  [key: string]: unknown;
}

export interface JobsPage {
  items: JobSummary[];
  /** Some endpoints also return `jobs`; prefer `items`. */
  jobs?: JobSummary[];
  next_cursor: string | null;
  limit: number;
  total?: number;
}

export interface HealthResponse {
  ok: boolean;
  app: string;
  version: string;
  chrome_path: string | null;
  chrome_ok: boolean;
  static_ok: boolean;
  extension_ok: boolean;
  profiles_writable: boolean;
  count: number;
  maxConcurrentProfiles?: number;
  windowTilingEnabled?: boolean;
  sandbox?: { ok: boolean; reason?: string };
  workArea?: { ok: boolean; error?: string; message?: string };
  slots?: unknown;
  reaped?: string[];
}

/** Uniform SSE envelope: {"type", "payload", "at"} */
export interface SSEEvent<T = Record<string, unknown>> {
  type: string;
  payload: T;
  at: string;
}

export interface ProfileStatusPayload {
  profile_id: string;
  old_state: string | null;
  new_state: string | null;
  at: string;
}

export interface JobProgressPayload {
  job_id: string;
  job_type: string;
  profile_id: string | null;
  status: string;
  detail: string | null;
  at: string;
}

export type ThemeMode = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export interface AuditItem {
  action: string;
  profileId?: string | null;
  detail?: string | null;
  ok?: boolean;
  ts: string;
  [key: string]: unknown;
}

export interface AuditPage {
  items: AuditItem[];
  next_cursor: string | null;
  limit: number;
  total?: number;
}

export interface MetricsSample {
  profileId: string;
  status: "OK" | "UNAVAILABLE";
  state?: string | null;
  cpu_percent?: number | null;
  memory_rss_mb?: number | null;
  uptime_s?: number | null;
  reason?: string | null;
  measured_at?: number | null;
}

export interface MetricsSummary {
  ok: boolean;
  running_profiles: number;
  measured: number;
  unavailable: number;
  total_memory_rss_mb: number;
  avg_cpu_percent: number | null;
  sample: string;
  measured_at: number;
  profiles: MetricsSample[];
}
