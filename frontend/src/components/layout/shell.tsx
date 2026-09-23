import { useCallback, useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { useHashRoute, type Route } from "../../lib/router";
import { DevicesPage } from "../devices/devices-page";
import { AuditPageView } from "../audit/audit-page";
import { FingerprintsPage } from "../fingerprint/fingerprints-page";
import { JobsPage } from "../jobs/jobs-page";
import { SettingsPage } from "../settings/settings-page";
import { NetworkPage } from "../network/network-page";
import { Overview } from "../dashboard/overview";
import { ProfileDetail } from "../profile-detail/profile-detail";
import { ProfilesPage } from "../profiles/profiles-page";
import { Suspense, lazy } from "react";
import { useSSE, type RecentEvent } from "../../lib/sse";
import { useGlobalShortcuts } from "../../lib/shortcuts";
import { onUnauthorized } from "../../lib/api";
import { useUI } from "../../stores/ui";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { CommandPalette } from "../palette/command-palette";
import { NewProfileDialog } from "../dialogs/new-profile-dialog";
import { ShortcutHelpDialog } from "../dialogs/shortcut-help";
import { TokenDialog } from "../dialogs/token-dialog";

// Code-split: the full wizard only loads when the user opens it.
const WizardDialog = lazy(() =>
  import("../wizard/wizard-dialog").then((m) => ({ default: m.WizardDialog })),
);
import { EmptyState } from "../ui/empty-state";
import { LayoutDashboard } from "lucide-react";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: true,
      // SSE drives invalidation; focus refetch is a cheap safety net.
    },
    mutations: {
      retry: 0,
    },
  },
});

/** Honest placeholder for sections that migrate in later phases. */
function SectionPlaceholder({ title, phase }: { title: string; phase: string }) {
  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="text-xl font-semibold tracking-tight text-ink-1">{title}</h1>
      <EmptyState
        icon={<LayoutDashboard size={28} aria-hidden />}
        title="Migrating to the new console"
        description={`The ${title.toLowerCase()} workspace keeps its full functionality and moves here in ${phase}. Nothing has been removed — the current interface still serves this section.`}
      />
    </div>
  );
}

const SECTION_PHASE: Record<Route["name"], { title: string; phase: string }> = {
  overview: { title: "Overview", phase: "Phase 4" },
  profiles: { title: "Profiles", phase: "Phase 5" },
  devices: { title: "Devices", phase: "Phase 7" },
  network: { title: "Network", phase: "Phase 9" },
  fingerprints: { title: "Fingerprints", phase: "Phase 8" },
  automation: { title: "Automation", phase: "a later phase" },
  jobs: { title: "Jobs", phase: "Phase 10" },
  gateways: { title: "Gateways", phase: "Phase 9" },
  extensions: { title: "Extensions", phase: "a later phase" },
  audit: { title: "Audit Log", phase: "Phase 11" },
  settings: { title: "Settings", phase: "Phase 12" },
};

function RoutedView({ route }: { route: Route }) {
  if (route.name === "overview") return <Overview />;
  if (route.name === "profiles") {
    return route.id ? <ProfileDetail key={route.id} id={route.id} /> : <ProfilesPage />;
  }
  if (route.name === "devices") return <DevicesPage />;
  if (route.name === "fingerprints") return <FingerprintsPage />;
  if (route.name === "network") return <NetworkPage />;
  if (route.name === "gateways") return <NetworkPage initialTab="gateways" />;
  if (route.name === "jobs") return <JobsPage />;
  if (route.name === "audit") return <AuditPageView />;
  if (route.name === "settings") return <SettingsPage />;
  if (route.name === "extensions") return <SettingsPage initialTab="extensions" />;
  const meta = SECTION_PHASE[route.name];
  return <SectionPlaceholder title={meta.title} phase={meta.phase} />;
}

function ShellInner() {
  const route = useHashRoute();
  const [events, setEvents] = useState<RecentEvent[]>([]);
  const openDialog = useUI((s) => s.openDialog);

  useGlobalShortcuts();

  const pushEvent = useCallback((e: RecentEvent) => {
    setEvents((prev) => [e, ...prev].slice(0, 20));
  }, []);
  useSSE(pushEvent);

  useEffect(() => onUnauthorized(() => openDialog("token")), [openDialog]);

  return (
    <div className="flex h-screen overflow-hidden bg-surface-0 text-ink-1">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-[100] focus:rounded-md focus:bg-brand focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-brand-on"
      >
        Skip to main content
      </a>
      <Sidebar route={route} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar events={events} />
        <main id="main-content" className="min-h-0 flex-1 overflow-y-auto" aria-label="Main content" tabIndex={-1}>
          <RoutedView route={route} />
        </main>
      </div>
      <CommandPalette />
      <NewProfileDialog />
      <Suspense fallback={null}>
        <WizardDialog />
      </Suspense>
      <ShortcutHelpDialog />
      <TokenDialog />
      <Toaster
        position="bottom-right"
        gap={8}
        toastOptions={{
          style: {
            background: "var(--color-surface-1)",
            border: "1px solid var(--color-border-subtle)",
            color: "var(--color-ink-1)",
          },
        }}
      />
    </div>
  );
}

export function Shell() {
  return (
    <QueryClientProvider client={queryClient}>
      <ShellInner />
    </QueryClientProvider>
  );
}
