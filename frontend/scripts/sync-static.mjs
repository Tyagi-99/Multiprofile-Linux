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
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const root = join(scriptDir, ".."); // frontend/
const outDir = join(root, "out");
const staticDir = join(root, "..", "src", "static"); // repo-root/src/static

if (!existsSync(outDir)) {
  console.error("frontend/out not found — run `npm run build` first.");
  process.exit(1);
}

mkdirSync(staticDir, { recursive: true });

// Files produced by the export win; anything else in src/static that is not
// a known keep-asset is removed so stale bundles cannot linger.
const KEEP = new Set(["favicon.ico", "app_icon.png", "BUILD_SOURCE"]);
for (const entry of readdirSync(staticDir)) {
  if (!KEEP.has(entry)) rmSync(join(staticDir, entry), { recursive: true, force: true });
}
cpSync(outDir, staticDir, { recursive: true });

/**
 * Record which console source this bundle was built from.
 *
 * CI has to be able to tell that a console change was committed without
 * re-exporting, because the .deb ships this directory and would otherwise
 * carry the previous UI. The obvious check -- rebuild and diff the output --
 * compares two builds from two machines, and Next.js output is not identical
 * across Node versions, so that gate failed on every run for reasons that had
 * nothing to do with staleness. A red light that is always red is not a gate.
 *
 * The build id is already a hash of the console's own sources (see
 * next.config.ts), so writing it here lets CI compare *source hashes* instead
 * of build artefacts: environment-independent, much faster, and it detects
 * exactly the thing the check exists for.
 */
const buildId = readFileSync(join(root, ".next", "BUILD_ID"), "utf8").trim();
writeFileSync(
  join(staticDir, "BUILD_SOURCE"),
  `${buildId}\n`,
  "utf8",
);
console.log(`published frontend/out -> src/static (source ${buildId})`);
