/**
 * Your data — Settings.
 *
 * Export and deletion, the two operations a data-subject request needs and
 * the two an operator needs to move to a new machine or hand a laptop back.
 *
 * The deletion side is written to be hard to do by accident and easy to
 * understand: the preview is fetched and shown *before* the confirm box
 * appears, so the numbers on screen are what is about to go, and the operator
 * has to type the confirmation rather than click a red button.
 */

import { useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Download } from "lucide-react";
import { api, apiMessage } from "../../lib/api";
import type { DeletionPreview } from "../../lib/types";
import { Button } from "../ui/button";
import { Card, CardContent } from "../ui/card";
import { Dialog } from "../ui/dialog";
import { Input } from "../ui/input";

const CONFIRMATION = "DELETE EVERYTHING";

export function AccountTab() {
  const [preview, setPreview] = useState<DeletionPreview | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);

  const exportData = () => {
    // A download, not a navigation. The endpoint is Flask's, not a Next.js
    // route, so the router has no business with it; and an anchor with
    // `download` lets the browser stream the file to disk rather than this
    // page holding the whole export in memory first.
    const link = document.createElement("a");
    link.href = "/api/account/export";
    link.download = "multiprofile-export.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    toast.success("Export started", {
      description: "Proxy passwords are not included — the file lists what it left out.",
    });
  };

  const openDelete = async () => {
    try {
      const body = await api<DeletionPreview>("/api/account/deletion-preview");
      setPreview(body);
      setTyped("");
    } catch (e) {
      toast.error("Couldn't check what would be deleted", { description: apiMessage(e) });
    }
  };

  const doDelete = async () => {
    setBusy(true);
    try {
      await api("/api/account/delete", { method: "POST", body: { confirm: CONFIRMATION } });
      setPreview(null);
      toast.success("Everything was deleted", {
        description: "This is now an empty installation.",
      });
      // Nothing on screen is true any more.
      setTimeout(() => window.location.reload(), 1200);
    } catch (e) {
      toast.error("Deletion failed", { description: apiMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="pt-6">
          <h2 className="text-[15px] font-semibold text-ink-1">Export your data</h2>
          <p className="mt-1 max-w-2xl text-[13px] text-ink-3">
            One file with your profiles, workspaces, templates, usage history, token names, the
            audit log and your cookie jars. Use it to move to another machine, or to keep a copy
            that outlives this disk.
          </p>
          <p className="mt-2 max-w-2xl text-[13px] text-ink-3">
            Proxy passwords are <span className="font-medium text-ink-2">not</span> included, and
            the file names the fields it left out so you find the gap now rather than during a
            restore. Keep the file somewhere private: it carries cookies, which are live logins.
          </p>
          <Button className="mt-3" variant="secondary" size="sm" onClick={exportData}>
            <Download size={14} aria-hidden /> Download export
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <h2 className="flex items-center gap-2 text-[15px] font-semibold text-ink-1">
            <AlertTriangle size={16} className="text-err" aria-hidden />
            Delete everything
          </h2>
          <p className="mt-1 max-w-2xl text-[13px] text-ink-3">
            Removes every profile, the browser directories with their cookies and logins, the
            trash, all usage history, every API token and the audit log. There is no undo and no
            backup taken on your behalf — export first if you want one.
          </p>
          <Button className="mt-3" variant="danger" size="sm" onClick={openDelete}>
            Delete all my data…
          </Button>
        </CardContent>
      </Card>

      <Dialog
        open={preview !== null}
        onClose={() => setPreview(null)}
        modal
        title="Delete everything?"
        description="This cannot be undone. Here is exactly what would go."
        footer={
          <>
            <Button variant="secondary" onClick={() => setPreview(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={busy || typed !== CONFIRMATION}
              onClick={doDelete}
            >
              {busy ? "Deleting…" : "Delete everything"}
            </Button>
          </>
        }
      >
        <div className="grid gap-3">
          <ul className="grid gap-1 rounded-lg border border-border-subtle bg-surface-0 p-3 text-[13px] text-ink-2">
            <li>{preview?.profiles ?? "—"} profiles</li>
            <li>{preview?.trashed ?? 0} in the trash</li>
            <li>
              {preview?.browserDirectories ?? 0} browser directories — the cookies and logins
            </li>
            <li>{preview?.usageSessions ?? 0} usage records</li>
            <li>{preview?.apiTokens ?? 0} API tokens</li>
          </ul>
          <Input
            id="delete-confirm"
            label={`Type ${CONFIRMATION} to confirm`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={CONFIRMATION}
            autoComplete="off"
            spellCheck={false}
          />
          <p className="text-xs text-ink-3">
            The confirmation is typed rather than clicked because there is nothing behind this
            one.
          </p>
        </div>
      </Dialog>
    </div>
  );
}
