import { Dialog } from "../ui/dialog";
import { useUI } from "../../stores/ui";

const SHORTCUTS: Array<{ keys: string[]; description: string }> = [
  { keys: ["Ctrl/⌘", "K"], description: "Command palette" },
  { keys: ["N"], description: "New profile" },
  { keys: ["G", "then", "P"], description: "Go to Profiles" },
  { keys: ["G", "then", "D"], description: "Go to Devices" },
  { keys: ["G", "then", "N"], description: "Go to Network" },
  { keys: ["G", "then", "J"], description: "Go to Jobs" },
  { keys: ["?"], description: "This help" },
  { keys: ["Esc"], description: "Close dialog / palette" },
];

export function ShortcutHelpDialog() {
  const dialog = useUI((s) => s.dialog);
  const closeDialog = useUI((s) => s.closeDialog);
  return (
    <Dialog
      open={dialog === "shortcuts"}
      onClose={closeDialog}
      title="Keyboard shortcuts"
      description="Single-key shortcuts only work when you are not typing in a field."
    >
      <ul className="space-y-2.5">
        {SHORTCUTS.map((s) => (
          <li key={s.description} className="flex items-center justify-between text-sm">
            <span className="text-ink-2">{s.description}</span>
            <span className="flex items-center gap-1">
              {s.keys.map((k) => (
                <kbd
                  key={k}
                  className="tnum rounded-md border border-border-subtle bg-surface-0 px-2 py-1 text-xs font-medium text-ink-2"
                >
                  {k}
                </kbd>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}
