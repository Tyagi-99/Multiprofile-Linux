import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiMessage, get } from "../../lib/api";
import { auditKeys } from "../../lib/query-keys";
import type { AuditItem, AuditPage } from "../../lib/types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { EmptyState } from "../ui/empty-state";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { Mono } from "../profile-detail/kv";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function formatTime(ts: string): string {
  const t = new Date(ts).getTime();
  return Number.isNaN(t) ? ts : new Date(t).toLocaleString();
}

function Row({ e }: { e: AuditItem }) {
  const detail = e.detail != null ? JSON.stringify(e.detail) : "";
  return (
    <TR>
      <TD className="whitespace-nowrap text-ink-3">{formatTime(e.ts)}</TD>
      <TD>
        <Mono>{e.action || "—"}</Mono>
      </TD>
      <TD>
        {e.profileId ? (
          <a
            href={`#/profiles/${encodeURIComponent(e.profileId)}`}
            className="font-mono text-[13px] text-brand-600 hover:underline dark:text-brand-400"
          >
            {String(e.profileId).slice(0, 12)}…
          </a>
        ) : (
          <span className="text-ink-3">—</span>
        )}
      </TD>
      <TD>
        <Badge tone={e.ok ? "ok" : "err"}>{e.ok ? "ok" : "fail"}</Badge>
      </TD>
      <TD
        className="max-w-[420px] truncate text-[13px] text-ink-3"
        title={detail}
      >
        <Mono>
          <span className="text-ink-3">{detail.slice(0, 140)}</span>
        </Mono>
      </TD>
    </TR>
  );
}

export function AuditPageView() {
  const [q, setQ] = useState("");
  const [onlyFail, setOnlyFail] = useState(false);
  const dq = useDebounced(q, 280);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: auditKeys.list({ limit: 200 }),
    queryFn: async () => {
      try {
        return await get<AuditPage>("/api/audit", { limit: 200 });
      } catch (e) {
        const msg = apiMessage(e);
        if (/404|not found/i.test(msg)) throw new Error("NOT_EXPOSED");
        throw e;
      }
    },
    staleTime: 10_000,
    retry: false,
  });

  const rows = useMemo(() => {
    const items = data?.items ?? [];
    const needle = dq.trim().toLowerCase();
    return items
      .filter((e) => {
        if (onlyFail && e.ok !== false) return false;
        if (!needle) return true;
        return [e.action, e.profileId, e.ts, JSON.stringify(e.detail ?? "")]
          .join(" ")
          .toLowerCase()
          .includes(needle);
      })
      .slice()
      .reverse();
  }, [data, dq, onlyFail]);

  const notExposed = isError && error instanceof Error && error.message === "NOT_EXPOSED";

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight text-ink-1">Audit Log</h1>
        <span className="flex-1" />
        {!isLoading && !notExposed && (
          <span className="text-xs text-ink-3">{rows.length} entries</span>
        )}
        <Button variant="secondary" size="sm" onClick={() => refetch()}>
          Refresh
        </Button>
      </div>

      {isLoading ? (
        <div className="grid gap-2">
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : notExposed ? (
        <EmptyState
          title="Audit log unavailable"
          description="This backend does not expose GET /api/audit yet. Actions are not being recorded for display."
        />
      ) : isError ? (
        <EmptyState
          title="Couldn't load audit log"
          description={apiMessage(error)}
          action={{ label: "Retry", onClick: () => refetch() }}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <Input
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter action, profile, detail…"
              aria-label="Filter audit log"
              className="w-64"
            />
            <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-2">
              <input
                type="checkbox"
                checked={onlyFail}
                onChange={(e) => setOnlyFail(e.target.checked)}
                className="h-4 w-4 accent-brand-600"
              />
              failures only
            </label>
          </div>

          {rows.length ? (
            <Card>
              <CardContent className="pt-6">
                <Table>
                  <THead>
                    <TR>
                      <TH>Time</TH>
                      <TH>Action</TH>
                      <TH>Profile</TH>
                      <TH>Result</TH>
                      <TH>Detail</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {rows.map((e, i) => (
                      <Row key={`${e.ts}-${e.action}-${i}`} e={e} />
                    ))}
                  </TBody>
                </Table>
                <p className="mt-3 text-xs text-ink-3">Source: GET /api/audit.</p>
              </CardContent>
            </Card>
          ) : (
            <EmptyState
              title="No audit entries"
              description={
                dq || onlyFail
                  ? "Nothing matches the current filter."
                  : "Actions are recorded here as they happen."
              }
            />
          )}
        </>
      )}

    </div>
  );
}
