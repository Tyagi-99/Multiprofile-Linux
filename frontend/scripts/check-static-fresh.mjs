#!/usr/bin/env node
/**
 * Is the committed src/static bundle built from the committed console source?
 *
 * The .deb ships src/static, so a console change committed without
 * re-exporting silently ships the previous UI. That is what this guards.
 *
 * It compares **source hashes**, not build outputs. The previous check
 * rebuilt the console in CI and diffed the result against the committed
 * bundle, which compares two builds produced by two different Node versions
 * -- Next.js output is not byte-identical across them, so that check failed
 * on every run including on main, for reasons that had nothing to do with
 * staleness. A gate that is always red gets ignored, which is worse than not
 * having one.
 *
 * The build id is already a hash of frontend/src plus the lockfile (see
 * next.config.ts), and sync-static.mjs writes it to src/static/BUILD_SOURCE.
 * So: recompute it from the working tree, compare. No build required.
 */
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const root = join(scriptDir, "..");
const marker = join(root, "..", "src", "static", "BUILD_SOURCE");

// Kept identical to sourceBuildId() in next.config.ts. Duplicated rather than
// imported because that file is TypeScript loaded by Next's own pipeline, and
// a check that needs the framework to run is a check that cannot run early.
function sourceBuildId(base) {
  const hash = createHash("sha256");
  const walk = (dir) => {
    for (const entry of readdirSync(dir).sort()) {
      const full = join(dir, entry);
      const info = statSync(full);
      if (info.isDirectory()) walk(full);
      else if (info.isFile()) {
        hash.update(full.slice(base.length));
        hash.update(readFileSync(full));
      }
    }
  };
  walk(join(base, "src"));
  for (const name of ["package.json", "package-lock.json", "tsconfig.json"]) {
    try {
      hash.update(readFileSync(join(base, name)));
    } catch {
      // Absent contributes nothing, deterministically.
    }
  }
  return hash.digest("base64url").slice(0, 21);
}

if (!existsSync(marker)) {
  console.error(
    "src/static/BUILD_SOURCE is missing — run `npm run publish` in frontend/ " +
    "and commit the result.",
  );
  process.exit(1);
}

const recorded = readFileSync(marker, "utf8").trim();
const actual = sourceBuildId(root);

if (recorded !== actual) {
  console.error(
    `src/static was built from console source ${recorded}, but the working ` +
    `tree is ${actual}.\n` +
    "The .deb ships src/static, so this would package the previous UI.\n" +
    "Run `npm run publish` in frontend/ and commit the result.",
  );
  process.exit(1);
}

console.log(`src/static matches the console source (${actual})`);
