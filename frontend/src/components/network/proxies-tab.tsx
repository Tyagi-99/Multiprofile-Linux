import { useState } from "react";
import { toast } from "sonner";
import { apiMessage, post } from "../../lib/api";
import { useProfilesSearch } from "../../lib/queries";
import { useProfileNetworkHealth } from "../../lib/queries";
import type { Profile } from "../../lib/types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { EmptyState } from "../ui/empty-state";
import { Field } from "../ui/field";
import { Input, Select } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { KV, Mono } from "../profile-detail/kv";

interface ProxyTestResult {
  ok?: boolean;
  success?: boolean;
  egress_ip?: string;
  egressIp?: string;
  country?: string;
  latency_ms?: number;
  error?: string;
}

function proxyLabel(p: Profile): string {
  const r = p as Record<string, unknown>;
  const host = (r.proxyHost ?? r.proxy_host) as string | undefined;
  if (!host) return "—";
  const auth = r.proxyUser ?? r.proxy_user ? "••••@" : "";
  return `${(r.proxyType ?? r.proxy_type ?? "http") as string}://${auth}${host}${r.proxyPort ?? r.proxy_port ? `:${(r.proxyPort ?? r.proxy_port) as number}` : ""}`;
}

function ProxyHealthCell({ id }: { id: string }) {
  const { data, isLoading, isError } = useProfileNetworkHealth(id);
  if (isLoading) return <span className="text-ink-3">…</span>;
  if (isError || !data)
    return (
      <Badge tone="idle">
        <span className="text-ink-3">unknown</span>
      </Badge>
    );
  if (data.status === "DIRECT") return <Badge tone="info">Direct</Badge>;
  return <Badge tone={data.ok ? "ok" : "err"}>{data.ok ? "OK" : "Failed"}</Badge>;
}

function ProxyEgressCell({ id }: { id: string }) {
  const { data, isLoading, isError } = useProfileNetworkHealth(id);
  if (isLoading) return <span className="text-ink-3">…</span>;
  if (isError || !data?.proxyHealth) return <span className="text-ink-3">—</span>;
  const ip = data.proxyHealth.egress_ip ?? data.proxyHealth.egressIp;
  return ip ? <Mono>{ip}</Mono> : <span className="text-ink-3">—</span>;
}

export function ProxiesTab() {
  const { data } = useProfilesSearch(500);
  const profiles = data?.items ?? [];
  const withProxy = profiles.filter((p) => {
    const r = p as Record<string, unknown>;
    return !!(r.proxyHost ?? r.proxy_host);
  });

  const [proto, setProto] = useState("http");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ProxyTestResult | null>(null);

  const test = async () => {
    if (!host.trim() || !port.trim()) {
      toast.error("Host and port are required");
      return;
    }
    setTesting(true);
    setResult(null);
    try {
      const d = await post<ProxyTestResult>("/api/proxy/test", {
        host: host.trim(),
        port: Number(port),
        protocol: proto,
        username: username || undefined,
        password: password || undefined,
        timeout: 10,
      });
      setResult(d);
    } catch (e) {
      setResult({ ok: false, error: apiMessage(e) });
    } finally {
      setTesting(false);
    }
  };

  const passed = result != null && (result.ok || result.success);

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Test a proxy</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Protocol">
              {({ id }) => (
                <Select
                  id={id}
                  value={proto}
                  onChange={(e) => setProto(e.target.value)}
                  options={[
                    { value: "http", label: "HTTP" },
                    { value: "https", label: "HTTPS" },
                    { value: "socks5", label: "SOCKS5" },
                  ]}
                />
              )}
            </Field>
            <Field label="Host" required>
              {({ id }) => (
                <Input
                  id={id}
                  value={host}
                  onChange={(e) => setHost(e.target.value)}
                  placeholder="203.0.113.10"
                />
              )}
            </Field>
            <Field label="Port" required>
              {({ id }) => (
                <Input
                  id={id}
                  type="number"
                  value={port}
                  onChange={(e) => setPort(e.target.value)}
                  placeholder="8080"
                />
              )}
            </Field>
            <Field label="Username">
              {({ id }) => (
                <Input
                  id={id}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="off"
                />
              )}
            </Field>
            <Field label="Password">
              {({ id }) => (
                <Input
                  id={id}
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="off"
                />
              )}
            </Field>
          </div>
          <div className="mt-4">
            <Button variant="primary" onClick={test} loading={testing}>
              Test connection
            </Button>
          </div>
          {testing && <Skeleton className="mt-4 h-20 w-full" />}
          {result && !testing && (
            <Card className="mt-4">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  Result{" "}
                  <Badge tone={passed ? "ok" : "err"}>{passed ? "pass" : "fail"}</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <KV
                  pairs={[
                    ["Egress IP", result.egress_ip ?? result.egressIp ? <Mono key="e">{result.egress_ip ?? result.egressIp}</Mono> : "—"],
                    ["Country", result.country ?? "—"],
                    ["Latency", result.latency_ms != null ? `${result.latency_ms} ms` : "—"],
                    ["Error", result.error ?? "—"],
                  ]}
                />
              </CardContent>
            </Card>
          )}
          <p className="mt-3 text-xs text-ink-3">
            Uses POST /api/proxy/test. Passwords are never displayed or logged.
          </p>
        </CardContent>
      </Card>

      <div>
        <h3 className="mb-2 text-sm font-semibold text-ink-1">
          Profiles using proxies{" "}
          <span className="font-normal text-ink-3">
            {withProxy.length} of {profiles.length}
          </span>
        </h3>
        {withProxy.length ? (
          <Card>
            <CardContent className="pt-6">
              <Table>
                <THead>
                  <TR>
                    <TH>Profile</TH>
                    <TH>Proxy</TH>
                    <TH>Health</TH>
                    <TH>Egress IP</TH>
                  </TR>
                </THead>
                <TBody>
                  {withProxy.map((p) => (
                    <TR key={p.id}>
                      <TD>
                        <a
                          href={`#/profiles/${encodeURIComponent(p.id)}`}
                          className="font-medium text-brand-600 hover:underline dark:text-brand-400"
                        >
                          {p.name || p.id}
                        </a>
                      </TD>
                      <TD>
                        <Mono>{proxyLabel(p)}</Mono>
                      </TD>
                      <TD>
                        <ProxyHealthCell id={p.id} />
                      </TD>
                      <TD>
                        <ProxyEgressCell id={p.id} />
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardContent>
          </Card>
        ) : (
          <EmptyState
            title="No proxies in use"
            description="No profiles currently have a proxy configured."
          />
        )}
      </div>
    </div>
  );
}
