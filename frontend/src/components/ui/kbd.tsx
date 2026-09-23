import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/** Kbd: keyboard hint chip. */

export function Kbd({ className, ...rest }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center rounded border border-border-strong",
        "bg-surface-2 px-1 font-mono text-[11px] font-medium text-ink-2",
        className,
      )}
      {...rest}
    />
  );
}
