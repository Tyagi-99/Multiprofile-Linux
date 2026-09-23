import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/**
 * Avatar: initials circle. Color is derived from the name (stable hash),
 * chosen from muted token pairs — never random per render.
 */

const PAIRS = [
  "bg-brand-100 text-brand-800 dark:bg-brand-900 dark:text-brand-200",
  "bg-info-dim text-info",
  "bg-ok-dim text-ok",
  "bg-warn-dim text-warn",
  "bg-err-dim text-err",
];

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

export function Avatar({
  name,
  size = "md",
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { name: string; size?: "sm" | "md" | "lg" }) {
  const initials = name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
  const sizes = {
    sm: "h-7 w-7 text-[11px]",
    md: "h-9 w-9 text-xs",
    lg: "h-12 w-12 text-sm",
  } as const;
  return (
    <div
      role="img"
      aria-label={name}
      title={name}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center rounded-full font-semibold",
        sizes[size],
        PAIRS[hash(name) % PAIRS.length],
        className,
      )}
      {...rest}
    >
      {initials || "?"}
    </div>
  );
}
