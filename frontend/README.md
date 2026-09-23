# Multiprofile Console — frontend

Next.js 15 + TypeScript + Tailwind CSS v4, built as a **static export**.
The Flask backend (`src/multiprofile/api/server.py`) serves the result from
`src/static/`, exactly like the previous hand-written bundle — no Node.js
runtime is needed on the user's machine, and `build-deb.sh` is unchanged.

## Workflow

```bash
cd frontend
npm install        # once
npm run dev        # local dev server (http://localhost:3000)
npm run typecheck  # tsc --noEmit
npm run build      # static export -> frontend/out
npm run publish    # build + copy into ../src/static (what the .deb ships)
```

## What lives where

- `src/app/` — App Router pages (shell + views)
- `src/components/ui/` — design-system primitives (button, input, dialog…)
- `src/components/layout/` — sidebar, topbar, command palette
- `src/components/<domain>/` — profile, device, network, fingerprint, jobs…
- `src/lib/` — typed API client, SSE client, formatting helpers
- `src/stores/` — Zustand UI state only (sidebar, palette, theme)
- `src/styles/tokens.css` — the design tokens; the single source of truth
- `public/` — favicon.ico, app_icon.png (brand assets)

## Rules

- Every color/spacing/radius/shadow comes from `tokens.css`. No ad-hoc hex.
- Server data lives in TanStack Query. Zustand holds UI prefs only.
- No fake data, ever. Empty states over placeholders.
- Proxy passwords, tokens, cookies: never in state, logs, or URLs.
- `output: "export"` must keep working — no Node server dependencies.

## Releasing

The built output under `src/static/` is committed (same practice as the old
bundle), so the laptop can build the .deb with bash alone. Bump the product
version in all seven markers per the packaging rule when the redesign ships.
