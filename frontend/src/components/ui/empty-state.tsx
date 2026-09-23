import type { ReactNode } from "react";
import { Button } from "./button";

/**
 * Empty state: icon, title, one honest sentence, optional action.
 * Never a fake illustration; never fake data.
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      {icon && <div className="mb-4 text-ink-3">{icon}</div>}
      <h3 className="text-sm font-semibold text-ink-1">{title}</h3>
      {description && (
        <p className="mt-1 max-w-sm text-sm leading-relaxed text-ink-3">{description}</p>
      )}
      {action && (
        <Button variant="secondary" size="sm" className="mt-4" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}

/**
 * Error state: what failed, why (API message), and a retry action.
 * Used for query failures across all views.
 */
export function ErrorState({
  title = "Couldn't load this view",
  message,
  onRetry,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <div
        aria-hidden
        className="mb-4 flex h-10 w-10 items-center justify-center rounded-full bg-err-dim text-err"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="10" />
          <path d="M12 8v4M12 16h.01" />
        </svg>
      </div>
      <h3 className="text-sm font-semibold text-ink-1">{title}</h3>
      {message && (
        <p className="tnum mt-1 max-w-md text-sm leading-relaxed text-ink-3">{message}</p>
      )}
      {onRetry && (
        <Button variant="secondary" size="sm" className="mt-4" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
