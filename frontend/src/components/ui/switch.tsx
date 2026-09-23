import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/** Switch: toggle button with aria-pressed semantics. */

export interface SwitchProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label?: string;
}

export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(
  ({ checked, onCheckedChange, label, className, disabled, ...rest }, ref) => (
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-[22px] w-10 shrink-0 cursor-pointer rounded-full transition-colors",
        "focus-visible:outline-2 focus-visible:outline-brand-500",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-brand-600 dark:bg-brand-500" : "bg-surface-3",
        className,
      )}
      {...rest}
    >
      <span
        aria-hidden
        className={cn(
          "absolute top-[3px] h-4 w-4 rounded-full bg-white shadow-1 transition-transform",
          checked ? "translate-x-[21px]" : "translate-x-[3px]",
        )}
      />
      {label && <span className="sr-only">{label}</span>}
    </button>
  ),
);
Switch.displayName = "Switch";
