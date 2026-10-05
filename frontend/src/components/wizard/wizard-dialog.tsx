import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiMessage, post } from "../../lib/api";
import { profilesKeys } from "../../lib/query-keys";
import { useClients, useDeviceDetail, useDevices, useGateways, type ClientSummary } from "../../lib/queries";
import type { DeviceSummary, Profile } from "../../lib/types";
import { navigate } from "../../lib/router";
import { useUI } from "../../stores/ui";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { Dialog } from "../ui/dialog";
import { EmptyState } from "../ui/empty-state";
import { Field } from "../ui/field";
import { Input, Select } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { Tabs, TabsList, TabsTrigger } from "../ui/tabs";
import { KV, Mono } from "../profile-detail/kv";

const STEPS = ["Basic", "Device", "Network", "Fingerprint", "Consistency", "Review"] as const;

type NetMode = "direct" | "http" | "https" | "socks5" | "gateway";

interface Draft {
  name: string;
  clientId: string;
  tags: string;
  notes: string;
  startUrl: string;
  deviceTab: "desktop" | "mobile" | "tablet";
  deviceId: string | null;
  netMode: NetMode;
  host: string;
  port: string;
  user: string;
  pass: string;
  gatewayId: string;
}

interface ProxyTest {
  ok?: boolean;
  success?: boolean;
  egress_ip?: string;
  egressIp?: string;
  country?: string;
  latency_ms?: number;
  error?: string;
}

function emptyDraft(deviceId?: string): Draft {
  return {
    name: "",
    clientId: "",
    tags: "",
    notes: "",
    startUrl: "https://example.com",
    deviceTab: "desktop",
    deviceId: deviceId ?? null,
    netMode: "direct",
    host: "",
    port: "",
    user: "",
    pass: "",
    gatewayId: "",
  };
}

export function WizardDialog() {
  const dialog = useUI((s) => s.dialog);
  if (dialog !== "wizard") return null;
  return <WizardOpen />;
}

