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
 */
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  // Keep asset paths relative-safe for serving under Flask's static dir.
  // (No basePath: the console is served from the server root, as today.)

  /**
   * Dev-only: proxy /api/* to the local Flask backend (127.0.0.1:8765) so
   * `npm run dev` talks to real data. Rewrites are ignored by `output:
   * "export"` — production is same-origin through Flask itself.
   */
  async rewrites() {
    if (process.env.MP_API_PROXY === "0") return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8765/api/:path*",
      },
    ];
  },
};

export default nextConfig;
