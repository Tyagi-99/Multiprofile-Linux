/**
 * Hash router. The console is served by Flask as static files with no SPA
 * fallback, so routes live in the hash: #/profiles, #/jobs, #/profiles/<id>.
 * Invisible inside pywebview; zero backend changes required.
 */

import { useCallback, useEffect, useState } from "react";

export type Route =
  | { name: "overview" }
  | { name: "profiles"; id?: string }
  | { name: "devices" }
  | { name: "network" }
  | { name: "fingerprints" }
  | { name: "automation" }
  | { name: "jobs"; id?: string }
  | { name: "gateways" }
  | { name: "extensions" }
  | { name: "trash" }
  | { name: "audit" }
  | { name: "settings" };

export function parseHash(hash: string): Route {
  // Strip the query BEFORE matching. A top-level route can carry one --
  // #/network?tab=pools is what the Network tabs write -- and matching the
  // whole string "network?tab=pools" against "network" missed every case and
  // fell through to the overview, so every Network tab click bounced the
  // operator back to the dashboard. Profile tabs escaped it only because
  // their query sits in the tail (#/profiles/<id>?tab=network).
  const [rawPath] = hash.replace(/^#\/?/, "").split("?");
  const clean = rawPath.replace(/\/$/, "");
  const [head, rawTail] = clean.split("/");
  const tail = rawTail || undefined;
  switch (head) {
    case "":
    case "overview":
      return { name: "overview" };
    case "profiles":
      return { name: "profiles", id: tail || undefined };
    case "devices":
      return { name: "devices" };
    case "network":
      return { name: "network" };
    case "fingerprints":
      return { name: "fingerprints" };
    case "automation":
      return { name: "automation" };
    case "jobs":
      return { name: "jobs", id: tail || undefined };
    case "gateways":
      return { name: "gateways" };
    case "extensions":
      return { name: "extensions" };
    case "trash":
      return { name: "trash" };
    case "audit":
      return { name: "audit" };
    case "settings":
      return { name: "settings" };
    default:
      return { name: "overview" };
  }
}

export function routeToHash(route: Route): string {
  switch (route.name) {
    case "overview":
      return "#/overview";
    case "profiles":
      return route.id ? `#/profiles/${route.id}` : "#/profiles";
    case "jobs":
      return route.id ? `#/jobs/${route.id}` : "#/jobs";
    default:
      return `#/${route.name}`;
  }
}

export function navigate(route: Route): void {
  window.location.hash = routeToHash(route);
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() =>
    typeof window === "undefined" ? { name: "overview" } : parseHash(window.location.hash),
  );
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

/** Sidebar active-state matching: a section is active for its route and children. */
export function isRouteActive(route: Route, section: Route["name"]): boolean {
  return route.name === section;
}

/** Parse the query string of the current hash (e.g. #/profiles/<id>?tab=network). */
export function getHashQuery(): URLSearchParams {
  const hash = typeof window === "undefined" ? "" : window.location.hash;
  const q = hash.indexOf("?");
  return new URLSearchParams(q >= 0 ? hash.slice(q + 1) : "");
}

/** Navigate to a profile detail tab via the hash query. */
export function navigateProfileTab(id: string, tab: string): void {
  window.location.hash = `#/profiles/${encodeURIComponent(id)}?tab=${tab}`;
}
