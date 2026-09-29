import { PHASE_DEVELOPMENT_SERVER } from "next/constants";
import type { NextConfig } from "next";

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
