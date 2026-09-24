import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Copy, Pencil, Play, RotateCcw, Square, Trash2 } from "lucide-react";
import { apiMessage, post, put } from "../../lib/api";
import { useProfileActions } from "../../lib/mutations";
import { useProfileDetail } from "../../lib/queries";
import { profilesKeys } from "../../lib/query-keys";
import { getHashQuery, navigateProfileTab } from "../../lib/router";
import type { Profile } from "../../lib/types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { EmptyState } from "../ui/empty-state";
import { Field } from "../ui/field";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "../ui/tabs";
import { AuditTab } from "./tabs/audit-tab";
import { AutomationTab } from "./tabs/automation-tab";
import { CookiesTab } from "./tabs/cookies-tab";
import { DeviceTab } from "./tabs/device-tab";
import { FingerprintInspector } from "../fingerprint/fingerprint-inspector";
import { HealthTab } from "./tabs/health-tab";
import { NetworkTab } from "./tabs/network-tab";
import { NotesTab } from "./tabs/notes-tab";
import { OverviewTab } from "./tabs/overview-tab";
import { RuntimeTab } from "./tabs/runtime-tab";
import { StorageTab } from "./tabs/storage-tab";

const TABS = [
  "overview",
  "network",
  "fingerprint",
  "device",
  "cookies",
  "storage",
  "runtime",
  "health",
  "automation",
  "notes",
  "audit",
] as const;

type TabId = (typeof TABS)[number];

function stateOf(p: Profile): string {
  return String(p.launchState ?? (p as Record<string, unknown>).status ?? "idle").toLowerCase();
}

