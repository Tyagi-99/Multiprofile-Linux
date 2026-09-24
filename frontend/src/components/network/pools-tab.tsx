import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { apiMessage, del, get, post, put } from "../../lib/api";
import type { PoolMember, ProxyPool } from "../../lib/types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Dialog } from "../ui/dialog";
import { EmptyState } from "../ui/empty-state";
import { Input } from "../ui/input";
import { Select } from "../ui/select";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";

const POOLS_KEY = ["pools", "list"] as const;

function usePools() {
  return useQuery({
    queryKey: POOLS_KEY,
    queryFn: () => get<ProxyPool[]>("/api/pools"),
    staleTime: 10_000,
    retry: 1,
  });
}

function endpointOf(m: PoolMember): string {
  if (m.gatewayId) return `gateway ${m.gatewayId}`;
  if (!m.host) return "— no host —";
  const auth = m.username ? `${m.username}${m.hasPassword ? ":••••" : ""}@` : "";
  return `${m.scheme ?? "socks5"}://${auth}${m.host}:${m.port ?? 1080}`;
}

/** A blank member row for the editor. */
function blankMember(): PoolMember {
  return { id: `new_${Math.random().toString(36).slice(2, 10)}`, scheme: "socks5", port: 1080, enabled: true };
}

export function PoolsTab() {
  const qc = useQueryClient();
  const query = usePools();
  const [editing, setEditing] = useState<ProxyPool | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProxyPool | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: POOLS_KEY });
    // A pool change can change which proxy a profile resolves to.
    qc.invalidateQueries({ queryKey: ["profiles"] });
  };

  const remove = useMutation({
    mutationFn: (pool: ProxyPool) => del(`/api/pools/${encodeURIComponent(pool.id)}`),
    onSuccess: (_d, pool) => {
      toast.success(`Deleted “${pool.label}”`, {
        description: "Profiles still pointing at it will refuse to launch until you pick another pool.",
      });
      invalidate();
    },
    onError: (e) => toast.error("Delete failed", { description: apiMessage(e) }),
  });

  const pools = query.data ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <p className="text-[13px] text-ink-3">
          A pool is a set of proxies many profiles share. Assignment is sticky, so a profile keeps
          the same egress IP on every launch.
        </p>
        <span className="flex-1" />
        <Button variant="primary" size="sm" onClick={() => setEditing({ ...emptyPool() })}>
          <Plus size={14} aria-hidden /> New pool
        </Button>
      </div>

      {query.isLoading && !query.data ? (
        <div className="grid gap-3">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-40 w-full" />
          ))}
        </div>
      ) : query.isError ? (
        <Card>
          <EmptyState
            title="Could not load pools"
            description="The backend did not respond."
            action={{ label: "Retry", onClick: () => query.refetch() }}
          />
        </Card>
      ) : pools.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Layers size={28} aria-hidden />}
            title="No proxy pools yet"
            description="Add your proxies once, then point profiles at the pool instead of pasting the same endpoint into each one."
            action={{ label: "New pool", onClick: () => setEditing({ ...emptyPool() }) }}
          />
        </Card>
      ) : (
        pools.map((pool) => (
          <PoolCard
            key={pool.id}
            pool={pool}
            onEdit={() => setEditing(pool)}
            onDelete={() => setDeleteTarget(pool)}
          />
        ))
      )}

      {editing && (
        <PoolEditor
          pool={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            invalidate();
          }}
        />
      )}

      <Dialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title={`Delete “${deleteTarget?.label}”?`}
        description="Profiles assigned to this pool keep pointing at it."
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                if (deleteTarget) remove.mutate(deleteTarget);
                setDeleteTarget(null);
              }}
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          {deleteTarget?.profileCount ? (
            <>
              <span className="font-medium text-ink-1">{deleteTarget.profileCount}</span> profile
              {deleteTarget.profileCount === 1 ? "" : "s"} draw from this pool. They will refuse to
              launch until you give them another pool — they will not silently fall back to your
              own IP.
            </>
          ) : (
            "No profiles are assigned to this pool."
          )}
        </p>
      </Dialog>
    </div>
  );
}

function emptyPool(): ProxyPool {
  return {
    id: "",
    label: "",
    strategy: "sticky",
    members: [blankMember()],
    assignments: {},
  };
}