function WizardOpen() {
  const closeDialog = useUI((s) => s.closeDialog);
  const payload = useUI((s) => s.dialogPayload) as { deviceId?: string } | null;
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(() => emptyDraft(payload?.deviceId));
  const [netTest, setNetTest] = useState<ProxyTest | null>(null);
  const [confirmErr, setConfirmErr] = useState(false);

  const qc = useQueryClient();
  const set = (patch: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setNetTest(null);
  };

  const create = useMutation({
    mutationFn: () => {
      const n = draft;
      const payload: Record<string, unknown> = {
        name: n.name.trim(),
        clientId: n.clientId || undefined,
        tags: n.tags
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        notes: n.notes,
        startUrl: n.startUrl.trim() || "https://example.com",
        deviceId: n.deviceId || undefined,
      };
      if (n.netMode === "direct") payload.networkMode = "direct";
      else if (n.netMode === "gateway") {
        payload.networkMode = "gateway";
        payload.gatewayId = n.gatewayId || undefined;
      } else {
        payload.networkMode = n.netMode;
        payload.proxyHost = n.host.trim();
        payload.proxyPort = n.port ? Number(n.port) : undefined;
        payload.proxyUser = n.user || undefined;
        payload.proxyPass = n.pass || undefined;
        payload.proxyType = n.netMode;
      }
      return post<{ ok: boolean; profile: Profile; warnings?: string[] }>("/api/profiles", payload);
    },
    onSuccess: (res) => {
      const np = res.profile;
      qc.invalidateQueries({ queryKey: profilesKeys.all });
      toast.success(`Profile "${np.name || np.id}" created`, {
        description: res.warnings?.length
          ? `${res.warnings.length} fingerprint warning(s)`
          : undefined,
      });
      closeDialog();
      navigate({ name: "profiles", id: np.id });
    },
    onError: (e) => toast.error("Create failed", { description: apiMessage(e) }),
  });

  const next = () => {
    if (step === 0 && !draft.name.trim()) {
      toast.error("Profile name is required");
      return;
    }
    if (step === 2 && draft.netMode !== "direct" && draft.netMode !== "gateway") {
      if (!draft.host.trim() || !draft.port.trim()) {
        toast.error("Host and port are required");
        return;
      }
    }
    if (step === 4) {
      const overall = consistencyOverall(draft, netTest);
      if (overall === "ERROR" && !confirmErr) {
        setConfirmErr(true);
        toast.error("Pre-flight found errors", {
          description: "Press Next again to continue anyway.",
        });
        return;
      }
    }
    setConfirmErr(false);
    setStep((s) => Math.min(STEPS.length - 1, s + 1));
  };

  const close = () => {
    create.reset();
    closeDialog();
  };

  return (
    <Dialog
      open
      onClose={close}
      wide
      title="New Profile"
      description="Full setup wizard — name, device, network, fingerprint, pre-flight, review."
      footer={
        <>
          <Button variant="secondary" onClick={close} disabled={create.isPending}>
            Cancel
          </Button>
          {step > 0 && (
            <Button
              variant="secondary"
              onClick={() => setStep((s) => s - 1)}
              disabled={create.isPending}
            >
              ‹ Back
            </Button>
          )}
          <span className="flex-1" />
          {step < STEPS.length - 1 ? (
            <Button variant="primary" onClick={next}>
              {step === 0
                ? "Next: Device ›"
                : step === 1
                  ? "Next: Network ›"
                  : step === 2
                    ? "Next: Fingerprint ›"
                    : step === 3
                      ? "Next: Consistency ›"
                      : "Next: Review ›"}
            </Button>
          ) : (
            <Button variant="primary" onClick={() => create.mutate()} loading={create.isPending}>
              Create profile
            </Button>
          )}
        </>
      }
    >
      <ol className="mb-5 flex flex-wrap items-center gap-1.5" aria-label="Wizard steps">
        {STEPS.map((s, i) => (
          <li
            key={s}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
              i === step
                ? "bg-brand-600 text-white"
                : i < step
                  ? "bg-brand-600/15 text-brand-700 dark:text-brand-300"
                  : "bg-surface-2 text-ink-3"
            }`}
            aria-current={i === step ? "step" : undefined}
          >
            <span className="tabular-nums">{i + 1}</span> {s}
          </li>
        ))}
      </ol>
      <div key={step} aria-live="polite" aria-atomic="false">
        {step === 0 && <BasicStep draft={draft} set={set} />}
        {step === 1 && <DeviceStep draft={draft} set={set} />}
        {step === 2 && <NetworkStep draft={draft} set={set} netTest={netTest} setNetTest={setNetTest} />}
        {step === 3 && <FingerprintStep draft={draft} />}
        {step === 4 && <ConsistencyStep draft={draft} netTest={netTest} />}
        {step === 5 && <ReviewStep draft={draft} />}
      </div>
    </Dialog>
  );
}

/* ---------------- Step 1: Basic ---------------- */

function BasicStep({ draft, set }: { draft: Draft; set: (p: Partial<Draft>) => void }) {
  const { data: clients, isLoading } = useClients();
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label="Profile name" required>
        {({ id }) => (
          <Input
            id={id}
            value={draft.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder="e.g. shopper-01"
            autoFocus
          />
        )}
      </Field>
      <Field label="Client">
        {({ id }) =>
          isLoading ? (
            <Skeleton className="h-10 w-full" />
          ) : (
            <Select
              id={id}
              value={draft.clientId}
              onChange={(e) => set({ clientId: e.target.value })}
              options={[
                { value: "", label: "— none —" },
                ...((clients ?? []) as ClientSummary[]).map((c) => ({
                  value: c.id,
                  label: c.name || c.id,
                })),
              ]}
            />
          )
        }
      </Field>
      <Field label="Tags" hint="comma separated">
        {({ id }) => <Input id={id} value={draft.tags} onChange={(e) => set({ tags: e.target.value })} />}
      </Field>
      <Field label="Start URL">
        {({ id }) => (
          <Input
            id={id}
            type="url"
            value={draft.startUrl}
            onChange={(e) => set({ startUrl: e.target.value })}
          />
        )}
      </Field>
      <div className="sm:col-span-2">
        <Field label="Notes">
          {({ id }) => (
            <textarea
              id={id}
              rows={2}
              value={draft.notes}
              onChange={(e) => set({ notes: e.target.value })}
              className="w-full rounded-lg border border-border-strong bg-surface-1 px-3 py-2 text-sm text-ink-1 placeholder:text-ink-3 focus:outline-2 focus:outline-brand-500"
            />
          )}
        </Field>
      </div>
    </div>
  );
}

/* ---------------- Step 2: Device ---------------- */

function supportTone(s: string): "ok" | "warn" | "err" | "idle" {
  const v = s.toLowerCase();
  if (v === "validated") return "ok";
  if (v === "partial") return "warn";
  if (v === "unsupported") return "err";
  return "idle";
}

function inTab(d: DeviceSummary, tab: string): boolean {
  const c = String(d.category ?? "").toLowerCase();
  if (tab === "desktop") return c.includes("desktop") || (!c.includes("mobile") && !c.includes("tablet"));
  if (tab === "mobile") return c.includes("mobile") && !c.includes("tablet");
  return c.includes("tablet");
}

function DeviceStep({ draft, set }: { draft: Draft; set: (p: Partial<Draft>) => void }) {
  const { data, isLoading, isError, refetch } = useDevices();
  const devices = useMemo(() => data?.devices ?? [], [data]);
  const list = useMemo(() => devices.filter((d) => inTab(d, draft.deviceTab)), [devices, draft.deviceTab]);

  if (isLoading) return <Skeleton className="h-40 w-full" />;
  if (isError || !devices.length) {
    return (
      <EmptyState
        title="Device library unavailable"
        description="GET /api/devices failed or returned no presets. You can continue without a device preset — the backend will generate a default fingerprint."
        action={{ label: "Retry", onClick: () => refetch() }}
      />
    );
  }

  return (
    <div className="grid gap-3">
      <Tabs value={draft.deviceTab} onValueChange={(t) => set({ deviceTab: t as Draft["deviceTab"] })}>
        <TabsList aria-label="Device category">
          <TabsTrigger value="desktop">Desktop</TabsTrigger>
          <TabsTrigger value="mobile">Mobile</TabsTrigger>
          <TabsTrigger value="tablet">Tablet</TabsTrigger>
        </TabsList>
      </Tabs>
      {list.length ? (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" role="listbox" aria-label="Device presets">
          {list.map((d) => {
            const picked = draft.deviceId === d.id;
            const vp = d.viewport as { width?: number; height?: number } | undefined;
            const dpr = d.device_scale_factor ?? d.dpr;
            return (
              <button
                key={d.id}
                type="button"
                role="option"
                aria-selected={picked}
                onClick={() => set({ deviceId: picked ? null : d.id })}
                className={`rounded-xl border p-3 text-left transition-colors ${
                  picked
                    ? "border-brand-500 bg-brand-500/10 ring-1 ring-brand-500"
                    : "border-border-subtle bg-surface-1 hover:border-border-strong"
                }`}
              >
                <p className="text-sm font-semibold text-ink-1">{d.label ?? d.id}</p>
                <p className="mt-1 text-xs text-ink-3">
                  {vp ? `${vp.width}×${vp.height}` : "?"} · DPR {String(dpr ?? "?")} ·{" "}
                  {d.touch ? "touch" : "no touch"}
                </p>
                <div className="mt-2">
                  <Badge tone={supportTone(String(d.support ?? ""))}>{String(d.support ?? "unknown")}</Badge>
                </div>
              </button>
            );
          })}
        </div>
      ) : (
        <p className="text-sm text-ink-3">No presets in this category.</p>
      )}
      <p className="text-xs text-ink-3">
        Device is optional. Skipping assigns no device preset; the backend builds a default
        fingerprint.
      </p>
    </div>
  );
}

/* ---------------- Step 3: Network ---------------- */

const NET_MODES: { id: NetMode; label: string; sub: string }[] = [
  { id: "direct", label: "Direct", sub: "No proxy — fastest" },
  { id: "http", label: "HTTP", sub: "HTTP proxy" },
  { id: "https", label: "HTTPS", sub: "HTTPS proxy" },
  { id: "socks5", label: "SOCKS5", sub: "SOCKS5 proxy" },
  { id: "gateway", label: "Gateway", sub: "Route via a configured gateway" },
];

function NetworkStep({
  draft,
  set,
  netTest,
  setNetTest,
}: {
  draft: Draft;
  set: (p: Partial<Draft>) => void;
  netTest: ProxyTest | null;
  setNetTest: (t: ProxyTest | null) => void;
}) {
  const { data: gateways, isError: gwError } = useGateways();
  const [testing, setTesting] = useState(false);

  const test = async () => {
    if (!draft.host.trim() || !draft.port.trim()) {
      toast.error("Host and port are required");
      return;
    }
    setTesting(true);
    try {
      const d = await post<ProxyTest>("/api/proxy/test", {
        host: draft.host.trim(),
        port: Number(draft.port),
        username: draft.user || undefined,
        password: draft.pass || undefined,
        protocol: draft.netMode,
        timeout: 10,
      });
      setNetTest(d);
    } catch (e) {
      setNetTest({ ok: false, error: apiMessage(e) });
    } finally {
      setTesting(false);
    }
  };

  const proxyMode = draft.netMode !== "direct" && draft.netMode !== "gateway";

  return (
    <div className="grid gap-4">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" role="radiogroup" aria-label="Network mode">
        {NET_MODES.map((m) => {
          const selected = draft.netMode === m.id;
          return (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => set({ netMode: m.id })}
              className={`rounded-xl border p-3 text-left transition-colors ${
                selected
                  ? "border-brand-500 bg-brand-500/10 ring-1 ring-brand-500"
                  : "border-border-subtle bg-surface-1 hover:border-border-strong"
              }`}
            >
              <p className="text-sm font-semibold text-ink-1">{m.label}</p>
              <p className="text-xs text-ink-3">{m.sub}</p>
            </button>
          );
        })}
      </div>

      {draft.netMode === "direct" && (
        <p className="text-xs text-ink-3">Direct connection — no proxy fields needed.</p>
      )}

      {draft.netMode === "gateway" && (
        <div className="grid gap-3">
          <Field label="Gateway">
            {({ id }) => (
              <Select
                id={id}
                value={draft.gatewayId}
                onChange={(e) => set({ gatewayId: e.target.value })}
                options={(gateways ?? []).map((g) => ({
                  value: g.id,
                  label: `${g.name || g.id} (${g.providerType || "?"})`,
                }))}
              />
            )}
          </Field>
          {gwError && (
            <p className="text-xs text-red-600 dark:text-red-400">
              Couldn&apos;t load gateways (GET /api/gateways failed). You can still continue and
              select one later.
            </p>
          )}
          {!(gateways ?? []).length && !gwError && (
            <p className="text-xs text-ink-3">
              No gateways configured. Add one under Network › Gateways first.
            </p>
          )}
        </div>
      )}

      {proxyMode && (
        <div className="grid gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Host" required>
              {({ id }) => (
                <Input
                  id={id}
                  value={draft.host}
                  onChange={(e) => set({ host: e.target.value })}
                  placeholder="203.0.113.10"
                />
              )}
            </Field>
            <Field label="Port" required>
              {({ id }) => (
                <Input
                  id={id}
                  type="number"
                  value={draft.port}
                  onChange={(e) => set({ port: e.target.value })}
                  placeholder="8080"
                />
              )}
            </Field>
            <Field label="Username">
              {({ id }) => (
                <Input
                  id={id}
                  value={draft.user}
                  onChange={(e) => set({ user: e.target.value })}
                  autoComplete="off"
                />
              )}
            </Field>
            <Field label="Password">
              {({ id }) => (
                <Input
                  id={id}
                  type="password"
                  value={draft.pass}
                  onChange={(e) => set({ pass: e.target.value })}
                  autoComplete="off"
                />
              )}
            </Field>
          </div>
          <div>
            <Button variant="secondary" size="sm" onClick={test} loading={testing}>
              Test connection
            </Button>
          </div>
          <p className="text-xs text-ink-3">
            Uses POST /api/proxy/test for ad-hoc checks. Passwords are never displayed or logged.
          </p>
          {netTest && (
            <Card>
              <CardContent className="pt-4">
                <KV
                  pairs={[
                    [
                      "Test result",
                      <Badge key="t" tone={netTest.ok || netTest.success ? "ok" : "err"}>
                        {netTest.ok || netTest.success ? "pass" : "fail"}
                      </Badge>,
                    ],
                    ["Egress IP", netTest.egress_ip ?? netTest.egressIp ? <Mono key="e">{netTest.egress_ip ?? netTest.egressIp}</Mono> : "—"],
                    ["Country", netTest.country ?? "—"],
                    ["Latency", netTest.latency_ms != null ? `${netTest.latency_ms} ms` : "—"],
                    ["Error", netTest.error ?? "—"],
                  ]}
                />
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------------- Step 4: Fingerprint ---------------- */

function FingerprintStep({ draft }: { draft: Draft }) {
  const { data, isLoading } = useDeviceDetail(draft.deviceId);
  const caps = data?.capability_matrix;
  const entries = caps ? Object.entries(caps) : [];

  return (
    <div className="grid gap-4">
      <h3 className="text-sm font-semibold text-ink-1">
        Capability status{draft.deviceId ? ` — ${draft.deviceId}` : ""}
      </h3>
      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : entries.length ? (
        <Card>
          <CardContent className="pt-4">
            <Table>
              <THead>
                <TR>
                  <TH>Component</TH>
                  <TH>Status</TH>
                  <TH>Note</TH>
                </TR>
              </THead>
              <TBody>
                {entries.map(([k, v]) => (
                  <TR key={k}>
                    <TD className="font-mono text-[13px]">{k}</TD>
                    <TD>
                      <Badge tone={supportTone(v.status ?? "")}>{v.status ?? "—"}</Badge>
                    </TD>
                    <TD className="text-ink-3">{v.note || "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            <p className="mt-2 text-xs text-ink-3">
              Source: GET /api/devices/{draft.deviceId} capability_matrix.
            </p>
          </CardContent>
        </Card>
      ) : (
        <p className="text-sm text-ink-3">
          {draft.deviceId
            ? "No capability data for this device (endpoint missing or empty)."
            : "Select a device preset in step 2 to see its capability status here."}
        </p>
      )}
    </div>
  );
}

/* ---------------- Step 5: Consistency (local pre-flight) ---------------- */

interface Check {
  domain: string;
  status: "PASS" | "WARNING" | "ERROR";
  expected: string;
  actual: string;
  message: string;
}

function consistencyChecks(draft: Draft, netTest: ProxyTest | null, device: DeviceSummary | null): Check[] {
  const checks: Check[] = [];
  const mobileDev =
    device != null &&
    /mobile|android|iphone/i.test(String(device.category ?? "") + String(device.label ?? ""));
  checks.push({
    domain: "device/network",
    status: "PASS",
    expected: "n/a (draft)",
    actual: "n/a (draft)",
    message:
      draft.netMode === "direct"
        ? "Direct connection selected — no proxy/DNS consistency to verify."
        : `${draft.netMode.toUpperCase()} proxy configured; egress will be verified after creation.`,
  });
  checks.push({
    domain: "device/fingerprint",
    status: draft.deviceId ? "PASS" : "WARNING",
    expected: mobileDev ? "mobile profile" : "desktop profile",
    actual: mobileDev ? "mobile preset" : "no preset",
    message: draft.deviceId
      ? `Device preset "${draft.deviceId}" selected; fingerprint will be derived from it.`
      : "No device preset — backend will generate a default (desktop-class) fingerprint.",
  });
  if (draft.netMode !== "direct" && draft.netMode !== "gateway" && netTest) {
    const ok = !!(netTest.ok || netTest.success);
    checks.push({
      domain: "network/reachability",
      status: ok ? "PASS" : "ERROR",
      expected: "reachable proxy",
      actual: ok ? `egress ${netTest.egress_ip ?? netTest.egressIp ?? "?"}` : "unreachable",
      message: ok
        ? `Proxy responded in ${netTest.latency_ms ?? "?"} ms.`
        : `Test failed: ${netTest.error || "unknown error"}`,
    });
  }
  return checks;
}

function consistencyOverall(draft: Draft, netTest: ProxyTest | null): "PASS" | "WARNING" | "ERROR" {
  const checks = consistencyChecks(draft, netTest, null);
  if (checks.some((c) => c.status === "ERROR")) return "ERROR";
  if (checks.some((c) => c.status === "WARNING")) return "WARNING";
  return "PASS";
}

function checkTone(s: string): "ok" | "warn" | "err" {
  if (s === "PASS") return "ok";
  if (s === "WARNING") return "warn";
  return "err";
}

function ConsistencyStep({ draft, netTest }: { draft: Draft; netTest: ProxyTest | null }) {
  const { data } = useDevices();
  const device = useMemo(
    () => (data?.devices ?? []).find((d) => d.id === draft.deviceId) ?? null,
    [data, draft.deviceId],
  );
  const checks = consistencyChecks(draft, netTest, device);
  const overall = checks.some((c) => c.status === "ERROR")
    ? "ERROR"
    : checks.some((c) => c.status === "WARNING")
      ? "WARNING"
      : "PASS";

  return (
    <div className="grid gap-3">
      <p className="text-xs text-ink-3">
        <strong className="text-ink-1">Local pre-flight</strong> — computed from the draft on this
        page. The server-side consistency check (<Mono>GET /api/profiles/:id/consistency</Mono>) runs
        after the profile is created and is shown in the profile&apos;s Health tab.
      </p>
      <div className="flex items-center gap-2">
        <Badge tone={checkTone(overall)}>{overall}</Badge>
        <span className="text-xs text-ink-3">pre-flight overall</span>
      </div>
      <Card>
        <CardContent className="pt-4">
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
              {checks.map((c) => (
                <TR key={c.domain}>
                  <TD className="font-mono text-[13px]">{c.domain}</TD>
                  <TD>
                    <Badge tone={checkTone(c.status)}>{c.status}</Badge>
                  </TD>
                  <TD className="text-ink-3">{c.expected}</TD>
                  <TD className="text-ink-3">{c.actual}</TD>
                  <TD className="text-ink-2">{c.message}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

/* ---------------- Step 6: Review ---------------- */

function ReviewStep({ draft }: { draft: Draft }) {
  const { data: clients } = useClients();
  const { data: devices } = useDevices();
  const client = (clients ?? []).find((c) => c.id === draft.clientId);
  const device = (devices?.devices ?? []).find((d) => d.id === draft.deviceId);

  const netSummary =
    draft.netMode === "direct"
      ? "Direct"
      : draft.netMode === "gateway"
        ? `Gateway (${draft.gatewayId || "none selected"})`
        : `${draft.netMode.toUpperCase()} ${draft.host}:${draft.port}${draft.user ? " (auth •••)" : ""}`;

  return (
    <div className="grid gap-3">
      <Card>
        <CardContent className="pt-4">
          <h4 className="mb-2 text-sm font-semibold text-ink-1">Basic</h4>
          <KV
            pairs={[
              ["Name", draft.name],
              ["Client", client ? client.name || client.id : draft.clientId || "—"],
              ["Tags", draft.tags || "—"],
              ["Start URL", <Mono key="u">{draft.startUrl}</Mono>],
              ["Notes", draft.notes || "—"],
            ]}
          />
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4">
          <h4 className="mb-2 text-sm font-semibold text-ink-1">Device</h4>
          <KV
            pairs={[
              ["Preset", device ? `${String(device.label ?? device.id)} (${device.id})` : "none — backend default"],
              [
                "Support",
                device ? (
                  <Badge key="s" tone={supportTone(String(device.support ?? ""))}>{String(device.support ?? "—")}</Badge>
                ) : (
                  "—"
                ),
              ],
            ]}
          />
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4">
          <h4 className="mb-2 text-sm font-semibold text-ink-1">Network</h4>
          <KV pairs={[["Mode", netSummary]]} />
        </CardContent>
      </Card>
    </div>
  );
}
