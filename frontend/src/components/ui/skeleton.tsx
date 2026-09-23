import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/** Skeleton: shimmering placeholder for loading content. */

export function Skeleton({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden
      className={cn("animate-pulse rounded-md bg-surface-3", className)}
      {...rest}
    />
  );
}
