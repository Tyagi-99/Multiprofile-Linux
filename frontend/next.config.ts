import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";
import type { NextConfig } from "next";

/**
 * A build id derived from the source, not from randomness.
 *
 * Next.js generates a random build id per build and writes it into the
 * exported RSC payloads. CI has a step asserting that `src/static` matches a
 * fresh build -- the package ships that directory, so a console change
 * committed without re-exporting would silently ship the previous UI. With a
 * random id that check compared two builds that could never be equal, so it
 * failed on every run including on main, which makes it a gate nobody can
 * act on. A red light that is always red is not a gate.
 *
 * Hashing the console's own sources and its lockfile makes the export
 * reproducible: identical input gives an identical bundle, so the check fails
 * only when the console actually changed without being re-exported. The id
 * still moves whenever the app does, which is what it is for.
 */
function sourceBuildId(root: string): string {
  const hash = createHash("sha256");
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir).sort()) {
      const full = join(dir, entry);
      const info = statSync(full);
      if (info.isDirectory()) {
        walk(full);
      } else if (info.isFile()) {
        hash.update(full.slice(root.length));
        hash.update(readFileSync(full));
      }
    }
  };
  walk(join(root, "src"));
  // Only the parts of package.json that can change the output. Hashing the
  // whole file would mean renaming an npm script forces a re-export, which
  // trains people to re-run the build to silence a check rather than because
  // the console changed.
  try {
    const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
    hash.update(JSON.stringify({
      dependencies: pkg.dependencies ?? {},
      devDependencies: pkg.devDependencies ?? {},
    }));
  } catch {
    // Absent contributes nothing, deterministically.
  }
  for (const name of ["package-lock.json", "tsconfig.json"]) {
    try {
      hash.update(readFileSync(join(root, name)));
    } catch {
      // Same.
    }
  }
  // Next.js requires a URL-safe id; base64url minus padding is both that and
  // shorter than hex.
  return hash.digest("base64url").slice(0, 21);
}

/**
 * Static export: the console is a localhost desktop app served by the Flask
 * backend (and the installed .deb), exactly like the current src/static
 * bundle. No Node.js runtime is needed on the user's machine.
 *
 * `next build` writes a fully static site to ./out, which scripts/sync-static.mjs
 * copies into ../src/static (the directory build-deb.sh packages).
 *
 * images.unoptimized is required for static export (no Next image server).
 *
 * The config is a function of the build phase because `rewrites` and
 * `output: "export"` are mutually exclusive and both are wanted -- just not
 * at the same time (F15). Declaring both unconditionally made every
 * production build print two warnings saying the rewrite would be ignored,
 * which was true and which trained whoever ran the build to skim past
 * warnings. Dev gets the rewrite; the export build does not declare one.
 */
export default (phase: string): NextConfig => {
  const isDev = phase === PHASE_DEVELOPMENT_SERVER;

  const config: NextConfig = {
    images: { unoptimized: true },
    // Keep asset paths relative-safe for serving under Flask's static dir.
    // (No basePath: the console is served from the server root, as today.)
    //
    // Deterministic build id: see sourceBuildId above. Without it the CI
    // check that src/static matches a fresh build can never pass.
    generateBuildId: async () => sourceBuildId(process.cwd()),
  };

  if (isDev) {
    /**
     * Dev-only: proxy /api/* to the local Flask backend (127.0.0.1:8765) so
     * `npm run dev` talks to real data. Production is same-origin through
     * Flask itself, so no rewrite is needed -- or possible -- there.
     */
    config.rewrites = async () => {
      if (process.env.MP_API_PROXY === "0") return [];
      return [
        {
          source: "/api/:path*",
          destination: "http://127.0.0.1:8765/api/:path*",
        },
      ];
    };
    return config;
  }

  config.output = "export";
  return config;
};
