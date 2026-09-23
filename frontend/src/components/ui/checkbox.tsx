import { forwardRef, type InputHTMLAttributes } from "react";
import { Check } from "lucide-react";
import { cn } from "../../lib/cn";

/** Checkbox: custom-styled, keyboard accessible. */

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: string;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ label, className, disabled, ...rest }, ref) => (
    <label
      className={cn(
        "inline-flex cursor-pointer items-center gap-2 text-sm text-ink-1",
        disabled && "cursor-not-allowed opacity-50",
        className,
      )}
    >
      <span className="relative inline-flex">
        <input
          ref={ref}
          type="checkbox"
          disabled={disabled}
          className="peer h-4 w-4 shrink-0 cursor-pointer appearance-none rounded border border-border-strong bg-surface-1 transition-colors checked:border-brand-600 checked:bg-brand-600 focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed dark:checked:border-brand-500 dark:checked:bg-brand-500"
          {...rest}
        />
        <Check
          size={12}
          aria-hidden
          className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-brand-on opacity-0 transition-opacity peer-checked:opacity-100"
        />
      </span>
      {label}
    </label>
  ),
);
Checkbox.displayName = "Checkbox";
