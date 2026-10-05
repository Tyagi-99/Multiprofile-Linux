/**
 * Usage — what the fleet actually ran, and against which limits.
 *
 * Nothing measured this before, so "which of my profiles is actually used?"
 * and "is this cap generous or mean?" had no answers. The metered quantity is
 * seconds spent running, because that is what a fleet costs; a launch count
 * is easy to record and says nothing.
 *
 * Two things this screen refuses to do. It does not round a number it did not
 * measure — a field the API could not read renders as "unavailable", never as
 * zero, because zero reads as an empty fleet rather than a broken query. And
 * it marks every limit with whether anything enforces it: showing five limits
 * of which two are real, without saying which, is the failure this audit kept
 * finding, in commercial dress.
 */

import { useState } from "react";
import { apiMessage } from "../../lib/api";
import { absoluteTime, timeAgo } from "../../lib/format";
import { usePlan, useUsage, useUsageSessions } from "../../lib/queries";
import type { PlanLimit, UsageBucket } from "../../lib/types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { EmptyState } from "../ui/empty-state";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { Mono } from "../profile-detail/kv";

const PERIODS = [
  { id: "day", label: "24 hours" },
  { id: "week", label: "7 days" },
  { id: "month", label: "30 days" },
  { id: "all", label: "All time" },
];

