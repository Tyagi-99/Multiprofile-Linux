/**
 * A small client for the Multiprofile Linux local API.
 *
 * Four operations plus the one that matters for automation:
 *
 *   import { Client } from './multiprofile.mjs';
 *
 *   const mp = new Client();                    // http://127.0.0.1:8765
 *   const profile = await mp.create('research-1');
 *   const session = await mp.launch(profile.id);
 *   console.log(session.cdpUrl);                // hand this to Puppeteer
 *   await mp.stop(profile.id);
 *
 * **Why cdpUrl is the point.** `launch()` returns a DevTools endpoint, and
 * that is how an automation framework attaches to a profile the product
 * launched -- with its identity, its route and its font view already
 * installed. Launching Chromium yourself through Puppeteer gets a browser
 * with none of that, which is the mistake this SDK exists to make hard:
 *
 *   const browser = await puppeteer.connect({ browserURL: session.cdpUrl });
 *   // Playwright:
 *   const browser = await chromium.connectOverCDP(session.cdpUrl);
 *
 * **Auth.** Loopback needs no token. A non-loopback caller needs one on
 * every route except `GET /api/health`; pass `{ token }` and it is sent as a
 * bearer. There is no TLS, so a token on a LAN travels in cleartext --
 * tunnel it. See `docs/API.md`.
 *
 * No dependencies: global `fetch` only (Node 18+), so this can be dropped
 * into a scraper without adding a package.
 */

export const DEFAULT_BASE_URL = 'http://127.0.0.1:8765';

export class MultiprofileError extends Error {}

/** The API answered, and the answer was an error. */
export class ApiError extends MultiprofileError {
  constructor(status, body, method, path) {
    const detail = body && typeof body === 'object' && body.error !== undefined
      ? JSON.stringify(body.error)
      : JSON.stringify(body);
    super(`${method} ${path} -> ${status}: ${detail}`);
    this.status = status;
    this.body = body;
  }
}

/** A valid token without the scope the route needs (403). */
export class ScopeError extends ApiError {}

/**
 * The launch was refused, with the product's reason.
 *
 * A distinct type because it is the *expected* outcome of a fail-closed
 * route decision, not a bug: a dead proxy on a profile set to BLOCK, a pool
 * with no members, or an authenticated SOCKS5 proxy Chromium cannot use. A
 * caller that wants to treat "refused" differently from "the server is down"
 * needs them to be different errors.
 */
export class LaunchBlocked extends MultiprofileError {
  constructor(reason, body) {
    super(reason);
    this.reason = reason;
    this.body = body;
  }
}

/** A launched profile, and how to attach to it. */
export class Session {
  constructor(profileId, raw) {
    this.profileId = profileId;
    this.raw = raw;
    this.pid = raw.pid ?? null;
    this.cdpHost = raw.cdpHost ?? '127.0.0.1';
    this.cdpPort = raw.cdpPort ?? raw.debugPort ?? null;
  }

  /** The DevTools HTTP endpoint, for connectOverCDP / puppeteer.connect. */
  get cdpUrl() {
    return this.cdpPort ? `http://${this.cdpHost}:${this.cdpPort}` : null;
  }

  /**
   * What the launcher recorded -- including identity faults.
   *
   * Worth reading rather than ignoring: an experimental identity claim, an
   * engine outside the supported range, and a font claim that cannot be
   * backed all arrive here.
   */
  get notes() {
    return [...(this.raw.notes ?? [])];
  }
}

export class Client {
  constructor({ baseUrl = DEFAULT_BASE_URL, token = null, timeoutMs = 30000 } = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.token = token;
    this.timeoutMs = timeoutMs;
  }

