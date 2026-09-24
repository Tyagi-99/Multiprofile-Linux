import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { apiMessage, del, get, post, put } from "../../../lib/api";
import { absoluteTime, timeAgo } from "../../../lib/format";
import type { NotesResponse, Profile } from "../../../lib/types";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../ui/card";
import { EmptyState } from "../../ui/empty-state";
import { Input } from "../../ui/input";
import { Skeleton } from "../../ui/skeleton";

/**
 * Two kinds of note, deliberately separate.
 *
 * The summary is the one line worth seeing without opening the profile —
 * it is the original `notes` field and still shows up in search. The log
 * below it is append-only and timestamped, so "switched proxy, watch for
 * captchas" carries the date it was true.
 */
export function NotesTab({ profile }: { profile: Profile }) {
  const qc = useQueryClient();
  const key = ["profiles", "notes", profile.id] as const;
  const [draft, setDraft] = useState("");
  const [summary, setSummary] = useState<string | null>(null);

  const query = useQuery({
    queryKey: key,
    queryFn: () => get<NotesResponse>(`/api/profiles/${encodeURIComponent(profile.id)}/notes`),
    staleTime: 5_000,
    retry: 1,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["profiles"] });
  };

  const add = useMutation({
    mutationFn: (text: string) =>
      post(`/api/profiles/${encodeURIComponent(profile.id)}/notes`, { text }),
    onSuccess: () => {
      setDraft("");
      refresh();
    },
    onError: (e) => toast.error("Could not add the note", { description: apiMessage(e) }),
  });

  const remove = useMutation({
    mutationFn: (noteId: string) =>
      del(`/api/profiles/${encodeURIComponent(profile.id)}/notes/${encodeURIComponent(noteId)}`),
    onSuccess: refresh,
    onError: (e) => toast.error("Could not delete the note", { description: apiMessage(e) }),
  });

  const saveSummary = useMutation({
    mutationFn: (text: string) => put(`/api/profiles/${encodeURIComponent(profile.id)}`, { notes: text }),
    onSuccess: () => {
      toast.success("Summary saved");
      setSummary(null);
      refresh();
    },
    onError: (e) => toast.error("Could not save the summary", { description: apiMessage(e) }),
  });

  const entries = query.data?.entries ?? [];
  const currentSummary = summary ?? query.data?.summary ?? "";

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Pinned summary</CardTitle>
          <CardDescription>
            One line about this profile. Searchable, and the only note shown outside this page.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex gap-2 pt-2">
          <Input
            value={currentSummary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="e.g. Client account — do not change the timezone"
            aria-label="Pinned summary"
          />
          <Button
            variant="secondary"
            disabled={summary === null || saveSummary.isPending}
            onClick={() => saveSummary.mutate(currentSummary)}
          >
            Save
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Note log</CardTitle>
          <CardDescription>
            Timestamped entries, newest first. Use it for what you changed and when.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 pt-2">
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.trim()) add.mutate(draft.trim());
            }}
          >
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Add a note…"
              aria-label="New note"
            />
            <Button type="submit" variant="primary" disabled={!draft.trim() || add.isPending}>
              {add.isPending ? "Adding…" : "Add"}
            </Button>
          </form>

          {query.isLoading && !query.data ? (
            <div className="grid gap-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : query.isError ? (
            <EmptyState
              title="Could not load notes"
              description="The backend did not respond."
              action={{ label: "Retry", onClick: () => query.refetch() }}
            />
          ) : entries.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-ink-3">
              No entries yet. The first note you add appears here with today&apos;s date.
            </p>
          ) : (
            <ul className="grid gap-2">
              {entries.map((e) => (
                <li
                  key={e.id}
                  className="flex items-start gap-3 rounded-lg border border-border-subtle px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <p className="whitespace-pre-wrap break-words text-[13px] text-ink-1">{e.text}</p>
                    <span className="mt-0.5 flex items-center gap-2 text-xs text-ink-3">
                      <span title={absoluteTime(e.at)}>{timeAgo(e.at)}</span>
                      {e.kind && e.kind !== "note" && <Badge tone="info">{e.kind}</Badge>}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => remove.mutate(e.id)}
                    className="rounded-md p-1.5 text-ink-3 transition-colors hover:bg-surface-3 hover:text-err"
                    title="Delete this entry"
                    aria-label="Delete this entry"
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
