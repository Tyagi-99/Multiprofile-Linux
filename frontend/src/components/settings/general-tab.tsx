import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiMessage, get, post, getApiToken, setApiToken } from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Field } from "../ui/field";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Mono } from "../profile-detail/kv";

interface HealthResponse {
  ok?: boolean;
  app?: string;
  version?: string;
  [key: string]: unknown;
}

export function GeneralTab() {
  const qc = useQueryClient();
  const [token, setToken] = useState(() => getApiToken() ?? "");
  const [out, setOut] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { data: sys, isLoading } = useQuery({
    queryKey: ["system", "health"],
    queryFn: () => get<HealthResponse>("/api/health"),
    staleTime: 30_000,
    retry: 1,
  });

  const saveToken = () => {
    const t = token.trim();
    setApiToken(t || null);
    toast.success(t ? "Token saved" : "Token cleared");
    qc.invalidateQueries({ queryKey: ["system", "health"] });
  };

  const clearToken = () => {
    setToken("");
    setApiToken(null);
    toast.success("Token cleared");
  };

  const reap = async () => {
    setBusy(true);
    try {
      const d = await post<{ count?: number }>("/api/maintenance/reap", {});
      setOut(`Reaped ${d.count != null ? d.count : "?"} orphan(s).`);
    } catch (e) {
      setOut(`Reap failed: ${apiMessage(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const updateStatus = async () => {
    setBusy(true);
    try {
      const d = await get<unknown>("/api/update/status");
      setOut(`Update status: ${JSON.stringify(d)}`);
    } catch (e) {
      setOut(`Update status unavailable (${apiMessage(e)})`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>API access</CardTitle>
        </CardHeader>
        <CardContent>
          <Field label="Bearer token" hint="stored in this browser's localStorage">
            {({ id }) => (
              <Input
                id={id}
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                autoComplete="off"
              />
            )}
          </Field>
          <div className="mt-3 flex gap-2">
            <Button variant="primary" size="sm" onClick={saveToken}>
              Save token
            </Button>
            <Button variant="secondary" size="sm" onClick={clearToken}>
              Clear
            </Button>
          </div>
          <p className="mt-3 text-xs text-ink-3">
            The backend only enforces a token when <Mono>MULTIPROFILE_API_TOKEN</Mono> is set
            server-side. If unset, all endpoints are open and no token is needed.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Maintenance</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={reap} loading={busy}>
              Reap orphaned browsers
            </Button>
            <Button variant="secondary" size="sm" onClick={updateStatus} loading={busy}>
              Check update status
            </Button>
          </div>
          {out && <p className="mt-3 text-xs text-ink-3">{out}</p>}
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Backend health{" "}
            <span className="font-mono text-xs font-normal text-ink-3">GET /api/health</span>
            {sys && <Badge tone={sys.ok === false ? "err" : "ok"}>{sys.ok === false ? "down" : "ok"}</Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : (
            <pre className="overflow-x-auto rounded-lg bg-surface-2 p-4 font-mono text-xs text-ink-2">
              {sys ? JSON.stringify(sys, null, 2) : "unavailable"}
            </pre>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
