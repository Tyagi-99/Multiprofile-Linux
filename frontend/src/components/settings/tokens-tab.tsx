/**
 * API tokens — Settings.
 *
 * The product had one token, in a file, unscoped and unrevocable, so a cron
 * job that starts a profile held the credential that deletes the fleet. This
 * screen is where that stops being true.
 *
 * The one thing it must get right is the moment of creation: the secret
 * exists once, in the response, and nowhere else ever again. So it is shown
 * in a panel the operator has to dismiss deliberately, with a copy button and
 * a warning — not in a toast that disappears while they are reaching for the
 * mouse.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { KeyRound, Plus, Trash2 } from "lucide-react";
import { api, apiMessage, del } from "../../lib/api";
import { absoluteTime, timeAgo } from "../../lib/format";
import { useApiTokens } from "../../lib/queries";
import { usageKeys } from "../../lib/query-keys";
import type { ApiToken } from "../../lib/types";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { Checkbox } from "../ui/checkbox";
import { CopyButton } from "../ui/copy-button";
import { Dialog } from "../ui/dialog";
import { EmptyState } from "../ui/empty-state";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "../ui/table";
import { Mono } from "../profile-detail/kv";

const SCOPE_HELP: Record<string, string> = {
  read: "Look at profiles, usage and settings.",
  launch: "Start and stop profiles. Implies read.",
  write: "Create, edit and delete profiles. Implies read.",
  admin: "Everything, including managing these tokens.",
};

export function TokensTab() {
  const qc = useQueryClient();
  const { data, isLoading, isError, error, refetch } = useApiTokens();
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["read"]);
  const [minted, setMinted] = useState<{ name: string; token: string } | null>(null);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => qc.invalidateQueries({ queryKey: usageKeys.tokens });

  const create = async () => {
    setBusy(true);
    try {
      const res = await api<{ token: { name: string; token: string } }>("/api/tokens", {
        method: "POST",
        body: { name, scopes },
      });
      setCreateOpen(false);
      setMinted({ name: res.token.name, token: res.token.token });
      setName("");
      setScopes(["read"]);
      refresh();
    } catch (e) {
      toast.error("Couldn't create the token", { description: apiMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (token: ApiToken) => {
    try {
      await del(`/api/tokens/${encodeURIComponent(token.id)}`);
      toast.success(`Revoked “${token.name}”`, {
        description: "Anything using it stops working immediately.",
      });
      refresh();
    } catch (e) {
      toast.error("Couldn't revoke", { description: apiMessage(e) });
    }
  };

  const toggleScope = (scope: string) =>
    setScopes((current) =>
      current.includes(scope) ? current.filter((s) => s !== scope) : [...current, scope],
    );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div>
          <h2 className="text-[15px] font-semibold text-ink-1">API tokens</h2>
          <p className="text-[13px] text-ink-3">
            For scripts and SDKs reaching this machine from elsewhere. Requests from this
            machine need no token.
          </p>
        </div>
        <span className="flex-1" />
        <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
          <Plus size={14} aria-hidden /> New token
        </Button>
      </div>

      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : isError ? (
        <EmptyState
          title="Couldn't load tokens"
          description={apiMessage(error)}
          action={{ label: "Retry", onClick: () => refetch() }}
        />
      ) : !data?.items.length ? (
        <EmptyState
          icon={<KeyRound size={28} aria-hidden />}
          title="No tokens yet"
          description="Create one per integration, with only the scopes it needs, so a leak costs you one script rather than the fleet."
        />
      ) : (
        <Card>
          <CardContent className="pt-6">
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Scopes</TH>
                  <TH>Created</TH>
                  <TH>Last used</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {data.items.map((token) => (
                  <TR key={token.id}>
                    <TD className="text-ink-1">
                      {token.name}
                      <Mono>
                        <span className="ml-2 text-xs text-ink-3">{token.id}</span>
                      </Mono>
                    </TD>
                    <TD>
                      <span className="flex flex-wrap gap-1">
                        {token.scopes.map((s) => (
                          <Badge key={s} tone={s === "admin" ? "warn" : "idle"}>
                            {s}
                          </Badge>
                        ))}
                      </span>
                    </TD>
                    <TD className="whitespace-nowrap text-ink-3" title={absoluteTime(token.createdAt)}>
                      {timeAgo(token.createdAt)}
                    </TD>
                    <TD className="whitespace-nowrap text-ink-3">
                      {token.lastUsedAt ? (
                        timeAgo(token.lastUsedAt)
                      ) : (
                        // Worth showing: a token nobody can tell is unused
                        // never gets retired.
                        <span className="text-ink-3">never</span>
                      )}
                    </TD>
                    <TD className="w-[1%] whitespace-nowrap text-right">
                      <Button variant="danger" size="sm" onClick={() => setRevoking(token)}>
                        <Trash2 size={14} aria-hidden /> Revoke
                      </Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Create */}
      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="New API token"
        description="Give it only the scopes its job needs. A leak then costs you one script, not the fleet."
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={busy || !name.trim()} onClick={create}>
              {busy ? "Creating…" : "Create token"}
            </Button>
          </>
        }
      >
        <div className="grid gap-4">
          <Input
            id="token-name"
            label="Name"
            hint="so a leaked one can be identified"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="nightly launch script"
          />
          <div className="grid gap-2">
            <span className="text-[13px] font-medium text-ink-2">Scopes</span>
            {Object.entries(SCOPE_HELP).map(([scope, help]) => (
              <label key={scope} className="flex cursor-pointer items-start gap-2">
                <Checkbox
                  checked={scopes.includes(scope)}
                  onChange={() => toggleScope(scope)}
                  aria-label={scope}
                />
                <span className="leading-tight">
                  <span className="text-[13px] font-medium text-ink-1">{scope}</span>
                  <span className="block text-xs text-ink-3">{help}</span>
                </span>
              </label>
            ))}
          </div>
        </div>
      </Dialog>

      {/* The one time the secret exists */}
      <Dialog
        open={minted !== null}
        onClose={() => setMinted(null)}
        modal
        title="Copy this token now"
        description="It is shown once and cannot be recovered. If you lose it, revoke it and make another."
        footer={
          <Button variant="primary" onClick={() => setMinted(null)}>
            I&rsquo;ve stored it
          </Button>
        }
      >
        <div className="grid gap-2">
          <p className="text-[13px] text-ink-2">
            Token for <span className="font-medium text-ink-1">{minted?.name}</span>:
          </p>
          <div className="flex items-center gap-2 rounded-lg border border-border-subtle bg-surface-0 p-3">
            <code className="flex-1 break-all font-mono text-[13px] text-ink-1">
              {minted?.token}
            </code>
            <CopyButton text={minted?.token ?? ""} />
          </div>
          <p className="text-xs text-ink-3">
            Send it as <Mono>Authorization: Bearer &lt;token&gt;</Mono>. There is no TLS on this
            API — tunnel it rather than sending it across a network.
          </p>
        </div>
      </Dialog>

      {/* Revoke */}
      <Dialog
        open={revoking !== null}
        onClose={() => setRevoking(null)}
        modal
        title={`Revoke “${revoking?.name}”?`}
        description="Anything using this token stops working immediately. Other tokens are unaffected."
        footer={
          <>
            <Button variant="secondary" onClick={() => setRevoking(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                if (revoking) revoke(revoking);
                setRevoking(null);
              }}
            >
              Revoke
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-ink-2">
          The record is kept, so the audit trail outlives the credential.
        </p>
      </Dialog>
    </div>
  );
}
