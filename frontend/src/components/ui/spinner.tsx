import { cn } from "../../lib/cn";

export function Spinner({ className, label = "Loading…" }: { className?: string; label?: string }) {
  return (
    <span role="status" aria-label={label} className={cn("inline-flex items-center", className)}>
      <span
        aria-hidden
        className="h-4 w-4 animate-spin rounded-full border-2 border-border-strong border-t-brand-500"
      />
    </span>
  );
}

/** Skeleton row for tables/lists while data loads. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn("animate-pulse rounded-md bg-surface-3", className)}
    />
  );
}
