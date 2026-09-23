import { useState, type ButtonHTMLAttributes } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "../../lib/cn";

/** CopyButton: copies text, shows transient confirmation. */

export function CopyButton({
  text,
  label = "Copy to clipboard",
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const onClick = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API unavailable (e.g. insecure context) — stay silent.
    }
  };
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-3 transition-colors",
        "hover:bg-surface-3 hover:text-ink-1 focus-visible:outline-2 focus-visible:outline-brand-500",
        className,
      )}
      {...rest}
    >
      {copied ? (
        <Check size={14} aria-hidden className="text-ok" />
      ) : (
        <Copy size={14} aria-hidden />
      )}
    </button>
  );
}
