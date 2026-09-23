import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiMessage, del, get, post, put } from "../../lib/api";
import { useGatewayHealth, useGateways, type Gateway } from "../../lib/queries";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { Dialog } from "../ui/dialog";
import { EmptyState } from "../ui/empty-state";
import { Field } from "../ui/field";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { Mono } from "../profile-detail/kv";

function HealthCell({ id }: { id: string }) {
  const { data, isLoading } = useGatewayHealth(id);
  if (isLoading) return <span className="text-ink-3">…</span>;
  if (!data?.ok) return <span className="text-ink-3">—</span>;
  return <Badge tone={data.healthy ? "ok" : "err"}>{data.healthy ? "healthy" : "unhealthy"}</Badge>;
}

export function GatewaysTab() {
  const qc = useQueryClient();
  const { data: gateways, isLoading, isError, refetch } = useGateways();
  const [editGw, setEditGw] = useState<Gateway | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [delGw, setDelGw] = useState<Gateway | null>(null);
  const [busy, setBusy] = useState(false);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["gateways"] });

  const health = async (g: Gateway) => {
    try {
      const d = await get<{ ok?: boolean; health?: { ok?: boolean } }>(
        `/api/gateways/${encodeURIComponent(g.id)}/health`,
      );
      toast[d.ok || d.health?.ok ? "success" : "error"](
        d.ok || d.health?.ok ? "Gateway healthy" : "Gateway unhealthy",
      );
      invalidate();
    } catch (e) {
      toast.error("Health check failed", { description: apiMessage(e) });
    }
  };

  const provision = async (g: Gateway) => {
    setBusy(true);
    try {
      const d = await post<{ ok?: boolean }>(
        `/api/gateways/${encodeURIComponent(g.id)}/provision`,
        {},
      );
      toast[d.ok ? "success" : "error"](d.ok ? "Provisioned" : "Provision failed");
    } catch (e) {
      toast.error("Provision failed", { description: apiMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!delGw) return;
    setBusy(true);
    try {
      await del(`/api/gateways/${encodeURIComponent(delGw.id)}`);
      toast.success("Deleted");
      setDelGw(null);
      invalidate();
    } catch (e) {
      toast.error("Delete failed", { description: apiMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      <div className="flex items-center gap-2">
        <span className="flex-1" />
        <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
          Add gateway
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <div className="grid gap-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : isError ? (
            <EmptyState
              title="Could not load gateways"
              description="GET /api/gateways failed."
              action={{ label: "Retry", onClick: () => refetch() }}
            />
          ) : !gateways?.length ? (
            <EmptyState
              title="No gateways"
              description="Add a gateway provider to route profile traffic through it."
              action={{ label: "Add gateway", onClick: () => setAddOpen(true) }}
            />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Provider</TH>
                  <TH>Health</TH>
                  <TH>ID</TH>
                  <TH>
                    <span className="sr-only">Actions</span>
                  </TH>
                </TR>
              </THead>
              <TBody>
                {gateways.map((g) => (
                  <TR key={g.id}>
                    <TD className="font-semibold">{g.name || g.id}</TD>
                    <TD>{String(g.providerType ?? g.provider ?? "—")}</TD>
                    <TD>
                      <HealthCell id={g.id} />
                    </TD>
                    <TD>
                      <Mono>
                        <span className="text-ink-3">{g.id}</span>
                      </Mono>
                    </TD>
                    <TD>
                      <div className="flex justify-end gap-1.5">
                        <Button variant="secondary" size="sm" onClick={() => health(g)}>
                          Health
                        </Button>
                        <Button variant="secondary" size="sm" onClick={() => provision(g)} disabled={busy}>
                          Provision
                        </Button>
                        <Button variant="secondary" size="sm" onClick={() => setEditGw(g)}>
                          Edit
                        </Button>
                        <Button variant="danger" size="sm" onClick={() => setDelGw(g)}>
                          Delete
                        </Button>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
          <p className="mt-3 text-xs text-ink-3">
            Provider secrets are stored server-side and never returned by the API.
          </p>
        </CardContent>
      </Card>

      <GatewayDialog
        key={addOpen ? "add" : editGw ? `edit-${editGw.id}` : "closed"}
        open={addOpen || !!editGw}
        gateway={editGw}
        onClose={() => {
          setAddOpen(false);
          setEditGw(null);
        }}
        onSaved={invalidate}
      />

      <Dialog
        open={!!delGw}
        onClose={() => setDelGw(null)}
        title={`Delete gateway “${delGw?.name || delGw?.id}”?`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDelGw(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="danger" onClick={remove} loading={busy}>
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          This gateway provider will be removed. Profiles referencing it keep their config but
          lose the route.
        </p>
      </Dialog>
    </div>
  );
}

function GatewayDialog({
  open,
  gateway,
  onClose,
  onSaved,
}: {
  open: boolean;
  gateway: Gateway | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = !gateway;
  const [name, setName] = useState(gateway?.name ?? "");
  const [providerType, setProviderType] = useState(
    String(gateway?.providerType ?? gateway?.provider ?? ""),
  );
  const [configJson, setConfigJson] = useState(
    gateway?.config ? JSON.stringify(gateway.config, null, 2) : "",
  );
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    let config: Record<string, unknown> = {};
    try {
      config = configJson.trim() ? JSON.parse(configJson) : {};
    } catch {
      toast.error("Config is not valid JSON");
      return;
    }
    setSaving(true);
    try {
      const payload = { name: name.trim(), providerType: providerType.trim(), config };
      if (isNew) await post("/api/gateways", payload);
      else await put(`/api/gateways/${encodeURIComponent(gateway.id)}`, payload);
      toast.success("Saved");
      onSaved();
      onClose();
    } catch (e) {
      toast.error("Save failed", { description: apiMessage(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`${isNew ? "Add" : "Edit"} gateway`}
      description="Secrets are never displayed: the API returns a redacted (safe) view."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} loading={saving}>
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <Field label="Name" required>
          {({ id }) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Provider type">
          {({ id }) => (
            <Input
              id={id}
              value={providerType}
              onChange={(e) => setProviderType(e.target.value)}
              placeholder="e.g. ssh, wireguard"
            />
          )}
        </Field>
        <label className="grid gap-1.5">
          <span className="text-[13px] font-medium text-ink-2">
            Config JSON{" "}
            <span className="font-normal text-ink-3">
              (no secrets in this form — manage secrets server-side)
            </span>
          </span>
          <textarea
            rows={5}
            value={configJson}
            onChange={(e) => setConfigJson(e.target.value)}
            placeholder='{"host":"...","port":22}'
            className="rounded-lg border border-border-strong bg-surface-1 px-3 py-2 font-mono text-[13px] text-ink-1 placeholder:text-ink-3 focus:outline-2 focus:outline-brand-500"
          />
        </label>
      </div>
    </Dialog>
  );
}
