/**
 * Fill the fleet — the two ways to make more than one profile at a time.
 *
 * Both already existed as endpoints and neither had a screen, so the only way
 * to create a fleet from the console was to click "New profile" once per
 * profile. The two modes are the two ways an operator actually arrives:
 * with a template they want N copies of, or with a block of proxies from a
 * provider and one profile wanted per line.
 *
 * The fleet's remaining room is shown before a number is chosen, not after it
 * is submitted, because the server refuses a batch that will not fit rather
 * than quietly making fewer (see F35). A paste that overruns is reported
 * line by line, so the operator can keep the part that worked.
 */

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Layers, Upload } from "lucide-react";
import { api, apiMessage } from "../../lib/api";
import { useFleetCapacity } from "../../lib/queries";
import { profilesKeys } from "../../lib/query-keys";
import { useUI } from "../../stores/ui";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { Input, Select, Textarea } from "../ui/input";

type Mode = "template" | "proxies";

interface Template {
  id: string;
  name: string;
}

interface ImportRow {
  line: number;
  input?: string;
  error?: string;
  message: string;
}

interface ImportReport {
  created: Array<{ line: number; id: string; name: string; proxy: string }>;
  failed: ImportRow[];
  skipped: Array<{ line: number; reason: string; message: string }>;
  counts: { created: number; failed: number; skipped: number };
}

export function BulkCreateDialog() {
  const dialog = useUI((s) => s.dialog);
  if (dialog !== "bulkCreate") return null;
  return <BulkCreateDialogOpen />;
}

function countLines(text: string): number {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#") && !l.startsWith("//")).length;
}

