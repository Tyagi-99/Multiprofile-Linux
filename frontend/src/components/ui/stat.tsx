import type { HTMLAttributes } from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "../../lib/cn";

/** Stat: dashboard metric card with optional delta. */

export function Stat({
  label,
  value,
  delta,
  deltaTone = "neutral",
  hint,
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & {
  label: string;
  value: string;
  delta?: string;
  deltaTone?: "up" | "down" | "neutral";
  hint?: string;
}) {
  const DeltaIcon = deltaTone === "up" ? ArrowUpRight : deltaTone === "down" ? ArrowDownRight : Minus;
  const deltaCls =
    deltaTone === "up" ? "text-ok" : deltaTone === "down" ? "text-err" : "text-ink-3";
  return (
    <div
      className={cn("rounded-lg border border-border-subtle bg-surface-1 p-4 shadow-1", className)}
      {...rest}
    >
      <p className="text-[13px] font-medium text-ink-3">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight text-ink-1">{value}</p>
      {(delta || hint) && (
        <p className="mt-1 flex items-center gap-1 text-xs text-ink-3">
          {delta && (
            <span className={cn("inline-flex items-center gap-0.5 font-medium", deltaCls)}>
              <DeltaIcon size={14} aria-hidden />
              {delta}
            </span>
          )}
          {hint && <span>{hint}</span>}
        </p>
      )}
    </div>
  );
}
