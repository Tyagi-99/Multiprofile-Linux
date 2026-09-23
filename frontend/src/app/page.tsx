"use client";

import { useEffect } from "react";
import { Shell } from "../components/layout/shell";
import { applyTheme, useUI } from "../stores/ui";

/**
 * Phase 2: the global application shell.
 * Views migrate one by one in later phases; the shell (sidebar, topbar,
 * command palette, shortcuts, theme, toasts, SSE) is the foundation.
 */
export default function Home() {
  const themeMode = useUI((s) => s.themeMode);

  // Apply the persisted theme on first client render (pre-paint script in
  // layout.tsx already set the initial attribute; this keeps it in sync).
  useEffect(() => {
    applyTheme(themeMode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <Shell />;
}
