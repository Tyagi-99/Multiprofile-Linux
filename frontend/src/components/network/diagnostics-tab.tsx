import { useMemo, useState } from "react";
import { apiMessage, post } from "../../lib/api";
import { useProfilesSearch } from "../../lib/queries";
import { useUI } from "../../stores/ui";
import { Alert } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { EmptyState } from "../ui/empty-state";
import { Select } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { KV, Mono } from "../profile-detail/kv";

interface NetTest {
  health?: string;
  egress_ip?: string;
  country?: string;
  timezone?: string;
  latency_ms?: number;
  dns?: { status?: string; detail?: string };
  ipv6?: { status?: string; detail?: string };
  error?: string;
}

function checkTone(s: string): "ok" | "warn" | "err" | "idle" {
  const v = s.toUpperCase();
  if (v === "PASS" || v === "OK" || v === "HEALTHY") return "ok";
  if (v === "WARN" || v === "WARNING") return "warn";
  if (v === "FAIL" || v === "ERROR" || v === "BLOCKED") return "err";
  return "idle";
}

export function DiagnosticsTab() {
  const { data, isLoading, isError } = useProfilesSearch(500);
  const openDialog = useUI((s) => s.openDialog);
  const profiles = useMemo(() => data?.items ?? [], [data]);
  const [selId, setSelId] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<NetTest | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const run = async () => {
    const id = selId || profiles[0]?.id;
    if (!id) return;
    setRunning(true);
    setResult(null);
    setUnavailable(false);
    setFailed(null);
    try {
      const nt = await post<NetTest>(`/api/profiles/${encodeURIComponent(id)}/network/test`, {});
      setResult(nt);
    } catch (e) {
      const msg = apiMessage(e);
      if (/404|not found/i.test(msg)) setUnavailable(true);
      else setFailed(msg);
    } finally {
      setRunning(false);
    }
  };

  if (isLoading) return <Skeleton className="h-40 w-full" />;
  if (isError || !profiles.length) {
    return (
      <EmptyState
        title="No profiles"
        description="Create a profile first."
        action={{ label: "New profile", onClick: () => openDialog("newProfile") }}
      />
    );
  }

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium text-ink-2">Profile</span>
        <Select
          aria-label="Select profile"
          className="w-72"
          value={selId || profiles[0].id}
          onChange={(e) => setSelId(e.target.value)}
          options={profiles.map((p) => ({ value: p.id, label: p.name || p.id }))}
        />
        <Button variant="primary" size="sm" onClick={run} loading={running}>
          Run diagnostics
        </Button>
      </div>

      {running && <Skeleton className="h-40 w-full" />}

      {!running && !result && !unavailable && !failed && (
        <EmptyState
          title="Network diagnostics"
          description="Pick a profile and run the check."
        />
      )}

      {unavailable && (
        <Alert tone="info" title="Extended test unavailable">
          The extended network test endpoint (POST /api/profiles/:id/network/test) is not
          available on this backend. Basic health is shown on the profile&apos;s Network tab
          instead.
        </Alert>
      )}

      {failed && (
        <Alert tone="error" title="Diagnostics failed">
          {failed}
        </Alert>
      )}

      {result && (
        <Card>
          <CardHeader>
            <CardTitle>Diagnostics result</CardTitle>
          </CardHeader>
          <CardContent>
            <KV
              pairs={[
                [
                  "Health",
                  result.health ? (
                    <Badge key="h" tone={checkTone(result.health)}>{result.health}</Badge>
                  ) : (
                    "—"
                  ),
                ],
                ["Egress IP", result.egress_ip ? <Mono key="e">{result.egress_ip}</Mono> : "—"],
                ["Country", result.country ?? "—"],
                ["Timezone", result.timezone ?? "—"],
                ["Latency", result.latency_ms != null ? `${result.latency_ms} ms` : "—"],
                [
                  "DNS",
                  result.dns ? (
                    <span key="d" className="flex items-center gap-2">
                      <Badge tone={checkTone(result.dns.status ?? "")}>{result.dns.status}</Badge>
                      <span className="text-ink-3">{result.dns.detail ?? ""}</span>
                    </span>
                  ) : (
                    "—"
                  ),
                ],
                [
                  "IPv6",
                  result.ipv6 ? (
                    <span key="v" className="flex items-center gap-2">
                      <Badge tone={checkTone(result.ipv6.status ?? "")}>{result.ipv6.status}</Badge>
                      <span className="text-ink-3">{result.ipv6.detail ?? ""}</span>
                    </span>
                  ) : (
                    "—"
                  ),
                ],
                ["Error", result.error ?? "—"],
              ]}
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
