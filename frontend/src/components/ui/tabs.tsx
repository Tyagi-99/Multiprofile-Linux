import {
  createContext,
  useContext,
  useId,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { cn } from "../../lib/cn";

/**
 * Tabs. Keyboard: Left/Right/Home/End move between triggers (roving tabindex).
 * Controlled via value/onValueChange, or uncontrolled via defaultValue.
 */

interface TabsCtx {
  value: string;
  setValue: (v: string) => void;
  baseId: string;
}

const Ctx = createContext<TabsCtx | null>(null);

function useTabs() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("Tabs components must be used inside <Tabs>");
  return ctx;
}

export function Tabs({
  value,
  onValueChange,
  defaultValue,
  children,
  className,
}: {
  value?: string;
  onValueChange?: (v: string) => void;
  defaultValue?: string;
  children: ReactNode;
  className?: string;
}) {
  const baseId = useId();
  const [internal, setInternal] = useState(defaultValue ?? "");
  const current = value ?? internal;
  const setValue = (v: string) => {
    if (value === undefined) setInternal(v);
    onValueChange?.(v);
  };
  return (
    <Ctx.Provider value={{ value: current, setValue, baseId }}>
      <div className={className}>{children}</div>
    </Ctx.Provider>
  );
}

export function TabsList({ className, children }: { className?: string; children: ReactNode }) {
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const triggers = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=tab]"));
    const idx = triggers.indexOf(document.activeElement as HTMLButtonElement);
    let next = -1;
    if (e.key === "ArrowRight") next = (idx + 1) % triggers.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + triggers.length) % triggers.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = triggers.length - 1;
    if (next >= 0 && triggers[next]) {
      e.preventDefault();
      triggers[next].focus();
      triggers[next].click();
    }
  };
  return (
    <div
      role="tablist"
      onKeyDown={onKeyDown}
      className={cn(
        "inline-flex items-center gap-1 rounded-lg border border-border-subtle bg-surface-1 p-1",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function TabsTrigger({
  value,
  className,
  children,
}: {
  value: string;
  className?: string;
  children: ReactNode;
}) {
  const { value: current, setValue, baseId } = useTabs();
  const selected = current === value;
  return (
    <button
      type="button"
      role="tab"
      id={`${baseId}-tab-${value}`}
      aria-selected={selected}
      aria-controls={`${baseId}-panel-${value}`}
      tabIndex={selected ? 0 : -1}
      onClick={() => setValue(value)}
      className={cn(
        "rounded-md px-3 py-1.5 text-[13px] font-medium text-ink-3 transition-colors",
        "hover:text-ink-1 focus-visible:outline-2 focus-visible:outline-brand-500",
        selected && "bg-surface-3 text-ink-1 shadow-1",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function TabsContent({
  value,
  className,
  children,
}: {
  value: string;
  className?: string;
  children: ReactNode;
}) {
  const { value: current, baseId } = useTabs();
  if (current !== value) return null;
  return (
    <div
      role="tabpanel"
      id={`${baseId}-panel-${value}`}
      aria-labelledby={`${baseId}-tab-${value}`}
      tabIndex={0}
      className={cn("pt-4 focus-visible:outline-none", className)}
    >
      {children}
    </div>
  );
}
