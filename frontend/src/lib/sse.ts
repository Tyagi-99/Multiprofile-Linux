/**
 * SSE client for GET /api/events/stream.
 *
 * The server emits a uniform envelope {"type", "payload", "at"}. Each event
 * invalidates ONLY the query keys it affects (see query-keys.ts) — never a
 * blanket invalidate-all, so the UI never loses scroll position or form state.
 *
 * Reconnects with exponential backoff (1s → 30s). No polling fallback here:
 * the stream is the single realtime path; queries refetch on window focus
 * via TanStack defaults if the operator wants fresher data.
 */

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  auditKeys,
  jobsKeys,
  profilesKeys,
  systemKeys,
} from "./query-keys";
import type { JobProgressPayload, ProfileStatusPayload, SSEEvent } from "./types";

export interface RecentEvent {
  id: number;
  type: string;
  summary: string;
  at: string;
}

let eventSeq = 0;

function summarize(type: string, payload: Record<string, unknown>): string {
  switch (type) {
    case "profile.status": {
      const p = payload as unknown as ProfileStatusPayload;
      return `Profile ${shortId(p.profile_id)}: ${p.old_state ?? "—"} → ${p.new_state ?? "—"}`;
    }
    case "job.progress": {
      const p = payload as unknown as JobProgressPayload;
      return `Job ${shortId(p.job_id)} (${p.job_type}): ${p.status}${p.detail ? ` — ${p.detail}` : ""}`;
    }
    default:
      return type;
  }
}

function shortId(id: unknown): string {
  const s = String(id ?? "");
  return s.length > 8 ? `${s.slice(0, 8)}…` : s;
}

const MAX_BACKOFF = 30_000;

export function useSSE(onEvent?: (e: RecentEvent) => void) {
  const queryClient = useQueryClient();
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  });

  useEffect(() => {
    let es: EventSource | null = null;
    let backoff = 1000;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const handle = (raw: string) => {
      let evt: SSEEvent;
      try {
        evt = JSON.parse(raw) as SSEEvent;
      } catch {
        return;
      }
      if (!evt || typeof evt.type !== "string") return;
      const payload = (evt.payload ?? {}) as Record<string, unknown>;

      // Targeted invalidation per event type.
      switch (evt.type) {
        case "profile.status": {
          const p = payload as unknown as ProfileStatusPayload;
          queryClient.invalidateQueries({ queryKey: profilesKeys.lists() });
          if (p.profile_id) {
            queryClient.invalidateQueries({ queryKey: profilesKeys.detail(p.profile_id) });
          }
          queryClient.invalidateQueries({ queryKey: systemKeys.health });
          break;
        }
        case "job.progress": {
          const p = payload as unknown as JobProgressPayload;
          queryClient.invalidateQueries({ queryKey: jobsKeys.lists() });
          if (p.job_id) {
            queryClient.invalidateQueries({ queryKey: jobsKeys.detail(p.job_id) });
          }
          if (p.profile_id) {
            queryClient.invalidateQueries({ queryKey: profilesKeys.detail(p.profile_id) });
          }
          break;
        }
        default:
          // Unknown future event types: refresh the audit trail only if the
          // type suggests operational relevance; otherwise ignore.
          if (evt.type.startsWith("audit.") || evt.type.startsWith("system.")) {
            queryClient.invalidateQueries({ queryKey: auditKeys.all });
          }
          break;
      }

      onEventRef.current?.({
        id: ++eventSeq,
        type: evt.type,
        summary: summarize(evt.type, payload),
        at: evt.at ?? new Date().toISOString(),
      });
    };

    const connect = () => {
      if (disposed) return;
      try {
        es = new EventSource("/api/events/stream");
      } catch {
        scheduleRetry();
        return;
      }
      es.onmessage = (e: MessageEvent) => {
        backoff = 1000; // healthy stream resets backoff
        handle(e.data as string);
      };
      es.onerror = () => {
        es?.close();
        es = null;
        scheduleRetry();
      };
    };

    const scheduleRetry = () => {
      if (disposed) return;
      retryTimer = setTimeout(() => {
        backoff = Math.min(backoff * 2, MAX_BACKOFF);
        connect();
      }, backoff);
    };

    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

/** Query hooks shared by the shell live in ./queries. */
