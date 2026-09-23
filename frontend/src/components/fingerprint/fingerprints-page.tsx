import { useMemo, useState } from "react";
import { useProfilesSearch } from "../../lib/queries";
import { useUI } from "../../stores/ui";
import { Button } from "../ui/button";
import { EmptyState } from "../ui/empty-state";
import { Select } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { FingerprintInspector } from "./fingerprint-inspector";

export function FingerprintsPage() {
  const { data, isLoading, isError } = useProfilesSearch(500);
  const openDialog = useUI((s) => s.openDialog);
  const profiles = useMemo(() => data?.items ?? [], [data]);
  const [selId, setSelId] = useState<string | null>(null);
  const activeId = selId ?? profiles[0]?.id ?? null;

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight text-ink-1">Fingerprint</h1>
        <span className="flex-1" />
        {activeId && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              window.location.hash = `#/profiles/${encodeURIComponent(activeId)}?tab=fingerprint`;
            }}
          >
            Open in profile view
          </Button>
        )}
      </div>

      {isLoading ? (
        <Skeleton className="h-10 w-72" />
      ) : isError || !profiles.length ? (
        <EmptyState
          title="No profiles"
          description="Create a profile to inspect its fingerprint."
          action={{ label: "New profile", onClick: () => openDialog("newProfile") }}
        />
      ) : (
        <>
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-ink-2">Profile</span>
            <Select
              aria-label="Select profile"
              className="w-72"
              value={activeId ?? ""}
              onChange={(e) => setSelId(e.target.value)}
              options={profiles.map((p) => ({ value: p.id, label: p.name || p.id }))}
            />
          </div>
          {activeId && <FingerprintInspector key={activeId} profileId={activeId} />}
        </>
      )}
    </div>
  );
}
