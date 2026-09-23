import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { Input, Select } from "../ui/input";
import { api, apiMessage } from "../../lib/api";
import { profilesKeys } from "../../lib/query-keys";
import { useUI } from "../../stores/ui";
import { useDevices } from "../../lib/queries";
import { navigate } from "../../lib/router";
import type { Profile } from "../../lib/types";

/**
 * Quick-create dialog. A minimal but REAL profile create (the full wizard
 * lands in Phase 13): name is required, device and start URL are optional.
 *
 * The open variant mounts fresh on every open, so a prefilled device id from
 * the device library ("Use in new profile", carried in the dialog payload)
 * is applied via the state initializer — no effects needed.
 */
export function NewProfileDialog() {
  const dialog = useUI((s) => s.dialog);
  if (dialog !== "newProfile") return null;
  return <NewProfileDialogOpen />;
}

function NewProfileDialogOpen() {
  const closeDialog = useUI((s) => s.closeDialog);
  const openDialog = useUI((s) => s.openDialog);
  const payload = useUI((s) => s.dialogPayload) as { deviceId?: string } | null;

  const [name, setName] = useState("");
  const [startUrl, setStartUrl] = useState("");
  const [deviceId, setDeviceId] = useState(payload?.deviceId ?? "");
  const [error, setError] = useState<string | null>(null);

  const { data: devicesData } = useDevices();
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: () =>
      api<{ ok: boolean; profile: Profile }>("/api/profiles", {
        method: "POST",
        body: {
          name: name.trim(),
          ...(startUrl.trim() ? { startUrl: startUrl.trim() } : {}),
          ...(deviceId ? { deviceId } : {}),
        },
      }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: profilesKeys.all });
      toast.success("Profile created", { description: res.profile.name });
      closeDialog();
      navigate({ name: "profiles", id: res.profile.id });
    },
    onError: (e) => setError(apiMessage(e, "Could not create the profile.")),
  });

  const close = () => {
    create.reset();
    closeDialog();
  };

  const deviceOptions = [
    { value: "", label: "No device preset" },
    ...(devicesData?.devices ?? []).map((d) => ({
      value: d.id,
      label: String(d.label ?? d.id),
    })),
  ];

  const canSubmit = name.trim().length > 0 && !create.isPending;

  const submit = () => {
    if (!canSubmit) return;
    setError(null);
    create.mutate();
  };

  return (
    <Dialog
      open
      onClose={close}
      title="New profile"
      description="Creates a real browser profile. For device presets, network, and fingerprint options, use the full setup wizard."
      footer={
        <>
          <Button
            variant="secondary"
            onClick={() =>
              openDialog("wizard", payload?.deviceId ? { deviceId: payload.deviceId } : undefined)
            }
            disabled={create.isPending}
          >
            Full setup ›
          </Button>
          <span className="flex-1" />
          <Button variant="secondary" onClick={close} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" loading={create.isPending} disabled={!canSubmit} onClick={submit}>
            Create profile
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Input
          id="np-name"
          label="Name"
          placeholder="e.g. Client — Shopping"
          value={name}
          maxLength={120}
          autoFocus
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          error={error ?? undefined}
        />
        <Select
          id="np-device"
          label="Device preset"
          hint="optional"
          options={deviceOptions}
          value={deviceId}
          onChange={(e) => setDeviceId(e.target.value)}
        />
        <Input
          id="np-url"
          label="Start URL"
          hint="optional"
          placeholder="https://example.com"
          value={startUrl}
          maxLength={2048}
          onChange={(e) => setStartUrl(e.target.value)}
        />
      </div>
    </Dialog>
  );
}
