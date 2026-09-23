import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/**
 * Button. Variants are the only allowed emphasis levels:
 * primary (one per view), secondary, ghost, danger, subtle.
 * No ad-hoc colors at call sites.
 */

type Variant = "primary" | "secondary" | "ghost" | "danger" | "subtle";
type Size = "sm" | "md" | "icon";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-brand-600 text-brand-on hover:bg-brand-500 active:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-400 shadow-1",
  secondary:
    "bg-surface-1 text-ink-1 border border-border-strong hover:bg-surface-3 active:bg-surface-2",
  ghost: "text-ink-2 hover:text-ink-1 hover:bg-surface-3",
  danger:
    "bg-err text-white hover:brightness-110 active:brightness-95 shadow-1",
  subtle: "text-ink-3 hover:text-ink-1",
};

const SIZES: Record<Size, string> = {
  sm: "h-8 px-3 text-[13px] gap-1.5",
  md: "h-9 px-4 text-sm gap-2",
  icon: "h-9 w-9",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "secondary", size = "md", loading, className, disabled, children, ...rest }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex select-none items-center justify-center rounded-md font-medium",
        "transition-colors duration-150 ease-out",
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {loading && (
        <span
          aria-hidden
          className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  ),
);
Button.displayName = "Button";
