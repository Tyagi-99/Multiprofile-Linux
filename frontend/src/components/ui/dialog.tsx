import { useEffect, useRef, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Button } from "./button";

/**
 * Accessible modal dialog. Focus-trapped (initial focus + Escape), labelled,
 * overlay click dismisses unless `modal` is set (destructive confirms).
 * Modals are reserved for critical/destructive confirmations; contextual
 * detail belongs in drawers (Phase 3).
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  modal = false,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  modal?: boolean;
  wide?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    panel?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !modal) {
        e.stopPropagation();
        onClose();
      }
      // Minimal focus trap: keep Tab inside the panel.
      if (e.key === "Tab" && panel) {
        const items = panel.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (items.length === 0) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onClose, modal]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="presentation"
      onMouseDown={(e) => {
        if (!modal && e.target === e.currentTarget) onClose();
      }}
    >
      <div aria-hidden className="absolute inset-0 bg-black/45 dark:bg-black/60" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cn(
          "relative w-full rounded-xl border border-border-subtle bg-surface-1 shadow-3",
          "animate-[mp-pop_160ms_var(--ease-out)]",
          wide ? "max-w-2xl" : "max-w-md",
        )}
      >
        <div className="border-b border-border-subtle px-5 py-4">
          <h2 className="text-[15px] font-semibold tracking-tight text-ink-1">{title}</h2>
          {description && <p className="mt-1 text-sm text-ink-3">{description}</p>}
        </div>
        <div className="px-5 py-4">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-border-subtle px-5 py-3.5">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

/** Convenience destructive-confirm dialog. */
export function ConfirmDialog({
  open,
  onClose,
  title,
  message,
  confirmLabel = "Delete",
  onConfirm,
  confirming,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  message: string;
  confirmLabel?: string;
  onConfirm: () => void;
  confirming?: boolean;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={confirming}>
            Cancel
          </Button>
          <Button variant="danger" onClick={onConfirm} loading={confirming}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="text-sm leading-relaxed text-ink-2">{message}</p>
    </Dialog>
  );
}
