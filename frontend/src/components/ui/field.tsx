import { useId, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Label } from "./label";

/**
 * Field: label + control + hint/error row. The control must accept `id`
 * and `aria-describedby` passthrough (all our inputs do).
 */

export function Field({
  label,
  hint,
  error,
  required,
  className,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  className?: string;
  children: (props: { id: string; describedBy?: string }) => ReactNode;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("grid gap-1.5", className)}>
      <Label htmlFor={id}>
        {label}
        {required && (
          <span aria-hidden className="ml-0.5 text-err">
            *
          </span>
        )}
      </Label>
      {children({ id, describedBy })}
      {error ? (
        <p id={errorId} role="alert" className="text-xs text-err">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="text-xs text-ink-3">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
