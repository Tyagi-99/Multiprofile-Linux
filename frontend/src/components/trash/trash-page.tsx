/**
 * Trash — deleted profiles that can still be brought back.
 *
 * Deleting used to remove the row and the profile's chrome-profiles directory
 * in one step. That directory holds the cookies, logins and warmed-up session
 * state, which is the part of a profile that cannot be recreated, so a
 * misclick on the multi-select delete could take a whole fleet's worth of it.
 *
 * Two things this screen is careful about. The retained disk is shown per row
 * and in total, because the trash deliberately does *not* free space and an
 * operator deciding whether to empty it should be told what they get back.
 * And the permanent actions are the only ones styled as destructive, with a
 * modal confirm naming the count — restoring is reversible, so it asks for
 * nothing.
 */

import { useMemo, useState } from "react";
import { RotateCcw, Trash2 } from "lucide-react";
import { apiMessage } from "../../lib/api";
import { absoluteTime, formatBytes, timeAgo } from "../../lib/format";
import { useProfileActions } from "../../lib/mutations";
import { useTrash } from "../../lib/queries";
import type { TrashItem } from "../../lib/types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { Checkbox } from "../ui/checkbox";
import { Dialog } from "../ui/dialog";
import { EmptyState } from "../ui/empty-state";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { Mono } from "../profile-detail/kv";

function Row({
  item,
  checked,
  onCheck,
  onRestore,
  onPurge,
}: {
  item: TrashItem;
  checked: boolean;
  onCheck: (checked: boolean) => void;
  onRestore: () => void;
  onPurge: () => void;
}) {
  return (
    <TR>
      <TD className="w-8">
        <Checkbox
          checked={checked}
          onChange={(e) => onCheck(e.target.checked)}
          aria-label={`Select ${item.name}`}
        />
      </TD>
      <TD className="font-medium text-ink-1">
        <span className="truncate">{item.name}</span>
      </TD>
      <TD className="whitespace-nowrap">
        <Mono>
          <span className="text-ink-3">{item.id.slice(0, 12)}…</span>
        </Mono>
      </TD>
      <TD className="whitespace-nowrap text-ink-3" title={absoluteTime(item.deletedAt)}>
        {timeAgo(item.deletedAt)}
      </TD>
      <TD className="whitespace-nowrap text-ink-3">{formatBytes(item.diskBytes)}</TD>
      <TD className="w-[1%] whitespace-nowrap">
        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onRestore}>
            <RotateCcw size={14} aria-hidden /> Restore
          </Button>
          <Button variant="danger" size="sm" onClick={onPurge}>
            <Trash2 size={14} aria-hidden /> Delete
          </Button>
        </div>
      </TD>
    </TR>
  );
}

