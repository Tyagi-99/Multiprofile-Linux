import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/** Progress: determinate progress bar. */

export function Progress({
  value,
  max = 100,
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { value: number; max?: number }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-3", className)}
      {...rest}
    >
      <div
        className="h-full rounded-full bg-brand-600 transition-[width] duration-300 ease-out dark:bg-brand-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
