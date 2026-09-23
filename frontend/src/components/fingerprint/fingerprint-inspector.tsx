import { useFingerprint } from "../../lib/queries";
import { Alert } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { EmptyState } from "../ui/empty-state";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { Mono } from "../profile-detail/kv";

function checkTone(s: string): "ok" | "warn" | "err" | "idle" {
  const v = s.toUpperCase();
  if (v === "PASS" || v === "OK" || v === "HEALTHY") return "ok";
  if (v === "WARN" || v === "WARNING") return "warn";
  if (v === "FAIL" || v === "ERROR" || v === "BLOCKED") return "err";
  return "idle";
}

/** Flatten a nested object into dotted-path [prop, value] rows. */
function flattenFp(obj: unknown, prefix: string, out: [string, unknown][] = []): [string, unknown][] {
  if (obj == null) return out;
  if (Array.isArray(obj)) {
    const s = obj
      .map((x) =>
        typeof x === "object" && x !== null
          ? Object.entries(x)
              .map(([k, v]) => `${k}=${v}`)
              .join(", ")
          : String(x),
      )
      .join("; ");
    out.push([prefix, s || "—"]);
    return out;
  }
  if (typeof obj !== "object") {
    out.push([prefix, String(obj)]);
    return out;
  }
  const keys = Object.keys(obj);
  if (!keys.length) {
    out.push([prefix, "—"]);
    return out;
  }
  for (const k of keys) {
    const v = (obj as Record<string, unknown>)[k];
    const path = prefix ? `${prefix}.${k}` : k;
    if (v != null && typeof v === "object") flattenFp(v, path, out);
    else out.push([path, v]);
  }
  return out;
}

function lookupPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj;
  for (const part of path.split(".")) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

function fmtVal(v: unknown): React.ReactNode {
  if (v == null || v === "") return <span className="text-ink-3">—</span>;
  if (typeof v === "object") {
    const s = JSON.stringify(v);
    return <Mono>{s.length > 120 ? s.slice(0, 120) + "…" : s}</Mono>;
  }
  return <Mono>{String(v)}</Mono>;
}

