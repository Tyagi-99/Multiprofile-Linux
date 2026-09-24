/** Shared formatting helpers for the operator console. */

/** "3m ago" for an ISO timestamp. Em dash when absent or unparseable. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** Same, for epoch seconds (the runtime registry's clock). */
export function timeAgoEpoch(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  return timeAgo(new Date(seconds * 1000).toISOString());
}

/** Absolute local timestamp for a title attribute. */
export function absoluteTime(iso: string | null | undefined): string | undefined {
  if (!iso) return undefined;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? undefined : new Date(t).toLocaleString();
}
