import { useProfileConsistency, useProfileHealth } from "../../../lib/queries";
import type { Profile } from "../../../lib/types";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";
import { EmptyState } from "../../ui/empty-state";
import { Skeleton } from "../../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../../ui/table";
import { KV, Mono, fmtVal } from "../kv";

function checkTone(s: string): "ok" | "warn" | "err" | "idle" {
  const v = s.toUpperCase();
  if (v === "PASS" || v === "OK" || v === "HEALTHY") return "ok";
  if (v === "WARN" || v === "WARNING") return "warn";
  if (v === "FAIL" || v === "ERROR" || v === "BLOCKED") return "err";
  return "idle";
}

function fmtFpVal(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "object") {
    const s = JSON.stringify(v);
    return s.length > 80 ? s.slice(0, 80) + "…" : s;
  }
  return String(v);
}

export function HealthTab({ profile }: { profile: Profile }) {
  const health = useProfileHealth(profile.id);
  const cons = useProfileConsistency(profile.id);
  const h = health.data;

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Health snapshot</CardTitle>
        </CardHeader>
        <CardContent>
          {health.isLoading ? (
            <div className="grid gap-2">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-5 w-1/2" />
            </div>
          ) : health.isError || !h ? (
            <EmptyState
              title="Health endpoint did not return data"
              description="GET /api/profiles/:id/health failed."
              action={{ label: "Retry", onClick: () => health.refetch() }}
            />
          ) : (
            <>
              <KV
                pairs={[
                  [
                    "Overall",
                    h.overallOk ? (
                      <Badge key="o" tone="ok">healthy</Badge>
                    ) : (
                      <Badge key="o" tone={h.status === "proxy_failed" ? "err" : "warn"}>
                        {String(h.status ?? "unknown")}
                      </Badge>
                    ),
                  ],
                  ["Network mode", fmtVal(h.network_mode)],
                  [
                    "Proxy OK",
                    h.proxy_ok == null ? "—" : h.proxy_ok ? (
                      <Badge key="p" tone="ok">yes</Badge>
                    ) : (
                      <Badge key="p" tone="err">no</Badge>
                    ),
                  ],
                  ["Egress IP", h.egress_ip ? <Mono key="e">{h.egress_ip}</Mono> : "—"],
                  [
                    "Fingerprint loaded",
                    h.fingerprint_loaded ? (
                      <Badge key="f" tone="ok">yes</Badge>
                    ) : (
                      <Badge key="f" tone="warn">no</Badge>
                    ),
                  ],
                  [
                    "Extension loaded",
                    h.extension_loaded == null ? "—" : h.extension_loaded ? (
                      <Badge key="x" tone="ok">yes</Badge>
                    ) : (
                      <Badge key="x" tone="warn">no</Badge>
                    ),
                  ],
                ]}
              />
              <div className="mt-3">
                <Button variant="secondary" size="sm" onClick={() => health.refetch()}>
                  Refresh
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Consistency checks</CardTitle>
        </CardHeader>
        <CardContent>
          {cons.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : cons.data && Array.isArray(cons.data.checks) ? (
            <>
              <div className="mb-3 flex items-center gap-2">
                <Badge tone={checkTone(cons.data.status)}>{cons.data.status}</Badge>
                <span className="text-[13px] text-ink-3">overall</span>
              </div>
              <Table>
                <THead>
                  <TR>
                    <TH>Domain</TH>
                    <TH>Status</TH>
                    <TH>Expected</TH>
                    <TH>Actual</TH>
                    <TH>Message</TH>
                  </TR>
                </THead>
                <TBody>
                  {cons.data.checks.map((c, i) => (
                    <TR key={`${c.domain}-${i}`}>
                      <TD className="font-medium">{c.domain}</TD>
                      <TD>
                        <Badge tone={checkTone(c.status)}>{c.status}</Badge>
                      </TD>
                      <TD>
                        <Mono>{fmtFpVal(c.expected)}</Mono>
                      </TD>
                      <TD>
                        <Mono>{fmtFpVal(c.actual)}</Mono>
                      </TD>
                      <TD className="text-ink-3">{c.message ?? "—"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
              <p className="mt-3 text-xs text-ink-3">Source: GET /api/profiles/:id/consistency.</p>
            </>
          ) : (
            <p className="text-[13px] text-ink-3">
              Consistency endpoint not available for this profile.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
