import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiMessage, del, get, post, put } from "../../lib/api";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { Dialog } from "../ui/dialog";
import { EmptyState } from "../ui/empty-state";
import { Field } from "../ui/field";
import { Input } from "../ui/input";
import { Select } from "../ui/select";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { Mono } from "../profile-detail/kv";
import type { ProxyPool } from "../../lib/types";

interface GroupItem {
  id: string;
  name?: string;
  description?: string;
}

type Kind = "clients" | "templates";

function useGroups(kind: Kind) {
  return useQuery({
    queryKey: ["groups", kind],
    queryFn: () =>
      get<{ clients?: GroupItem[]; templates?: GroupItem[]; items?: GroupItem[] }>(`/api/${kind}`),
    select: (d) =>
      kind === "clients" ? (d.clients ?? d.items ?? []) : (d.templates ?? d.items ?? []),
    staleTime: 15_000,
    retry: 1,
  });
}

export function GroupsTab() {
  return (
    <div className="grid gap-6">
      <GroupSection kind="clients" singular="client" />
      <GroupSection kind="templates" singular="template" />
      <p className="text-xs text-ink-3">
        Groups are organized through clients and templates — the backend exposes these via{" "}
        <Mono>/api/clients</Mono> and <Mono>/api/templates</Mono>. Profiles reference a client via{" "}
        <Mono>clientId</Mono>.
      </p>
    </div>
  );
}

function GroupSection({ kind, singular }: { kind: Kind; singular: string }) {
  const qc = useQueryClient();
  const { data: items, isLoading, isError, refetch } = useGroups(kind);
  const [editItem, setEditItem] = useState<GroupItem | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [delItem, setDelItem] = useState<GroupItem | null>(null);
  const [busy, setBusy] = useState(false);

  const title = kind === "clients" ? "Clients" : "Templates";

  const remove = async () => {
    if (!delItem) return;
    setBusy(true);
    try {
      await del(`/api/${kind}/${encodeURIComponent(delItem.id)}`);
      toast.success("Deleted");
      setDelItem(null);
      qc.invalidateQueries({ queryKey: ["groups", kind] });
    } catch (e) {
      toast.error("Delete failed", { description: apiMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-2 flex items-center gap-3">
        <h3 className="text-sm font-semibold text-ink-1">{title}</h3>
        <span className="flex-1" />
        <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
          Add {singular}
        </Button>
      </div>
      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : isError ? (
            <EmptyState
              title={`Couldn't load ${kind}`}
              description={`GET /api/${kind} failed.`}
              action={{ label: "Retry", onClick: () => refetch() }}
            />
          ) : !items?.length ? (
            <EmptyState title={`No ${kind}`} description={`No ${kind} defined yet.`} />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Description</TH>
                  <TH>ID</TH>
                  <TH>
                    <span className="sr-only">Actions</span>
                  </TH>
                </TR>
              </THead>
              <TBody>
                {items.map((it) => (
                  <TR key={it.id}>
                    <TD className="font-semibold">{it.name || it.id}</TD>
                    <TD className="text-ink-3">{it.description || "—"}</TD>
                    <TD>
                      <Mono>
                        <span className="text-ink-3">{it.id}</span>
                      </Mono>
                    </TD>
                    <TD>
                      <div className="flex justify-end gap-1.5">
                        <Button variant="secondary" size="sm" onClick={() => setEditItem(it)}>
                          Edit
                        </Button>
                        <Button variant="danger" size="sm" onClick={() => setDelItem(it)}>
                          Delete
                        </Button>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <GroupDialog
        key={addOpen ? "add" : editItem ? `edit-${editItem.id}` : "closed"}
        open={addOpen || !!editItem}
        kind={kind}
        singular={singular}
        item={editItem}
        onClose={() => {
          setAddOpen(false);
          setEditItem(null);
        }}
        onSaved={() => qc.invalidateQueries({ queryKey: ["groups", kind] })}
      />

      <Dialog
        open={!!delItem}
        onClose={() => setDelItem(null)}
        title={`Delete ${singular} “${delItem?.name || delItem?.id}”?`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDelItem(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="danger" onClick={remove} loading={busy}>
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          This {singular} will be removed. Profiles referencing it are not affected.
        </p>
      </Dialog>
    </div>
  );
}

function GroupDialog({
  open,
  kind,
  singular,
  item,
  onClose,
  onSaved,
}: {
  open: boolean;
  kind: Kind;
  singular: string;
  item: GroupItem | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = !item;
  const [name, setName] = useState(item?.name ?? "");
  const [description, setDescription] = useState(item?.description ?? "");
  const [saving, setSaving] = useState(false);
  // Workspace defaults apply to clients only; templates have no route.
  const isWorkspace = kind === "clients";
  const [defaultPoolId, setDefaultPoolId] = useState(
    (item as { defaultPoolId?: string | null } | null)?.defaultPoolId ?? "",
  );
  const pools = useQuery({
    queryKey: ["pools", "list"],
    queryFn: () => get<ProxyPool[]>("/api/pools"),
    enabled: isWorkspace,
    staleTime: 60_000,
    retry: 1,
  });

  const save = async () => {
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        description: description.trim(),
        // null clears it; undefined would leave the old pool in place.
        ...(isWorkspace ? { defaultPoolId: defaultPoolId || null } : {}),
      };
      if (isNew) await post(`/api/${kind}`, payload);
      else await put(`/api/${kind}/${encodeURIComponent(item.id)}`, payload);
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
      title={`${isNew ? "Add" : "Edit"} ${singular}`}
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
        <Field label="Description">
          {({ id }) => (
            <Input
              id={id}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          )}
        </Field>
        {isWorkspace && (
          <Field label="Default proxy pool">
            {({ id }) => (
              <>
                <Select
                  id={id}
                  value={defaultPoolId}
                  onChange={(e) => setDefaultPoolId(e.target.value)}
                >
                  <option value="">None — profiles keep their own proxy</option>
                  {(pools.data ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </Select>
                <p className="mt-1 text-xs text-ink-3">
                  New profiles created in this group join this pool automatically.
                </p>
              </>
            )}
          </Field>
        )}
      </div>
    </Dialog>
  );
}
