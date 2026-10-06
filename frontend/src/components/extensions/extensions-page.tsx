"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, apiMessage, del, get, post } from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { Checkbox } from "../ui/checkbox";
import { Dialog } from "../ui/dialog";
import { EmptyState } from "../ui/empty-state";
import { Input } from "../ui/input";
import { Select } from "../ui/select";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { ProfileSelectDialog } from "./profile-select-dialog";
import {
  type BulkResponse,
  type Capability,
  type ExtensionsResponse,
  type ManagedExtension,
  type PreviewResponse,
  type SelectableProfile,
  stateTone,
} from "./types";

/** A 207 arrives as an ApiError because ok is false. The per-profile ledger
 *  is in its payload, and showing it is the whole point of answering 207. */
function bulkFrom(err: unknown): BulkResponse | null {
  if (!(err instanceof ApiError)) return null;
  const p = err.payload as BulkResponse | null;
  return p && Array.isArray(p.results) ? p : null;
}

function formatBytes(n: number): string {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function CapabilityBanner({ cap }: { cap: Capability }) {
  if (cap.unpackedLoading) {
    return (
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 pt-6">
          <Badge tone="ok">supported</Badge>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-ink-1">
              {cap.brand} {cap.version}
            </p>
            <p className="text-xs text-ink-3">{cap.reason}</p>
          </div>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex flex-wrap items-center gap-3">
          <Badge tone="err">not supported</Badge>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-ink-1">
              This browser cannot load unpacked extensions
            </p>
            <p className="text-xs text-ink-3">{cap.reason}</p>
            {cap.remediation && (
              <p className="mt-1 text-xs text-ink-2">{cap.remediation}</p>
            )}
          </div>
        </div>
        <p className="mt-3 text-xs text-ink-3">
          Your browser is not changed for you. Installing from the Chrome Web
          Store still works per profile, through the browser&apos;s own flow.
        </p>
      </CardContent>
    </Card>
  );
}

/** Shown before anything is written, so permissions are seen first. */
function ImportDialog({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported: (ext: ManagedExtension) => void;
}) {
  const [mode, setMode] = useState<"zip" | "folder">("zip");
  const [path, setPath] = useState("");
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setPath("");
    setPreview(null);
    setError("");
    setBusy(false);
  };

  const doPreview = async () => {
    setBusy(true);
    setError("");
    setPreview(null);
    try {
      const body = mode === "zip" ? { zipPath: path } : { folder: path };
      setPreview(await post<PreviewResponse>("/api/extensions/preview", body));
    } catch (e) {
      setError(apiMessage(e, "Could not read that package."));
    } finally {
      setBusy(false);
    }
  };

  const doImport = async () => {
    setBusy(true);
    setError("");
    try {
      const body = mode === "zip" ? { zipPath: path } : { folder: path };
      const res = await post<{ extension: ManagedExtension }>(
        "/api/extensions/import",
        body,
      );
      toast.success(`Imported ${res.extension.name} ${res.extension.version}`);
      onImported(res.extension);
      reset();
      onClose();
    } catch (e) {
      setError(apiMessage(e, "Import failed."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="Import an extension"
      description="A ZIP of the unpacked source, or a folder containing manifest.json. Validated before anything is written."
      wide
      footer={
        <div className="flex w-full justify-end gap-2">
          <Button
            variant="ghost"
            onClick={() => {
              reset();
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button variant="secondary" onClick={doPreview} disabled={!path || busy}>
            Check package
          </Button>
          <Button onClick={doImport} disabled={!preview || busy}>
            Import
          </Button>
        </div>
      }
    >
      <div className="grid gap-3">
        <div className="flex gap-2">
          <Select
            value={mode}
            onChange={(e) => {
              setMode(e.target.value as "zip" | "folder");
              setPreview(null);
            }}
            aria-label="Import source"
            className="w-44"
          >
            <option value="zip">ZIP archive</option>
            <option value="folder">Unpacked folder</option>
          </Select>
          <Input
            className="flex-1"
            placeholder={
              mode === "zip"
                ? "/path/to/extension.zip"
                : "/path/to/unpacked-extension"
            }
            value={path}
            onChange={(e) => {
              setPath(e.target.value);
              setPreview(null);
            }}
            aria-label="Package path"
          />
        </div>
        <p className="text-xs text-ink-3">
          Paths are read by the local backend, which is the only thing that can
          reach your filesystem and the browser. A .crx is refused with the
          reason and a supported alternative.
        </p>

        {error && (
          <div className="rounded-md border border-err/40 bg-err-dim p-3 text-sm text-err">
            {error}
          </div>
        )}

        {preview && (
          <div className="rounded-md border border-line bg-surface-2 p-3">
            <p className="text-sm font-medium text-ink-1">
              {preview.manifest.name} {preview.manifest.version}
              <span className="ml-2 text-xs text-ink-3">
                Manifest V{preview.manifest.manifestVersion}
              </span>
            </p>
            {preview.manifest.description && (
              <p className="mt-1 text-xs text-ink-3">
                {preview.manifest.description}
              </p>
            )}
            <p className="mt-2 text-xs font-medium text-ink-2">
              Permissions it will be granted
            </p>
            <div className="mt-1 flex flex-wrap gap-1">
              {[
                ...preview.manifest.permissions,
                ...preview.manifest.hostPermissions,
              ].length === 0 ? (
                <span className="text-xs text-ink-3">None declared.</span>
              ) : (
                [
                  ...preview.manifest.permissions,
                  ...preview.manifest.hostPermissions,
                ].map((p) => (
                  <Badge key={p} tone="warn">
                    {p}
                  </Badge>
                ))
              )}
            </div>
            {preview.missingReferencedFiles.length > 0 && (
              <p className="mt-2 text-xs text-err">
                manifest.json references {preview.missingReferencedFiles.length}{" "}
                file(s) that are not in the package:{" "}
                {preview.missingReferencedFiles.slice(0, 5).join(", ")}. Import
                will be refused.
              </p>
            )}
            <p className="mt-2 text-xs text-ink-3">
              Root: {preview.root}
              {preview.members ? ` · ${preview.members} files` : ""}
              {preview.totalBytes
                ? ` · ${formatBytes(preview.totalBytes)}`
                : ""}
            </p>
          </div>
        )}
      </div>
    </Dialog>
  );
}

function ResultsDialog({
  open,
  onClose,
  result,
  onRetryFailed,
}: {
  open: boolean;
  onClose: () => void;
  result: BulkResponse | null;
  onRetryFailed?: () => void;
}) {
  if (!result) return null;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={
        result.failed === 0
          ? `Applied to ${result.succeeded} profile(s)`
          : `${result.succeeded} of ${result.total} profiles succeeded`
      }
      description={
        result.failed === 0
          ? undefined
          : "One failure does not make the batch successful. Each profile is listed with its own outcome."
      }
      wide
      footer={
        <div className="flex w-full justify-end gap-2">
          {result.failed > 0 && onRetryFailed && (
            <Button variant="secondary" onClick={onRetryFailed}>
              Retry {result.failed} failed
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </div>
      }
    >
      <ul className="max-h-72 divide-y divide-line overflow-auto">
        {result.results.map((r) => (
          <li key={r.profileId} className="flex items-start gap-3 py-2">
            <Badge tone={r.ok ? stateTone(r.state) : "err"}>{r.stateLabel}</Badge>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-ink-1">
                {r.profileName || r.profileId}
              </p>
              <p className="text-xs text-ink-3">{r.detail}</p>
            </div>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}

function WebStoreDialog({
  open,
  onClose,
  profiles,
}: {
  open: boolean;
  onClose: () => void;
  profiles: SelectableProfile[];
}) {
  const [profileId, setProfileId] = useState("");
  const [value, setValue] = useState("");
  const running = profiles.filter((p) => p.isRunning);

  const go = async () => {
    const body: Record<string, string> = { profileId };
    if (/^[a-p]{32}$/.test(value.trim())) body.webstoreId = value.trim();
    else body.url = value.trim();
    try {
      await post("/api/extensions/webstore", body);
      toast.success("Opened the Web Store page in that profile.");
      onClose();
    } catch (e) {
      toast.error(apiMessage(e, "Could not open the Web Store."));
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Install from the Chrome Web Store"
      description="Opens the listing in a running profile. The browser's own install flow does the install, in that profile, with you present."
      footer={
        <div className="flex w-full justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={go} disabled={!profileId || !value.trim()}>
            Open in profile
          </Button>
        </div>
      }
    >
      <div className="grid gap-3">
        <Select
          value={profileId}
          onChange={(e) => setProfileId(e.target.value)}
          aria-label="Profile"
        >
          <option value="">Choose a running profile…</option>
          {running.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name ?? p.id}
            </option>
          ))}
        </Select>
        {running.length === 0 && (
          <p className="text-xs text-warn">
            No profile is running. A Web Store install needs a visible browser
            window, so launch the profile you want it in first.
          </p>
        )}
        <Input
          placeholder="Extension ID or chromewebstore.google.com URL"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label="Web Store extension"
        />
        <p className="text-xs text-ink-3">
          Only chromewebstore.google.com is opened. This product does not fetch
          or unpack .crx files from third-party mirrors, and it does not work
          around Web Store install restrictions.
        </p>
      </div>
    </Dialog>
  );
}

export function ExtensionsPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [webstoreOpen, setWebstoreOpen] = useState(false);
  const [assignFor, setAssignFor] = useState<ManagedExtension | null>(null);
  const [detail, setDetail] = useState<ManagedExtension | null>(null);
  const [bulk, setBulk] = useState<BulkResponse | null>(null);
  const [bulkFor, setBulkFor] = useState<string | null>(null);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["extensions", "list"],
    queryFn: () => get<ExtensionsResponse>("/api/extensions"),
    staleTime: 10_000,
  });

  const { data: profiles } = useQuery({
    queryKey: ["extensions", "profiles-lite"],
    queryFn: () =>
      get<{ profiles?: SelectableProfile[] }>("/api/profiles").then(
        (d) => d.profiles ?? [],
      ),
    staleTime: 10_000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["extensions"] });
  };

  const assignMutation = useMutation({
    mutationFn: async (vars: { extId: string; profileIds: string[] }) => {
      try {
        return await post<BulkResponse>(
          `/api/extensions/${vars.extId}/assign`,
          { profileIds: vars.profileIds },
        );
      } catch (e) {
        const partial = bulkFrom(e);
        if (partial) return partial;
        throw e;
      }
    },
    onSuccess: (res, vars) => {
      setBulk(res);
      setBulkFor(vars.extId);
      setAssignFor(null);
      invalidate();
    },
    onError: (e) => toast.error(apiMessage(e, "Assignment failed.")),
  });

  const retryMutation = useMutation({
    mutationFn: async (extId: string) => {
      try {
        return await post<BulkResponse>(`/api/extensions/${extId}/retry`, {});
      } catch (e) {
        const partial = bulkFrom(e);
        if (partial) return partial;
        throw e;
      }
    },
    onSuccess: (res) => {
      setBulk(res);
      invalidate();
    },
    onError: (e) => toast.error(apiMessage(e, "Retry failed.")),
  });

  const items = useMemo(() => {
    const all = data?.extensions ?? [];
    const needle = search.trim().toLowerCase();
    return all.filter((e) => {
      if (needle) {
        const hay = `${e.name} ${e.version} ${e.extensionId ?? ""} ${e.description}`;
        if (!hay.toLowerCase().includes(needle)) return false;
      }
      if (sourceFilter && e.source !== sourceFilter) return false;
      if (stateFilter) {
        if (stateFilter === "unassigned" && e.assignedProfileCount > 0) return false;
        if (stateFilter === "verified" && e.verifiedProfileCount === 0) return false;
        if (
          stateFilter === "pending" &&
          !(e.assignmentStates.pending_restart ?? 0)
        )
          return false;
        if (stateFilter === "failed" && !(e.assignmentStates.failed ?? 0))
          return false;
      }
      return true;
    });
  }, [data, search, sourceFilter, stateFilter]);

  const cap = data?.capability;

  return (
    <div className="grid gap-4">
      {cap && <CapabilityBanner cap={cap} />}

      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="w-56"
          placeholder="Search name, version, ID"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search extensions"
        />
        <Select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
          aria-label="Filter by source"
          className="w-40"
        >
          <option value="">Any source</option>
          <option value="zip">ZIP import</option>
          <option value="folder">Unpacked folder</option>
          <option value="webstore">Web Store</option>
          <option value="download">Download</option>
        </Select>
        <Select
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value)}
          aria-label="Filter by install state"
          className="w-48"
        >
          <option value="">Any state</option>
          <option value="unassigned">Not assigned anywhere</option>
          <option value="verified">Installed and verified somewhere</option>
          <option value="pending">Pending restart somewhere</option>
          <option value="failed">Failed somewhere</option>
        </Select>
        <div className="ml-auto flex gap-2">
          <Button variant="secondary" onClick={() => setWebstoreOpen(true)}>
            Install from Web Store
          </Button>
          <Button onClick={() => setImportOpen(true)}>Import ZIP or folder</Button>
        </div>
      </div>

      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <div className="grid gap-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : isError ? (
            <EmptyState
              title="Couldn't load extensions"
              description="GET /api/extensions failed."
              action={{ label: "Retry", onClick: () => refetch() }}
            />
          ) : items.length === 0 ? (
            <EmptyState
              title={
                (data?.extensions ?? []).length === 0
                  ? "No extensions yet"
                  : "No extensions match these filters"
              }
              description={
                (data?.extensions ?? []).length === 0
                  ? "Import a ZIP of an unpacked extension, or a folder containing manifest.json."
                  : undefined
              }
              action={
                (data?.extensions ?? []).length === 0
                  ? { label: "Import", onClick: () => setImportOpen(true) }
                  : undefined
              }
            />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Extension</TH>
                  <TH>Version</TH>
                  <TH>ID</TH>
                  <TH>Source</TH>
                  <TH>Permissions</TH>
                  <TH>Profiles</TH>
                  <TH>State</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {items.map((e) => {
                  const verified = e.verifiedProfileCount;
                  const pending = e.assignmentStates.pending_restart ?? 0;
                  const failed = e.assignmentStates.failed ?? 0;
                  const assignedOnly = e.assignmentStates.assigned ?? 0;
                  return (
                    <TR key={e.id}>
                      <TD>
                        <button
                          className="text-left text-sm font-medium text-ink-1 hover:underline"
                          onClick={() => setDetail(e)}
                        >
                          {e.name}
                        </button>
                        {!e.payloadPresent && (
                          <Badge tone="err" className="ml-2">
                            files missing
                          </Badge>
                        )}
                        {e.defaultForNewProfiles && (
                          <Badge tone="info" className="ml-2">
                            default for new
                          </Badge>
                        )}
                      </TD>
                      <TD className="text-xs">
                        {e.version}
                        <span className="ml-1 text-ink-3">MV{e.manifestVersion}</span>
                      </TD>
                      <TD className="font-mono text-[11px] text-ink-3">
                        {e.extensionId ? `${e.extensionId.slice(0, 12)}…` : "—"}
                      </TD>
                      <TD className="text-xs text-ink-3">{e.source}</TD>
                      <TD className="text-xs text-ink-3">
                        {e.permissions.length + e.hostPermissions.length || "none"}
                      </TD>
                      <TD className="text-xs">{e.assignedProfileCount}</TD>
                      <TD>
                        <div className="flex flex-wrap gap-1">
                          {/* Package state is bytes on disk. It is shown as
                              its own word and never as "installed". */}
                          <Badge tone="idle">{e.packageState}</Badge>
                          {verified > 0 && (
                            <Badge tone="ok">{verified} verified</Badge>
                          )}
                          {pending > 0 && (
                            <Badge tone="warn">{pending} pending restart</Badge>
                          )}
                          {assignedOnly > 0 && (
                            <Badge tone="info">{assignedOnly} assigned</Badge>
                          )}
                          {failed > 0 && <Badge tone="err">{failed} failed</Badge>}
                        </div>
                      </TD>
                      <TD>
                        <div className="flex justify-end gap-1">
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => setAssignFor(e)}
                            disabled={!cap?.unpackedLoading}
                            title={
                              cap?.unpackedLoading
                                ? "Assign to profiles"
                                : cap?.reason
                            }
                          >
                            Assign
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setDetail(e)}
                          >
                            Details
                          </Button>
                        </div>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ImportDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={invalidate}
      />
      <WebStoreDialog
        open={webstoreOpen}
        onClose={() => setWebstoreOpen(false)}
        profiles={profiles ?? []}
      />
      <ProfileSelectDialog
        // Keyed so each operation gets a fresh dialog: no stale selection,
        // no filter left over from the last extension.
        key={assignFor?.id ?? "none"}
        open={assignFor !== null}
        onClose={() => setAssignFor(null)}
        title={`Assign ${assignFor?.name ?? ""} to profiles`}
        confirmLabel="Assign"
        busy={assignMutation.isPending}
        onConfirm={(profileIds) =>
          assignFor &&
          assignMutation.mutate({ extId: assignFor.id, profileIds })
        }
      />
      <ResultsDialog
        open={bulk !== null}
        onClose={() => setBulk(null)}
        result={bulk}
        onRetryFailed={
          bulkFor ? () => retryMutation.mutate(bulkFor) : undefined
        }
      />
      {detail && (
        <ExtensionDetail
          ext={detail}
          profiles={profiles ?? []}
          onClose={() => setDetail(null)}
          onChanged={invalidate}
        />
      )}
    </div>
  );
}

function ExtensionDetail({
  ext,
  profiles,
  onClose,
  onChanged,
}: {
  ext: ManagedExtension;
  profiles: SelectableProfile[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [updatePath, setUpdatePath] = useState("");
  const byId = useMemo(
    () => new Map(profiles.map((p) => [p.id, p])),
    [profiles],
  );

  const act = async (fn: () => Promise<unknown>, okMessage: string) => {
    try {
      await fn();
      toast.success(okMessage);
      onChanged();
    } catch (e) {
      toast.error(apiMessage(e, "That did not work."));
    }
  };

  return (
    <Dialog open onClose={onClose} title={`${ext.name} ${ext.version}`} wide>
      <div className="grid gap-4">
        <div className="grid gap-1 text-xs text-ink-3">
          <p>
            <span className="text-ink-2">Extension ID</span>{" "}
            <span className="font-mono">{ext.extensionId ?? "—"}</span>
          </p>
          <p>
            <span className="text-ink-2">Manifest</span> V{ext.manifestVersion}
            {ext.hasKey ? " · identity pinned by manifest key" : ""}
          </p>
          <p>
            <span className="text-ink-2">Source</span> {ext.source}
            {ext.sourceDetail ? ` — ${ext.sourceDetail}` : ""}
          </p>
          <p>
            <span className="text-ink-2">Package</span> {ext.packageState} ·{" "}
            {formatBytes(ext.sizeBytes)}
            {ext.payloadPresent ? "" : " · FILES MISSING"}
          </p>
          <p className="break-all">
            <span className="text-ink-2">Managed path</span> {ext.payloadPath}
          </p>
        </div>

        <div>
          <p className="mb-1 text-xs font-medium text-ink-2">Permissions</p>
          <div className="flex flex-wrap gap-1">
            {[...ext.permissions, ...ext.hostPermissions].length === 0 ? (
              <span className="text-xs text-ink-3">None declared.</span>
            ) : (
              [...ext.permissions, ...ext.hostPermissions].map((p) => (
                <Badge key={p} tone="warn">
                  {p}
                </Badge>
              ))
            )}
          </div>
        </div>

        <div>
          <div className="mb-1 flex items-center gap-2">
            <p className="text-xs font-medium text-ink-2">
              Assigned profiles ({ext.assignments.length})
            </p>
          </div>
          {ext.assignments.length === 0 ? (
            <p className="text-xs text-ink-3">Not assigned to any profile.</p>
          ) : (
            <ul className="max-h-48 divide-y divide-line overflow-auto rounded-md border border-line">
              {ext.assignments.map((a) => {
                const p = byId.get(a.profileId);
                return (
                  <li
                    key={a.profileId}
                    className="flex items-center gap-2 px-3 py-2"
                  >
                    <Badge tone={stateTone(a.state)}>{a.stateLabel}</Badge>
                    <span className="min-w-0 flex-1 truncate text-sm text-ink-1">
                      {p?.name ?? a.profileId}
                    </span>
                    {a.lastError && (
                      <span
                        className="max-w-[16rem] truncate text-xs text-err"
                        title={a.lastError}
                      >
                        {a.lastError}
                      </span>
                    )}
                    <Checkbox
                      checked={a.enabled}
                      onChange={(e) =>
                        act(
                          () =>
                            post(`/api/extensions/${ext.id}/enabled`, {
                              profileId: a.profileId,
                              enabled: e.target.checked,
                            }),
                          e.target.checked ? "Enabled" : "Disabled",
                        )
                      }
                      aria-label={`Enabled in ${p?.name ?? a.profileId}`}
                    />
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        act(
                          () =>
                            post(`/api/extensions/${ext.id}/unassign`, {
                              profileId: a.profileId,
                            }),
                          "Removed from that profile.",
                        )
                      }
                    >
                      Remove
                    </Button>
                    {p?.isRunning && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          act(
                            () =>
                              post(
                                `/api/profiles/${a.profileId}/extensions-page`,
                                {},
                              ),
                            "Opened chrome://extensions in that profile.",
                          )
                        }
                        title="Opens the browser's own Developer Mode and Load unpacked"
                      >
                        chrome://extensions
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="grid gap-2 rounded-md border border-line p-3">
          <p className="text-xs font-medium text-ink-2">Update</p>
          <div className="flex gap-2">
            <Input
              className="flex-1"
              placeholder="/path/to/new-version.zip"
              value={updatePath}
              onChange={(e) => setUpdatePath(e.target.value)}
              aria-label="New package path"
            />
            <Button
              variant="secondary"
              disabled={!updatePath}
              onClick={() =>
                act(
                  () =>
                    post(`/api/extensions/${ext.id}/update`, {
                      zipPath: updatePath,
                    }),
                  "Updated. Assigned profiles pick it up on next launch.",
                )
              }
            >
              Update
            </Button>
            <Button
              variant="ghost"
              onClick={() =>
                act(
                  () => post(`/api/extensions/${ext.id}/rollback`, {}),
                  "Rolled back to the previous version.",
                )
              }
            >
              Roll back
            </Button>
          </div>
          <p className="text-xs text-ink-3">
            The managed path does not change, so the extension keeps its ID and
            its per-profile storage. Running profiles keep the old version until
            they restart.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3 rounded-md border border-line p-3">
          <Checkbox
            checked={ext.defaultForNewProfiles}
            onChange={(e) =>
              act(
                () =>
                  post(`/api/extensions/${ext.id}/default-for-new`, {
                    enabled: e.target.checked,
                  }),
                e.target.checked
                  ? "Will be assigned to profiles created from now on."
                  : "No longer a default for new profiles.",
              )
            }
            label="Assign to new profiles automatically"
          />
          <span className="text-xs text-ink-3">
            Applies to profiles created from now on. Existing profiles are not
            touched.
          </span>
        </div>

        <div className="flex justify-end gap-2">
          <Button
            variant="ghost"
            onClick={() =>
              act(async () => {
                try {
                  await del(`/api/extensions/${ext.id}`);
                } catch (e) {
                  if (
                    e instanceof ApiError &&
                    e.code === "still_assigned" &&
                    window.confirm(
                      `${e.message}\n\nRemove those assignments and delete anyway?`,
                    )
                  ) {
                    await del(`/api/extensions/${ext.id}?force=1`);
                    return;
                  }
                  throw e;
                }
              }, "Deleted.").then(onClose)
            }
          >
            Delete
          </Button>
          <Button onClick={onClose}>Close</Button>
        </div>
      </div>
    </Dialog>
  );
}