/** "3h 12m". Mirrors usage.format_seconds so both sides read alike. */
function duration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${s}s`;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <p className="text-[13px] text-ink-3">{label}</p>
        <p className="mt-1 text-2xl font-semibold tracking-tight text-ink-1">{value}</p>
        {hint && <p className="mt-1 text-xs text-ink-3">{hint}</p>}
      </CardContent>
    </Card>
  );
}

function LimitRow({ name, limit }: { name: string; limit: PlanLimit }) {
  return (
    <TR>
      <TD className="text-ink-2">{name}</TD>
      <TD className="whitespace-nowrap font-medium text-ink-1">
        {limit.value == null ? <span className="text-ink-3">—</span> : limit.value}
      </TD>
      <TD className="whitespace-nowrap">
        <Badge tone={limit.source === "env" ? "warn" : "idle"}>{limit.source}</Badge>
      </TD>
      <TD className="whitespace-nowrap">
        {limit.enforced ? (
          <Badge tone="ok">enforced</Badge>
        ) : (
          // Said out loud rather than omitted: a limit nothing checks is not
          // a limit, and letting it sit unlabelled beside real ones is how a
          // plan comes to describe a product that does not exist.
          <Badge tone="idle">declared only</Badge>
        )}
      </TD>
    </TR>
  );
}

function BucketTable({
  rows,
  label,
  empty,
}: {
  rows: UsageBucket[];
  label: string;
  empty: string;
}) {
  if (!rows.length) return <EmptyState title={empty} description="Nothing ran in this window." />;
  return (
    <Card>
      <CardContent className="pt-6">
        <Table>
          <THead>
            <TR>
              <TH>{label}</TH>
              <TH>Runtime</TH>
              <TH>Sessions</TH>
              <TH>Running now</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((row, i) => (
              <TR key={`${row.profileId ?? row.clientId ?? i}`}>
                <TD className="text-ink-1">
                  {row.name ?? (
                    // Usage for a deleted profile keeps counting and loses
                    // only its name; dropping the row would make the totals
                    // stop adding up.
                    <span className="text-ink-3">
                      {row.clientId === null && !row.profileId ? "No workspace" : "Deleted"}
                    </span>
                  )}
                </TD>
                <TD className="whitespace-nowrap font-medium text-ink-1">
                  {duration(row.seconds)}
                </TD>
                <TD className="text-ink-3">{row.sessions}</TD>
                <TD>{row.open > 0 ? <Badge tone="ok">{row.open}</Badge> : <span className="text-ink-3">—</span>}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

export function UsagePageView() {
  const [period, setPeriod] = useState("month");
  const [showSessions, setShowSessions] = useState(false);
  const usage = useUsage(period);
  const plan = usePlan();
  const sessions = useUsageSessions(period);

  const total = usage.data?.total;

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight text-ink-1">Usage</h1>
        <span className="flex-1" />
        <div className="flex gap-1 rounded-lg border border-border-subtle p-1">
          {PERIODS.map((p) => (
            <Button
              key={p.id}
              size="sm"
              variant={period === p.id ? "primary" : "ghost"}
              onClick={() => setPeriod(p.id)}
            >
              {p.label}
            </Button>
          ))}
        </div>
      </div>

      {usage.isError ? (
        <EmptyState
          title="Couldn't load usage"
          description={apiMessage(usage.error)}
          action={{ label: "Retry", onClick: () => usage.refetch() }}
        />
      ) : usage.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat
              label="Runtime"
              value={duration(total?.seconds)}
              hint="Time spent running, not launches"
            />
            <Stat label="Sessions" value={String(total?.sessions ?? 0)} />
            <Stat
              label="Running now"
              value={String(total?.open ?? 0)}
              hint={
                plan.data?.limits?.maxConcurrent?.value != null
                  ? `of ${plan.data.limits.maxConcurrent.value} allowed`
                  : undefined
              }
            />
          </div>

          <h2 className="pt-2 text-[15px] font-semibold text-ink-1">By profile</h2>
          <BucketTable rows={usage.data?.byProfile ?? []} label="Profile" empty="No usage yet" />

          {(usage.data?.byClient?.length ?? 0) > 1 && (
            <>
              <h2 className="pt-2 text-[15px] font-semibold text-ink-1">By workspace</h2>
              <BucketTable
                rows={usage.data?.byClient ?? []}
                label="Workspace"
                empty="No usage yet"
              />
            </>
          )}

          <div className="flex items-center gap-2 pt-2">
            <h2 className="text-[15px] font-semibold text-ink-1">Sessions</h2>
            <Button size="sm" variant="ghost" onClick={() => setShowSessions((v) => !v)}>
              {showSessions ? "Hide" : "Show the rows behind these totals"}
            </Button>
          </div>
          {showSessions && (
            <Card>
              <CardContent className="pt-6">
                <Table>
                  <THead>
                    <TR>
                      <TH>Profile</TH>
                      <TH>Started</TH>
                      <TH>Ran for</TH>
                      <TH>Ended</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {(sessions.data?.items ?? []).map((s) => (
                      <TR key={s.id}>
                        <TD className="text-ink-1">
                          {s.profileName ?? <span className="text-ink-3">Deleted</span>}
                        </TD>
                        <TD className="whitespace-nowrap text-ink-3" title={absoluteTime(s.started_at)}>
                          {timeAgo(s.started_at)}
                        </TD>
                        <TD className="whitespace-nowrap font-medium text-ink-1">
                          {duration(s.seconds)}
                        </TD>
                        <TD className="whitespace-nowrap">
                          {s.open ? (
                            <Badge tone="ok">running</Badge>
                          ) : (
                            // "stopped" and "the machine lost power" are the
                            // same duration and very different facts.
                            <Mono>
                              <span className="text-ink-3">{s.end_reason ?? "—"}</span>
                            </Mono>
                          )}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
                <p className="mt-3 text-xs text-ink-3">Source: GET /api/usage/sessions.</p>
              </CardContent>
            </Card>
          )}
        </>
      )}

      <h2 className="pt-2 text-[15px] font-semibold text-ink-1">Limits</h2>
      {plan.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : plan.isError ? (
        <EmptyState title="Couldn't load the plan" description={apiMessage(plan.error)} />
      ) : (
        <Card>
          <CardContent className="pt-6">
            <div className="mb-3 flex items-center gap-2">
              <span className="text-[13px] text-ink-2">Plan</span>
              <Badge tone="brand">{plan.data?.plan.name}</Badge>
              {plan.data?.recognised === false && (
                <span className="text-xs text-warn">{plan.data.warning}</span>
              )}
            </div>
            <Table>
              <THead>
                <TR>
                  <TH>Limit</TH>
                  <TH>Value</TH>
                  <TH>From</TH>
                  <TH>Status</TH>
                </TR>
              </THead>
              <TBody>
                {Object.entries(plan.data?.limits ?? {}).map(([name, limit]) => (
                  <LimitRow key={name} name={name} limit={limit} />
                ))}
              </TBody>
            </Table>
            <p className="mt-3 text-xs text-ink-3">
              Source: GET /api/plan. An environment variable overrides the plan — on a local
              install the operator owns the machine, and pretending otherwise would be theatre.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
