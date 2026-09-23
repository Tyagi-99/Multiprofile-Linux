import { useState } from "react";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { setApiToken } from "../../lib/api";
import { useUI } from "../../stores/ui";

/**
 * Token gate. Shown when the backend answers 401 (remote access with a token
 * configured, or no token set). The operator pastes the API token — the same
 * flow as the previous console. Never shown on localhost desktops.
 */
export function TokenDialog() {
  const dialog = useUI((s) => s.dialog);
  const closeDialog = useUI((s) => s.closeDialog);
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    const t = token.trim();
    if (!t) {
      setError("Paste a token first.");
      return;
    }
    setApiToken(t);
    setToken("");
    setError(null);
    closeDialog();
    window.location.reload();
  };

  return (
    <Dialog
      open={dialog === "token"}
      onClose={closeDialog}
      title="API token required"
      description="This server requires a bearer token for remote access. Paste the token configured as MULTIPROFILE_API_TOKEN on the host."
      footer={
        <>
          <Button variant="secondary" onClick={closeDialog}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save}>
            Save token
          </Button>
        </>
      }
    >
      <Input
        id="token-input"
        label="API token"
        type="password"
        autoComplete="off"
        placeholder="Paste token…"
        value={token}
        autoFocus
        onChange={(e) => setToken(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") save();
        }}
        error={error ?? undefined}
      />
      <p className="mt-3 text-xs leading-relaxed text-ink-3">
        The token is stored only in this browser&apos;s local storage and sent as an
        Authorization header. It is never written to logs.
      </p>
    </Dialog>
  );
}
