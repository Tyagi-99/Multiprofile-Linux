#!/usr/bin/env node
/**
 * Publishes the Next.js static export into ../src/static — the directory the
 * Flask backend serves and build-deb.sh packages into the .deb.
 *
 * Brand assets that must survive (favicon.ico, app_icon.png) live in
 * frontend/public/ and are copied by `next build` automatically.
 *
 * Safety: refuses to run if ../src/static contains files the export did not
 * produce and that are not on the keep-list (never silently delete).
 */
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const outDir = join(root, "out");
const staticDir = join(root, "..", "src", "static");

if (!existsSync(outDir)) {
  console.error("frontend/out not found — run `npm run build` first.");
  process.exit(1);
}

mkdirSync(staticDir, { recursive: true });

// Files produced by the export win; anything else in src/static that is not
// a known keep-asset is removed so stale bundles cannot linger.
const KEEP = new Set(["favicon.ico", "app_icon.png"]);
for (const entry of readdirSync(staticDir)) {
  if (!KEEP.has(entry)) rmSync(join(staticDir, entry), { recursive: true, force: true });
}
cpSync(outDir, staticDir, { recursive: true });
console.log(`published frontend/out -> src/static`);