export function ProfileDetail({ id }: { id: string }) {
  const qc = useQueryClient();
  const actions = useProfileActions();
  const { data: profile, isLoading, isError, refetch } = useProfileDetail(id);

  const [tab, setTab] = useState<TabId>(() => {
    const t = getHashQuery().get("tab");
    return (TABS as readonly string[]).includes(t ?? "") ? (t as TabId) : "overview";
  });
  const [editOpen, setEditOpen] = useState(false);
  const [cloneOpen, setCloneOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  if (isLoading) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (isError || !profile) {
    return (
      <div className="mx-auto max-w-6xl px-6 py-6">
        <EmptyState
          title="Profile not found"
          description="It may have been deleted."
          action={{ label: "Back to profiles", onClick: () => (window.location.hash = "#/profiles") }}
        />
        {isError && (
          <div className="mt-2 text-center">
            <Button variant="ghost" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          </div>
        )}
      </div>
    );
  }

  const state = stateOf(profile);
  const running = state === "running";

  const onTabChange = (t: string) => {
    const next = t as TabId;
    setTab(next);
    navigateProfileTab(id, next);
  };

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <a
          href="#/profiles"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-[13px] text-ink-3 transition-colors hover:bg-surface-3 hover:text-ink-1"
        >
          <ArrowLeft size={14} aria-hidden /> Profiles
        </a>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-semibold tracking-tight text-ink-1">
            {profile.name}
          </h1>
          <p className="font-mono text-xs text-ink-3">{profile.id}</p>
        </div>
        <Badge
          tone={running ? "ok" : state === "error" ? "err" : state === "parked" ? "warn" : "idle"}
          dot
        >
          {state === "idle" ? "Stopped" : state.charAt(0).toUpperCase() + state.slice(1)}
        </Badge>
        <div className="flex flex-wrap items-center gap-1.5">
          {running ? (
            <>
              <Button variant="secondary" size="sm" onClick={() => actions.stop([profile])}>
                <Square size={14} aria-hidden /> Stop
              </Button>
              <Button variant="secondary" size="sm" onClick={() => actions.restart(profile)}>
                <RotateCcw size={14} aria-hidden /> Restart
              </Button>
            </>
          ) : (
            <Button variant="secondary" size="sm" onClick={() => actions.launch([profile])}>
              <Play size={14} aria-hidden /> Launch
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={() => setEditOpen(true)}>
            <Pencil size={14} aria-hidden /> Edit
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setCloneOpen(true)}>
            <Copy size={14} aria-hidden /> Clone
          </Button>
          <Button variant="danger" size="sm" onClick={() => setDeleteOpen(true)}>
            <Trash2 size={14} aria-hidden /> Delete
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={tab} onValueChange={onTabChange}>
        <TabsList aria-label="Profile sections">
          {TABS.map((t) => (
            <TabsTrigger key={t} value={t}>
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Tab body */}
      <div key={tab}>
        {tab === "overview" && <OverviewTab profile={profile} />}
        {tab === "network" && <NetworkTab profile={profile} />}
        {tab === "fingerprint" && <FingerprintInspector profileId={profile.id} />}
        {tab === "device" && <DeviceTab profile={profile} />}
        {tab === "cookies" && <CookiesTab profile={profile} />}
        {tab === "storage" && <StorageTab profile={profile} />}
        {tab === "runtime" && <RuntimeTab profile={profile} />}
        {tab === "health" && <HealthTab profile={profile} />}
        {tab === "notes" && <NotesTab profile={profile} />}
        {tab === "automation" && <AutomationTab profile={profile} />}
        {tab === "audit" && <AuditTab profile={profile} />}
      </div>

      <EditDialog
        open={editOpen}
        onClose={() => setEditOpen(false)}
        profile={profile}
        onSaved={() => {
          qc.invalidateQueries({ queryKey: profilesKeys.detail(id) });
          qc.invalidateQueries({ queryKey: profilesKeys.lists() });
        }}
      />
      <CloneDialog open={cloneOpen} onClose={() => setCloneOpen(false)} profile={profile} />
      <Dialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title={`Delete “${profile.name}”?`}
        description="Browser storage will be purged. This cannot be undone."
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={async () => {
                await actions.remove([profile]);
                window.location.hash = "#/profiles";
              }}
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          Profile <span className="font-medium text-ink-1">{profile.name}</span> and its browser
          storage will be permanently removed.
        </p>
      </Dialog>
    </div>
  );
}

function EditDialog({
  open,
  onClose,
  profile,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  profile: Profile;
  onSaved: () => void;
}) {
  const [name, setName] = useState(profile.name ?? "");
  const [startUrl, setStartUrl] = useState((profile.startUrl as string | undefined) ?? "");
  const [tags, setTags] = useState((profile.tags ?? []).join(", "));
  const [notes, setNotes] = useState((profile.notes as string | undefined) ?? "");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await put(`/api/profiles/${encodeURIComponent(profile.id)}`, {
        name: name.trim(),
        startUrl: startUrl.trim() || null,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        notes: notes.trim() || null,
      });
      toast.success("Profile updated");
      onSaved();
      onClose();
    } catch (e) {
      toast.error("Update failed", { description: apiMessage(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Edit profile — ${profile.name}`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} loading={saving} disabled={!name.trim()}>
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <Field label="Name" >
          {({ id }) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Start URL" >
          {({ id }) => (
            <Input
              id={id}
              type="url"
              value={startUrl}
              onChange={(e) => setStartUrl(e.target.value)}
              placeholder="https://…"
            />
          )}
        </Field>
        <Field label="Tags" hint="Comma separated" >
          {({ id }) => <Input id={id} value={tags} onChange={(e) => setTags(e.target.value)} />}
        </Field>
        <Field label="Notes" >
          {({ id }) => <Input id={id} value={notes} onChange={(e) => setNotes(e.target.value)} />}
        </Field>
      </div>
    </Dialog>
  );
}

function CloneDialog({
  open,
  onClose,
  profile,
}: {
  open: boolean;
  onClose: () => void;
  profile: Profile;
}) {
  const [name, setName] = useState(`Copy of ${profile.name}`);
  const [cloning, setCloning] = useState(false);

  const clone = async () => {
    setCloning(true);
    try {
      const r = await post<{ id?: string; profileId?: string }>(
        `/api/profiles/${encodeURIComponent(profile.id)}/clone`,
        { name: name.trim() },
      );
      const newId = r.id ?? r.profileId;
      toast.success("Profile cloned", { description: name.trim() });
      onClose();
      if (newId) window.location.hash = `#/profiles/${encodeURIComponent(newId)}`;
    } catch (e) {
      toast.error("Clone failed", { description: apiMessage(e) });
    } finally {
      setCloning(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Clone profile"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={cloning}>
            Cancel
          </Button>
          <Button variant="primary" onClick={clone} loading={cloning} disabled={!name.trim()}>
            Clone
          </Button>
        </>
      }
    >
      <Field label="New name" >
        {({ id }) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />}
      </Field>
    </Dialog>
  );
}
