import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "../../lib/api";
import { useDevices } from "../../lib/queries";
import { useUI } from "../../stores/ui";
import type { DeviceSummary } from "../../lib/types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { EmptyState } from "../ui/empty-state";
import { Input, Select } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { cn } from "../../lib/cn";

const CATEGORIES = ["android", "ios", "tablet", "mobile", "desktop"];
const SUPPORT_LEVELS = ["VALIDATED", "PARTIAL", "PENDING", "UNSUPPORTED"];

function supportTone(s: string): "ok" | "warn" | "idle" | "err" {
  const v = s.toUpperCase();
  if (v === "VALIDATED") return "ok";
  if (v === "PARTIAL") return "warn";
  if (v === "PENDING") return "idle";
  return "err";
}

function catMatches(d: DeviceSummary, cat: string): boolean {
  const hay = `${String(d.category ?? "")} ${String(d.platform ?? "")}`.toLowerCase();
  if (cat === "ios") return /ios|iphone|ipad/.test(hay);
  return hay.includes(cat);
}

export function DevicesPage() {
  const { data, isLoading, isError, refetch } = useDevices();
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [cat, setCat] = useState("");
  const [support, setSupport] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim().toLowerCase()), 280);
    return () => clearTimeout(t);
  }, [q]);

  const devices = useMemo(() => data?.devices ?? [], [data]);
  const rows = useMemo(
    () =>
      devices.filter((d) => {
        if (debouncedQ) {
          const hay = [d.name, d.manufacturer, d.model, d.platform, d.category, d.id]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();
          if (!hay.includes(debouncedQ)) return false;
        }
        if (cat && !catMatches(d, cat)) return false;
        if (support && String(d.support ?? "").toUpperCase() !== support) return false;
        return true;
      }),
    [devices, debouncedQ, cat, support],
  );

  return (
    <div className="mx-auto max-w-7xl space-y-4 px-6 py-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight text-ink-1">Device Library</h1>
        <span className="flex-1" />
        <div className="flex flex-wrap items-center gap-2">
          <Input
            type="search"
            placeholder="Search devices…"
            aria-label="Search devices"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="w-52"
          />
          <Select
            aria-label="Category filter"
            value={cat}
            onChange={(e) => setCat(e.target.value)}
            options={[
              { value: "", label: "All categories" },
              ...CATEGORIES.map((c) => ({ value: c, label: c.charAt(0).toUpperCase() + c.slice(1) })),
            ]}
          />
          <Select
            aria-label="Support filter"
            value={support}
            onChange={(e) => setSupport(e.target.value)}
            options={[
              { value: "", label: "All support levels" },
              ...SUPPORT_LEVELS.map((s) => ({ value: s, label: s.charAt(0) + s.slice(1).toLowerCase() })),
            ]}
          />
        </div>
      </div>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-36 w-full" />
          ))}
        </div>
      ) : isError || !devices.length ? (
        <EmptyState
          title="Device library unavailable"
          description="GET /api/devices failed or returned no presets."
          action={{ label: "Retry", onClick: () => refetch() }}
        />
      ) : (
        <>
          <p className="text-[13px] text-ink-3">
            {rows.length} of {devices.length} presets
          </p>
          {rows.length ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {rows.map((d) => (
                <DeviceCard key={d.id} device={d} onOpen={() => setSelectedId(d.id)} />
              ))}
            </div>
          ) : (
            <EmptyState title="No devices match" description="Adjust the search or filters." />
          )}
        </>
      )}

      <DeviceDetailDialog
        id={selectedId}
        cached={selectedId ? devices.find((d) => d.id === selectedId) : undefined}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}

