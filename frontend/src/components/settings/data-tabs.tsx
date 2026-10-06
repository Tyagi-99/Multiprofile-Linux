import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiMessage, get, post } from "../../lib/api";
import { navigate } from "../../lib/router";
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

interface Backup {
  id?: string;
  backupId?: string;
  path?: string;
  backupPath?: string;
  name?: string;
  profileId?: string;
  profile_id?: string;
  createdAt?: string;
  created_at?: string;
  ts?: string;
  size?: number | string;
}

function backupId(b: Backup): string | null {
  return (b.id ?? b.backupId ?? b.path ?? b.backupPath ?? null) as string | null;
}

function formatSize(size: number | string | undefined): string {
  if (size == null) return "—";
  if (typeof size === "string") return size;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function BackupsTab() {
  const qc = useQueryClient();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["settings", "backups"],
    queryFn: () => get<{ backups?: Backup[] }>("/api/backups"),
    select: (d) => d.backups ?? [],
    staleTime: 15_000,
    retry: 1,
  });
  const [restore, setRestore] = useState<Backup | null>(null);

  return (
    <div>
      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <div className="grid gap-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : isError ? (
            <EmptyState
              title="Couldn't load backups"
              description="GET /api/backups failed."
              action={{ label: "Retry", onClick: () => refetch() }}
            />
          ) : !data?.length ? (
            <EmptyState
              title="No backups"
              description="Backups appear here after using Backup on a profile."
            />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Backup</TH>
                  <TH>Profile</TH>
                  <TH>Created</TH>
                  <TH>Size</TH>
                  <TH>
                    <span className="sr-only">Actions</span>
                  </TH>
                </TR>
              </THead>
              <TBody>
                {data.map((b, i) => {
                  const id = backupId(b);
                  const created = b.createdAt ?? b.created_at ?? b.ts;
                  return (
                    <TR key={id ?? i}>
                      <TD>
                        <Mono>{b.name ?? id ?? "backup"}</Mono>
                      </TD>
                      <TD>{String(b.profileId ?? b.profile_id ?? "—")}</TD>
                      <TD className="text-ink-3">{created ? String(created) : "—"}</TD>
                      <TD className="text-right tabular-nums">{formatSize(b.size)}</TD>
                      <TD>
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={!id}
                          onClick={() => setRestore(b)}
                        >
                          Restore
                        </Button>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
          <p className="mt-3 text-xs text-ink-3">
            Source: GET /api/backups. Restore uses POST /api/backups/restore with backupId/backupPath
            + optional profileId + passphrase. Passphrases are consumed by the endpoint and never
            logged.
          </p>
        </CardContent>
      </Card>

      <RestoreDialog
        key={restore ? backupId(restore) ?? "open" : "closed"}
        backup={restore}
        onClose={() => setRestore(null)}
        onDone={() => qc.invalidateQueries({ queryKey: ["settings", "backups"] })}
      />
    </div>
  );
}

function RestoreDialog({
  backup,
  onClose,
  onDone,
}: {
  backup: Backup | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [target, setTarget] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [busy, setBusy] = useState(false);

  const go = async () => {
    const id = backup ? backupId(backup) : null;
    if (!id) return;
    setBusy(true);
    try {
      const payload: Record<string, unknown> = { backupId: id };
      if (target.trim()) payload.profileId = target.trim();
      if (passphrase) payload.passphrase = passphrase;
      setPassphrase("");
      const d = await post<{ ok?: boolean }>("/api/backups/restore", payload);
      if (d.ok !== false) {
        toast.success("Restore completed");
        onDone();
        onClose();
      } else {
        toast.error("Restore failed");
      }
    } catch (e) {
      toast.error("Restore failed", { description: apiMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={!!backup}
      onClose={onClose}
      title="Restore backup"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={go} loading={busy}>
            Restore
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-ink-3">Backup</span>
          <Mono>{backup ? (backup.name ?? backupId(backup)) : ""}</Mono>
        </div>
        <Field label="Target profile ID" hint="optional — defaults to the backup's profile">
          {({ id }) => (
            <Input
              id={id}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="profile_…"
            />
          )}
        </Field>
        <Field label="Passphrase" hint="if encrypted">
          {({ id }) => (
            <Input
              id={id}
              type="password"
              value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)}
              autoComplete="off"
            />
          )}
        </Field>
        <p className="text-xs text-ink-3">
          The passphrase is sent once to the restore endpoint and is never stored or logged by the
          UI.
        </p>
      </div>
    </Dialog>
  );
}

export function ExtensionsTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["system", "health"],
    queryFn: () => get<{ extension_ok?: boolean }>("/api/health"),
    staleTime: 60_000,
    retry: 1,
  });

  return (
    <div className="grid gap-4">
      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : (
            <div className="flex items-center gap-3">
              <Badge tone={data?.extension_ok ? "ok" : "warn"}>
                {data?.extension_ok ? "present" : "missing"}
              </Badge>
              <div>
                <p className="text-sm font-medium text-ink-1">Identity script</p>
                <p className="text-xs text-ink-3">
                  The bundled anti-detect bundle is the <em>source</em> of the
                  identity script, which the backend injects over CDP. It is not
                  loaded as a browser extension and nothing is staged per
                  profile — the copy that used to be loaded ran twice on
                  Chromium and not at all on Chrome.
                </p>
              </div>
            </div>
          )}
          <p className="mt-3 text-xs text-ink-3">Source: GET /api/health → extension_ok.</p>
        </CardContent>
      </Card>
      <EmptyState
        title="Browser extensions moved"
        description="Importing extensions and assigning them to profiles now has its own section."
        action={{ label: "Open Extensions", onClick: () => navigate({ name: "extensions" }) }}
      />
    </div>
  );
}
