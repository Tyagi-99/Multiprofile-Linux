"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, post } from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Dialog } from "../ui/dialog";
import { Input } from "../ui/input";
import { Select } from "../ui/select";
import { Skeleton } from "../ui/skeleton";
import type { SelectableProfile } from "./types";

type RunningFilter = "any" | "running" | "stopped";

interface Props {
  open: boolean;
  onClose: () => void;
  title: string;
  confirmLabel: string;
  busy?: boolean;
  /** Called with the exact profile IDs, resolved at confirm time. */
  onConfirm: (profileIds: string[]) => void;
}

/**
 * Bulk profile selection for an extension operation.
 *
 * Two things here are correctness, not polish.
 *
 * "Select all matching" names its scope in the label and selects exactly the
 * rows the current filter produced -- not "everything". A control that says
 * "all" while meaning "all 12 of 340 shown" is how a bulk action reaches
 * profiles nobody chose.
 *
 * The confirm handler passes a concrete, already-resolved ID list. The
 * selection is never re-evaluated server-side from the filter, so a profile
 * created while the dialog is open cannot be swept in, and a profile hidden
 * by the filter cannot reappear.
 */
export function ProfileSelectDialog({
  open,
  onClose,
  title,
  confirmLabel,
  busy,
  onConfirm,
}: Props) {
  const [search, setSearch] = useState("");
  const [tag, setTag] = useState("");
  const [running, setRunning] = useState<RunningFilter>("any");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data, isLoading, isError } = useQuery({
    queryKey: ["extensions", "selectable-profiles"],
    queryFn: () =>
      get<{ profiles?: SelectableProfile[] }>("/api/profiles").then(
        (d) => d.profiles ?? [],
      ),
    enabled: open,
    staleTime: 10_000,
  });

  // A dialog must not carry a stale selection into a new operation. That is
  // handled by remounting: the caller keys this component on what it is
  // acting upon, so every open starts with fresh state. Resetting from an
  // effect instead would run a render late, and briefly show the previous
  // operation's selection.
  const allProfiles = useMemo(() => data ?? [], [data]);
  const allTags = useMemo(() => {
    const t = new Set<string>();
    allProfiles.forEach((p) => (p.tags ?? []).forEach((x) => t.add(x)));
    return Array.from(t).sort();
  }, [allProfiles]);

  const matching = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return allProfiles.filter((p) => {
      if (needle && !String(p.name ?? "").toLowerCase().includes(needle)) return false;
      if (tag && !(p.tags ?? []).includes(tag)) return false;
      if (running === "running" && !p.isRunning) return false;
      if (running === "stopped" && p.isRunning) return false;
      return true;
    });
  }, [allProfiles, search, tag, running]);

  const matchingIds = useMemo(() => matching.map((p) => p.id), [matching]);
  const allMatchingSelected =
    matchingIds.length > 0 && matchingIds.every((id) => selected.has(id));

  // The final target list: only profiles that are both selected AND still
  // present. A selected profile the operator then filtered out stays
  // selected on purpose -- the count and the list below both show it, so
  // nothing is applied invisibly.
  const targets = useMemo(
    () => allProfiles.filter((p) => selected.has(p.id)),
    [allProfiles, selected],
  );

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const selectAllMatching = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      matchingIds.forEach((id) => next.add(id));
      return next;
    });

  const filterDescription =
    [
      search.trim() ? `name contains “${search.trim()}”` : null,
      tag ? `tag “${tag}”` : null,
      running !== "any" ? running : null,
    ]
      .filter(Boolean)
      .join(", ") || "no filter";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description="Choose the profiles this applies to. Nothing happens until you confirm."
      wide
      footer={
        <div className="flex w-full items-center justify-between gap-3">
          <span className="text-xs text-ink-3">
            {targets.length} profile{targets.length === 1 ? "" : "s"} selected
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              onClick={() => onConfirm(targets.map((p) => p.id))}
              disabled={busy || targets.length === 0}
            >
              {busy ? "Working…" : `${confirmLabel} (${targets.length})`}
            </Button>
          </div>
        </div>
      }
    >
      <div className="grid gap-3">
        <div className="grid gap-2 sm:grid-cols-3">
          <Input
            placeholder="Search by profile name"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search by profile name"
          />
          <Select
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            aria-label="Filter by tag"
          >
            <option value="">All tags</option>
            {allTags.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </Select>
          <Select
            value={running}
            onChange={(e) => setRunning(e.target.value as RunningFilter)}
            aria-label="Filter by running state"
          >
            <option value="any">Running and stopped</option>
            <option value="running">Running only</option>
            <option value="stopped">Stopped only</option>
          </Select>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-3">
          <Button
            size="sm"
            variant="secondary"
            onClick={selectAllMatching}
            disabled={matchingIds.length === 0 || allMatchingSelected}
          >
            {/* The scope is in the label, not implied. */}
            Select all {matchingIds.length} matching ({filterDescription})
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setSelected(new Set())}
            disabled={selected.size === 0}
          >
            Clear selection
          </Button>
          <span>
            {matchingIds.length} of {allProfiles.length} profiles match the filter
          </span>
        </div>

        <div className="max-h-64 overflow-auto rounded-md border border-line">
          {isLoading ? (
            <div className="grid gap-2 p-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </div>
          ) : isError ? (
            <p className="p-3 text-sm text-err">Could not load profiles.</p>
          ) : matching.length === 0 ? (
            <p className="p-3 text-sm text-ink-3">No profiles match this filter.</p>
          ) : (
            <ul className="divide-y divide-line">
              {matching.map((p) => (
                <li key={p.id} className="flex items-center gap-3 px-3 py-2">
                  <Checkbox
                    checked={selected.has(p.id)}
                    onChange={() => toggle(p.id)}
                    aria-label={`Select ${p.name ?? p.id}`}
                  />
                  <span className="flex-1 truncate text-sm text-ink-1">
                    {p.name ?? p.id}
                  </span>
                  {(p.tags ?? []).slice(0, 2).map((t) => (
                    <Badge key={t} tone="idle">
                      {t}
                    </Badge>
                  ))}
                  <Badge tone={p.isRunning ? "ok" : "idle"}>
                    {p.isRunning ? "running" : "stopped"}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>

        {targets.length > 0 && (
          <div className="rounded-md border border-line bg-surface-2 p-3">
            <p className="mb-1 text-xs font-medium text-ink-2">
              This will apply to exactly these {targets.length} profile
              {targets.length === 1 ? "" : "s"}:
            </p>
            <p className="text-xs text-ink-3">
              {targets.map((p) => p.name ?? p.id).join(", ")}
            </p>
            {targets.some((p) => p.isRunning) && (
              <p className="mt-2 text-xs text-warn">
                {targets.filter((p) => p.isRunning).length} of these are running.
                An unpacked extension is loaded by a command-line flag, so those
                show <strong>Pending restart</strong> until you restart them. No
                running session is closed for you.
              </p>
            )}
          </div>
        )}
      </div>
    </Dialog>
  );
}

/** Resolve a selection server-side, used to preview a filter's exact scope. */
export async function resolveProfiles(body: {
  profileIds?: string[];
  search?: string;
  tags?: string[];
  running?: RunningFilter;
  selectAllMatching?: boolean;
}): Promise<SelectableProfile[]> {
  const res = await post<{ profiles?: SelectableProfile[] }>(
    "/api/extensions/resolve-profiles",
    body,
  );
  return res.profiles ?? [];
}
