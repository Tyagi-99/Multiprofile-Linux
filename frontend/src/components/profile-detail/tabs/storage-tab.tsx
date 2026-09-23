import { useProfileHealth } from "../../../lib/queries";
import type { Profile } from "../../../lib/types";
import { Badge } from "../../ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";
import { EmptyState } from "../../ui/empty-state";
import { Skeleton } from "../../ui/skeleton";
import { KV, Mono, fmtVal } from "../kv";

export function StorageTab({ profile }: { profile: Profile }) {
  const { data: h, isLoading, isError, refetch } = useProfileHealth(profile.id);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Storage</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : isError || !h ? (
          <EmptyState
            title="Storage info unavailable"
            description="GET /api/profiles/:id/health did not return data."
            action={{ label: "Retry", onClick: () => refetch() }}
          />
        ) : (
          <>
            <KV
              pairs={[
                [
                  "User data dir",
                  h.user_data_dir ? <Mono key="d">{h.user_data_dir}</Mono> : "—",
                ],
                [
                  "Storage accessible",
                  h.storage_accessible == null ? (
                    "—"
                  ) : h.storage_accessible ? (
                    <Badge key="s" tone="ok">yes</Badge>
                  ) : (
                    <Badge key="s" tone="err">no</Badge>
                  ),
                ],
              ]}
            />
            <p className="mt-3 text-xs text-ink-3">
              The backend does not expose per-profile disk usage; only accessibility and the data
              directory are available.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

