import { useEffect, useRef, useState } from "react";
import { Bell, Command, Moon, Plus, Search, Sun, Monitor } from "lucide-react";
import { cn } from "../../lib/cn";
import { navigate } from "../../lib/router";
import { useUI } from "../../stores/ui";
import { useSystemHealth } from "../../lib/queries";
import { Button } from "../ui/button";
import { Tooltip } from "../ui/tooltip";
import type { RecentEvent } from "../../lib/sse";
import type { ThemeMode } from "../../lib/types";

function ThemeButton() {
  const themeMode = useUI((s) => s.themeMode);
  const cycleTheme = useUI((s) => s.cycleTheme);
  const Icon = themeMode === "light" ? Sun : themeMode === "dark" ? Moon : Monitor;
  const labels: Record<ThemeMode, string> = {
    light: "Theme: Light — switch to Dark",
    dark: "Theme: Dark — switch to System",
    system: "Theme: System — switch to Light",
  };
  return (
    <Tooltip label={labels[themeMode]}>
      <Button variant="ghost" size="icon" onClick={cycleTheme} aria-label={labels[themeMode]}>
        <Icon size={17} aria-hidden />
      </Button>
    </Tooltip>
  );
}

function HealthIndicator() {
  const { data, isError, isLoading, refetch } = useSystemHealth();
  const ok = !isError && data?.ok === true && data?.chrome_ok === true;
  const label = isLoading
    ? "Checking system health…"
    : isError
      ? "Backend unreachable — click to retry"
      : ok
        ? `Healthy · ${data.count} profiles · Chrome OK`
        : "Degraded — Chrome or sandbox probe failed. Click to retry.";
  return (
    <Tooltip label={label}>
      <button
        onClick={() => refetch()}
        aria-label={label}
        className={cn(
          "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[13px] text-ink-2",
          "transition-colors duration-150 hover:bg-surface-3 hover:text-ink-1",
        )}
      >
        <span className="relative flex h-2 w-2">
          {ok && (
            <span aria-hidden className="absolute inline-flex h-full w-full animate-ping rounded-full bg-ok opacity-60" />
          )}
          <span
            aria-hidden
            className={cn(
              "relative inline-flex h-2 w-2 rounded-full",
              isLoading ? "bg-warn" : isError ? "bg-err" : ok ? "bg-ok" : "bg-warn",
            )}
          />
        </span>
        <span className="hidden tnum xl:inline">
          {isLoading ? "…" : isError ? "Offline" : ok ? "Healthy" : "Degraded"}
        </span>
      </button>
    </Tooltip>
  );
}

function Notifications({ events }: { events: RecentEvent[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open ]);

  const unread = events.length;

  return (
    <div ref={ref} className="relative">
      <Tooltip label={unread ? `${unread} recent events` : "No recent events"}>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setOpen((v) => !v)}
          aria-label="Recent events"
          aria-expanded={open}
          aria-haspopup="true"
        >
          <Bell size={17} aria-hidden />
          {unread > 0 && (
            <span
              aria-hidden
              className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-brand-500 ring-2 ring-surface-1"
            />
          )}
        </Button>
      </Tooltip>
      {open && (
        <div
          role="menu"
          aria-label="Recent events"
          className="absolute right-0 top-full z-50 mt-2 w-80 overflow-hidden rounded-xl border border-border-subtle bg-surface-1 shadow-3"
        >
          <p className="border-b border-border-subtle px-4 py-2.5 text-xs font-semibold uppercase tracking-wider text-ink-3">
            Live events
          </p>
          {events.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-ink-3">
              No events yet. Profile and job activity will appear here.
            </p>
          ) : (
            <ul className="max-h-72 overflow-y-auto py-1">
              {events.slice(0, 12).map((e) => (
                <li
                  key={e.id}
                  className="px-4 py-2 text-[13px] leading-snug text-ink-2"
                >
                  <span className="tnum mr-2 text-ink-3">
                    {new Date(e.at).toLocaleTimeString()}
                  </span>
                  {e.summary}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export function Topbar({ events }: { events: RecentEvent[] }) {
  const setPaletteOpen = useUI((s) => s.setPaletteOpen);
  const openDialog = useUI((s) => s.openDialog);

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border-subtle bg-surface-1 px-4">
      {/* Command palette trigger — the global search */}
      <button
        onClick={() => setPaletteOpen(true)}
        aria-label="Open command palette (Ctrl+K)"
        className={cn(
          "flex h-9 w-full max-w-md items-center gap-2.5 rounded-md border border-border-subtle",
          "bg-surface-0 px-3 text-sm text-ink-3",
          "transition-colors duration-150 hover:border-border-strong hover:text-ink-2",
        )}
      >
        <Search size={15} aria-hidden className="shrink-0" />
        <span className="flex-1 truncate text-left">Search profiles, commands…</span>
        <kbd className="tnum hidden items-center gap-0.5 rounded border border-border-subtle bg-surface-1 px-1.5 py-0.5 text-[11px] text-ink-3 sm:inline-flex">
          <Command size={11} aria-hidden />K
        </kbd>
      </button>

      <div className="ml-auto flex items-center gap-1">
        <HealthIndicator />
        <span aria-hidden className="mx-1 h-5 w-px bg-border-subtle" />
        <ThemeButton />
        <Notifications events={events} />
        <span aria-hidden className="mx-1 h-5 w-px bg-border-subtle" />
        <Button
          variant="primary"
          size="sm"
          onClick={() => {
            navigate({ name: "profiles" });
            openDialog("newProfile");
          }}
          aria-label="New profile (N)"
        >
          <Plus size={15} aria-hidden />
          <span className="hidden md:inline">New Profile</span>
        </Button>
      </div>
    </header>
  );
}