export function TrashPageView() {
  const { data, isLoading, isError, error, refetch } = useTrash();
  const actions = useProfileActions();
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [purgeTargets, setPurgeTargets] = useState<TrashItem[] | null>(null);
  const [emptyOpen, setEmptyOpen] = useState(false);

  const items = useMemo(() => data?.items ?? [], [data]);
  const selectedItems = useMemo(
    () => items.filter((p) => selected[p.id]),
    [items, selected],
  );
  const allChecked = items.length > 0 && selectedItems.length === items.length;

  const run = async (fn: () => Promise<void>) => {
    await fn();
    setSelected({});
    refetch();
  };

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight text-ink-1">Trash</h1>
        <span className="flex-1" />
        {!isLoading && !isError && items.length > 0 && (
          <span className="text-xs text-ink-3">
            {items.length} profile{items.length === 1 ? "" : "s"} · {formatBytes(data?.diskBytes)} kept
          </span>
        )}
        <Button variant="secondary" size="sm" onClick={() => refetch()}>
          Refresh
        </Button>
        {items.length > 0 && (
          <Button variant="danger" size="sm" onClick={() => setEmptyOpen(true)}>
            <Trash2 size={14} aria-hidden /> Empty trash
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="grid gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : isError ? (
        <EmptyState
          title="Couldn't load the trash"
          description={apiMessage(error)}
          action={{ label: "Retry", onClick: () => refetch() }}
        />
      ) : items.length === 0 ? (
        <EmptyState
          title="Trash is empty"
          description="Deleted profiles appear here, with their browser storage intact, until you empty the trash."
        />
      ) : (
        <>
          {selectedItems.length > 0 && (
            <div className="flex items-center gap-3 rounded-lg border border-line bg-surface-1 px-3 py-2">
              <span className="text-sm text-ink-2">
                {selectedItems.length} selected
              </span>
              <span className="flex-1" />
              <Button
                variant="secondary"
                size="sm"
                onClick={() => run(() => actions.restore(selectedItems.map((p) => p.id)))}
              >
                <RotateCcw size={14} aria-hidden /> Restore
              </Button>
              <Button variant="danger" size="sm" onClick={() => setPurgeTargets(selectedItems)}>
                <Trash2 size={14} aria-hidden /> Delete permanently
              </Button>
            </div>
          )}

          <Card>
            <CardContent className="pt-6">
              <Table>
                <THead>
                  <TR>
                    <TH className="w-8">
                      <Checkbox
                        checked={allChecked}
                        onChange={(e) =>
                          setSelected(
                            e.target.checked
                              ? Object.fromEntries(items.map((p) => [p.id, true]))
                              : {},
                          )
                        }
                        aria-label="Select all"
                      />
                    </TH>
                    <TH>Name</TH>
                    <TH>ID</TH>
                    <TH>Deleted</TH>
                    <TH>Storage kept</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {items.map((item) => (
                    <Row
                      key={item.id}
                      item={item}
                      checked={Boolean(selected[item.id])}
                      onCheck={(c) => setSelected((s) => ({ ...s, [item.id]: c }))}
                      onRestore={() => run(() => actions.restore([item.id]))}
                      onPurge={() => setPurgeTargets([item])}
                    />
                  ))}
                </TBody>
              </Table>
              <p className="mt-3 text-xs text-ink-3">
                Source: GET /api/trash.{" "}
                {data && data.retentionDays > 0 ? (
                  <>
                    Profiles are deleted permanently{" "}
                    <Badge tone="warn">after {data.retentionDays} days</Badge> in the trash.
                  </>
                ) : (
                  "Automatic purging is off; nothing leaves the trash until you empty it."
                )}
              </p>
            </CardContent>
          </Card>
        </>
      )}

      <Dialog
        open={purgeTargets !== null}
        onClose={() => setPurgeTargets(null)}
        modal
        title={`Permanently delete ${purgeTargets?.length ?? 0} profile${
          (purgeTargets?.length ?? 0) === 1 ? "" : "s"
        }?`}
        description="Their cookies, logins and browser storage are removed from disk. This cannot be undone."
        footer={
          <>
            <Button variant="secondary" onClick={() => setPurgeTargets(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                const ids = (purgeTargets ?? []).map((p) => p.id);
                setPurgeTargets(null);
                run(() => actions.purge(ids));
              }}
            >
              Delete permanently
            </Button>
          </>
        }
      >
        <ul className="grid max-h-40 gap-1 overflow-auto">
          {(purgeTargets ?? []).map((p) => (
            <li key={p.id} className="truncate text-[13px] text-ink-2">
              {p.name}{" "}
              <span className="font-mono text-xs text-ink-3">
                {p.id.slice(0, 8)}… · {formatBytes(p.diskBytes)}
              </span>
            </li>
          ))}
        </ul>
      </Dialog>

      <Dialog
        open={emptyOpen}
        onClose={() => setEmptyOpen(false)}
        modal
        title="Empty the trash?"
        description={`All ${items.length} profile${
          items.length === 1 ? "" : "s"
        } and ${formatBytes(data?.diskBytes)} of browser storage are removed from disk. This cannot be undone.`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEmptyOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setEmptyOpen(false);
                run(() => actions.emptyTrash());
              }}
            >
              Empty trash
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-ink-2">
          Restoring a profile brings back the sessions it was logged into. Emptying the trash is
          the only action here that loses them.
        </p>
      </Dialog>
    </div>
  );
}
