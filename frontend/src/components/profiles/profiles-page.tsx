import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Download,
  Eye,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { apiMessage, get } from "../../lib/api";
import { absoluteTime, timeAgo } from "../../lib/format";
import { downloadJson, sanitizeForExport, useProfileActions, type BlockedLaunch } from "../../lib/mutations";
import { profilesKeys } from "../../lib/query-keys";
import type {
  DeviceSummary,
  NetworkHealth,
  Profile,
  ProfilesPage as ProfilesPageData,
} from "../../lib/types";
import { useUI } from "../../stores/ui";
import { Badge, launchStateTone } from "../ui/badge";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Dialog } from "../ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
  MenuItem,
  MenuSeparator,
} from "../ui/dropdown-menu";
import { EmptyState } from "../ui/empty-state";
import { Input } from "../ui/input";
import { CursorPagination } from "../ui/pagination";
import { Select } from "../ui/select";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";

// One page holds the whole fleet at the size this product is designed for
// (150 profiles), so the operator scrolls rather than pages, "select all"
// really does mean all of them, and bulk actions are not bounded by a page
// boundary that no longer exists.
//
// 25 was chosen when a list row carried its generated fingerprint -- 2,795
// bytes, of which the table read none. A row is 663 bytes now, so a full
// page is about 130 KB and the database returns it in roughly a millisecond.
//
// Deliberately NOT virtualized. 200 rows of a plain table is not a rendering
// problem, and a windowed list is machinery with no benefit until the design
// target moves. If it does, the API is already the part that would have been
// hard: it pages, filters and sorts in SQL, and /api/profiles/ids resolves a
// whole filter for select-all.
const PAGE_SIZE = 200;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/**
 * The operator-facing state word.
 *
 * The API normalises the finer LaunchState machine into `state`, so twelve
 * launch states collapse to the four the console colours. Fall back to the
 * raw fields only for an older backend that does not send `state`.
 */
function stateOf(p: Profile): string {
  const s = String(p.state ?? p.launchState ?? p.status ?? "idle").toLowerCase();
  if (!s) return "idle";
  if (s === "running" || s === "verified") return "running";
  if (s.endsWith("_failed") || s === "window_not_found" || s === "error") return "error";
  if (s === "launching" || s === "cdp_ready" || s === "window_found" || s === "positioning") {
    return "starting";
  }
  if (s === "created") return "stopped";
  return s;
}

// Colour language lives in ui/badge (launchStateTone) so the table, the
// detail page and the dashboard cannot drift apart. Do not add a local map.

function statusLabel(state: string): string {
  if (state === "idle") return "Stopped";
  return state.charAt(0).toUpperCase() + state.slice(1);
}

/** Transitional states own the row's primary button until they settle. */
function isBusy(state: string): boolean {
  return state === "starting" || state === "stopping";
}

function fpSummary(p: Profile): string {
  const parts: string[] = [];
  const ua = (p.userAgent as string | undefined) ?? (p as Record<string, unknown>).user_agent as string | undefined;
  if (ua) {
    const m = String(ua).match(/Chrome\/(\d+)/);
    parts.push(m ? `Chrome ${m[1]}` : "Custom UA");
  }
  const locale = (p as Record<string, unknown>).locale ?? (p as Record<string, unknown>).language;
  if (locale) parts.push(String(locale));
  const tz = (p as Record<string, unknown>).timezone;
  if (tz) parts.push(String(tz));
  return parts.length ? parts.join(" · ") : "—";
}

function proxyLabel(p: Profile): string {
  const r = p as Record<string, unknown>;
  const host = (r.proxyHost ?? r.proxy_host) as string | undefined;
  if (!host) return "—";
  const proto = String(r.proxyType ?? r.networkMode ?? "http").toLowerCase();
  const port = r.proxyPort ? `:${r.proxyPort}` : "";
  const auth = r.proxyUser ?? r.proxy_user ?? r.hasProxyAuth ? "••••@" : "";
  return `${proto}://${auth}${host}${port}`;
}

