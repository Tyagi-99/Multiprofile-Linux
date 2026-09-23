import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

/**
 * Status badge. Tone is the ONLY way to color a badge — the mapping to the
 * design tokens is fixed here (green=ok/running, amber=warning, red=error,
 * blue=info, gray=inactive).
 */
export type BadgeTone = "ok" | "warn" | "err" | "info" | "idle" | "brand";

const TONES: Record<BadgeTone, string> = {
  ok: "bg-ok-dim text-ok",
  warn: "bg-warn-dim text-warn",
  err: "bg-err-dim text-err",
  info: "bg-info-dim text-info",
  idle: "bg-surface-3 text-ink-3",
  brand: "bg-brand-50 text-brand-700 dark:bg-brand-50 dark:text-brand-300",
};

export function Badge({
  tone = "idle",
  dot = false,
  children,
  className,
  title,
}: {
  tone?: BadgeTone;
  dot?: boolean;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium leading-5",
        TONES[tone],
        className,
      )}
    >
      {dot && <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

/** Map a profile launchState to a badge tone. Single source of truth. */
export function launchStateTone(state: string | null | undefined): BadgeTone {
  switch (state) {
    case "running":
      return "ok";
    case "starting":
    case "stopping":
      return "info";
    case "parked":
      return "warn";
    case "error":
      return "err";
    case "idle":
    default:
      return "idle";
  }
}