function PoolCard({
  pool,
  onEdit,
  onDelete,
}: {
  pool: ProxyPool;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const usage = pool.usage ?? {};
  const enabled = pool.members.filter((m) => m.enabled !== false).length;

  return (
    <Card>
      <CardHeader className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <CardTitle>{pool.label}</CardTitle>
          <CardDescription>
            {pool.members.length} member{pool.members.length === 1 ? "" : "s"}
            {enabled !== pool.members.length && ` (${enabled} enabled)`} ·{" "}
            {pool.profileCount ?? 0} profile{(pool.profileCount ?? 0) === 1 ? "" : "s"}
          </CardDescription>
        </div>
        <Badge tone={pool.strategy === "sticky" ? "info" : "warn"}>
          {pool.strategy === "sticky" ? "Sticky" : "Rotating"}
        </Badge>
        <Button variant="secondary" size="sm" onClick={onEdit}>
          Edit
        </Button>
        <Button variant="ghost" size="sm" onClick={onDelete} aria-label={`Delete ${pool.label}`}>
          <Trash2 size={14} aria-hidden />
        </Button>
      </CardHeader>
      <CardContent className="pt-2">
        {pool.members.length === 0 ? (
          <p className="text-[13px] text-err">
            This pool has no members. Profiles using it will refuse to launch.
          </p>
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Member</TH>
                <TH>Endpoint</TH>
                <TH>State</TH>
                <TH>Profiles</TH>
              </TR>
            </THead>
            <TBody>
              {pool.members.map((m) => (
                <TR key={m.id}>
                  <TD className="text-[13px] text-ink-2">{m.label || m.id}</TD>
                  <TD className="font-mono text-xs text-ink-3">{endpointOf(m)}</TD>
                  <TD>
                    <Badge tone={m.enabled === false ? "idle" : "ok"} dot>
                      {m.enabled === false ? "Disabled" : "Enabled"}
                    </Badge>
                  </TD>
                  <TD className="text-[13px] text-ink-2">{usage[m.id] ?? 0}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function PoolEditor({
  pool,
  onClose,
  onSaved,
}: {
  pool: ProxyPool;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = !pool.id;
  const [label, setLabel] = useState(pool.label);
  const [strategy, setStrategy] = useState<ProxyPool["strategy"]>(pool.strategy);
  const [members, setMembers] = useState<PoolMember[]>(
    pool.members.length ? pool.members : [blankMember()],
  );
  const [passwords, setPasswords] = useState<Record<string, string>>({});

  const valid = useMemo(
    () => label.trim().length > 0 && members.some((m) => (m.host ?? "").trim() || m.gatewayId),
    [label, members],
  );

  const save = useMutation({
    mutationFn: () => {
      const body = {
        label: label.trim(),
        strategy,
        members: members
          .filter((m) => (m.host ?? "").trim() || m.gatewayId)
          .map((m) => ({
            // A member the server already knows keeps its id, so assignments
            // survive the edit. A freshly added one lets the server mint one.
            ...(m.id.startsWith("new_") ? {} : { id: m.id }),
            label: m.label ?? "",
            enabled: m.enabled !== false,
            scheme: m.scheme ?? "socks5",
            host: (m.host ?? "").trim(),
            port: Number(m.port) || 1080,
            ...(m.username ? { username: m.username } : {}),
            ...(passwords[m.id] ? { password: passwords[m.id] } : {}),
          })),
      };
      return isNew
        ? post<{ pool: ProxyPool }>("/api/pools", body)
        : put<{ pool: ProxyPool }>(`/api/pools/${encodeURIComponent(pool.id)}`, body);
    },
    onSuccess: () => {
      toast.success(isNew ? "Pool created" : "Pool saved");
      onSaved();
    },
    onError: (e) => toast.error("Save failed", { description: apiMessage(e) }),
  });

  const patch = (id: string, fields: Partial<PoolMember>) =>
    setMembers((ms) => ms.map((m) => (m.id === id ? { ...m, ...fields } : m)));

  return (
    <Dialog
      open
      onClose={onClose}
      title={isNew ? "New proxy pool" : `Edit “${pool.label}”`}
      description="Profiles draw their route from this pool. Sticky keeps each profile on the same member."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save pool"}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1 text-[13px] text-ink-2">
            Name
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="US residential" />
          </label>
          <label className="grid gap-1 text-[13px] text-ink-2">
            Assignment
            <Select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as ProxyPool["strategy"])}
            >
              <option value="sticky">Sticky — a profile keeps its member</option>
              <option value="rotate">Rotating — next member each launch</option>
            </Select>
          </label>
        </div>

        {strategy === "rotate" && (
          <p className="rounded-md bg-warn-dim px-3 py-2 text-xs text-warn">
            Rotating changes a profile&apos;s egress IP between launches. Only use it where the site
            does not tie the session to an IP.
          </p>
        )}

        <div className="grid gap-2">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-medium text-ink-1">Members</span>
            <span className="flex-1" />
            <Button variant="ghost" size="sm" onClick={() => setMembers((m) => [...m, blankMember()])}>
              <Plus size={14} aria-hidden /> Add
            </Button>
          </div>

          {members.map((m) => (
            <div key={m.id} className="grid gap-2 rounded-lg border border-border-subtle p-3">
              <div className="grid gap-2 sm:grid-cols-[1fr_7rem_1fr_5rem]">
                <Input
                  value={m.label ?? ""}
                  onChange={(e) => patch(m.id, { label: e.target.value })}
                  placeholder="Label"
                  aria-label="Member label"
                />
                <Select
                  value={m.scheme ?? "socks5"}
                  onChange={(e) => patch(m.id, { scheme: e.target.value })}
                  aria-label="Scheme"
                >
                  <option value="socks5">socks5</option>
                  <option value="http">http</option>
                  <option value="https">https</option>
                </Select>
                <Input
                  value={m.host ?? ""}
                  onChange={(e) => patch(m.id, { host: e.target.value })}
                  placeholder="host"
                  aria-label="Host"
                />
                <Input
                  value={String(m.port ?? 1080)}
                  onChange={(e) => patch(m.id, { port: Number(e.target.value) || 0 })}
                  placeholder="port"
                  aria-label="Port"
                />
              </div>
              <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto_auto]">
                <Input
                  value={m.username ?? ""}
                  onChange={(e) => patch(m.id, { username: e.target.value })}
                  placeholder="username (optional)"
                  aria-label="Username"
                />
                <Input
                  type="password"
                  value={passwords[m.id] ?? ""}
                  onChange={(e) => setPasswords((p) => ({ ...p, [m.id]: e.target.value }))}
                  placeholder={m.hasPassword ? "password set — type to replace" : "password (optional)"}
                  aria-label="Password"
                />
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => patch(m.id, { enabled: m.enabled === false })}
                  title={
                    m.enabled === false
                      ? "Enable this member"
                      : "Disable it — profiles assigned to it will refuse to launch until reassigned"
                  }
                >
                  {m.enabled === false ? "Enable" : "Disable"}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setMembers((ms) => ms.filter((x) => x.id !== m.id))}
                  aria-label="Remove member"
                >
                  <Trash2 size={14} aria-hidden />
                </Button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Dialog>
  );
}
