/**
 * UI-only state (Zustand). Server data lives in TanStack Query — never here.
 *
 * Persisted: sidebar collapsed, theme mode. Ephemeral: palette, dialogs.
 * All localStorage access is try/catch (pywebview may run without storage).
 */

import { create } from "zustand";
import type { ResolvedTheme, ThemeMode } from "../lib/types";

const SIDEBAR_KEY = "mp_sidebar_collapsed";
const THEME_KEY = "mp_theme";

function readLS(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLS(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* non-fatal */
  }
}

function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode !== "system") return mode;
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(mode: ThemeMode): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", resolveTheme(mode));
}

type DialogKind = "newProfile" | "bulkCreate" | "wizard" | "shortcuts" | "token" | null;

interface UIState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;

  themeMode: ThemeMode;
  resolvedTheme: ResolvedTheme;
  setThemeMode: (mode: ThemeMode) => void;
  cycleTheme: () => void;

  paletteOpen: boolean;
  setPaletteOpen: (open: boolean) => void;

  dialog: DialogKind;
  /** Optional payload carried with the open dialog (e.g. prefilled device id). */
  dialogPayload: unknown;
  openDialog: (kind: Exclude<DialogKind, null>, payload?: unknown) => void;
  closeDialog: () => void;

  /** Bump to force the palette to re-run its default view (e.g. after actions). */
  paletteNonce: number;
  bumpPalette: () => void;
}

const initialTheme = ((): ThemeMode => {
  if (typeof window === "undefined") return "system";
  const saved = readLS(THEME_KEY);
  return saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
})();

const initialCollapsed = (() => {
  if (typeof window === "undefined") return false;
  return readLS(SIDEBAR_KEY) === "1";
})();

const THEME_ORDER: ThemeMode[] = ["light", "dark", "system"];

export const useUI = create<UIState>()((set, get) => ({
  sidebarCollapsed: initialCollapsed,
  toggleSidebar: () => {
    const next = !get().sidebarCollapsed;
    writeLS(SIDEBAR_KEY, next ? "1" : "0");
    set({ sidebarCollapsed: next });
  },

  themeMode: initialTheme,
  resolvedTheme: resolveTheme(initialTheme),
  setThemeMode: (mode) => {
    writeLS(THEME_KEY, mode);
    applyTheme(mode);
    set({ themeMode: mode, resolvedTheme: resolveTheme(mode) });
  },
  cycleTheme: () => {
    const idx = THEME_ORDER.indexOf(get().themeMode);
    const next = THEME_ORDER[(idx + 1) % THEME_ORDER.length];
    get().setThemeMode(next);
  },

  paletteOpen: false,
  setPaletteOpen: (open) => set({ paletteOpen: open }),

  dialog: null,
  dialogPayload: null,
  openDialog: (kind, payload) => set({ dialog: kind, dialogPayload: payload ?? null, paletteOpen: false }),
  closeDialog: () => set({ dialog: null, dialogPayload: null }),

  paletteNonce: 0,
  bumpPalette: () => set((s) => ({ paletteNonce: s.paletteNonce + 1 })),
}));

// Keep resolved theme in sync with the OS while in "system" mode.
if (typeof window !== "undefined" && window.matchMedia) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    const { themeMode } = useUI.getState();
    if (themeMode === "system") {
      applyTheme("system");
      useUI.setState({ resolvedTheme: resolveTheme("system") });
    }
  };
  if (typeof mq.addEventListener === "function") mq.addEventListener("change", onChange);
  else mq.addListener(onChange);
}