function hasProxy(p: Profile): boolean {
  const r = p as Record<string, unknown>;
  return Boolean(r.proxyHost ?? r.proxy_host ?? r.proxyId);
}

/**
 * Proxy cell: the probe verdict first, the address second.
 *
 * "Unchecked" is deliberately not the same as "OK" — a profile whose proxy
 * has never been probed must not read as healthy. The check button runs the
 * same pre-launch probe the launcher uses, so what the operator sees here is
 * what will gate the launch.
 */
function ProxyCell({ profile }: { profile: Profile }) {
  const qc = useQueryClient();
  const [checking, setChecking] = useState(false);

  if (!hasProxy(profile)) {
    return (
      <span className="inline-flex items-center gap-2">
        <Badge tone="idle">Direct</Badge>
      </span>
    );
  }

  const ok = profile.proxyOk;
  const tone = ok === true ? "ok" : ok === false ? "err" : "idle";
  const label = ok === true ? "OK" : ok === false ? "Failed" : "Unchecked";

  const check = async () => {
    setChecking(true);
    try {
      const res = await get<NetworkHealth>(
        `/api/profiles/${encodeURIComponent(profile.id)}/network-health`,
      );
      const ip = res.proxyHealth?.egress_ip;
      if (res.ok) {
        toast.success(`Proxy OK — ${profile.name}`, {
          description: ip ? `Egress ${ip}` : res.message,
        });
      } else {
        toast.error(`Proxy failed — ${profile.name}`, {
          description: res.proxyHealth?.error ?? res.error ?? "Probe did not succeed",
        });
      }
      qc.invalidateQueries({ queryKey: profilesKeys.lists() });
    } catch (e) {
      toast.error(`Proxy check failed — ${profile.name}`, { description: apiMessage(e) });
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <Badge tone={tone} dot title={profile.egressIp ? `Egress ${profile.egressIp}` : undefined}>
        {label}
      </Badge>
      <span className="truncate font-mono text-xs text-ink-3" title={proxyLabel(profile)}>
        {profile.egressIp ?? proxyLabel(profile)}
      </span>
      <button
        type="button"
        onClick={check}
        disabled={checking}
        title="Run the pre-launch proxy probe"
        aria-label={`Check proxy for ${profile.name}`}
        className="rounded-md p-1 text-ink-3 transition-colors hover:bg-surface-3 hover:text-ink-1 disabled:opacity-50"
      >
        <ShieldCheck size={14} className={checking ? "animate-pulse" : undefined} />
      </button>
    </div>
  );
}

interface Client { id: string; name?: string; clientId?: string }

/** Checkbox with indeterminate support for the select-all header. */
function HeaderCheckbox({
  checked,
  indeterminate,
  onChange,
  label,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate && !checked;
  }, [indeterminate, checked]);
  return (
    <Checkbox
      ref={ref}
      checked={checked}
      onChange={(e) => onChange(e.target.checked)}
      aria-label={label}
    />
  );
}

function useClients() {
  return useQuery({
    queryKey: ["clients", "list"],
    queryFn: () => get<Client[]>("/api/clients"),
    staleTime: 60_000,
    retry: 1,
  });
}

function useDevices() {
  return useQuery({
    queryKey: ["devices", "list"],
    queryFn: () => get<{ devices: DeviceSummary[] }>("/api/devices"),
    staleTime: 60_000,
    retry: 1,
  });
}

