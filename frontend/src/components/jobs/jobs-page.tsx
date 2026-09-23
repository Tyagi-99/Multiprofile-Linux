import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiMessage, get, post } from "../../lib/api";
import { jobsKeys } from "../../lib/query-keys";
import type { JobsPage, JobSummary } from "../../lib/types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { EmptyState } from "../ui/empty-state";
import { Field } from "../ui/field";
import { Input, Select } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { Mono } from "../profile-detail/kv";

const PAGE_SIZE = 25;

function timeAgo(iso: string | null | undefined): string {
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

function jobTone(status: string): "ok" | "warn" | "err" | "idle" | "info" {
  switch (status.toLowerCase()) {
    case "completed":
      return "ok";
    case "failed":
      return "err";
    case "cancelled":
      return "idle";
    case "running":
    case "in_progress":
      return "info";
    case "queued":
    case "pending":
      return "warn";
    default:
      return "idle";
  }
}

function isActive(j: JobSummary): boolean {
  return ["queued", "pending", "running", "in_progress"].includes(
    String(j.status ?? "").toLowerCase(),
  );
}

function useJobs(cursor: string | null) {
  return useQuery({
    queryKey: [...jobsKeys.lists(), { cursor, limit: PAGE_SIZE }],
    queryFn: async () => {
      try {
        return await get<JobsPage>("/api/jobs", {
          limit: PAGE_SIZE,
          ...(cursor ? { cursor } : {}),
        });
      } catch (e) {
        const msg = apiMessage(e);
        if (/503|not initialized|unavailable/i.test(msg)) throw new Error("UNAVAILABLE");
        throw e;
      }
    },
    staleTime: 5_000,
    retry: false,
    // Live-update the queue while any job is still active.
    refetchInterval: (query) =>
      (query.state.data?.items ?? query.state.data?.jobs ?? []).some(isActive) ? 5_000 : false,
  });
}

export function JobsPage() {
  const qc = useQueryClient();
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1];
  const { data, isLoading, isError, error, refetch } = useJobs(cursor);

  const jobs = useMemo(() => data?.items ?? data?.jobs ?? [], [data]);
  const hasActive = jobs.some(isActive);

  const queue = useMutation({
    mutationFn: (payload: {
      type: string;
      profileIds: string[];
      url?: string;
      direct: boolean;
    }) => post<{ ok: boolean; job: { id: string } }>("/api/jobs", payload),
    onSuccess: (d) => {
      toast.success("Job queued", { description: `Job ${d.job.id}` });
      setCursors([null]);
      qc.invalidateQueries({ queryKey: jobsKeys.all });
    },
    onError: (e) => toast.error("Queue failed", { description: apiMessage(e) }),
  });

  const cancel = useMutation({
    mutationFn: (id: string) => post(`/api/jobs/${encodeURIComponent(id)}/cancel`, {}),
    onSuccess: () => {
      toast.success("Job cancelled");
      qc.invalidateQueries({ queryKey: jobsKeys.all });
    },
    onError: (e) => toast.error("Cancel failed", { description: apiMessage(e) }),
  });

  const unavailable = isError && error instanceof Error && error.message === "UNAVAILABLE";

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight text-ink-1">Jobs</h1>
        <span className="flex-1" />
        <Button variant="secondary" size="sm" onClick={() => refetch()}>
          Refresh
        </Button>
      </div>

      <QueueCard
        pending={queue.isPending}
        onQueue={(payload) => queue.mutate(payload)}
      />

      <div>
        <h3 className="mb-2 text-sm font-semibold text-ink-1">Job queue</h3>
        <Card>
          <CardContent className="pt-6">
            {isLoading ? (
              <div className="grid gap-2">
                {Array.from({ length: 8 }).map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : unavailable ? (
              <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">
                The job system is not initialized on this backend (GET /api/jobs returned 503).
                Jobs queued from this console will not run until the job service is started.
              </div>
            ) : isError ? (
              <EmptyState
                title="Couldn't load jobs"
                description={apiMessage(error)}
                action={{ label: "Retry", onClick: () => refetch() }}
              />
            ) : !jobs.length ? (
              <EmptyState
                title="No jobs"
                description="Queue a bulk job above, or start/stop profiles from the Profiles page."
              />
            ) : (
              <JobsTable
                jobs={jobs}
                total={data?.total}
                hasNext={!!data?.next_cursor}
                canPrev={cursors.length > 1}
                onNext={() => data?.next_cursor && setCursors((c) => [...c, data.next_cursor])}
                onPrev={() => setCursors((c) => (c.length > 1 ? c.slice(0, -1) : c))}
                onCancel={(id) => cancel.mutate(id)}
                cancelling={cancel.isPending}
              />
            )}
            {hasActive && !isLoading && (
              <p className="mt-3 text-xs text-ink-3">
                Active jobs refresh automatically.{" "}
                <button
                  type="button"
                  className="underline"
                  onClick={() => refetch()}
                >
                  Refresh now
                </button>
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function QueueCard({
  pending,
  onQueue,
}: {
  pending: boolean;
  onQueue: (p: { type: string; profileIds: string[]; url?: string; direct: boolean }) => void;
}) {
  const [type, setType] = useState("bulk_launch");
  const [url, setUrl] = useState("");
  const [ids, setIds] = useState("");
  const [direct, setDirect] = useState("");

  const submit = () => {
    const profileIds = ids
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!profileIds.length) {
      toast.error("Enter at least one profile ID");
      return;
    }
    onQueue({
      type,
      profileIds,
      url: url.trim() || undefined,
      direct: direct === "1",
    });
    setIds("");
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Queue a bulk job</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Job type">
            {({ id }) => (
              <Select
                id={id}
                value={type}
                onChange={(e) => setType(e.target.value)}
                options={[
                  { value: "bulk_launch", label: "Bulk launch" },
                  { value: "bulk_stop", label: "Bulk stop" },
                  { value: "launch_profile", label: "Launch (single)" },
                  { value: "stop_profile", label: "Stop (single)" },
                ]}
              />
            )}
          </Field>
          <Field label="Start URL" hint="optional">
            {({ id }) => (
              <Input
                id={id}
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://…"
              />
            )}
          </Field>
          <Field label="Profile IDs" required hint="comma separated">
            {({ id }) => (
              <Input
                id={id}
                value={ids}
                onChange={(e) => setIds(e.target.value)}
                placeholder="profile_xxx, profile_yyy"
              />
            )}
          </Field>
          <Field label="Options">
            {({ id }) => (
              <Select
                id={id}
                value={direct}
                onChange={(e) => setDirect(e.target.value)}
                options={[
                  { value: "", label: "Default launch" },
                  { value: "1", label: "Direct (fast)" },
                ]}
              />
            )}
          </Field>
        </div>
        <div className="mt-4">
          <Button variant="primary" onClick={submit} loading={pending}>
            Queue job
          </Button>
        </div>
        <p className="mt-3 text-xs text-ink-3">
          Job types come from the backend (
          <Mono>launch_profile, stop_profile, bulk_launch, bulk_stop</Mono>).
        </p>
      </CardContent>
    </Card>
  );
}

function JobsTable({
  jobs,
  total,
  hasNext,
  canPrev,
  onNext,
  onPrev,
  onCancel,
  cancelling,
}: {
  jobs: JobSummary[];
  total?: number;
  hasNext: boolean;
  canPrev: boolean;
  onNext: () => void;
  onPrev: () => void;
  onCancel: (id: string) => void;
  cancelling: boolean;
}) {
  return (
    <>
      <Table>
        <THead>
          <TR>
            <TH>Job</TH>
            <TH>Type</TH>
            <TH>Status</TH>
            <TH>Progress</TH>
            <TH>Profiles</TH>
            <TH>Created</TH>
            <TH>Error</TH>
            <TH>
              <span className="sr-only">Actions</span>
            </TH>
          </TR>
        </THead>
        <TBody>
          {jobs.map((j) => {
            const pct =
              j.progress != null
                ? Math.min(100, Math.max(0, Number(j.progress)))
                : null;
            const active = isActive(j);
            const profileIds = (j.profileIds as string[] | undefined) ?? [];
            return (
              <TR key={j.id}>
                <TD>
                  <Mono>{j.id}</Mono>
                </TD>
                <TD>{String(j.type ?? "")}</TD>
                <TD>
                  <Badge tone={jobTone(String(j.status ?? ""))}>{String(j.status ?? "—")}</Badge>
                </TD>
                <TD className="min-w-28">
                  {pct != null ? (
                    <div
                      className="h-1.5 w-full overflow-hidden rounded-full bg-border"
                      role="progressbar"
                      aria-valuenow={pct}
                      aria-valuemin={0}
                      aria-valuemax={100}
                    >
                      <div className="h-full rounded-full bg-brand-500" style={{ width: `${pct}%` }} />
                    </div>
                  ) : (
                    <span className="text-ink-3">—</span>
                  )}
                </TD>
                <TD className="text-right tabular-nums">{profileIds.length}</TD>
                <TD className="whitespace-nowrap text-ink-3">
                  {timeAgo((j.created_at ?? j.createdAt) as string | undefined)}
                </TD>
                <TD className="max-w-56 truncate text-ink-3">
                  {j.error ? String(j.error).slice(0, 80) : "—"}
                </TD>
                <TD>
                  {active && (
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => onCancel(j.id)}
                      disabled={cancelling}
                    >
                      Cancel
                    </Button>
                  )}
                </TD>
              </TR>
            );
          })}
        </TBody>
      </Table>
      <div className="mt-3 flex items-center gap-2">
        <Button variant="secondary" size="sm" onClick={onPrev} disabled={!canPrev}>
          ← Prev
        </Button>
        <Button variant="secondary" size="sm" onClick={onNext} disabled={!hasNext}>
          Next →
        </Button>
        <span className="flex-1" />
        {total != null && (
          <span className="text-xs text-ink-3">
            {jobs.length} of {total} jobs
          </span>
        )}
      </div>
    </>
  );
}
