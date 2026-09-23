import {
  cloneElement,
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import { cn } from "../../lib/cn";

/**
 * DropdownMenu: action menu.
 *
 *   <DropdownMenu>
 *     <DropdownMenuTrigger><Button>Actions</Button></DropdownMenuTrigger>
 *     <DropdownMenuContent>
 *       <MenuItem onSelect={...}>Rename</MenuItem>
 *       <MenuSeparator />
 *       <MenuItem danger onSelect={...}>Delete</MenuItem>
 *     </DropdownMenuContent>
 *   </DropdownMenu>
 *
 * Keyboard: Enter/Space/Down on trigger opens; Up/Down/Home/End navigate
 * items; Enter/Space activate; Escape closes and refocuses the trigger.
 */

interface MenuCtx {
  open: boolean;
  setOpen: (open: boolean) => void;
  menuId: string;
  registerTrigger: (el: HTMLElement | null) => void;
  focusTrigger: () => void;
  isTrigger: (node: Node) => boolean;
}

const Ctx = createContext<MenuCtx | null>(null);

function useMenu() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("DropdownMenu components must be used inside <DropdownMenu>");
  return ctx;
}

export function DropdownMenu({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const triggerEl = useRef<HTMLElement | null>(null);
  const registerTrigger = (el: HTMLElement | null) => {
    triggerEl.current = el;
  };
  const focusTrigger = () => triggerEl.current?.focus();
  const isTrigger = (node: Node) => !!triggerEl.current?.contains(node);
  return (
    <Ctx.Provider value={{ open, setOpen, menuId, registerTrigger, focusTrigger, isTrigger }}>
      <span className="relative inline-flex">{children}</span>
    </Ctx.Provider>
  );
}

type TriggerChildProps = {
  onClick?: (e: React.MouseEvent) => void;
  onKeyDown?: (e: React.KeyboardEvent) => void;
  ref?: React.Ref<HTMLElement>;
  // Trigger is typically a Button; allow passthrough of aria/data props.
  [key: string]: unknown;
};

export function DropdownMenuTrigger({
  children,
}: {
  children: ReactElement<TriggerChildProps>;
}) {
  const { open, setOpen, menuId, registerTrigger } = useMenu();

  return cloneElement(children, {
    ref: registerTrigger,
    "aria-haspopup": "menu",
    "aria-expanded": open,
    "aria-controls": menuId,
    onClick: (e: React.MouseEvent) => {
      children.props.onClick?.(e);
      setOpen(!open);
    },
    onKeyDown: (e: React.KeyboardEvent) => {
      children.props.onKeyDown?.(e);
      if (["ArrowDown", "Enter", " "].includes(e.key) && !open) {
        e.preventDefault();
        setOpen(true);
      }
    },
  });
}

export function DropdownMenuContent({ children }: { children: ReactNode }) {
  const { open, setOpen, menuId, focusTrigger, isTrigger } = useMenu();
  const panelRef = useRef<HTMLDivElement>(null);
  const itemsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    // Focus the first item on open.
    const first = itemsRef.current?.querySelector<HTMLButtonElement>("[role=menuitem]:not([disabled])");
    first?.focus();
    const onDown = (e: PointerEvent) => {
      if (!panelRef.current?.contains(e.target as Node) && !isTrigger(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
        focusTrigger();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, setOpen, focusTrigger, isTrigger]);

  if (!open) return null;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(
      itemsRef.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not([disabled])") ?? [],
    );
    const idx = items.indexOf(document.activeElement as HTMLButtonElement);
    let next = -1;
    if (e.key === "ArrowDown") next = (idx + 1) % items.length;
    else if (e.key === "ArrowUp") next = (idx - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    if (next >= 0 && items[next]) {
      e.preventDefault();
      items[next].focus();
    }
  };

  return (
    <div
      ref={panelRef}
      role="menu"
      id={menuId}
      onKeyDown={onKeyDown}
      className="absolute right-0 top-full z-50 mt-1.5 min-w-44 overflow-hidden rounded-lg border border-border-strong bg-surface-1 p-1 shadow-3"
    >
      <div ref={itemsRef} className="grid gap-px">
        {children}
      </div>
    </div>
  );
}

export function MenuItem({
  onSelect,
  danger,
  disabled,
  className,
  children,
}: {
  onSelect?: () => void;
  danger?: boolean;
  disabled?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const { setOpen } = useMenu();
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={() => {
        onSelect?.();
        setOpen(false);
      }}
      className={cn(
        "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[13px] text-ink-1",
        "transition-colors hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-40",
        danger && "text-err hover:bg-err-dim",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function MenuSeparator() {
  return <div role="separator" className="my-1 h-px bg-border-subtle" />;
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return (
    <p className="px-2.5 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-3">
      {children}
    </p>
  );
}