function LegacySections({ fp }: { fp: Record<string, unknown> }) {
  const f = fp;
  const scr = (f.screen ?? {}) as Record<string, unknown>;
  const ch = (f.clientHints ?? {}) as Record<string, unknown>;
  const gpu = (f.gpu ?? {}) as Record<string, unknown>;
  const media = (f.mediaDeviceIds ?? {}) as Record<string, unknown>;

  const sections: [string, [string, unknown][]][] = [
    [
      "Navigator",
      [
        ["userAgent", f.userAgent],
        ["platform", f.platform],
        ["isMobile", f.isMobile],
        ["hardwareConcurrency", f.cores],
        ["deviceMemory (GB)", f.memory],
      ],
    ],
    [
      "Screen",
      [
        ["width × height", scr.w != null ? `${scr.w}×${scr.h}` : null],
        ["availHeight", scr.availH],
        ["devicePixelRatio", scr.dpr],
        ["touch points", f.touch],
      ],
    ],
    [
      "Client Hints",
      [
        ["platform", ch.platform ?? f.ch_platform],
        ["mobile", ch.mobile ?? (typeof f.touch === "number" ? f.touch > 0 : null)],
        ["architecture", ch.architecture ?? f.architecture],
        ["bitness", ch.bitness ?? f.bitness],
        ["platformVersion", ch.platformVersion ?? f.platformVersion],
        ["model", ch.model ?? f.model],
        ["uaFullVersion", ch.uaFullVersion],
        [
          "brands",
          ((ch.brands ?? f.ch_brands ?? []) as { brand?: string; version?: string }[])
            .map((b) => `${b.brand} ${b.version}`)
            .join("; "),
        ],
      ],
    ],
    ["Canvas", [["seed", f.seed]]],
    [
      "WebGL",
      [
        ["vendor", gpu.vendor],
        ["renderer", gpu.renderer],
      ],
    ],
    [
      "Audio / Media",
      [
        ["audioinput id", media.audioinput],
        ["audiooutput id", media.audiooutput],
        ["videoinput id", media.videoinput],
      ],
    ],
    ["Network", [["webrtc policy", f.webrtcPolicy ?? f.webrtcMode]]],
    ["Timezone", [["timezone", f.timezone]]],
    [
      "Language",
      [
        ["language", f.language],
        ["languages", Array.isArray(f.languages) ? (f.languages as string[]).join(", ") : null],
      ],
    ],
    [
      "Hardware",
      [
        ["cores", f.cores],
        ["memory (GB)", f.memory],
        ["gpu vendor", gpu.vendor],
        ["gpu renderer", gpu.renderer],
      ],
    ],
  ];

  return (
    <>
      {sections.map(([title, rows]) => (
        <Card key={title}>
          <CardHeader>
            <CardTitle>{title}</CardTitle>
            <p className="text-xs text-ink-3">Legacy report format — runtime values unavailable.</p>
          </CardHeader>
          <CardContent>
            <Table>
              <TBody>
                {rows.map(([prop, exp]) => (
                  <TR key={prop}>
                    <TD className="w-48 font-medium">{prop}</TD>
                    <TD>{fmtVal(exp)}</TD>
                    <TD className="w-48 text-ink-3">Not running</TD>
                    <TD className="w-28">
                      <Badge tone="idle">Unknown</Badge>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      ))}
    </>
  );
}

export function FingerprintInspector({ profileId }: { profileId: string }) {
  const { data: rep, isLoading, isError, refetch } = useFingerprint(profileId);

  if (isLoading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (isError || !rep) {
    return (
      <EmptyState
        title="Fingerprint unavailable"
        description={`GET /api/profiles/${profileId}/fingerprint did not return a report.`}
        action={{ label: "Retry", onClick: () => refetch() }}
      />
    );
  }

  const fp = rep.fingerprint ?? {};
  const warnings = rep.warnings ?? [];
  const components = Array.isArray(rep.components) ? rep.components : null;

  return (
    <div className="grid gap-4">
      {warnings.length > 0 && (
        <Alert tone="warning" title={`${warnings.length} warning${warnings.length > 1 ? "s" : ""}`}>
          <ul className="list-disc pl-5">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </Alert>
      )}

      {(rep.summary || rep.generated_at) && (
        <p className="text-[13px] text-ink-3">
          {rep.summary}
          {rep.generated_at ? ` · generated ${rep.generated_at}` : ""}
        </p>
      )}

      {components && components.length ? (
        components.map((c, i) => {
          const rows = flattenFp(c.expected, "");
          return (
            <Card key={c.name ?? i}>
              <CardHeader>
                <CardTitle>{c.name ?? "Section"}</CardTitle>
                {c.note && <p className="text-xs text-ink-3">{c.note}</p>}
              </CardHeader>
              <CardContent>
                <Table>
                  <THead>
                    <TR>
                      <TH>Property</TH>
                      <TH>Expected</TH>
                      <TH>Runtime</TH>
                      <TH>Status</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {rows.length ? (
                      rows.map(([prop, exp]) => (
                        <TR key={prop}>
                          <TD className="font-medium">{prop}</TD>
                          <TD>{fmtVal(exp)}</TD>
                          <TD>
                            {c.runtime ? (
                              fmtVal(lookupPath(c.runtime, prop))
                            ) : (
                              <span className="text-ink-3">Not running</span>
                            )}
                          </TD>
                          <TD>
                            <Badge tone={checkTone(String(c.status ?? ""))}>
                              {String(c.status ?? "—")}
                            </Badge>
                          </TD>
                        </TR>
                      ))
                    ) : (
                      <TR>
                        <TD colSpan={4} className="text-ink-3">
                          No expected values exposed for this section.
                        </TD>
                      </TR>
                    )}
                  </TBody>
                </Table>
              </CardContent>
            </Card>
          );
        })
      ) : (
        <LegacySections fp={fp} />
      )}
    </div>
  );
}
