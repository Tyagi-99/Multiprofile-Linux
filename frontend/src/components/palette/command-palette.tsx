import { useMemo } from "react";
import { Command } from "cmdk";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowRight,
  Bot,
  Fingerprint,
  Globe,
  LayoutDashboard,
  ListChecks,
  Moon,
  Monitor,
  Puzzle,
  Plus,
  ScrollText,
  Server,
  Settings,
  Smartphone,
  Square,
  Play,
  Grid2x2,
  Activity,
  Sun,
  Users,
  type LucideIcon,
} from "lucide-react";
import { api, apiMessage, get } from "../../lib/api";
import { navigate, type Route } from "../../lib/router";
import { profilesKeys, systemKeys } from "../../lib/query-keys";
import { useUI } from "../../stores/ui";
import { useProfilesSearch, useSystemHealth } from "../../lib/queries";
import { Badge, launchStateTone } from "../ui/badge";
import type { HealthResponse, Profile } from "../../lib/types";

/**
 * Command palette (Ctrl/Cmd+K). Every command performs a REAL action:
 * navigation, a real API call, or a real UI state change. No decorative
 * entries.
 */

interface NavEntry {
  name: Route["name"];
  label: string;
  icon: LucideIcon;
}

const NAV: NavEntry[] = [
  { name: "overview", label: "Open Overview", icon: LayoutDashboard },
  { name: "profiles", label: "Open Profiles", icon: Users },
  { name: "devices", label: "Open Devices", icon: Smartphone },
  { name: "network", label: "Open Network", icon: Globe },
  { name: "fingerprints", label: "Open Fingerprint Inspector", icon: Fingerprint },
  { name: "automation", label: "Open Automation", icon: Bot },
  { name: "jobs", label: "Open Jobs", icon: ListChecks },
  { name: "gateways", label: "Open Gateways", icon: Server },
  { name: "extensions", label: "Open Extensions", icon: Puzzle },
  { name: "audit", label: "Open Audit Log", icon: ScrollText },
  { name: "settings", label: "Open Settings", icon: Settings },
];

function useProfileActions() {
  const queryClient = useQueryClient();
  const launch = useMutation({
    mutationFn: (id: string) => api(`/api/profiles/${id}/launch`, { method: "POST" }),
    onSuccess: (_d, id) => {
      queryClient.invalidateQueries({ queryKey: profilesKeys.all });
      toast.success("Launch requested", { description: `Profile ${id.slice(0, 8)}… is starting.` });
    },
    onError: (e) => toast.error("Launch failed", { description: apiMessage(e) }),
  });
  const stop = useMutation({
    mutationFn: (id: string) => api(`/api/profiles/${id}/stop`, { method: "POST" }),
    onSuccess: (_d, id) => {
      queryClient.invalidateQueries({ queryKey: profilesKeys.all });
      toast.success("Stop requested", { description: `Profile ${id.slice(0, 8)}… is stopping.` });
    },
    onError: (e) => toast.error("Stop failed", { description: apiMessage(e) }),
  });
  return { launch, stop };
}

function ProfileRow({ profile, onSelect }: { profile: Profile; onSelect: () => void }) {
  return (
    <Command.Item
      value={`profile ${profile.name} ${profile.id}`}
      onSelect={onSelect}
      className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-ink-1 aria-selected:bg-surface-3"
    >
      <Users size={15} aria-hidden className="shrink-0 text-ink-3" />
      <span className="min-w-0 flex-1 truncate">{profile.name}</span>
      <Badge tone={launchStateTone(profile.launchState)} dot>
        {profile.launchState ?? "idle"}
      </Badge>
      <ArrowRight size={14} aria-hidden className="shrink-0 text-ink-3" />
    </Command.Item>
  );
}

