import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

/** Key/value rows used across the detail tabs. */

export function KV({ pairs }: { pairs: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[160px_1fr] gap-x-4 gap-y-2">
      {pairs.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-[13px] text-ink-3">{k}</dt>
          <dd className="min-w-0 text-[13px] text-ink-1">{v ?? <NA />}</dd>
        </div>
      ))}
    </dl>
  );
}

export function NA() {
  return <span className="text-ink-3">—</span>;
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className={cn("break-all font-mono text-[12.5px]")}>{children}</span>;
}

export function fmtVal(v: unknown): ReactNode {
  if (v == null || v === "") return <NA />;
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}
