import { useProfileAudit } from "../../../lib/queries";
import type { Profile } from "../../../lib/types";
import { Badge } from "../../ui/badge";
import { Card, CardContent } from "../../ui/card";
import { EmptyState } from "../../ui/empty-state";
import { Skeleton } from "../../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../../ui/table";
import { Mono } from "../kv";

export function AuditTab({ profile }: { profile: Profile }) {
  const { data: items, isLoading, isError, refetch } = useProfileAudit(profile.id);

  return (
    <Card>
      <CardContent className="pt-6">
        {isLoading ? (
          <div className="grid gap-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : isError ? (
          <EmptyState
            title="Could not load audit entries"
            description="GET /api/audit failed."
            action={{ label: "Retry", onClick: () => refetch() }}
          />
        ) : !items?.length ? (
          <EmptyState
            title="No audit entries"
            description="No recorded actions reference this profile."
          />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Time</TH>
                <TH>Action</TH>
                <TH>Result</TH>
                <TH>Detail</TH>
              </TR>
            </THead>
            <TBody>
              {items.map((e, i) => (
                <TR key={`${e.ts}-${i}`}>
                  <TD className="whitespace-nowrap text-ink-3">
                    {e.ts ? new Date(e.ts).toLocaleString() : "—"}
                  </TD>
                  <TD>
                    <Mono>{e.action}</Mono>
                  </TD>
                  <TD>
                    <Badge tone={e.ok === false ? "err" : "ok"}>
                      {e.ok === false ? "fail" : "ok"}
                    </Badge>
                  </TD>
                  <TD>
                    <Mono>
                      <span className="text-ink-3">
                        {JSON.stringify(e.detail ?? "").slice(0, 120)}
                      </span>
                    </Mono>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
