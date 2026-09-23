/**
 * Typed HTTP client for the Multiprofile Flask API.
 *
 * The ONLY place fetch() is called. Every domain module imports from here.
 * Same-origin: in production the console is served by Flask itself, so
 * relative /api/* paths just work. In `npm run dev`, next.config rewrites
 * /api/* to the local Flask server (127.0.0.1:8765).
 *
 * Error contract: routes return either a non-2xx status, or 200 with
 * {"ok": false, "error": "<code>", ...}. Both become ApiError.
 * Secrets (proxy passwords, tokens, cookies) are never logged here.
 */

const TOKEN_KEY = "mp_api_token";

export function getApiToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setApiToken(token: string | null): void {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* pywebview may run without storage — non-fatal */
  }
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly payload: unknown;

  constructor(status: number, code: string, message: string, payload?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}

/** Subscribers are notified on 401 so the shell can show the token gate. */
type UnauthorizedHandler = () => void;
const unauthorizedHandlers = new Set<UnauthorizedHandler>();
export function onUnauthorized(fn: UnauthorizedHandler): () => void {
  unauthorizedHandlers.add(fn);
  return () => {
    unauthorizedHandlers.delete(fn);
  };
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query } = opts;
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getApiToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    // Backend unreachable (server not running, port blocked, …)
    throw new ApiError(0, "network_unreachable", "Could not reach the Multiprofile backend. Is the server running?", e);
  }

  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }

  if (res.status === 401) {
    unauthorizedHandlers.forEach((fn) => fn());
    throw new ApiError(401, "unauthorized", "Authentication required.", data);
  }

  const rec = data as Record<string, unknown> | null;
  if (rec && rec["ok"] === false) {
    const code = typeof rec["error"] === "string" ? rec["error"] : "request_failed";
    const message =
      typeof rec["message"] === "string" && rec["message"]
        ? rec["message"]
        : humanizeCode(code);
    throw new ApiError(res.status, code, message, data);
  }

  if (!res.ok) {
    const code = rec && typeof rec["error"] === "string" ? rec["error"] : `http_${res.status}`;
    throw new ApiError(res.status, code, humanizeCode(code), data);
  }

  return data as T;
}

export const get = <T,>(path: string, query?: RequestOptions["query"]) =>
  api<T>(path, { method: "GET", query });
export const post = <T,>(path: string, body?: unknown, query?: RequestOptions["query"]) =>
  api<T>(path, { method: "POST", body, query });
export const put = <T,>(path: string, body?: unknown) => api<T>(path, { method: "PUT", body });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

function humanizeCode(code: string): string {
  return code
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** User-friendly message for toasts: prefers the API's own message. */
export function apiMessage(err: unknown, fallback = "Something went wrong."): string {
  if (err instanceof ApiError) return err.message || fallback;
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}
