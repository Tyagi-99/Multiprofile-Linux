import {
  Bot,
  ChevronLeft,
  Fingerprint,
  Globe,
  LayoutDashboard,
  Puzzle,
  ScrollText,
  Trash2,
  Server,
  Settings,
  Smartphone,
  Users,
  ListChecks,
  type LucideIcon,
} from "lucide-react";
import { cn } from "../../lib/cn";
import { isRouteActive, routeToHash, type Route } from "../../lib/router";
import { useUI } from "../../stores/ui";
import { Tooltip } from "../ui/tooltip";
import { useActiveJobs, useSystemHealth } from "../../lib/queries";

interface NavItem {
  name: Route["name"];
  label: string;
  icon: LucideIcon;
  badge?: number;
}

const GROUPS: Array<{ title: string; items: NavItem[] }> = [
  {
    title: "Workspace",
    items: [
      { name: "overview", label: "Overview", icon: LayoutDashboard },
      { name: "profiles", label: "Profiles", icon: Users },
      { name: "devices", label: "Devices", icon: Smartphone },
      { name: "network", label: "Network", icon: Globe },
    ],
  },
  {
    title: "Operations",
    items: [
      { name: "fingerprints", label: "Fingerprints", icon: Fingerprint },
      { name: "automation", label: "Automation", icon: Bot },
      { name: "jobs", label: "Jobs", icon: ListChecks },
    ],
  },
  {
    title: "System",
    items: [
      { name: "gateways", label: "Gateways", icon: Server },
      { name: "extensions", label: "Extensions", icon: Puzzle },
      { name: "trash", label: "Trash", icon: Trash2 },
      { name: "audit", label: "Audit", icon: ScrollText },
      { name: "settings", label: "Settings", icon: Settings },
    ],
  },
];

function Brand({ collapsed }: { collapsed: boolean }) {
  return (
    <a
      href="#/overview"
      className={cn(
        "flex items-center gap-3 rounded-lg px-3 py-2",
        collapsed && "justify-center px-0",
      )}
      aria-label="Multiprofile — Overview"
    >
      <span
        aria-hidden
        className="grid h-8 w-8 shrink-0 grid-cols-2 gap-[3px] rounded-[7px] bg-surface-0 p-[7px]"
      >
        <i className="rounded-[2px] bg-brand-500" />
        <i className="rounded-[2px] bg-brand-500" />
        <i className="rounded-[2px] bg-brand-500" />
        <i className="rounded-[2px] bg-brand-500" />
      </span>
      {!collapsed && (
        <span className="min-w-0 leading-tight">
          <span className="block truncate text-[15px] font-semibold tracking-tight text-ink-1">
            Multiprofile
          </span>
          <span className="block text-xs text-ink-3">Linux Console</span>
        </span>
      )}
    </a>
  );
}

function NavLink({
  item,
  active,
  collapsed,
}: {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
}) {
  const Icon = item.icon;
  const link = (
    <a
      href={routeToHash({ name: item.name })}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative flex items-center gap-3 rounded-md px-3 py-[7px] text-sm",
        "transition-colors duration-150 ease-out",
        collapsed && "justify-center px-0",
        active
          ? "bg-brand-50 font-medium text-brand-700 dark:bg-brand-50 dark:text-brand-200"
          : "text-ink-2 hover:bg-surface-3 hover:text-ink-1",
      )}
    >
      {active && (
        <span
          aria-hidden
          className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-brand-500"
        />
      )}
      <Icon
        size={17}
        strokeWidth={active ? 2.25 : 2}
        aria-hidden
        className="shrink-0"
      />
      {!collapsed && <span className="min-w-0 flex-1 truncate">{item.label}</span>}
      {!collapsed && item.badge != null && item.badge > 0 && (
        <span className="tnum rounded-full bg-surface-3 px-1.5 py-0.5 text-[11px] font-medium text-ink-2">
          {item.badge}
        </span>
      )}
      {collapsed && item.badge != null && item.badge > 0 && (
        <span
          aria-hidden
          className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-brand-500"
        />
      )}
    </a>
  );
  if (collapsed) {
    return (
      <Tooltip label={item.label} side="right">
        {link}
      </Tooltip>
    );
  }
  return link;
}

export function Sidebar({ route }: { route: Route }) {
  const collapsed = useUI((s) => s.sidebarCollapsed);
  const toggleSidebar = useUI((s) => s.toggleSidebar);
  const health = useSystemHealth();
  const jobs = useActiveJobs();

  const activeJobs =
    jobs.data?.items.filter((j) => j.status === "queued" || j.status === "running").length ?? 0;

  const groups = GROUPS.map((g) => ({
    ...g,
    items: g.items.map((i) =>
      i.name === "jobs" && activeJobs > 0 ? { ...i, badge: activeJobs } : i,
    ),
  }));

  const healthOk = health.data?.ok === true && health.data?.chrome_ok === true;
  const version = health.data?.version ?? "…";

  return (
    <aside
      aria-label="Primary navigation"
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border-subtle bg-surface-1",
        "transition-[width] duration-200 ease-out",
        collapsed ? "w-[68px]" : "w-60",
      )}
    >
      <div className="px-3 pb-2 pt-4">
        <Brand collapsed={collapsed} />
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-2" aria-label="Sections">
        {groups.map((group) => (
          <div key={group.title}>
            {!collapsed && (
              <p className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider text-ink-3">
                {group.title}
              </p>
            )}
            <ul className="space-y-0.5">
              {group.items.map((item) => (
                <li key={item.name}>
                  <NavLink
                    item={item}
                    active={isRouteActive(route, item.name)}
                    collapsed={collapsed}
                  />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-border-subtle px-3 py-3">
        {!collapsed ? (
          <div className="flex items-center gap-2 px-1 text-xs text-ink-3">
            <span
              aria-hidden
              className={cn(
                "h-2 w-2 rounded-full",
                health.isError ? "bg-err" : healthOk ? "bg-ok" : "bg-warn",
              )}
            />
            <span className="tnum truncate">
              {health.isError ? "Backend unreachable" : `v${version}`}
            </span>
            <button
              onClick={toggleSidebar}
              aria-label="Collapse sidebar"
              className="ml-auto rounded-md p-1.5 text-ink-3 transition-colors hover:bg-surface-3 hover:text-ink-1"
            >
              <ChevronLeft size={15} aria-hidden />
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Tooltip label={health.isError ? "Backend unreachable" : `Healthy · v${version}`} side="right">
              <span
                aria-hidden
                className={cn(
                  "h-2 w-2 rounded-full",
                  health.isError ? "bg-err" : healthOk ? "bg-ok" : "bg-warn",
                )}
              />
            </Tooltip>
            <button
              onClick={toggleSidebar}
              aria-label="Expand sidebar"
              className="rounded-md p-1.5 text-ink-3 transition-colors hover:bg-surface-3 hover:text-ink-1"
            >
              <ChevronLeft size={15} aria-hidden className="rotate-180" />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
