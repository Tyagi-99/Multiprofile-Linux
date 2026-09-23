import { forwardRef, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

const FIELD =
  "w-full rounded-md border border-border-strong bg-surface-1 px-3 text-sm text-ink-1 placeholder:text-ink-3 " +
  "transition-colors duration-150 hover:border-ink-3 focus:border-brand-500 focus:outline-none " +
  "disabled:cursor-not-allowed disabled:opacity-50";

function Label({ htmlFor, children, hint }: { htmlFor?: string; children: ReactNode; hint?: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 block text-[13px] font-medium text-ink-2">
      {children}
      {hint && <span className="ml-1.5 font-normal text-ink-3">{hint}</span>}
    </label>
  );
}

export interface FieldProps {
  label?: ReactNode;
  hint?: string;
  error?: string;
}

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement> & FieldProps
>(({ label, hint, error, id, className, ...rest }, ref) => (
  <div className={className}>
    {label && <Label htmlFor={id} hint={hint}>{label}</Label>}
    <input
      ref={ref}
      id={id}
      className={cn(FIELD, "h-9", error && "border-err focus:border-err")}
      aria-invalid={!!error}
      {...rest}
    />
    {error && <p className="mt-1 text-xs text-err">{error}</p>}
  </div>
));
Input.displayName = "Input";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement> & FieldProps
>(({ label, hint, error, id, className, ...rest }, ref) => (
  <div className={className}>
    {label && <Label htmlFor={id} hint={hint}>{label}</Label>}
    <textarea
      ref={ref}
      id={id}
      className={cn(FIELD, "min-h-[88px] py-2", error && "border-err focus:border-err")}
      aria-invalid={!!error}
      {...rest}
    />
    {error && <p className="mt-1 text-xs text-err">{error}</p>}
  </div>
));
Textarea.displayName = "Textarea";

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement> & FieldProps & { options: Array<{ value: string; label: string }> }
>(({ label, hint, error, id, options, className, ...rest }, ref) => (
  <div className={className}>
    {label && <Label htmlFor={id} hint={hint}>{label}</Label>}
    <select
      ref={ref}
      id={id}
      className={cn(FIELD, "h-9 pr-8", error && "border-err focus:border-err")}
      aria-invalid={!!error}
      {...rest}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
    {error && <p className="mt-1 text-xs text-err">{error}</p>}
  </div>
));
Select.displayName = "Select";
