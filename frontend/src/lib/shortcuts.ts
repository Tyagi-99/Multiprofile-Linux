/**
 * Global keyboard shortcuts. Mounted once by the Shell.
 *
 * Rules:
 * - Single-key shortcuts fire ONLY when the user is not typing (input,
 *   textarea, select, contenteditable, or cmdk's own input).
 * - Modifier shortcuts (mod+K) always work except inside the palette itself,
 *   where cmdk owns Escape/Enter.
 * - Never preventDefault browser-reserved chords.
 */

import { useEffect } from "react";
import { navigate } from "./router";
import { useUI } from "../stores/ui";

function isTypingTarget(el: EventTarget | null): boolean {
  const t = el as HTMLElement | null;
  if (!t || !("tagName" in t)) return false;
  const tag = (t.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (t.isContentEditable) return true;
  // cmdk input
  if (t.getAttribute && t.getAttribute("role") === "combobox") return true;
  return false;
}

export function useGlobalShortcuts() {
  useEffect(() => {
    let gPending = false;
    let gTimer: ReturnType<typeof setTimeout> | null = null;

    const clearG = () => {
      gPending = false;
      if (gTimer) clearTimeout(gTimer);
      gTimer = null;
    };

    const onKeyDown = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      const ui = useUI.getState();

      // mod+K: command palette (toggle)
      if (mod && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        ui.setPaletteOpen(!ui.paletteOpen);
        clearG();
        return;
      }

      // Escape closes dialogs first
      if (e.key === "Escape") {
        if (ui.dialog) {
          ui.closeDialog();
          clearG();
          return;
        }
        clearG();
        return;
      }

      // Single-key shortcuts: never while typing, never with modifiers.
      if (mod || e.altKey || isTypingTarget(e.target)) {
        clearG();
        return;
      }

      // "g then <key>" sequences (Vim-style navigation)
      if (gPending) {
        const k = e.key.toLowerCase();
        const dest =
          k === "p" ? { name: "profiles" as const }
          : k === "d" ? { name: "devices" as const }
          : k === "n" ? { name: "network" as const }
          : k === "j" ? { name: "jobs" as const }
          : null;
        clearG();
        if (dest) {
          e.preventDefault();
          ui.setPaletteOpen(false);
          navigate(dest);
        }
        return;
      }

      switch (e.key) {
        case "g":
        case "G":
          gPending = true;
          gTimer = setTimeout(clearG, 800);
          break;
        case "n":
        case "N":
          e.preventDefault();
          ui.openDialog("newProfile");
          break;
        case "?":
          ui.openDialog("shortcuts");
          break;
        default:
          break;
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      clearG();
    };
  }, []);
}