  async #request(method, path, payload, params) {
    let url = `${this.baseUrl}${path}`;
    if (params) {
      const search = new URLSearchParams(
        Object.entries(params).filter(([, v]) => v !== undefined && v !== null),
      );
      if ([...search].length) url += `?${search}`;
    }
    const headers = { Accept: 'application/json' };
    // Writes require this exact content type; see docs/API.md.
    if (payload !== undefined) headers['Content-Type'] = 'application/json';
    if (this.token) headers.Authorization = `Bearer ${this.token}`;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body: payload === undefined ? undefined : JSON.stringify(payload),
        signal: controller.signal,
      });
    } catch (cause) {
      throw new MultiprofileError(
        `${method} ${path}: could not reach ${this.baseUrl} (${cause.message}). `
        + 'Is `multiprofile --server-only` running?',
      );
    } finally {
      clearTimeout(timer);
    }

    const text = await response.text();
    let body = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    if (!response.ok) {
      if (response.status === 403) throw new ScopeError(403, body, method, path);
      // 409 on a launch is a refusal, not a fault: the product worked and
      // declined. Thrown as LaunchBlocked so a caller can tell it from the
      // server being broken.
      if (response.status === 409 && path.endsWith('/launch')) {
        const reason = body && typeof body === 'object'
          ? JSON.stringify(body.error ?? body.errors ?? body) : '';
        throw new LaunchBlocked(reason || 'the launch was refused', body);
      }
      throw new ApiError(response.status, body, method, path);
    }
    return body;
  }

  /** Everything the product knows about itself. Needs no token. */
  health() {
    return this.#request('GET', '/api/health');
  }

  /**
   * The same diagnosis as `multiprofile --doctor`.
   *
   * Worth calling once at the start of a run: it reports a blocked route or
   * an unsupported engine *before* a launch fails.
   */
  doctor() {
    return this.#request('GET', '/api/doctor');
  }

  /** Profiles, newest first. Paginated; pass `cursor` to continue. */
  async list({ limit, cursor } = {}) {
    const body = await this.#request('GET', '/api/profiles', undefined, { limit, cursor });
    if (body && typeof body === 'object' && !Array.isArray(body)) {
      return body.items ?? body.profiles ?? [];
    }
    return body ?? [];
  }

  /**
   * One profile, with its resolved route and identity verdict.
   *
   * The detail endpoint carries `effectiveTimezone` and `identity`, which
   * the list does not -- resolving a route per row is expensive.
   */
  async get(profileId) {
    const body = await this.#request('GET', `/api/profiles/${encodeURIComponent(profileId)}`);
    return body?.profile ?? body;
  }

  /**
   * Create a profile. Extra fields are passed through, so anything the API
   * accepts works: proxyType/proxyHost/proxyPort/proxyUser/proxyPass,
   * userAgentId, deviceId, timezone, startUrl, tags.
   *
   * Unknown fields are **rejected** rather than stored, so a typo is a 400
   * and not a setting that silently does nothing.
   */
  async create(name, fields = {}) {
    const body = await this.#request('POST', '/api/profiles', { name, ...fields });
    return body?.profile ?? body;
  }

  /**
   * Launch a profile and return how to attach to it.
   *
   * Throws LaunchBlocked when the product refused -- a dead proxy on a
   * fail-closed profile, an empty pool, or a route this engine cannot use.
   */
  async launch(profileId, { url, direct = false } = {}) {
    const params = {};
    if (url) params.url = url;
    if (direct) params.direct = '1';
    const body = await this.#request(
      'POST', `/api/profiles/${encodeURIComponent(profileId)}/launch`, {},
      Object.keys(params).length ? params : undefined,
    );
    if (!body || body.ok !== true) {
      const reason = body ? JSON.stringify(body.error ?? body.errors ?? body) : '';
      throw new LaunchBlocked(reason || 'the launch was refused', body);
    }
    return new Session(profileId, body);
  }

  stop(profileId) {
    return this.#request('POST', `/api/profiles/${encodeURIComponent(profileId)}/stop`, {});
  }

  /**
   * Trash a profile, or remove it for good.
   *
   * The default is the trash, which is reversible. With `permanent: true`
   * the response's `purgeStorage` is the **outcome**, not the request -- a
   * purge that could not remove the Chrome directory says so and names what
   * it left (F51).
   */
  delete(profileId, { permanent = false, purgeStorage = true } = {}) {
    return this.#request(
      'DELETE', `/api/profiles/${encodeURIComponent(profileId)}`, undefined,
      { permanent: permanent ? '1' : '0', purgeStorage: purgeStorage ? '1' : '0' },
    );
  }

  /**
   * Block until the DevTools endpoint answers, then return its URL.
   *
   * `launch()` returns once the process is up, which is not quite the moment
   * DevTools starts accepting connections. An automation framework that
   * connects too early gets a confusing refusal, so this exists to be called
   * instead of a sleep.
   */
  async waitForCdp(session, { timeoutMs = 20000 } = {}) {
    const url = session.cdpUrl;
    if (!url) {
      throw new MultiprofileError(
        `${session.profileId}: the launch recorded no CDP port, so nothing can attach to it`,
      );
    }
    const deadline = Date.now() + timeoutMs;
    let last = '';
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`${url}/json/version`, {
          headers: { Host: `localhost:${session.cdpPort}` },
        });
        if (response.ok) return url;
        last = `HTTP ${response.status}`;
      } catch (cause) {
        last = cause.message;
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    throw new MultiprofileError(
      `${session.profileId}: DevTools did not answer on ${url} within ${timeoutMs}ms (${last})`,
    );
  }
}

export default Client;
