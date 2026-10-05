import { useQuery } from "@tanstack/react-query";
import { get } from "../../../lib/api";
import type { Profile } from "../../../lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { KV, Mono, fmtVal } from "../kv";

interface Client { id: string; name?: string }

/**
 * The timezone the browser will actually report, not the one on the record.
 *
 * They are not always the same. When `geoAuto` is set the stored value is only
 * the creation default, and the launch replaces it with one that agrees with
 * where the traffic leaves from -- the exit IP's country through a proxy, this
 * machine's own zone with no proxy. Rendering the stored value would be a field
 * labelled "Timezone" showing something the page never sees, which is the first
 * rule in the UI spec's list of things this console must never do.
 *
 * `geoSource` says which input decided it, so the reason is shown rather than
 * the value appearing as a fact from nowhere.
 */
function EffectiveTz({ r }: { r: Record<string, unknown> }) {
  const effective = (r.effectiveTimezone as string) ?? (r.timezone as string) ?? null;
  if (!effective) return <>{fmtVal(null)}</>;
  const source = r.geoSource as string | undefined;
  const why =
    source === "host"
      ? "no proxy \u2014 this machine"
      : source === "exit_ip"
        ? "from the exit IP"
        : null;
  const conflicts = (r.geoConflicts as string[] | undefined) ?? [];
  return (
    <span className="flex flex-col gap-0.5">
      <span>{effective}</span>
      {why ? (
        <span className="text-xs text-ink-3">{why}</span>
      ) : null}
      {conflicts.map((c) => (
        <span key={c} className="text-xs text-warn">
          {c}
        </span>
      ))}
    </span>
  );
}

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
              ["Language", fmtVal((r.effectiveLanguage as string) ?? r.language)],
              ["Timezone", <EffectiveTz key="tz" r={r} />],
              ["WebRTC", fmtVal(r.webrtcMode)],
              ["Seed", r.seed != null ? <Mono key="s">{String(r.seed)}</Mono> : "—"],
            ]}
          />
        </CardContent>
      </Card>
    </div>
  );
}
