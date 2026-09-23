import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

/**
 * CSS-only tooltip. Wraps icon-only controls; the label is required for
 * screen readers (aria-label) and shown visually on hover/focus.
 */
export function Tooltip({
  label,
  children,
  side = "bottom",
  className,
}: {
  label: string;
  children: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  className?: string;
}) {
  return (
    <span className={cn("mp-tip", `mp-tip-${side}`, "inline-flex", className)} data-tip={label}>
      {children}
    </span>
  );
}