export function CommandPalette() {
  const open = useUI((s) => s.paletteOpen);
  const setOpen = useUI((s) => s.setPaletteOpen);
  const openDialog = useUI((s) => s.openDialog);
  const cycleTheme = useUI((s) => s.cycleTheme);
  const themeMode = useUI((s) => s.themeMode);
  const { data: profilesData } = useProfilesSearch(100);
  const { refetch: refetchHealth } = useSystemHealth();
  const queryClient = useQueryClient();
  const { launch, stop } = useProfileActions();

  const profiles = useMemo(() => profilesData?.items ?? [], [profilesData]);

  const runAndClose = (fn: () => void | Promise<void>) => () => {
    setOpen(false);
    void fn();
  };

  const go = (name: Route["name"]) =>
    runAndClose(() => navigate({ name }));

  const tileWindows = runAndClose(async () => {
    const t = toast.loading("Tiling windows…");
    try {
      const res = await api<{ ok: boolean; results?: unknown[]; error?: string }>("/api/windows/tile", {
        method: "POST",
        body: {},
      });
      const n = Array.isArray(res.results) ? res.results.length : 0;
      toast.success("Windows tiled", { id: t, description: `${n} window${n === 1 ? "" : "s"} arranged.` });
    } catch (e) {
      toast.error("Tile failed", { id: t, description: apiMessage(e) });
    }
  });

  const healthCheck = runAndClose(async () => {
    const t = toast.loading("Running health check…");
    try {
      const h = await get<HealthResponse>("/api/health");
      queryClient.setQueryData(systemKeys.health, h);
      const ok = h.ok && h.chrome_ok;
      if (ok) {
        toast.success("System healthy", {
          id: t,
          description: `${h.count} profiles · Chrome OK · sandbox ${h.sandbox?.ok ? "OK" : "issue"}.`,
        });
      } else {
        toast.warning("Health check found issues", {
          id: t,
          description: !h.chrome_ok ? "Chrome was not found." : "One or more probes failed.",
        });
      }
    } catch (e) {
      toast.error("Health check failed", { id: t, description: apiMessage(e) });
    }
  });

  const ThemeIcon = themeMode === "light" ? Moon : themeMode === "dark" ? Monitor : Sun;

  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command palette"
      className="fixed left-1/2 top-[18vh] z-[70] w-full max-w-xl -translate-x-1/2 overflow-hidden rounded-xl border border-border-subtle bg-surface-1 shadow-3"
      overlayClassName="fixed inset-0 z-[60] bg-black/45 dark:bg-black/60"
    >
      <div className="flex items-center gap-2 border-b border-border-subtle px-4">
        <Command.Input
          placeholder="Type a command or search profiles…"
          className="h-12 w-full bg-transparent text-[15px] text-ink-1 placeholder:text-ink-3 focus:outline-none"
        />
        <kbd className="tnum shrink-0 rounded border border-border-subtle bg-surface-0 px-1.5 py-0.5 text-[11px] text-ink-3">
          esc
        </kbd>
      </div>
      <Command.List className="max-h-[46vh] overflow-y-auto p-2">
        <Command.Empty className="px-3 py-8 text-center text-sm text-ink-3">
          No matching commands or profiles.
        </Command.Empty>

        {profiles.length > 0 && (
          <Command.Group
            heading="Profiles"
            className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-2 [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-ink-3"
          >
            {profiles.slice(0, 25).map((p) => (
              <ProfileRow
                key={p.id}
                profile={p}
                onSelect={runAndClose(() => navigate({ name: "profiles", id: p.id }))}
              />
            ))}
          </Command.Group>
        )}

        <Command.Group
          heading="Profile actions"
          className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-2 [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-ink-3"
        >
          <Command.Item
            value="new profile create"
            onSelect={runAndClose(() => openDialog("newProfile"))}
            className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-ink-1 aria-selected:bg-surface-3"
          >
            <Plus size={15} aria-hidden className="text-ink-3" />
            New profile…
          </Command.Item>
          {profiles
            .filter((p) => p.launchState === "running")
            .slice(0, 15)
            .map((p) => (
              <Command.Item
                key={`stop-${p.id}`}
                value={`stop ${p.name} ${p.id}`}
                onSelect={runAndClose(() => stop.mutate(p.id))}
                className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-ink-1 aria-selected:bg-surface-3"
              >
                <Square size={15} aria-hidden className="text-ink-3" />
                Stop {p.name}
              </Command.Item>
            ))}
          {profiles
            .filter((p) => p.launchState !== "running")
            .slice(0, 15)
            .map((p) => (
              <Command.Item
                key={`launch-${p.id}`}
                value={`launch ${p.name} ${p.id}`}
                onSelect={runAndClose(() => launch.mutate(p.id))}
                className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-ink-1 aria-selected:bg-surface-3"
              >
                <Play size={15} aria-hidden className="text-ink-3" />
                Launch {p.name}
              </Command.Item>
            ))}
        </Command.Group>

        <Command.Group
          heading="System"
          className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-2 [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-ink-3"
        >
          <Command.Item
            value="run health check"
            onSelect={healthCheck}
            className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-ink-1 aria-selected:bg-surface-3"
          >
            <Activity size={15} aria-hidden className="text-ink-3" />
            Run health check
          </Command.Item>
          <Command.Item
            value="tile windows"
            onSelect={tileWindows}
            className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-ink-1 aria-selected:bg-surface-3"
          >
            <Grid2x2 size={15} aria-hidden className="text-ink-3" />
            Tile windows
          </Command.Item>
          <Command.Item
            value="toggle theme"
            onSelect={runAndClose(() => cycleTheme())}
            className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-ink-1 aria-selected:bg-surface-3"
          >
            <ThemeIcon size={15} aria-hidden className="text-ink-3" />
            Toggle theme
            <span className="tnum ml-auto text-xs text-ink-3">{themeMode}</span>
          </Command.Item>
        </Command.Group>

        <Command.Group
          heading="Go to"
          className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-2 [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-ink-3"
        >
          {NAV.map((n) => (
            <Command.Item
              key={n.name}
              value={n.label}
              onSelect={go(n.name)}
              className="flex cursor-pointer items-center gap-2.5 rounded-md px-3 py-2 text-sm text-ink-1 aria-selected:bg-surface-3"
            >
              <n.icon size={15} aria-hidden className="text-ink-3" />
              {n.label}
            </Command.Item>
          ))}
        </Command.Group>
      </Command.List>
      <div className="flex items-center gap-4 border-t border-border-subtle px-4 py-2 text-xs text-ink-3">
        <span><kbd className="tnum rounded border border-border-subtle px-1">↑↓</kbd> navigate</span>
        <span><kbd className="tnum rounded border border-border-subtle px-1">↵</kbd> select</span>
        <span><kbd className="tnum rounded border border-border-subtle px-1">esc</kbd> close</span>
      </div>
    </Command.Dialog>
  );
}
