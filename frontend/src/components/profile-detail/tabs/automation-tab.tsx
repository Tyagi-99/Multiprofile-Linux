import { toast } from "sonner";
import { Play, Square } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { apiMessage, get, post } from "../../../lib/api";
import { useProfileActions } from "../../../lib/mutations";
import { jobsKeys } from "../../../lib/query-keys";
import type { JobSummary, Profile } from "../../../lib/types";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";
import { EmptyState } from "../../ui/empty-state";
import { Progress } from "../../ui/progress";
import { Skeleton } from "../../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../../ui/table";
import { Mono } from "../kv";

interface Job extends JobSummary {
  profileIds?: string[];
  profile_ids?: string[];
  type?: string;
  progress?: number | null;
  created_at?: string | null;
  createdAt?: string | null;
}

function jobTone(s: string): "ok" | "err" | "info" | "idle" | "warn" {
  const v = s.toLowerCase();
  if (v === "completed") return "ok";
  if (v === "failed") return "err";
  if (v === "running" || v === "in_progress") return "info";
  if (v === "cancelled") return "idle";
  return "warn";
}

function timeAgo(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = Date.now() - new Date(ts).getTime();
  if (Number.isNaN(d) || d < 0) return "—";
  if (d < 60_000) return "just now";
  if (d < 3_600_000) return `${Math.floor(d / 60_000)}m ago`;
  if (d < 86_400_000) return `${Math.floor(d / 3_600_000)}h ago`;
  return new Date(ts).toLocaleString();
}

export function AutomationTab({ profile }: { profile: Profile }) {
  const actions = useProfileActions();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: jobsKeys.list({ limit: 100 }),
    queryFn: () => get<{ jobs?: Job[]; items?: Job[] }>("/api/jobs", { limit: 100 }),
    select: (page) => (page.jobs ?? page.items ?? []).filter((j) =>
      (j.profileIds ?? j.profile_ids ?? []).includes(profile.id),
    ),
    staleTime: 10_000,
    retry: 1,
  });

  const cancel = async (id: string) => {
    try {
      await post(`/api/jobs/${encodeURIComponent(id)}/cancel`, {});
      toast.success("Job cancelled");
      refetch();
    } catch (e) {
      toast.error("Cancel failed", { description: apiMessage(e) });
    }
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <div className="grid gap-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : isError ? (
            <EmptyState
              title="Could not load jobs"
              description="GET /api/jobs failed."
              action={{ label: "Retry", onClick: () => refetch() }}
            />
          ) : !data?.length ? (
            <EmptyState
              title="No jobs for this profile"
              description="Queue a launch or stop below."
            />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Job</TH>
                  <TH>Type</TH>
                  <TH>Status</TH>
                  <TH>Progress</TH>
                  <TH>Created</TH>
                  <TH>
                    <span className="sr-only">Cancel</span>
                  </TH>
                </TR>
              </THead>
              <TBody>
                {data.map((j) => (
                  <TR key={j.id}>
                    <TD>
                      <Mono>{j.id.slice(0, 13)}…</Mono>
                    </TD>
                    <TD>{j.type ?? j.job_type ?? "—"}</TD>
                    <TD>
                      <Badge tone={jobTone(String(j.status))}>{String(j.status)}</Badge>
                    </TD>
                    <TD>
                      {j.progress != null ? (
                        <Progress value={Math.min(100, j.progress)} className="min-w-24" />
                      ) : (
                        <span className="text-ink-3">—</span>
                      )}
                    </TD>
                    <TD className="text-ink-3">{timeAgo(j.created_at ?? j.createdAt)}</TD>
                    <TD className="text-right">
                      {["queued", "pending", "running", "in_progress"].includes(
                        String(j.status).toLowerCase(),
                      ) && (
                        <Button variant="danger" size="sm" onClick={() => cancel(j.id)}>
                          Cancel
                        </Button>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Queue a job</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={async () => {
                await actions.launch([profile]);
                refetch();
              }}
            >
              <Play size={14} aria-hidden /> Launch job
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={async () => {
                await actions.stop([profile]);
                refetch();
              }}
            >
              <Square size={14} aria-hidden /> Stop job
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
