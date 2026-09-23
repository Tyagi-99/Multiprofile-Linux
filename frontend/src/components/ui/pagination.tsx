import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "../../lib/cn";

/** Pagination: compact page controls. */

export function Pagination({
  page,
  totalPages,
  onPageChange,
  className,
}: {
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  className?: string;
}) {
  if (totalPages <= 1) return null;
  const btn =
    "inline-flex h-8 min-w-8 items-center justify-center rounded-md px-2 text-[13px] text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink-1 disabled:cursor-not-allowed disabled:opacity-40";
  // Window of up to 5 page numbers around the current page.
  const start = Math.max(1, Math.min(page - 2, totalPages - 4));
  const nums = Array.from({ length: Math.min(5, totalPages) }, (_, i) => start + i);
  return (
    <nav aria-label="Pagination" className={cn("flex items-center gap-1", className)}>
      <button
        type="button"
        className={btn}
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
        aria-label="Previous page"
      >
        <ChevronLeft size={16} aria-hidden />
      </button>
      {nums.map((n) => (
        <button
          key={n}
          type="button"
          className={cn(btn, n === page && "bg-surface-3 font-semibold text-ink-1")}
          aria-current={n === page ? "page" : undefined}
          onClick={() => onPageChange(n)}
        >
          {n}
        </button>
      ))}
      <button
        type="button"
        className={btn}
        disabled={page >= totalPages}
        onClick={() => onPageChange(page + 1)}
        aria-label="Next page"
      >
        <ChevronRight size={16} aria-hidden />
      </button>
    </nav>
  );
}

/** CursorPagination: prev/next for cursor-based APIs (no total pages). */
export function CursorPagination({
  page,
  hasNext,
  hasPrev,
  onNext,
  onPrev,
  disabled,
  className,
}: {
  page: number;
  hasNext: boolean;
  hasPrev: boolean;
  onNext: () => void;
  onPrev: () => void;
  disabled?: boolean;
  className?: string;
}) {
  if (!hasNext && !hasPrev) return null;
  const btn =
    "inline-flex h-8 items-center gap-1 rounded-md px-2.5 text-[13px] text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink-1 disabled:cursor-not-allowed disabled:opacity-40";
  return (
    <nav aria-label="Pagination" className={cn("flex items-center gap-1", className)}>
      <button type="button" className={btn} disabled={!hasPrev || disabled} onClick={onPrev}>
        <ChevronLeft size={16} aria-hidden /> Prev
      </button>
      <span className="px-1 text-[13px] tabular-nums text-ink-3" aria-current="page">
        Page {page}
      </span>
      <button type="button" className={btn} disabled={!hasNext || disabled} onClick={onNext}>
        Next <ChevronRight size={16} aria-hidden />
      </button>
    </nav>
  );
}
