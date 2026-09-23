import { useQuery } from "@tanstack/react-query";
import { get } from "../../../lib/api";
import type { Profile } from "../../../lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { KV, Mono, fmtVal } from "../kv";

interface Client { id: string; name?: string }

export function OverviewTab({ profile }: { profile: Profile }) {
  const { data: clients } = useQuery({
    queryKey: ["clients", "list"],
    queryFn: () => get<Client[]>("/api/clients"),
    staleTime: 60_000,
    retry: 1,
  });
  const clientName =
    clients?.find((c) => c.id === profile.clientId)?.name ?? profile.clientId ?? null;
  const r = profile as Record<string, unknown>;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Identity</CardTitle>
        </CardHeader>
        <CardContent>
          <KV
            pairs={[
              ["Name", fmtVal(profile.name)],
              ["ID", <Mono key="id">{profile.id}</Mono>],
              ["Client", fmtVal(clientName)],
              [
                "Tags",
                (profile.tags ?? []).length ? (
                  <span key="tags" className="flex flex-wrap gap-1">
                    {(profile.tags ?? []).map((t) => (
                      <Badge key={t} tone="idle">
                        {t}
                      </Badge>
                    ))}
                  </span>
                ) : (
                  "—"
                ),
              ],
              ["Notes", fmtVal(profile.notes)],
              ["Start URL", fmtVal(profile.startUrl)],
              ["Created", fmtVal(profile.createdAt)],
              ["Color", profile.color ? <Mono key="c">{profile.color}</Mono> : "—"],
            ]}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <KV
            pairs={[
              ["Device", fmtVal(profile.deviceId)],
              ["Network mode", fmtVal(r.networkMode)],
              [
                "Proxy",
                (() => {
                  const host = (r.proxyHost ?? r.proxy_host) as string | undefined;
                  if (!host) return "—";
                  const auth = r.proxyUser ?? r.proxy_user ? "••••@" : "";
                  return <Mono key="p">{`${r.proxyType ?? "http"}://${auth}${host}${r.proxyPort ? `:${r.proxyPort}` : ""}`}</Mono>;
                })(),
              ],
              ["Language", fmtVal(r.language)],
              ["Timezone", fmtVal(r.timezone)],
              ["WebRTC", fmtVal(r.webrtcMode)],
              ["Seed", r.seed != null ? <Mono key="s">{String(r.seed)}</Mono> : "—"],
            ]}
          />
        </CardContent>
      </Card>
    </div>
  );
}
