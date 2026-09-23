import { useProfileHealth } from "../../../lib/queries";
import type { Profile } from "../../../lib/types";
import { Badge } from "../../ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";
import { EmptyState } from "../../ui/empty-state";
import { Skeleton } from "../../ui/skeleton";
import { KV, Mono, fmtVal } from "../kv";

function fmtDur(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function timeAgo(ts: number | null | undefined): string {
  if (ts == null) return "—";
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleString();
}

export function RuntimeTab({ profile }: { profile: Profile }) {
  const { data: h, isLoading, isError, refetch } = useProfileHealth(profile.id);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Runtime</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="grid gap-2">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-5 w-3/5" />
          </div>
        ) : isError || !h ? (
          <EmptyState
            title="Runtime info unavailable"
            description="GET /api/profiles/:id/health did not return data."
            action={{ label: "Retry", onClick: () => refetch() }}
          />
        ) : (
          <>
            <KV
              pairs={[
                [
                  "Launch state",
                  <Badge
                    key="l"
                    tone={h.launch_state === "RUNNING" ? "ok" : "idle"}
                  >
                    {fmtVal(h.launch_state)}
                  </Badge>,
                ],
                [
                  "Process alive",
                  h.process_alive == null ? "—" : h.process_alive ? (
                    <Badge key="p" tone="ok">yes</Badge>
                  ) : (
                    <Badge key="p" tone="idle">no</Badge>
                  ),
                ],
                ["PID", h.pid != null ? <Mono key="pid">{String(h.pid)}</Mono> : "—"],
                ["Uptime", fmtDur(h.uptime_s)],
                [
                  "CDP alive",
                  h.cdp_alive == null ? "—" : h.cdp_alive ? (
                    <Badge key="c" tone="ok">yes</Badge>
                  ) : (
                    <Badge key="c" tone="idle">no</Badge>
                  ),
                ],
                ["CDP port", h.cdp_port != null ? <Mono key="cp">{String(h.cdp_port)}</Mono> : "—"],
                ["Last launch", timeAgo(h.last_launch)],
                ["Last error", fmtVal(h.last_error)],
                [
                  "Checked at",
                  h.checked_at ? new Date(h.checked_at * 1000).toLocaleString() : "—",
                ],
              ]}
            />
            <p className="mt-3 text-xs text-ink-3">Source: GET /api/profiles/:id/health.</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