function DeviceCard({ device: d, onOpen }: { device: DeviceSummary; onOpen: () => void }) {
  const vp = d.viewport as { width?: number; height?: number } | undefined;
  const sup = String(d.support ?? "");
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "group rounded-xl border border-border bg-surface-2 p-4 text-left",
        "transition-colors hover:border-border-strong hover:bg-surface-3",
        "focus-visible:outline-2 focus-visible:outline-brand-500",
      )}
      aria-label={String(d.name ?? d.model ?? d.id)}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold text-ink-1">
          {String(d.name ?? d.model ?? d.id)}
        </span>
        {sup && (
          <Badge tone={supportTone(sup)}>
            {sup.charAt(0) + sup.slice(1).toLowerCase()}
          </Badge>
        )}
      </div>
      <div className="space-y-0.5 text-[13px] text-ink-3">
        <p className="truncate">
          {String(d.manufacturer ?? "")} {String(d.model ?? "")}
        </p>
        <p>
          {String(d.platform ?? d.os ?? "")} · {String(d.category ?? "—")}
        </p>
        <p>
          {vp ? `${vp.width}×${vp.height}` : "—"} · DPR {String(d.device_scale_factor ?? "—")} ·{" "}
          {d.touch ? "touch" : "no touch"}
        </p>
      </div>
      <div className="mt-2">
        <Badge tone={d.mobile ? "info" : "idle"}>{d.mobile ? "Mobile" : "Desktop"}</Badge>
      </div>
    </button>
  );
}

function DeviceDetailDialog({
  id,
  cached,
  onClose,
}: {
  id: string | null;
  cached?: DeviceSummary;
  onClose: () => void;
}) {
  const openDialog = useUI((s) => s.openDialog);
  const { data: fetched } = useQuery({
    queryKey: ["devices", "detail", id],
    queryFn: () => get<DeviceSummary>(`/api/devices/${encodeURIComponent(id!)}`),
    enabled: !!id && !cached,
    staleTime: 60_000,
    retry: 1,
  });
  const d = (cached ?? fetched ?? {}) as Record<string, unknown>;
  const cm = (d.capability_matrix ?? {}) as Record<string, { status?: string; note?: string }>;
  const rows = Object.entries(cm);
  const vp = d.viewport as { width?: number; height?: number } | undefined;

  const useInNew = () => {
    onClose();
    openDialog("wizard", id ? { deviceId: id } : undefined);
  };

  return (
    <Dialog
      open={!!id}
      onClose={onClose}
      title={`Device preset — ${String(d.name ?? d.model ?? id ?? "")}`}
      wide
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button variant="primary" onClick={useInNew}>
            Use in new profile
          </Button>
        </>
      }
    >
      {!id ? null : (
        <div className="grid gap-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-ink-1">
              {String(d.name ?? d.model ?? d.id ?? "")}
            </h3>
            {d.support != null && d.support !== "" && (
              <Badge tone={supportTone(String(d.support))}>{String(d.support)}</Badge>
            )}
          </div>

          <section>
            <h4 className="mb-2 text-[13px] font-medium text-ink-2">Capability matrix</h4>
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
          </section>

          {Array.isArray(d.limitations) && d.limitations.length > 0 && (
            <section>
              <h4 className="mb-2 text-[13px] font-medium text-ink-2">Limitations</h4>
              <ul className="list-disc pl-5 text-[13px] text-ink-3">
                {(d.limitations as string[]).map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h4 className="mb-2 text-[13px] font-medium text-ink-2">Specification</h4>
            <dl className="grid grid-cols-[160px_1fr] gap-x-4 gap-y-2 text-[13px]">
              {(
                [
                  ["Manufacturer", d.manufacturer],
                  ["Model", d.model],
                  ["Platform", d.platform ?? d.os],
                  ["Category", d.category],
                  ["Viewport", vp ? `${vp.width}×${vp.height}` : null],
                  ["Device scale factor", d.device_scale_factor],
                  ["Touch", d.touch == null ? null : d.touch ? "yes" : "no"],
                  ["Mobile", d.mobile == null ? null : d.mobile ? "yes" : "no"],
                  ["Validation date", d.validation_date],
                ] as [string, unknown][]
              ).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-ink-3">{k}</dt>
                  <dd className="text-ink-1">{v != null && v !== "" ? String(v) : "—"}</dd>
                </div>
              ))}
            </dl>
          </section>
        </div>
      )}
    </Dialog>
  );
}
