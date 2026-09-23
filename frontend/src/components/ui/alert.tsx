import type { HTMLAttributes, ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info, TriangleAlert } from "lucide-react";
import { cn } from "../../lib/cn";

/** Alert: inline callout for info / success / warning / error. */

type Tone = "info" | "success" | "warning" | "error";

const TONES: Record<Tone, { icon: ReactNode; cls: string }> = {
  info: {
    icon: <Info size={16} aria-hidden />,
    cls: "border-info/30 bg-info-dim text-ink-1",
  },
  success: {
    icon: <CheckCircle2 size={16} aria-hidden />,
    cls: "border-ok/30 bg-ok-dim text-ink-1",
  },
  warning: {
    icon: <TriangleAlert size={16} aria-hidden />,
    cls: "border-warn/30 bg-warn-dim text-ink-1",
  },
  error: {
    icon: <AlertCircle size={16} aria-hidden />,
    cls: "border-err/30 bg-err-dim text-ink-1",
  },
};

export function Alert({
  tone = "info",
  title,
  className,
  children,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { tone?: Tone; title?: string }) {
  const t = TONES[tone];
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn("flex gap-2.5 rounded-lg border px-3.5 py-3 text-[13px]", t.cls, className)}
      {...rest}
    >
      <span className="mt-px shrink-0" aria-hidden={false}>
        {t.icon}
      </span>
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={cn(title && "mt-0.5 text-ink-2")}>{children}</div>}
      </div>
    </div>
  );
}
