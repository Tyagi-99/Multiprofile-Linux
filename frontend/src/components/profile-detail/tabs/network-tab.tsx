import { useState } from "react";
import { toast } from "sonner";
import { apiMessage, post } from "../../../lib/api";
import { useProfileNetworkHealth } from "../../../lib/queries";
import type { Profile } from "../../../lib/types";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";
import { EmptyState } from "../../ui/empty-state";
import { Skeleton } from "../../ui/skeleton";
import { KV, Mono, fmtVal } from "../kv";

export function NetworkTab({ profile }: { profile: Profile }) {
  const { data, isLoading, isError, refetch, isFetching } = useProfileNetworkHealth(profile.id);
  const [testing, setTesting] = useState(false);
  const r = profile as Record<string, unknown>;
  const hasProxy = !!(r.proxyHost ?? r.proxy_host);

  const testProxy = async () => {
    setTesting(true);
    try {
      const t = await post<{ ok?: boolean; success?: boolean }>("/api/proxy/test", {
        host: r.proxyHost ?? r.proxy_host,
        port: r.proxyPort ?? r.proxy_port,
        username: r.proxyUser ?? r.proxy_user,
        password: r.proxyPass ?? r.proxy_pass,
        protocol: r.proxyType ?? r.proxy_type ?? "http",
      });
      if (t.ok || t.success) toast.success("Proxy test passed");
      else toast.error("Proxy test failed");
    } catch (e) {
      toast.error("Proxy test failed", { description: apiMessage(e) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Connection</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="grid gap-2">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-5 w-1/2" />
            </div>
          ) : isError || !data ? (
            <EmptyState
              title="Network health unavailable"
              description="GET /api/profiles/:id/network-health did not return data."
              action={{ label: "Retry", onClick: () => refetch() }}
            />
          ) : (
            <>
              <KV
                pairs={[
                  ["Mode", fmtVal(data.mode)],
                  [
                    "Status",
                    data.status === "DIRECT" ? (
                      <Badge key="s" tone="info">Direct</Badge>
                    ) : data.ok ? (
                      <Badge key="s" tone="ok">OK</Badge>
                    ) : (
                      <Badge key="s" tone="err">Failed</Badge>
                    ),
                  ],
                  [
                    "Egress IP",
                    data.proxyHealth ? (
                      <Mono key="e">{data.proxyHealth.egress_ip ?? data.proxyHealth.egressIp ?? "—"}</Mono>
                    ) : (
                      "—"
                    ),
                  ],
                  [
                    "Latency",
                    data.proxyHealth?.latency_ms != null ? `${data.proxyHealth.latency_ms} ms` : "—",
                  ],
                  ["Message", fmtVal(data.message)],
                ]}
              />
              <p className="mt-3 text-xs text-ink-3">
                Source: GET /api/profiles/:id/network-health.
              </p>
              <div className="mt-3 flex gap-2">
                <Button variant="secondary" size="sm" onClick={() => refetch()} disabled={isFetching}>
                  Re-check
                </Button>
                {hasProxy && (
                  <Button variant="secondary" size="sm" onClick={testProxy} loading={testing}>
                    Test proxy config
                  </Button>
                )}
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
