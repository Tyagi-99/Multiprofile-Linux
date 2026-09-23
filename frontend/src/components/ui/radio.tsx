import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/**
 * Radio + RadioGroup. Native inputs, custom styling, arrow-key navigation
 * comes free from the browser.
 */

export interface RadioProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: string;
  description?: string;
}

export const Radio = forwardRef<HTMLInputElement, RadioProps>(
  ({ label, description, className, disabled, ...rest }, ref) => (
    <label
      className={cn(
        "inline-flex cursor-pointer items-start gap-2 text-sm text-ink-1",
        disabled && "cursor-not-allowed opacity-50",
        className,
      )}
    >
      <input
        ref={ref}
        type="radio"
        disabled={disabled}
        className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer appearance-none rounded-full border border-border-strong bg-surface-1 transition-colors checked:border-[5px] checked:border-brand-600 focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed dark:checked:border-brand-500"
        {...rest}
      />
      {(label || description) && (
        <span className="grid">
          {label && <span>{label}</span>}
          {description && <span className="text-xs text-ink-3">{description}</span>}
        </span>
      )}
    </label>
  ),
);
Radio.displayName = "Radio";

export function RadioGroup({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset className={cn("grid gap-2", className)}>
      <legend className="mb-0.5 text-[13px] font-medium text-ink-2">{label}</legend>
      {children}
    </fieldset>
  );
}
