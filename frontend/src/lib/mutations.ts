import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiMessage, del, post, put } from "./api";
import { profilesKeys } from "./query-keys";
import type { Profile } from "./types";

/**
 * Profile lifecycle mutations. All go through the single api() path.
 * SSE events (profile.status) also invalidate these queries, so the UI
 * converges even if a mutation's own invalidation races the event.
 */

function invalidateProfiles(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: profilesKeys.lists() });
  qc.invalidateQueries({ queryKey: profilesKeys.details() });
}

export interface BlockedLaunch {
  id: string;
  name: string;
  warnings: string[];
}

export function useProfileActions() {
  const qc = useQueryClient();

  const launchOne = async (p: Profile, force = false) => {
    await post(`/api/profiles/${encodeURIComponent(p.id)}/launch`, undefined, force ? { force: 1 } : undefined);
  };

  /** Launch many; returns profiles blocked by consistency checks (409). */
  const launch = async (profiles: Profile[]): Promise<BlockedLaunch[]> => {
    const blocked: BlockedLaunch[] = [];
    let ok = 0;
    for (const p of profiles) {
      try {
        await launchOne(p);
        ok++;
      } catch (e: unknown) {
        const status = (e as { status?: number })?.status;
        const data = (e as { data?: { warnings?: string[] } })?.data;
        if (status === 409) {
          blocked.push({ id: p.id, name: p.name, warnings: data?.warnings ?? [] });
        } else {
          toast.error("Launch failed", { description: `${p.name}: ${apiMessage(e)}` });
        }
      }
    }
    if (ok > 0) toast.success(`Launched ${ok} profile${ok === 1 ? "" : "s"}`);
    invalidateProfiles(qc);
    return blocked;
  };

  const stop = async (profiles: Profile[]) => {
    let ok = 0;
    for (const p of profiles) {
      try {
        await post(`/api/profiles/${encodeURIComponent(p.id)}/stop`);
        ok++;
      } catch (e) {
        toast.error("Stop failed", { description: `${p.name}: ${apiMessage(e)}` });
      }
    }
    if (ok > 0) toast.success(`Stopped ${ok} profile${ok === 1 ? "" : "s"}`);
    invalidateProfiles(qc);
  };

  const restart = async (p: Profile) => {
    const t = toast.loading(`Restarting ${p.name}…`);
    try {
      await post(`/api/profiles/${encodeURIComponent(p.id)}/stop`);
    } catch (e) {
      toast.error("Stop failed", { description: apiMessage(e), id: t });
      return;
    }
    await new Promise((r) => setTimeout(r, 1500));
    try {
      await launchOne(p);
      toast.success("Relaunched", { description: p.name, id: t });
    } catch (e) {
      const status = (e as { status?: number })?.status;
      toast.error(status === 409 ? "Relaunch blocked by consistency checks" : "Relaunch failed", {
        description: apiMessage(e),
        id: t,
      });
    }
    invalidateProfiles(qc);
  };

  const park = async (profiles: Profile[], doPark: boolean) => {
    let ok = 0;
    for (const p of profiles) {
      try {
        if (doPark) await post(`/api/profiles/${encodeURIComponent(p.id)}/stop`);
        await put(`/api/profiles/${encodeURIComponent(p.id)}`, { status: doPark ? "parked" : "idle" });
        ok++;
      } catch (e) {
        toast.error(doPark ? "Park failed" : "Unpark failed", {
          description: `${p.name}: ${apiMessage(e)}`,
        });
      }
    }
    if (ok > 0) toast.success(doPark ? `Parked ${ok}` : `Unparked ${ok}`);
    invalidateProfiles(qc);
  };

  const remove = async (profiles: Profile[]) => {
    let ok = 0;
    for (const p of profiles) {
      try {
        await del(`/api/profiles/${encodeURIComponent(p.id)}`);
        ok++;
      } catch (e) {
        toast.error("Delete failed", { description: `${p.name}: ${apiMessage(e)}` });
      }
    }
    if (ok > 0) toast.success(`Deleted ${ok} profile${ok === 1 ? "" : "s"}`);
    invalidateProfiles(qc);
  };

  return { launch, launchOne, stop, restart, park, remove };
}

/** Strip credential-bearing keys before export. */
export function sanitizeForExport(p: Profile): Record<string, unknown> {
  const c: Record<string, unknown> = { ...p };
  for (const k of ["proxyPass", "proxy_pass", "proxyPassword", "proxy_password"]) delete c[k];
  return c;
}

export function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