export function ProfilesPage() {
  const openDialog = useUI((s) => s.openDialog);
  const actions = useProfileActions();

  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [clientId, setClientId] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const cursorStack = useRef<string[]>([]);
  const debouncedQ = useDebounced(q, 350);

  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  const [deleteTargets, setDeleteTargets] = useState<Profile[] | null>(null);
  const [blocked, setBlocked] = useState<BlockedLaunch[] | null>(null);

  const { data: clients } = useClients();
  const { data: devicesData } = useDevices();
  const deviceMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const d of devicesData?.devices ?? []) m.set(d.id, d.label ?? d.id);
    return m;
  }, [devicesData]);
  const clientName = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of clients ?? []) m.set(c.id, c.name ?? c.id);
    return (id?: string | null) => (id ? m.get(id) ?? id : "—");
  }, [clients]);

  const query = useQuery({
    queryKey: profilesKeys.list({ q: debouncedQ, status, clientId, cursor, limit: PAGE_SIZE }),
    queryFn: () =>
      get<ProfilesPageData>("/api/profiles", {
        limit: PAGE_SIZE,
        ...(debouncedQ ? { q: debouncedQ } : {}),
        ...(status ? { status } : {}),
        ...(clientId ? { clientId } : {}),
        ...(cursor ? { cursor } : {}),
      }),
    staleTime: 10_000,
    retry: 1,
    placeholderData: (prev) => prev,
  });

  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const total = query.data?.total;

  const resetPage = () => {
    cursorStack.current = [];
    setCursor(null);
    setRowSelection({});
  };

  const clearFilters = () => {
    setQ("");
    setStatus("");
    setClientId("");
    resetPage();
  };

  const selectedProfiles = useMemo(
    () => items.filter((p) => rowSelection[p.id]),
    [items, rowSelection],
  );

  const doLaunch = useCallback(
    async (profiles: Profile[]) => {
      const blockedList = await actions.launch(profiles);
      setRowSelection({});
      if (blockedList.length) setBlocked(blockedList);
    },
    [actions],
  );

  const doForceLaunch = async () => {
    if (!blocked) return;
    for (const b of blocked) {
      const p = items.find((x) => x.id === b.id) ?? ({ id: b.id, name: b.name } as Profile);
      try {
        await actions.launchOne(p, true);
      } catch {
        /* toast already handled per-profile in launch(); force errors surface via SSE */
      }
    }
    setBlocked(null);
  };

  const columns = useMemo<ColumnDef<Profile>[]>(
    () => [
      {
        id: "select",
        header: ({ table }) => (
          <HeaderCheckbox
            checked={table.getIsAllPageRowsSelected()}
            indeterminate={table.getIsSomePageRowsSelected()}
            onChange={(v) => table.toggleAllPageRowsSelected(v)}
            label="Select all profiles on this page"
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            checked={row.getIsSelected()}
            onChange={(e) => row.toggleSelected(e.target.checked)}
            aria-label={`Select ${row.original.name}`}
          />
        ),
        enableSorting: false,
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) => {
          const s = stateOf(row.original);
          return (
            <span className="inline-flex items-center gap-1.5">
              <Badge tone={launchStateTone(s)} dot>
                {statusLabel(s)}
              </Badge>
            </span>
          );
        },
      },
      {
        id: "name",
        header: "Profile",
        cell: ({ row }) => {
          const p = row.original;
          // A failed launch explains itself in the row. The API message is
          // shown verbatim — "GitHub Account is not a network gateway" is
          // the operator's fix, and swallowing it wastes their time.
          const err = p.launchError;
          return (
            <div className="min-w-0">
              <a
                href={`#/profiles/${encodeURIComponent(p.id)}`}
                className="block truncate font-medium text-ink-1 hover:text-brand-500"
              >
                {p.name}
              </a>
              {err ? (
                <span
                  className="mt-0.5 flex items-start gap-1 text-xs text-err"
                  title={p.launchErrorCode ? `${p.launchErrorCode}: ${err}` : err}
                >
                  <AlertCircle size={12} aria-hidden className="mt-0.5 shrink-0" />
                  <span className="line-clamp-2">{err}</span>
                </span>
              ) : (
                <span className="font-mono text-xs text-ink-3">{p.id.slice(0, 13)}…</span>
              )}
            </div>
          );
        },
      },
      {
        id: "group",
        header: "Group",
        cell: ({ row }) => (
          <span className="text-[13px] text-ink-2">{clientName(row.original.clientId)}</span>
        ),
      },
      {
        id: "device",
        header: "Device",
        cell: ({ row }) => (
          <span className="text-[13px] text-ink-2">
            {row.original.deviceId ? deviceMap.get(row.original.deviceId) ?? row.original.deviceId : "—"}
          </span>
        ),
      },
      {
        id: "proxy",
        header: "Proxy",
        cell: ({ row }) => <ProxyCell profile={row.original} />,
      },
      {
        id: "lastLaunch",
        header: "Last launch",
        cell: ({ row }) => {
          const t = row.original.lastLaunch;
          return (
            <span className="whitespace-nowrap text-[13px] text-ink-3" title={absoluteTime(t)}>
              {timeAgo(t)}
            </span>
          );
        },
      },
      {
        id: "fingerprint",
        header: "Fingerprint",
        cell: ({ row }) => (
          <span className="text-[13px] text-ink-2">{fpSummary(row.original)}</span>
        ),
      },
      {
        id: "actions",
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => <RowActions profile={row.original} onLaunch={doLaunch} />,
      },
    ],
    [clientName, deviceMap, doLaunch],
  );

  // react-compiler cannot memoize TanStack Table's function-returning API; skipped by design.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable<Profile>({
    data: items,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (p) => p.id,
    state: { rowSelection },
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
  });

  const nextCursor = query.data?.next_cursor ?? null;
  const pageIndex = cursorStack.current.length;

  const goNext = () => {
    if (!nextCursor) return;
    cursorStack.current.push(cursor ?? "");
    setCursor(nextCursor);
    setRowSelection({});
  };
  const goPrev = () => {
    cursorStack.current.pop();
    setCursor(cursorStack.current[cursorStack.current.length - 1] || null);
    setRowSelection({});
  };

  return (
    <div className="mx-auto max-w-7xl space-y-4 px-6 py-6">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-2 text-xl font-semibold tracking-tight text-ink-1">Profiles</h1>
        {total != null && (
          <span className="text-[13px] text-ink-3">
            {total} total
          </span>
        )}
        <span className="flex-1" />
        <div className="relative">
          <Search size={14} aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3" />
          <Input
            type="search"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              resetPage();
            }}
            placeholder="Search name, tag, UA…"
            aria-label="Search profiles"
            className="w-52 pl-8"
          />
        </div>
        <Select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            resetPage();
          }}
          aria-label="Status filter"
          className="w-36"
        >
          <option value="">All states</option>
          <option value="running">Running</option>
          <option value="idle">Stopped</option>
          <option value="parked">Parked</option>
          <option value="error">Error</option>
        </Select>
        <Select
          value={clientId}
          onChange={(e) => {
            setClientId(e.target.value);
            resetPage();
          }}
          aria-label="Group filter"
          className="w-40"
        >
          <option value="">All groups</option>
          {(clients ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name ?? c.id}
            </option>
          ))}
        </Select>
        {(q || status || clientId) && (
          <Button variant="ghost" size="sm" onClick={clearFilters}>
            <X size={14} aria-hidden /> Clear
          </Button>
        )}
        <Button
          variant="secondary"
          size="sm"
          onClick={() => downloadJson("multiprofile-export.json", items.map(sanitizeForExport))}
          title="Download the currently listed profiles as JSON (proxy passwords stripped)"
        >
          <Download size={14} aria-hidden /> Export
        </Button>
        <Button variant="primary" size="sm" onClick={() => openDialog("wizard")}>
          <Plus size={14} aria-hidden /> New profile
        </Button>
      </div>

      {/* Bulk bar */}
      {selectedProfiles.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-brand-500/30 bg-brand-500/10 px-3 py-2">
          <span className="text-[13px] font-medium text-ink-1">
            {selectedProfiles.length} selected
            {/* Selection is per page. At the size this is designed for the
                page holds everything, so the two numbers agree -- but if a
                filter ever matches more than one page, saying so is the
                difference between an operator acting on all of it and
                believing they did. */}
            {nextCursor && total != null && total > selectedProfiles.length && (
              <span className="ml-1 font-normal text-ink-3">
                of {total} matching — this page only
              </span>
            )}
          </span>
          <Button variant="ghost" size="sm" onClick={() => setRowSelection({})}>
            Clear
          </Button>
          <span className="flex-1" />
          <Button variant="secondary" size="sm" onClick={() => doLaunch(selectedProfiles)}>
            <Play size={14} aria-hidden /> Start
          </Button>
          <Button variant="secondary" size="sm" onClick={() => { actions.stop(selectedProfiles); setRowSelection({}); }}>
            <Square size={14} aria-hidden /> Stop
          </Button>
          <Button variant="secondary" size="sm" onClick={() => { actions.park(selectedProfiles, true); setRowSelection({}); }}>
            <Pause size={14} aria-hidden /> Park
          </Button>
          <Button variant="danger" size="sm" onClick={() => setDeleteTargets(selectedProfiles)}>
            <Trash2 size={14} aria-hidden /> Delete
          </Button>
        </div>
      )}

      {/* Table */}
      <div className="overflow-hidden rounded-xl border border-border-subtle bg-surface-1">
        {query.isLoading && !query.data ? (
          <div className="grid gap-px p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : query.isError ? (
          <EmptyState
            title="Could not load profiles"
            description="The backend did not respond. Check that the server is running, then retry."
            action={{ label: "Retry", onClick: () => query.refetch() }}
          />
        ) : items.length === 0 ? (
          <EmptyState
            title={q || status || clientId ? "No profiles match" : "No profiles yet"}
            description={
              q || status || clientId
                ? "Try clearing the filters."
                : "Create your first profile to get started."
            }
            action={
              q || status || clientId
                ? { label: "Clear filters", onClick: clearFilters }
                : { label: "New profile", onClick: () => openDialog("wizard") }
            }
          />
        ) : (
          <Table>
            <THead>
              {table.getHeaderGroups().map((hg) => (
                <TR key={hg.id}>
                  {hg.headers.map((h) => (
                    <TH key={h.id}>
                      {flexRender(h.column.columnDef.header, h.getContext())}
                    </TH>
                  ))}
                </TR>
              ))}
            </THead>
            <TBody>
              {table.getRowModel().rows.map((row) => (
                <TR key={row.id} data-state={row.getIsSelected() ? "selected" : undefined}>
                  {row.getVisibleCells().map((cell) => (
                    <TD key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TD>
                  ))}
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </div>

      {/* Pagination */}
      <CursorPagination
        page={pageIndex + 1}
        hasNext={!!nextCursor}
        hasPrev={pageIndex > 0}
        onNext={goNext}
        onPrev={goPrev}
        disabled={query.isFetching}
      />

      {/* Delete confirmation */}
      <Dialog
        open={deleteTargets !== null}
        onClose={() => setDeleteTargets(null)}
        title={`Delete ${deleteTargets?.length ?? 0} profile${(deleteTargets?.length ?? 0) === 1 ? "" : "s"}?`}
        description="Browser storage will be purged. This cannot be undone."
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteTargets(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                if (deleteTargets) actions.remove(deleteTargets);
                setDeleteTargets(null);
                setRowSelection({});
              }}
            >
              Delete
            </Button>
          </>
        }
      >
        <ul className="grid max-h-40 gap-1 overflow-auto">
          {(deleteTargets ?? []).map((p) => (
            <li key={p.id} className="truncate text-[13px] text-ink-2">
              {p.name} <span className="font-mono text-xs text-ink-3">{p.id.slice(0, 8)}…</span>
            </li>
          ))}
        </ul>
      </Dialog>

      {/* Force-launch confirmation */}
      <Dialog
        open={blocked !== null}
        onClose={() => setBlocked(null)}
        title="Launch blocked by consistency checks"
        description="These profiles failed pre-launch checks. Force launch anyway?"
        footer={
          <>
            <Button variant="secondary" onClick={() => setBlocked(null)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={doForceLaunch}>
              Force launch
            </Button>
          </>
        }
      >
        <ul className="grid max-h-40 gap-2 overflow-auto">
          {(blocked ?? []).map((b) => (
            <li key={b.id} className="text-[13px]">
              <span className="font-medium text-ink-1">{b.name}</span>
              {b.warnings.length > 0 && (
                <ul className="mt-0.5 list-disc pl-5 text-ink-3">
                  {b.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </Dialog>
    </div>
  );
}

function RowActions({
  profile,
  onLaunch,
}: {
  profile: Profile;
  onLaunch: (profiles: Profile[]) => Promise<void>;
}) {
  const actions = useProfileActions();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const state = stateOf(profile);
  const running = state === "running";
  const parked = state === "parked";
  const busy = pending || isBusy(state);

  // Launch and Stop belong on the row, not two clicks deep in a menu. An
  // operator running twenty profiles reaches for these constantly.
  const primary = async () => {
    setPending(true);
    try {
      if (running) await actions.stop([profile]);
      else await onLaunch([profile]);
    } finally {
      setPending(false);
    }
  };

  return (
    <>
      <div className="flex items-center justify-end gap-0.5">
        <Button
          variant={running ? "secondary" : "primary"}
          size="sm"
          onClick={primary}
          disabled={busy}
          title={running ? `Stop ${profile.name}` : `Launch ${profile.name}`}
        >
          {running ? <Square size={13} aria-hidden /> : <Play size={13} aria-hidden />}
          {busy ? (state === "stopping" ? "Stopping…" : state === "starting" ? "Starting…" : "…") : running ? "Stop" : "Launch"}
        </Button>
        <a
          href={`#/profiles/${encodeURIComponent(profile.id)}`}
          className="rounded-md p-1.5 text-ink-3 transition-colors hover:bg-surface-3 hover:text-ink-1"
          title="Open detail"
          aria-label={`Open ${profile.name}`}
        >
          <Eye size={15} />
        </a>
        <DropdownMenu>
          <DropdownMenuTrigger>
            <button
              type="button"
              className="rounded-md p-1.5 text-ink-3 transition-colors hover:bg-surface-3 hover:text-ink-1"
              title="More actions"
              aria-label={`Actions for ${profile.name}`}
            >
              <MoreHorizontal size={15} />
            </button>
          </DropdownMenuTrigger>
          {/* Launch and Stop are on the row; this menu holds the rest. */}
          <DropdownMenuContent>
            {running && (
              <MenuItem onSelect={() => actions.restart(profile)}>
                <RotateCcw size={14} aria-hidden /> Restart
              </MenuItem>
            )}
            {parked ? (
              <MenuItem onSelect={() => actions.park([profile], false)}>
                <Play size={14} aria-hidden /> Unpark
              </MenuItem>
            ) : (
              !running && (
                <MenuItem onSelect={() => actions.park([profile], true)}>
                  <Pause size={14} aria-hidden /> Park
                </MenuItem>
              )
            )}
            <MenuSeparator />
            <MenuItem danger onSelect={() => setDeleteOpen(true)}>
              <Trash2 size={14} aria-hidden /> Delete
            </MenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <Dialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title={`Delete “${profile.name}”?`}
        description="Browser storage will be purged. This cannot be undone."
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                actions.remove([profile]);
                setDeleteOpen(false);
              }}
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          Profile <span className="font-medium text-ink-1">{profile.name}</span> and its browser
          storage will be permanently removed.
        </p>
      </Dialog>
    </>
  );
}
