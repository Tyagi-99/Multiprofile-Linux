import { useState } from "react";
import { toast } from "sonner";
import { Download, Upload } from "lucide-react";
import { apiMessage, get, post } from "../../../lib/api";
import { downloadJson } from "../../../lib/mutations";
import { useProfileCookies } from "../../../lib/queries";
import type { Profile } from "../../../lib/types";
import { Button } from "../../ui/button";
import { Card, CardContent } from "../../ui/card";
import { Dialog } from "../../ui/dialog";
import { EmptyState } from "../../ui/empty-state";
import { Skeleton } from "../../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../../ui/table";
import { Mono } from "../kv";

export function CookiesTab({ profile }: { profile: Profile }) {
  const { data: cookies, isLoading, isError, refetch } = useProfileCookies(profile.id);
  const [importOpen, setImportOpen] = useState(false);
  const [json, setJson] = useState("");
  const [importing, setImporting] = useState(false);

  const doExport = async () => {
    try {
      const data = await get(`/api/profiles/${encodeURIComponent(profile.id)}/cookies/export`);
      downloadJson(`${profile.id}-cookies.json`, data);
    } catch (e) {
      toast.error("Export failed", { description: apiMessage(e) });
    }
  };

  const doImport = async () => {
    let arr: unknown;
    try {
      arr = JSON.parse(json);
    } catch {
      toast.error("Invalid JSON");
      return;
    }
    setImporting(true);
    try {
      await post(`/api/profiles/${encodeURIComponent(profile.id)}/cookies/import`, { cookies: arr });
      toast.success("Cookies imported");
      setImportOpen(false);
      setJson("");
      refetch();
    } catch (e) {
      toast.error("Import failed", { description: apiMessage(e) });
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="grid gap-4">
      <div className="flex items-center gap-2">
        <span className="flex-1" />
        <Button variant="secondary" size="sm" onClick={doExport}>
          <Download size={14} aria-hidden /> Export
        </Button>
        <Button variant="secondary" size="sm" onClick={() => setImportOpen(true)}>
          <Upload size={14} aria-hidden /> Import
        </Button>
      </div>
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
              title="Could not load cookies"
              description="GET /api/profiles/:id/cookies failed."
              action={{ label: "Retry", onClick: () => refetch() }}
            />
          ) : !cookies?.length ? (
            <EmptyState
              title="No cookies"
              description="This profile has no stored cookies, or the endpoint returned none."
            />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Value</TH>
                  <TH>Domain</TH>
                  <TH>Path</TH>
                  <TH>Expires</TH>
                  <TH>Secure</TH>
                  <TH>HttpOnly</TH>
                </TR>
              </THead>
              <TBody>
                {cookies.map((c, i) => {
                  const v = String(c.value ?? "");
                  return (
                    <TR key={`${c.name}-${c.domain}-${i}`}>
                      <TD>
                        <Mono>{c.name ?? ""}</Mono>
                      </TD>
                      <TD>
                        <Mono>
                          <span className="text-ink-3">
                            {v.slice(0, 40)}
                            {v.length > 40 ? "…" : ""}
                          </span>
                        </Mono>
                      </TD>
                      <TD>
                        <Mono>{c.domain ?? ""}</Mono>
                      </TD>
                      <TD>
                        <Mono>{c.path ?? ""}</Mono>
                      </TD>
                      <TD className="text-ink-3">{String(c.expires ?? c.expirationDate ?? "")}</TD>
                      <TD>{c.secure ? "yes" : "—"}</TD>
                      <TD>{c.httpOnly || c.httponly ? "yes" : "—"}</TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
          <p className="mt-3 text-xs text-ink-3">
            Source: GET /api/profiles/:id/cookies. Cookie values are truncated in this view.
          </p>
        </CardContent>
      </Card>

      <Dialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title="Import cookies"
        footer={
          <>
            <Button variant="secondary" onClick={() => setImportOpen(false)} disabled={importing}>
              Cancel
            </Button>
            <Button variant="primary" onClick={doImport} loading={importing}>
              Import
            </Button>
          </>
        }
      >
        <label className="grid gap-1.5">
          <span className="text-[13px] font-medium text-ink-2">Paste cookies JSON (array)</span>
          <textarea
            rows={8}
            value={json}
            onChange={(e) => setJson(e.target.value)}
            placeholder='[{"name":"...","value":"...","domain":"..."}]'
            className="rounded-lg border border-border-strong bg-surface-1 px-3 py-2 font-mono text-[13px] text-ink-1 placeholder:text-ink-3 focus:outline-2 focus:outline-brand-500"
          />
        </label>
      </Dialog>
    </div>
  );
}
