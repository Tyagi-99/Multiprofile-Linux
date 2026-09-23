import {
  useActiveJobsCount,
  useMetricsSummary,
  useProfilesCount,
  useProfilesSearch,
  useRecentAudit,
  useSystemHealth,
} from "../../lib/queries";
import { useUI } from "../../stores/ui";
import { Alert } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { EmptyState } from "../ui/empty-state";
import { Skeleton } from "../ui/skeleton";
import { Stat } from "../ui/stat";
import { Activity, Cpu, MemoryStick, Plus } from "lucide-react";

function formatTime(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function StatGrid() {
  const total = useProfilesCount();
  const running = useProfilesCount("running");
  const idle = useProfilesCount("idle");
  const error = useProfilesCount("error");
  const jobs = useActiveJobsCount();

  const stats = [
    { label: "Total profiles", q: total },
    { label: "Running", q: running, deltaTone: "neutral" as const },
    { label: "Idle", q: idle },
    {
      label: "Error",
      q: error,
      deltaTone: (error.data ?? 0) > 0 ? ("down" as const) : ("neutral" as const),
    },
    { label: "Active jobs", q: jobs },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      {stats.map((s) => (
        <Stat
          key={s.label}
          label={s.label}
          value={s.q.isLoading ? "…" : String(s.q.data ?? 0)}
          deltaTone={s.deltaTone}
        />
      ))}
    </div>
  );
}

function ResourcesCard() {
  const { data, isLoading, isError } = useMetricsSummary();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Resources</CardTitle>
        <CardDescription>Live point-in-time measurements across running profiles</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="grid gap-2">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-5 w-3/5" />
          </div>
        ) : isError || !data?.ok ? (
          <EmptyState
            icon={<Cpu size={24} aria-hidden />}
            title="Measurements unavailable"
            description="Could not read resource usage right now."
          />
        ) : data.measured === 0 ? (
          <EmptyState
            icon={<Cpu size={24} aria-hidden />}
            title="No live measurements"
            description={
              data.running_profiles > 0
                ? `${data.running_profiles} profile${data.running_profiles === 1 ? " is" : "s are"} marked running, but no live process could be sampled.`
                : "Launch a profile to see CPU and memory here. Nothing is estimated."
            }
          />
        ) : (
          <dl className="grid gap-3">
            <div className="flex items-center justify-between">
              <dt className="flex items-center gap-2 text-[13px] text-ink-3">
                <Cpu size={14} aria-hidden /> Avg CPU
              </dt>
              <dd className="text-sm font-semibold text-ink-1">
                {data.avg_cpu_percent == null ? "—" : `${data.avg_cpu_percent.toFixed(1)}%`}
              </dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="flex items-center gap-2 text-[13px] text-ink-3">
                <MemoryStick size={14} aria-hidden /> Memory (RSS)
              </dt>
              <dd className="text-sm font-semibold text-ink-1">
                {data.total_memory_rss_mb >= 1024
                  ? `${(data.total_memory_rss_mb / 1024).toFixed(1)} GB`
                  : `${Math.round(data.total_memory_rss_mb)} MB`}
              </dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-[13px] text-ink-3">Sampled profiles</dt>
              <dd className="text-sm font-semibold text-ink-1">
                {data.measured} of {data.running_profiles}
              </dd>
            </div>
            {data.unavailable > 0 && (
              <p className="text-xs text-ink-3">
                {data.unavailable} running profile{data.unavailable === 1 ? "" : "s"} had no live
                process to sample — shown honestly, not estimated.
              </p>
            )}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function ActivityCard() {
  const { data, isLoading, isError } = useRecentAudit(8);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Recent activity</CardTitle>
            <CardDescription>Latest entries from the audit log</CardDescription>
          </div>
          <a href="#/audit" className="text-[13px] font-medium text-brand-600 hover:text-brand-500 dark:text-brand-400">
            View all
          </a>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading ? (
          <div className="grid gap-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : isError || !data?.items?.length ? (
          <EmptyState
            icon={<Activity size={24} aria-hidden />}
            title="No recent activity"
            description="The audit log is empty or unavailable."
          />
        ) : (
          <ul className="divide-y divide-border-subtle">
            {data.items.map((a, i) => (
              <li key={`${a.ts}-${i}`} className="flex items-center gap-3 py-2.5">
                <Badge tone={a.ok === false ? "err" : "idle"}>{a.action}</Badge>
                <span className="min-w-0 flex-1 truncate text-[13px] text-ink-2">
                  {a.profileId ?? a.detail ?? "—"}
                </span>
                <time className="shrink-0 text-xs text-ink-3">{formatTime(a.ts)}</time>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function AttentionCard() {
  const { data } = useProfilesSearch(5);
  // Error-state profiles from the cached search; the count query drives the badge.
  const { data: errorCount } = useProfilesCount("error");
  const errors = (data?.items ?? []).filter((p) => p.launchState === "error").slice(0, 5);
  if ((errorCount ?? 0) === 0) return null;
  return (
    <Alert tone="error" title={`${errorCount} profile${errorCount === 1 ? "" : "s"} in error state`}>
      <ul className="mt-1 grid gap-1">
        {errors.map((p) => (
          <li key={p.id}>
            <a href={`#/profiles/${p.id}`} className="font-medium underline underline-offset-2">
              {p.name}
            </a>
          </li>
        ))}
        {errors.length === 0 && <li>Open Profiles to review them.</li>}
      </ul>
    </Alert>
  );
}

export function Overview() {
  const openDialog = useUI((s) => s.openDialog);
  const { data: health } = useSystemHealth();
  const today = new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  return (
    <div className="mx-auto max-w-6xl space-y-5 px-6 py-6">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight text-ink-1">Overview</h1>
          <p className="mt-0.5 text-[13px] text-ink-3">
            {today}
            {health && !health.chrome_ok && (
              <span className="text-warn"> · Chrome not detected — launches will fail</span>
            )}
          </p>
        </div>
        <Button variant="primary" onClick={() => openDialog("newProfile")}>
          <Plus size={16} aria-hidden /> New profile
        </Button>
      </div>

      <AttentionCard />
      <StatGrid />

      <div className="grid gap-4 lg:grid-cols-2">
        <ActivityCard />
        <ResourcesCard />
      </div>
    </div>
  );
}
