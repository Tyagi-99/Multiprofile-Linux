import { forwardRef, type SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "../../lib/cn";

/** Select: styled native select (robust + accessible). */

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...rest }, ref) => (
    <span className={cn("relative inline-flex w-full items-center", className)}>
      <select
        ref={ref}
        className={cn(
          "h-9 w-full appearance-none rounded-md border border-border-strong bg-surface-1",
          "pl-3 pr-8 text-sm text-ink-1 transition-colors",
          "hover:border-ink-3 focus:border-brand-500 focus:outline-none",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown
        size={16}
        aria-hidden
        className="pointer-events-none absolute right-2.5 text-ink-3"
      />
    </span>
  ),
);
Select.displayName = "Select";
