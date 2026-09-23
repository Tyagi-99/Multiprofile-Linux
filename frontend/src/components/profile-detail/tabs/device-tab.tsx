import { useQuery } from "@tanstack/react-query";
import { get } from "../../../lib/api";
import type { DeviceSummary, Profile } from "../../../lib/types";
import { Badge } from "../../ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";
import { Skeleton } from "../../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../../ui/table";
import { KV, fmtVal } from "../kv";

function supportTone(s: string): "ok" | "warn" | "idle" | "err" {
  const v = s.toUpperCase();
  if (v === "VALIDATED") return "ok";
  if (v === "PARTIAL") return "warn";
  if (v === "PENDING") return "idle";
  return "err";
}

export function DeviceTab({ profile }: { profile: Profile }) {
  const { data: detail, isLoading } = useQuery({
    queryKey: ["devices", "detail", profile.deviceId],
    queryFn: () => get<DeviceSummary>(`/api/devices/${encodeURIComponent(profile.deviceId!)}`),
    enabled: !!profile.deviceId,
    staleTime: 60_000,
    retry: 1,
  });
  const d = (detail ?? {}) as Record<string, unknown>;
  const cm = (d.capability_matrix ?? {}) as Record<string, { status?: string; note?: string }>;
  const rows = Object.entries(cm);

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Assigned device</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : (
            <KV
              pairs={[
                ["Device ID", fmtVal(profile.deviceId)],
                [
                  "Support",
                  detail?.status ? (
                    <Badge key="s" tone={supportTone(String(detail.status))}>
                      {String(detail.status)}
                    </Badge>
                  ) : (
                    "—"
                  ),
                ],
                ["Validation date", fmtVal(d.validation_date)],
              ]}
            />
          )}
          {!profile.deviceId && (
            <p className="mt-2 text-[13px] text-ink-3">No device preset assigned to this profile.</p>
          )}
        </CardContent>
      </Card>

      {detail && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Capability matrix</CardTitle>
            </CardHeader>
            <CardContent>
              {rows.length ? (
                <Table>
                  <THead>
                    <TR>
                      <TH>Component</TH>
                      <TH>Status</TH>
                      <TH>Note</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {rows.map(([k, v]) => (
                      <TR key={k}>
                        <TD className="font-medium">{k}</TD>
                        <TD>
                          <Badge tone={supportTone(String(v.status ?? ""))}>
                            {String(v.status ?? "—")}
                          </Badge>
                        </TD>
                        <TD className="text-ink-3">{v.note ?? "—"}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              ) : (
                <p className="text-[13px] text-ink-3">No capability data.</p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Specification</CardTitle>
            </CardHeader>
            <CardContent>
              <KV
                pairs={[
                  ["Manufacturer", fmtVal(d.manufacturer)],
                  ["Model", fmtVal(d.model)],
                  ["Platform", fmtVal(d.platform ?? d.os)],
                  ["Category", fmtVal(d.category)],
                  [
                    "Viewport",
                    (() => {
                      const vp = d.viewport as { width?: number; height?: number } | undefined;
                      return vp ? `${vp.width}×${vp.height}` : "—";
                    })(),
                  ],
                  ["Touch", d.touch == null ? "—" : d.touch ? "yes" : "no"],
                  ["Mobile", d.mobile == null ? "—" : d.mobile ? "yes" : "no"],
                ]}
              />
              {Array.isArray(d.limitations) && d.limitations.length > 0 && (
                <div className="mt-3">
                  <h4 className="mb-1 text-[13px] font-medium text-ink-2">Limitations</h4>
                  <ul className="list-disc pl-5 text-[13px] text-ink-3">
                    {(d.limitations as string[]).map((l, i) => (
                      <li key={i}>{l}</li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