function BulkCreateDialogOpen() {
  const closeDialog = useUI((s) => s.closeDialog);
  const qc = useQueryClient();

  const [mode, setMode] = useState<Mode>("template");
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [templateId, setTemplateId] = useState("");
  const [count, setCount] = useState("10");
  const [namePrefix, setNamePrefix] = useState("Imported");
  const [proxyType, setProxyType] = useState("http");
  const [paste, setPaste] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: capData } = useFleetCapacity();
  const fleet = capData?.fleet;

  // Templates are only needed by one of the two modes, so they are fetched
  // when that mode is first shown rather than on open. GET /api/templates
  // answers with a bare array, not an envelope.
  useEffect(() => {
    if (mode !== "template" || templates !== null) return;
    let live = true;
    api<Template[] | { templates?: Template[] }>("/api/templates")
      .then((res) => {
        if (!live) return;
        setTemplates(Array.isArray(res) ? res : (res.templates ?? []));
      })
      .catch(() => live && setTemplates([]));
    return () => {
      live = false;
    };
  }, [mode, templates]);

  const wanted = mode === "template" ? Number(count) || 0 : countLines(paste);
  const overCapacity = fleet != null && wanted > fleet.free;

  const switchMode = (next: Mode) => {
    setMode(next);
    setReport(null);
    setError(null);
  };

  const done = (created: number) => {
    qc.invalidateQueries({ queryKey: profilesKeys.all });
    if (created > 0) {
      toast.success(`Created ${created} profile${created === 1 ? "" : "s"}`);
    }
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      if (mode === "template") {
        if (!templateId) throw new Error("Choose a template first.");
        const res = await api<{ count: number }>("/api/profiles/from-template", {
          method: "POST",
          // A number, not the input's string: the server validates the type
          // and answers 400 rather than reinterpreting it.
          body: { templateId, count: Number(count) },
        });
        done(res.count);
        closeDialog();
      } else {
        const res = await api<ImportReport>("/api/profiles/import", {
          method: "POST",
          body: { proxies: paste, namePrefix, proxyType },
        });
        done(res.counts.created);
        setReport(res);
        // The dialog stays open when anything did not import, so the report
        // is readable; a clean run has nothing to say and closes.
        if (res.counts.failed === 0 && res.counts.skipped === 0) closeDialog();
      }
    } catch (e) {
      setError(apiMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={closeDialog}
      wide
      title="Create profiles in bulk"
      description={
        fleet
          ? `${fleet.stored} of ${fleet.capacity} profiles stored — room for ${fleet.free} more.`
          : undefined
      }
      footer={
        <>
          <Button variant="secondary" onClick={closeDialog}>
            {report ? "Close" : "Cancel"}
          </Button>
          <Button
            variant="primary"
            disabled={busy || wanted === 0 || overCapacity}
            onClick={submit}
          >
            {busy
              ? "Creating…"
              : wanted > 0
                ? `Create ${wanted} profile${wanted === 1 ? "" : "s"}`
                : "Create"}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <div className="flex gap-2">
          {/* Switching modes clears the last report: it describes the other
              mode's input, and leaving it under a different form reads as a
              result of what is on screen now. */}
          <Button
            variant={mode === "template" ? "primary" : "secondary"}
            size="sm"
            onClick={() => switchMode("template")}
          >
            <Layers size={14} aria-hidden /> From a template
          </Button>
          <Button
            variant={mode === "proxies" ? "primary" : "secondary"}
            size="sm"
            onClick={() => switchMode("proxies")}
          >
            <Upload size={14} aria-hidden /> From a proxy list
          </Button>
        </div>

        {mode === "template" ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Select
                id="bulk-template"
                label="Template"
                value={templateId}
                onChange={(e) => setTemplateId(e.target.value)}
                options={[
                  { value: "", label: "Choose a template…" },
                  ...(templates ?? []).map((t) => ({ value: t.id, label: t.name })),
                ]}
              />
              {templates?.length === 0 && (
                <p className="mt-1 text-xs text-ink-3">
                  No templates yet. Save one from a profile first.
                </p>
              )}
            </div>
            <Input
              id="bulk-count"
              label="How many"
              type="number"
              min={1}
              max={fleet?.free ?? 150}
              value={count}
              onChange={(e) => setCount(e.target.value)}
            />
          </div>
        ) : (
          <div className="grid gap-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Input
                id="bulk-prefix"
                label="Name prefix"
                value={namePrefix}
                onChange={(e) => setNamePrefix(e.target.value)}
                placeholder="Imported"
              />
              {/* A list from one provider is all one kind, and without this
                  every bare host:port line silently became HTTP. */}
              <Select
                id="bulk-proxy-type"
                label="Proxy type"
                hint="for lines with no scheme"
                value={proxyType}
                onChange={(e) => setProxyType(e.target.value)}
                options={[
                  { value: "http", label: "HTTP" },
                  { value: "https", label: "HTTPS" },
                  { value: "socks5", label: "SOCKS5" },
                  { value: "socks5h", label: "SOCKS5h" },
                  { value: "socks4", label: "SOCKS4" },
                ]}
              />
            </div>
            <Textarea
              id="bulk-proxies"
              label="One proxy per line"
              value={paste}
              onChange={(e) => setPaste(e.target.value)}
              rows={8}
              spellCheck={false}
              placeholder={"1.2.3.4:8080\n1.2.3.4:8080:user:pass\nuser:pass@host:8080\nsocks5://host:1080"}
              className="font-mono"
            />
          </div>
        )}

        {overCapacity && fleet && (
          <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[13px] text-ink-2">
            That is {wanted} profiles, and there is room for {fleet.free}. Delete some, or
            raise the limit with <code className="font-mono text-xs">MAX_PROFILES</code>.
          </p>
        )}

        {error && (
          <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[13px] text-ink-2">
            {error}
          </p>
        )}

        {report && (
          <div className="grid gap-2 rounded-lg border border-border-subtle bg-surface-0 p-3">
            <p className="text-[13px] text-ink-2">
              Created {report.counts.created}
              {report.counts.failed > 0 && `, ${report.counts.failed} failed`}
              {report.counts.skipped > 0 && `, ${report.counts.skipped} skipped`}.
            </p>
            {[...report.failed, ...report.skipped].length > 0 && (
              <ul className="grid max-h-40 gap-1 overflow-auto">
                {[...report.failed, ...report.skipped].map((row, i) => (
                  <li key={i} className="font-mono text-xs text-ink-3">
                    line {row.line}: {row.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Dialog>
  );
}
