import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/** Separator: hairline divider, horizontal or vertical. */

export function Separator({
  orientation = "horizontal",
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { orientation?: "horizontal" | "vertical" }) {
  return (
    <div
      role="separator"
      aria-orientation={orientation}
      className={cn(
        "bg-border-subtle",
        orientation === "horizontal" ? "h-px w-full" : "h-full w-px",
        className,
      )}
      {...rest}
    />
  );
}
