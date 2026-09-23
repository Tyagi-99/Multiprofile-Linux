/* ============================================================
   Multiprofile Linux — Operations Console (v1.7.0 restyle)
   Vanilla JS SPA. All numbers come from live API responses;
   unavailable endpoints render as "—", never fabricated.
   Proxy passwords are never displayed (masked as ••••).
   Brand: Multiprofile Linux — original design language.   UX grammar (sidebar, dense table, bulk bar, stat cards).
   ============================================================ */
"use strict";

/* ============================== Utilities ============================== */
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const esc = (v) => String(v == null ? "" : v)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const NA = '<span class="muted">—</span>';
const val = (v) => (v === undefined || v === null || v === "") ? NA : esc(v);
const num = (v) => (v === undefined || v === null || v === "")
  ? '<span class="muted">—</span>'
  : `<span class="mono">${esc(v)}</span>`;
const hasV = (v) => v !== undefined && v !== null && v !== "";

/* Local-storage access goes ONLY through these three helpers. Direct
   localStorage touches elsewhere break the webview desktop build. */
function storeGet(key) {
  try { return window.localStorage.getItem(key); } catch (e) { return null; }
}
function storeSet(key, value) {
  try { window.localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ }
}
function storeDel(key) {
  try { window.localStorage.removeItem(key); } catch (e) { /* storage unavailable */ }
}

function debounce(fn, ms) {
  let t = 0;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
function timeAgo(ts) {
  if (!ts) return "—";
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 0) return "in the future";
  if (s < 60) return s + "s ago";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m ago";
  const h = Math.floor(m / 60);
  if (h < 24) return h + "h ago";
  return Math.floor(h / 24) + "d ago";
}
function fmtDate(ts) {
  if (!ts) return "—";
  try { return new Date(ts).toLocaleString(); } catch (e) { return "—"; }
}
function fmtDur(sec) {
  if (sec == null || isNaN(sec)) return "—";
  sec = Math.floor(sec);
  if (sec < 60) return sec + "s";
  if (sec < 3600) return Math.floor(sec / 60) + "m";
  return (sec / 3600).toFixed(1) + "h";
}
function fmtBytes(b) {
  if (b == null || isNaN(b)) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return b.toFixed(b >= 10 ? 0 : 1) + " " + u[i];
}
function shortId(id) {
  id = String(id || "");
  return id.length > 12 ? id.slice(0, 8) + "…" : id;
}

/* ============================== Icons (inline SVG) ============================== */
const IC = {
  dash: '<svg viewBox="0 0 16 16" width="16" height="16"><rect x="2" y="2" width="5" height="5" rx="1.2" fill="currentColor" opacity="0.85"/><rect x="9" y="2" width="5" height="5" rx="1.2" fill="currentColor" opacity="0.5"/><rect x="2" y="9" width="5" height="5" rx="1.2" fill="currentColor" opacity="0.5"/><rect x="9" y="9" width="5" height="5" rx="1.2" fill="currentColor" opacity="0.85"/></svg>',
  prof: '<svg viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="5.2" r="2.8" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M2.8 13.4c.7-2.4 2.8-3.7 5.2-3.7s4.5 1.3 5.2 3.7" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>',
  dev: '<svg viewBox="0 0 16 16" width="16" height="16"><rect x="4" y="2" width="8" height="12" rx="1.6" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M7 12.2h2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  fp: '<svg viewBox="0 0 16 16" width="16" height="16"><path d="M5.5 13.5c-.9-1-1.5-2.5-1.5-4.3 0-3 2-5.7 4-5.7 2 0 4 2.7 4 5.7 0 1.3-.3 2.5-.8 3.4M6.2 13.2c-.3-.8-.5-1.7-.5-2.6 0-2.2 1.2-4.1 2.3-4.1s2.3 1.9 2.3 4.1c0 1-.2 1.9-.5 2.7M8 13.4v-2.2c0-1 .4-2 .4-2" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/></svg>',
  net: '<svg viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M2 8h12M8 2c1.8 1.7 2.7 3.8 2.7 6S9.8 12.3 8 14c-1.8-1.7-2.7-3.8-2.7-6S6.2 3.7 8 2z" stroke="currentColor" stroke-width="1.2" fill="none"/></svg>',
  jobs: '<svg viewBox="0 0 16 16" width="16" height="16"><rect x="2.5" y="2.5" width="11" height="11" rx="2" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M5.5 8l2 2 3.2-3.6" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  audit: '<svg viewBox="0 0 16 16" width="16" height="16"><path d="M4 2h8v12H4z" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linejoin="round"/><path d="M6 5.5h4M6 8h4M6 10.5h2.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  gear: '<svg viewBox="0 0 16 16" width="16" height="16"><circle cx="8" cy="8" r="2.2" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M12.4 3.6 11 5M5 11l-1.4 1.4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  chevL: '<svg viewBox="0 0 16 16" width="15" height="15"><path d="M10 3 5.5 8 10 13" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  play: '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M4.5 3.2v9.6L12.5 8z" fill="currentColor"/></svg>',
  stop: '<svg viewBox="0 0 16 16" width="12" height="12"><rect x="3.5" y="3.5" width="9" height="9" rx="1.4" fill="currentColor"/></svg>',
  restart: '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v2.6h-2.6" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  park: '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M6 3.5h7M6 8h7M6 12.5h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M2.5 3.5v9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  trash: '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M2.8 4h10.4M6.5 2.5h3M4 4l.7 9.2a1 1 0 0 0 1 .8h4.6a1 1 0 0 0 1-.8L12 4M6.6 6.6v4M9.4 6.6v4" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  eye: '<svg viewBox="0 0 16 16" width="13" height="13"><path d="M1.8 8S4 4.4 8 4.4 14.2 8 14.2 8 12 11.6 8 11.6 1.8 8 1.8 8z" stroke="currentColor" stroke-width="1.4" fill="none"/><circle cx="8" cy="8" r="1.8" stroke="currentColor" stroke-width="1.4" fill="none"/></svg>',
};

/* ============================== API client ============================== */
const TOKEN_KEY = "mp_api_token";
const THEME_KEY = "mp_theme";
let apiToken = storeGet(TOKEN_KEY) || "";

/* ============================== Theme (Light / Dark) ============================== */
/* data-theme is set on <html> before paint by the inline script in index.html.
   Here we only sync the toggle control and persist the user's choice. */
function currentTheme() {
  const t = storeGet(THEME_KEY);
  if (t === "dark" || t === "light") return t;
  return document.documentElement.getAttribute("data-theme") || "light";
}
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  const light = $("#themeLight"), dark = $("#themeDark");
  if (light) light.setAttribute("aria-pressed", t === "light" ? "true" : "false");
  if (dark) dark.setAttribute("aria-pressed", t === "dark" ? "true" : "false");
}
function initTheme() {
  applyTheme(currentTheme());
  const light = $("#themeLight"), dark = $("#themeDark");
  if (light) light.onclick = () => { storeSet(THEME_KEY, "light"); applyTheme("light"); };
  if (dark) dark.onclick = () => { storeSet(THEME_KEY, "dark"); applyTheme("dark"); };
}
let authFailed = false;

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (apiToken) headers["Authorization"] = "Bearer " + apiToken;
  let res;
  try {
    res = await fetch(path, Object.assign({}, opts, { headers }));
  } catch (e) {
    return { ok: false, status: 0, networkError: true, error: String((e && e.message) || e) };
  }
  if (res.status === 401) {
    authFailed = true;
    return { ok: false, status: 401, error: "unauthorized" };
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON body */ }
  return { ok: res.ok, status: res.status, data };
}
const apiGet = (p) => api(p, { method: "GET" });
const apiPost = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });
const apiPut = (p, body) => api(p, { method: "PUT", body: JSON.stringify(body || {}) });
const apiDel = (p) => api(p, { method: "DELETE" });

/* ============================== Cache ============================== */
const cache = {
  profiles: null, devices: null, clients: null, templates: null,
  gateways: null, jobs: null, healthMap: null, sysHealth: null,
  fp: {}, netHealth: {},
};
function invalidate(...keys) { keys.forEach((k) => { cache[k] = null; }); }

async function loadDevices(force) {
  if (!force && cache.devices) return cache.devices;
  const r = await apiGet("/api/devices");
  if (r.ok && r.data) {
    const list = Array.isArray(r.data) ? r.data : (r.data.devices || []);
    cache.devices = list;
    return list;
  }
  cache.devices = [];
  return [];
}
async function loadClients(force) {
  if (!force && cache.clients) return cache.clients;
  const r = await apiGet("/api/clients");
  cache.clients = (r.ok && r.data && Array.isArray(r.data.clients)) ? r.data.clients : [];
  return cache.clients;
}
async function loadTemplates(force) {
  if (!force && cache.templates) return cache.templates;
  const r = await apiGet("/api/templates");
  cache.templates = (r.ok && r.data && Array.isArray(r.data.templates)) ? r.data.templates : [];
  return cache.templates;
}
async function loadGateways(force) {
  if (!force && cache.gateways) return cache.gateways;
  const r = await apiGet("/api/gateways");
  cache.gateways = (r.ok && r.data && Array.isArray(r.data.gateways)) ? r.data.gateways : [];
  return cache.gateways;
}
async function loadHealthMap(force) {
  if (!force && cache.healthMap) return cache.healthMap;
  const r = await apiGet("/api/health/profiles");
  if (r.ok && r.data) {
    const h = r.data.profiles || r.data.health || r.data || {};
    cache.healthMap = Array.isArray(h) ? {} : h;
    return cache.healthMap;
  }
  cache.healthMap = {};
  return {};
}
function clientName(id) {
  if (!id) return null;
  const c = (cache.clients || []).find((x) => x.id === id || x.clientId === id);
  return c ? (c.name || c.id) : null;
}
function deviceById(id) {
  return (cache.devices || []).find((d) => d.id === id) || null;
}
function isRunning(p) {
  return p.status === "running" || !!p.isRunning || !!p.launchState && /running/i.test(String(p.launchState));
}

/* ============================== Components ============================== */
function toast(msg, type) {
  const el = document.createElement("div");
  el.className = "toast" + (type === "err" ? " err" : type === "warn" ? " warn" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 4200);
}
function badge(text, kind) {
  return `<span class="badge ${kind || "grey"}">${esc(text)}</span>`;
}
function supportBadge(s) {
  const v = String(s || "").toUpperCase();
  if (v === "VALIDATED") return badge("Validated", "green");
  if (v === "PARTIAL") return badge("Partial", "amber");
  if (v === "PENDING") return badge("Pending", "grey");
  if (v === "UNSUPPORTED" || v === "UNAVAILABLE") return badge("Unavailable", "red");
  return NA;
}
function runDot(p) {
  if (p.status === "error") return '<span class="status-dot error" title="Error"></span>';
  if (p.status === "parked") return '<span class="status-dot parked" title="Parked"></span>';
  if (isRunning(p)) return '<span class="status-dot running" title="Running"></span>';
  return '<span class="status-dot stopped" title="Stopped"></span>';
}
function statusBadge(s) {
  const v = String(s || "").toLowerCase();
  if (v === "running") return badge("Running", "green");
  if (v === "error") return badge("Error", "red");
  if (v === "parked") return badge("Parked", "amber");
  if (v === "starting" || v === "stopping") return badge(s, "blue");
  return badge("Stopped", "grey");
}
function jobStatusBadge(s) {
  const v = String(s || "").toLowerCase();
  if (["done", "completed", "success"].includes(v)) return badge(s, "green");
  if (["failed", "error"].includes(v)) return badge(s, "red");
  if (["running", "in_progress"].includes(v)) return badge(s, "blue");
  if (["queued", "pending"].includes(v)) return badge(s, "amber");
  if (["cancelled", "canceled"].includes(v)) return badge(s, "grey");
  return badge(s || "—", "grey");
}
function emptyState(title, text, actionHtml) {
  return `<div class="empty">
    <h3>${esc(title)}</h3><p>${esc(text)}</p>${actionHtml || ""}
  </div>`;
}
function skelTable(cols, rows) {
  let h = '<div class="table-card"><table class="grid"><tbody>';
  for (let i = 0; i < (rows || 6); i++) {
    h += "<tr>" + `<td colspan="${cols}"><div class="skel" style="height:16px;margin:6px 0"></div></td>` + "</tr>";
  }
  return h + "</tbody></table></div>";
}
/* Proxy summaries NEVER show passwords — auth is rendered as •••• */
function proxyLabel(p) {
  const host = p.proxyHost || p.proxy_host;
  if (!host) return NA;
  const proto = String(p.proxyType || p.networkMode || "http").toLowerCase();
  const port = p.proxyPort ? ":" + p.proxyPort : "";
  const auth = (p.proxyUser || p.proxy_user || p.hasProxyAuth) ? "••••@" : "";
  return `<span class="mono">${esc(proto)}://${auth}${esc(host)}${esc(port)}</span>`;
}
function fpSummary(p) {
  const parts = [];
  const ua = p.userAgent || p.user_agent;
  if (ua) {
    const m = String(ua).match(/Chrome\/(\d+)/);
    parts.push(m ? "Chrome " + m[1] : "Custom UA");
  }
  if (p.locale || p.language) parts.push(esc(p.locale || p.language));
  if (p.timezone) parts.push(esc(p.timezone));
  return parts.length ? parts.join(" · ") : NA;
}
function openModal(title, bodyHtml, footHtml, wide) {
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = bodyHtml;
  const foot = $("#modalFoot");
  if (footHtml) { foot.innerHTML = footHtml; foot.hidden = false; } else { foot.hidden = true; foot.innerHTML = ""; }
  $("#modal").classList.toggle("wide", !!wide);
  $("#modalBackdrop").hidden = false;
  const first = $("#modalBody").querySelector("input, select, textarea, button");
  if (first) setTimeout(() => first.focus(), 60);
}
function closeModal() { $("#modalBackdrop").hidden = true; $("#modalBody").innerHTML = ""; }
function openDrawer(title, bodyHtml) {
  $("#drawerTitle").textContent = title;
  $("#drawerBody").innerHTML = bodyHtml;
  $("#drawerBackdrop").hidden = false;
  $("#drawer").hidden = false;
}
function closeDrawer() { $("#drawer").hidden = true; $("#drawerBackdrop").hidden = true; }

/* ============================== Shared actions ============================== */
function apiErr(r, fallback) {
  return esc((r.data && (r.data.error || r.data.message)) || r.error || fallback || "unknown error");
}
async function actLaunch(ids) {
  if (!ids.length) return;
  const failed = [];
  for (const id of ids) {
    const r = await apiPost(`/api/profiles/${encodeURIComponent(id)}/launch`);
    if (!r.ok || !(r.data || {}).ok) {
      failed.push({ id, blocked: r.status === 409, data: r.data });
    }
  }
  if (!failed.length) {
    toast(`Launched ${ids.length} profile${ids.length > 1 ? "s" : ""}`, "ok");
  } else {
    const blocked = failed.filter((f) => f.blocked);
    if (blocked.length) {
      const names = blocked.map((f) => f.id).join(", ");
      const warn = (blocked[0].data && blocked[0].data.warnings || []).join("; ");
      if (!confirm(`Launch blocked by consistency checks for: ${names}\n${warn}\n\nForce launch anyway?`)) {
        invalidate("profiles");
        return;
      }
      for (const f of blocked) {
        const r = await apiPost(`/api/profiles/${encodeURIComponent(f.id)}/launch?force=1`);
        if (!r.ok || !(r.data || {}).ok) toast("Force launch failed for " + f.id + ": " + apiErr(r), "err");
      }
    }
    const other = failed.filter((f) => !f.blocked);
    other.forEach((f) => toast("Launch failed for " + f.id, "err"));
  }
  invalidate("profiles", "healthMap");
  if (currentRoute && currentRoute.page === "profiles") refreshProfilesTable(true);
}
async function actStop(ids) {
  if (!ids.length) return;
  for (const id of ids) {
    const r = await apiPost(`/api/profiles/${encodeURIComponent(id)}/stop`);
    if (!r.ok || !(r.data || {}).ok) toast("Stop failed for " + id + ": " + apiErr(r), "err");
  }
  toast(`Stopped ${ids.length} profile${ids.length > 1 ? "s" : ""}`, "ok");
  invalidate("profiles", "healthMap");
  if (currentRoute && currentRoute.page === "profiles") refreshProfilesTable(true);
}
async function actRestart(id) {
  const r1 = await apiPost(`/api/profiles/${encodeURIComponent(id)}/stop`);
  if (!r1.ok) { toast("Stop failed: " + apiErr(r1), "err"); return; }
  toast("Stopped — relaunching…");
  setTimeout(async () => {
    const r2 = await apiPost(`/api/profiles/${encodeURIComponent(id)}/launch`);
    if (r2.ok && (r2.data || {}).ok) toast("Relaunched", "ok");
    else if (r2.status === 409) toast("Relaunch blocked by consistency checks", "warn");
    else toast("Relaunch failed: " + apiErr(r2), "err");
    invalidate("profiles", "healthMap");
    if (currentRoute && currentRoute.page === "profiles") refreshProfilesTable(true);
  }, 1500);
}
async function actPark(ids, park) {
  if (!ids.length) return;
  for (const id of ids) {
    if (park) await apiPost(`/api/profiles/${encodeURIComponent(id)}/stop`);
    const r = await apiPut(`/api/profiles/${encodeURIComponent(id)}`, { status: park ? "parked" : "idle" });
    if (!r.ok) toast((park ? "Park" : "Unpark") + " failed for " + id + ": " + apiErr(r), "err");
  }
  toast(park ? `Parked ${ids.length} profile${ids.length > 1 ? "s" : ""}` : `Unparked ${ids.length} profile${ids.length > 1 ? "s" : ""}`, "ok");
  invalidate("profiles");
  if (currentRoute && currentRoute.page === "profiles") refreshProfilesTable(true);
}
async function actDelete(ids) {
  if (!ids.length) return;
  const label = ids.length > 1 ? `${ids.length} profiles` : `"${ids[0]}"`;
  if (!confirm(`Delete ${label}? Browser storage will be purged. This cannot be undone.`)) return;
  for (const id of ids) {
    const r = await apiDel(`/api/profiles/${encodeURIComponent(id)}`);
    if (!r.ok) toast("Delete failed for " + id + ": " + apiErr(r), "err");
    delete cache.fp[id]; delete cache.netHealth[id];
  }
  toast("Deleted", "ok");
  invalidate("profiles", "healthMap");
  if (currentRoute && currentRoute.page === "profiles") refreshProfilesTable(true);
  else route();
}
function exportProfiles(profiles) {
  const safe = profiles.map((p) => {
    const c = Object.assign({}, p);
    ["proxyPass", "proxy_pass", "proxyPassword", "proxy_password"].forEach((k) => delete c[k]);
    return c;
  });
  const blob = new Blob([JSON.stringify(safe, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "multiprofile-export.json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
async function refreshHealthPill() {
  const r = await apiGet("/api/health");
  const dot = $("#healthDot"), txt = $("#healthText");
  if (r.ok && r.data && r.data.ok) {
    dot.className = "dot ok";
    txt.textContent = "Online" + (r.data.version ? " · v" + r.data.version : "");
    $("#versionText").textContent = `multiprofile ${r.data.version || ""} · chrome ${r.data.chrome_ok ? "ok" : "missing"}`;
    cache.sysHealth = r.data;
  } else if (r.status === 401) {
    dot.className = "dot bad"; txt.textContent = "Auth required";
  } else {
    dot.className = "dot bad";
    txt.textContent = r.networkError ? "Unreachable" : "Error " + r.status;
  }
}

/* ============================== Navigation ============================== */
const NAV = [
  { id: "dashboard", label: "Dashboard", icon: IC.dash, route: "#/dashboard" },
  { id: "profiles", label: "Profiles", icon: IC.prof, route: "#/profiles" },
  { id: "devices", label: "Devices", icon: IC.dev, route: "#/devices" },
  { id: "fingerprint", label: "Fingerprint", icon: IC.fp, route: "#/fingerprint" },
  { id: "network", label: "Network", icon: IC.net, route: "#/network" },
  { id: "jobs", label: "Jobs", icon: IC.jobs, route: "#/jobs" },
  { id: "audit", label: "Audit Log", icon: IC.audit, route: "#/audit" },
  { id: "settings", label: "Settings", icon: IC.gear, route: "#/settings" },
];
function renderNav(activeId, counts) {
  $("#mainNav").innerHTML = NAV.map((n) => {
    const c = counts && counts[n.id] != null ? `<span class="nav-badge">${esc(counts[n.id])}</span>` : "";
    return `<a href="${n.route}" class="nav-item ${n.id === activeId ? "active" : ""}" data-nav="${n.id}">
      ${n.icon}<span>${esc(n.label)}</span>${c}</a>`;
  }).join("");
  $("#navCollapse").innerHTML = IC.chevL;
}
function toggleSidebar(collapse) {
  const app = $("#app");
  const willCollapse = collapse == null ? !app.classList.contains("nav-collapsed") : collapse;
  app.classList.toggle("nav-collapsed", willCollapse);
  $("#navExpand").hidden = !willCollapse;
  storeSet("mp_nav_collapsed", willCollapse ? "1" : "0");
}

/* ============================== Router ============================== */
const TITLES = {
  dashboard: ["Dashboard", "Live operational overview"],
  profiles: ["Profiles", "Launch, monitor and manage browser profiles"],
  profile: ["Profile", "Profile detail"],
  devices: ["Device Library", "Device presets for profile creation"],
  fingerprint: ["Fingerprint", "Per-profile fingerprint inspection"],
  network: ["Network", "Proxies, gateways and diagnostics"],
  jobs: ["Jobs", "Background job queue"],
  audit: ["Audit Log", "Action history"],
  settings: ["Settings", "Console and backend configuration"],
};
let currentRoute = null;

function parseHash() {
  const h = location.hash || "#/dashboard";
  const m = h.match(/^#\/([a-z]+)(?:\/([^?]*))?(\?(.*))?$/);
  if (!m) return { page: "dashboard", param: null, query: {} };
  const query = {};
  if (m[4]) m[4].split("&").forEach((kv) => {
    const [k, v] = kv.split("=");
    query[decodeURIComponent(k)] = decodeURIComponent(v || "");
  });
  return { page: m[1], param: m[2] ? decodeURIComponent(m[2]) : null, query };
}

async function route() {
  if (authFailed) { showLoginGate(); return; }
  const { page, param, query } = parseHash();
  currentRoute = { page, param, query };
  const known = Object.prototype.hasOwnProperty.call(TITLES, page);
  const key = known ? page : "dashboard";
  $("#pageTitle").textContent = TITLES[key][0];
  $("#pageSub").textContent = TITLES[key][1];
  renderNav(page === "profile" ? "profiles" : key);
  const view = $("#view");
  view.innerHTML = skelTable(4, 6);
  try {
    switch (page) {
      case "dashboard": await vDashboard(view); break;
      case "profiles": await vProfiles(view, query); break;
      case "profile": await vProfileDetail(view, param, query); break;
      case "devices": await vDevices(view, query); break;
      case "fingerprint": await vFingerprint(view, query); break;
      case "network": await vNetwork(view, query); break;
      case "jobs": await vJobs(view, query); break;
      case "audit": await vAudit(view, query); break;
      case "settings": await vSettings(view, query); break;
      default: await vDashboard(view);
    }
  } catch (e) {
    view.innerHTML = emptyState("View failed to render", "An unexpected error occurred: " + String((e && e.message) || e));
  }
  window.scrollTo(0, 0);
}

/* ============================== Login gate ============================== */
function showLoginGate() {
  $("#loginGate").hidden = false;
  $("#app").hidden = true;
}
function hideLoginGate() {
  $("#loginGate").hidden = true;
  $("#app").hidden = false;
}
function initAuth() {
  $("#btnAuth").onclick = () => {
    openModal("API access token",
      `<label class="field"><span class="field-label">Bearer token</span>
       <input type="password" id="authToken" value="${esc(apiToken)}" autocomplete="off">
       <span class="field-hint">Stored only in this browser's local storage.</span></label>`,
      `<button class="btn" id="authClear">Clear</button><button class="btn btn-primary" id="authSave">Save &amp; retry</button>`);
    $("#authSave").onclick = () => {
      apiToken = $("#authToken").value.trim();
      if (apiToken) storeSet(TOKEN_KEY, apiToken); else storeDel(TOKEN_KEY);
      authFailed = false;
      invalidate("profiles", "healthMap", "clients", "templates", "devices", "gateways", "jobs");
      closeModal(); hideLoginGate(); refreshHealthPill(); route();
    };
    $("#authClear").onclick = () => {
      apiToken = ""; storeDel(TOKEN_KEY);
      $("#authToken").value = ""; toast("Token cleared");
    };
  };
  $("#loginSubmit").onclick = () => {
    const t = $("#loginToken").value.trim();
    $("#loginError").hidden = true;
    if (!t) { $("#loginError").textContent = "Paste a token first."; $("#loginError").hidden = false; return; }
    apiToken = t; storeSet(TOKEN_KEY, t);
    authFailed = false; hideLoginGate(); refreshHealthPill(); route();
  };
  $("#loginSkip").onclick = () => { authFailed = false; hideLoginGate(); route(); };
}

/* ============================== Dashboard ============================== */
function statCard(label, value, hint, tone) {
  return `<div class="stat-card ${tone || ""}">
    <div class="stat-label">${esc(label)}</div>
    <div class="stat-num">${value == null ? '<span class="muted">—</span>' : esc(value)}</div>
    ${hint ? `<div class="stat-hint">${esc(hint)}</div>` : ""}
  </div>`;
}
/* Count via limit=1 so we only fetch totals, not full lists */
async function profileCount(status) {
  const qs = new URLSearchParams({ limit: "1" });
  if (status) qs.set("status", status);
  const r = await apiGet("/api/profiles?" + qs.toString());
  if (r.ok && r.data) {
    if (typeof r.data.total === "number") return r.data.total;
    if (Array.isArray(r.data)) return r.data.length;
    if (Array.isArray(r.data.items)) return r.data.items.length;
  }
  return null;
}

async function vDashboard(view) {
  view.innerHTML = `<div class="stat-grid" id="dashStats">${"<div class='stat-card'><div class='skel' style='height:64px'></div></div>".repeat(5)}</div>
    <div class="toolbar-row"><h2 class="section-title" style="margin:0">Recent activity</h2><span class="spacer"></span>
    <a class="btn btn-sm" href="#/audit">View audit log</a></div>
    <div class="table-card"><div class="table-scroll" id="dashAudit"></div></div>`;

  const [total, running, stopped, error] = await Promise.all([
    profileCount(null),
    profileCount("running"),
    profileCount("idle"),
    profileCount("error"),
  ]);

  let queued = null, jobsOk = true;
  try {
    const r = await apiGet("/api/jobs?limit=100");
    if (r.ok && r.data && r.data.ok) {
      const jobs = r.data.jobs || [];
      queued = jobs.filter((j) => ["queued", "pending", "running", "in_progress"].includes(String(j.status || "").toLowerCase())).length;
    } else if (r.status === 503) { jobsOk = false; }
    else if (!r.ok) { jobsOk = false; }
  } catch (e) { jobsOk = false; }

  const stats = [
    statCard("Total profiles", total, "GET /api/profiles"),
    statCard("Running", running, "status=running", "tone-ok"),
    statCard("Stopped", stopped, "idle / not running"),
    statCard("Error", error, "status=error", error ? "tone-err" : ""),
    statCard("Queued jobs", queued, jobsOk ? "GET /api/jobs" : "job system unavailable", jobsOk ? "tone-info" : "tone-warn"),
  ];
  $("#dashStats").innerHTML = stats.join("");

  /* Recent audit — honest empty state when unavailable */
  try {
    const r = await apiGet("/api/audit?limit=8");
    if (r.ok && r.data && Array.isArray(r.data.items) && r.data.items.length) {
      $("#dashAudit").innerHTML = `<table class="grid"><thead><tr>
        <th>Time</th><th>Action</th><th>Profile</th><th>Result</th></tr></thead><tbody>` +
        r.data.items.map(auditRow).join("") + "</tbody></table>";
    } else {
      $("#dashAudit").innerHTML = emptyState("No recent activity", "The audit log is empty or unavailable.");
    }
  } catch (e) {
    $("#dashAudit").innerHTML = emptyState("No recent activity", "Could not load the audit log.");
  }
}
function auditRow(a) {
  const ok = a.ok === true ? badge("ok", "green") : a.ok === false ? badge("failed", "red") : NA;
  return `<tr>
    <td class="muted small">${esc(fmtDate(a.ts || a.time || a.created))}</td>
    <td><span class="mono">${esc(a.action || a.type || "—")}</span></td>
    <td>${a.profile_id || a.profileId ? `<a href="#/profile/${encodeURIComponent(a.profile_id || a.profileId)}">${esc(shortId(a.profile_id || a.profileId))}</a>` : NA}</td>
    <td>${ok}</td>
  </tr>`;
}

/* ============================== Profiles ============================== */
const PAGE_SIZE = 25;
const pv = {
  q: "", status: "", clientId: "", tag: "",
  items: [], total: null, offset: 0, cursor: null, useCursor: false,
  selected: new Set(), metrics: {}, metricsLoading: false,
  initialized: false,
};

function pvQueryParams() {
  const p = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (pv.useCursor && pv.cursor) p.set("cursor", pv.cursor);
  else if (!pv.useCursor) p.set("offset", String(pv.offset));
  if (pv.q) p.set("q", pv.q);
  if (pv.status) p.set("status", pv.status);
  if (pv.clientId) p.set("clientId", pv.clientId);
  if (pv.tag) p.set("tag", pv.tag);
  return p.toString();
}

async function pvFetch() {
  const r = await apiGet("/api/profiles?" + pvQueryParams());
  if (!r.ok) {
    return { error: r.status === 401 ? "unauthorized" : "GET /api/profiles failed (" + r.status + ")" };
  }
  const d = r.data || {};
  const items = Array.isArray(d) ? d : (d.items || []);
  let nextCursor = null, total = null;
  if (!Array.isArray(d)) {
    if (typeof d.next_cursor === "string") { pv.useCursor = true; nextCursor = d.next_cursor || null; }
    if (typeof d.total === "number") total = d.total;
  }
  return { items, nextCursor, total };
}

async function vProfiles(view, query) {
  pv.q = query.q || pv.q || "";
  pv.status = query.status || "";
  pv.clientId = query.clientId || "";
  pv.selected = new Set();
  pv.offset = 0; pv.cursor = null; pv.useCursor = false; pv.metrics = {};
  pv.initialized = false;

  const [clients] = await Promise.all([loadClients(), loadDevices()]);

  view.innerHTML = `
    <div class="action-bar" id="pvBar">
      <span class="bar-title">Profiles</span>
      <span class="sel-count" id="selCount" hidden></span>
      <div class="btn-row" id="bulkBtns" hidden>
        <button class="btn btn-sm" data-bulk="start">${IC.play} Start</button>
        <button class="btn btn-sm" data-bulk="stop">${IC.stop} Stop</button>
        <button class="btn btn-sm" data-bulk="park">${IC.park} Park</button>
        <button class="btn btn-sm btn-danger" data-bulk="delete">${IC.trash} Delete</button>
      </div>
      <span class="spacer"></span>
      <div class="filter-row">
        <input type="search" id="pvSearch" placeholder="Search name, tag, UA…" value="${esc(pv.q)}" aria-label="Search profiles">
        <select id="pvStatus" aria-label="Status filter">
          <option value="">All states</option>
          <option value="running">Running</option>
          <option value="idle">Stopped</option>
          <option value="parked">Parked</option>
          <option value="error">Error</option>
        </select>
        <select id="pvGroup" aria-label="Group filter">
          <option value="">All groups</option>
          ${clients.map((c) => `<option value="${esc(c.id)}">${esc(c.name || c.id)}</option>`).join("")}
        </select>
        <button class="btn btn-sm" id="pvClear">Clear</button>
        <button class="btn btn-sm" id="pvExport" title="Download the currently listed profiles as JSON (proxy passwords stripped)">Export</button>
      </div>
    </div>
    <div id="pvTableWrap">${skelTable(8, 8)}</div>
    <div class="pager" id="pvPager" hidden>
      <button class="btn btn-sm" id="pvPrev">← Prev</button>
      <button class="btn btn-sm" id="pvNext">Next →</button>
      <span class="spacer"></span>
      <span id="pvPageInfo"></span>
    </div>`;

  $("#pvStatus").value = pv.status;
  $("#pvGroup").value = pv.clientId;

  const debSearch = debounce(() => { pv.q = $("#pvSearch").value.trim(); pvReset(); loadProfilesPage(); }, 350);
  $("#pvSearch").addEventListener("input", debSearch);
  $("#pvStatus").onchange = (e) => { pv.status = e.target.value; pvReset(); loadProfilesPage(); };
  $("#pvGroup").onchange = (e) => { pv.clientId = e.target.value; pvReset(); loadProfilesPage(); };
  $("#pvClear").onclick = () => {
    pv.q = ""; pv.status = ""; pv.clientId = ""; pv.tag = "";
    $("#pvSearch").value = ""; $("#pvStatus").value = ""; $("#pvGroup").value = "";
    pvReset(); loadProfilesPage();
  };
  $("#pvExport").onclick = () => exportProfiles(pv.items || []);
  $("#bulkBtns").addEventListener("click", (e) => {
    const b = e.target.closest("[data-bulk]");
    if (!b) return;
    const ids = Array.from(pv.selected);
    if (b.dataset.bulk === "start") actLaunch(ids);
    else if (b.dataset.bulk === "stop") actStop(ids);
    else if (b.dataset.bulk === "park") actPark(ids, true);
    else if (b.dataset.bulk === "delete") actDelete(ids);
  });
  $("#pvPrev").onclick = () => { pvPrevPage(); };
  $("#pvNext").onclick = () => { pvNextPage(); };

  pv.initialized = true;
  loadProfilesPage();
}
function pvReset() { pv.offset = 0; pv.cursor = null; pv.useCursor = false; pv.selected = new Set(); pv.metrics = {}; }

async function loadProfilesPage() {
  if (!pv.initialized) return;
  const wrap = $("#pvTableWrap");
  if (!wrap) return;
  wrap.innerHTML = skelTable(8, 8);
  const res = await pvFetch();
  if (res.error) {
    wrap.innerHTML = emptyState("Couldn't load profiles", res.error, `<button class="btn" onclick="loadProfilesPage()">Retry</button>`);
    return;
  }
  pv.items = res.items;
  pv.total = res.total;
  pv.nextCursor = res.nextCursor;
  renderProfilesTable();
  lazyLoadMetrics();
  updateBulkBar();
  updatePager();
}

function renderProfilesTable() {
  const wrap = $("#pvTableWrap");
  if (!wrap) return;
  if (!pv.items.length) {
    wrap.innerHTML = `<div class="table-card">${emptyState("No profiles found",
      "Adjust the filters, or create your first profile.",
      `<button class="btn btn-primary" onclick="openWizard()">New profile</button>`)}</div>`;
    $("#pvPager").hidden = true;
    return;
  }
  const rows = pv.items.map(profileRow).join("");
  wrap.innerHTML = `<div class="table-card"><div class="table-scroll"><table class="grid">
    <thead><tr>
      <th class="row-check"><input type="checkbox" id="pvAll" aria-label="Select all"></th>
      <th>Status</th><th>Profile</th><th>Group</th><th>Device</th>
      <th>Proxy</th><th>Fingerprint</th><th>CPU / Mem</th><th>Actions</th>
    </tr></thead><tbody id="pvBody">${rows}</tbody></table></div></div>`;
  const all = $("#pvAll");
  all.checked = pv.items.length > 0 && pv.items.every((p) => pv.selected.has(p.id));
  all.onchange = () => {
    pv.items.forEach((p) => { all.checked ? pv.selected.add(p.id) : pv.selected.delete(p.id); });
    $$("#pvBody tr").forEach((tr) => tr.classList.toggle("selected", pv.selected.has(tr.dataset.pid)));
    $$("#pvBody input.row-sel").forEach((c) => { c.checked = all.checked; });
    updateBulkBar();
  };
  $("#pvBody").addEventListener("change", (e) => {
    const c = e.target.closest("input.row-sel");
    if (!c) return;
    const tr = c.closest("tr");
    c.checked ? pv.selected.add(tr.dataset.pid) : pv.selected.delete(tr.dataset.pid);
    tr.classList.toggle("selected", c.checked);
    updateBulkBar();
  });
  $("#pvBody").addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const id = b.closest("tr").dataset.pid;
    const act = b.dataset.act;
    if (act === "start") actLaunch([id]);
    else if (act === "stop") actStop([id]);
    else if (act === "restart") actRestart(id);
    else if (act === "park") actPark([id], true);
    else if (act === "unpark") actPark([id], false);
    else if (act === "delete") actDelete([id]);
  });
}

function deviceBadgeFor(p) {
  const devId = p.deviceId || p.device_id;
  if (!devId) return NA;
  const d = deviceById(devId);
  if (!d) return badge(shortId(devId), "grey");
  return `<span title="${esc(d.manufacturer || "")} ${esc(d.model || "")}">${esc(d.name || devId)}</span> ${supportBadge(d.support)}`;
}

function profileRow(p) {
  const id = p.id;
  const running = isRunning(p);
  const parked = p.status === "parked";
  const group = clientName(p.clientId) || p.clientId || "";
  const sel = pv.selected.has(id) ? " selected" : "";
  const checked = pv.selected.has(id) ? " checked" : "";
  const m = pv.metrics[id];
  const metricHtml = m ? metricCell(m) : '<span class="muted metric-pending" data-metric>—</span>';
  return `<tr data-pid="${esc(id)}" class="${sel.trim()}">
    <td class="row-check"><input type="checkbox" class="row-sel" aria-label="Select ${esc(p.name || id)}"${checked}></td>
    <td data-cell="status">${runDot(p)}</td>
    <td><span class="profile-name"><a href="#/profile/${encodeURIComponent(id)}">${esc(p.name || id)}</a></span>
      <div class="small muted mono">${esc(shortId(id))}</div></td>
    <td>${group ? esc(group) : NA}</td>
    <td data-cell="device">${deviceBadgeFor(p)}</td>
    <td data-cell="proxy">${proxyLabel(p)}</td>
    <td class="small">${fpSummary(p)}</td>
    <td class="metric-cell" data-cell="metrics">${metricHtml}</td>
    <td><div class="row-actions">
      <button class="icon-btn" data-act="view" title="Open detail" onclick="location.hash='#/profile/${encodeURIComponent(id)}'">${IC.eye}</button>
      ${running
        ? `<button class="icon-btn" data-act="stop" title="Stop">${IC.stop}</button>
           <button class="icon-btn" data-act="restart" title="Restart">${IC.restart}</button>`
        : parked
          ? `<button class="icon-btn" data-act="unpark" title="Unpark">${IC.park}</button>`
          : `<button class="icon-btn" data-act="start" title="Start">${IC.play}</button>`}
      ${!running && !parked ? `<button class="icon-btn" data-act="park" title="Park">${IC.park}</button>` : ""}
      <button class="icon-btn danger" data-act="delete" title="Delete">${IC.trash}</button>
    </div></td>
  </tr>`;
}

function metricCell(m) {
  /* Backend contract (v1.7.0): {cpu_percent, memory_rss_mb, ...}; tolerate alternates */
  const cpu = m.cpu_percent != null ? Math.round(m.cpu_percent)
    : (m.cpu_pct != null ? Math.round(m.cpu_pct) : null);
  const mem = m.memory_rss_mb != null ? Math.round(m.memory_rss_mb) + " MB"
    : (m.mem_mb != null ? Math.round(m.mem_mb) + " MB"
    : (m.mem_bytes != null ? fmtBytes(m.mem_bytes) : null));
  if (cpu == null && mem == null) return NA;
  const bar = cpu != null ? `<span class="bar-mini"><i style="width:${Math.min(100, cpu)}%"></i></span>` : "";
  return `${bar}<span>${cpu != null ? cpu + "%" : "—"}</span> <span class="muted">/</span> <span>${mem || "—"}</span>`;
}
/* Metrics endpoint may not exist (404) — degrade cell to "—" */
async function lazyLoadMetrics() {
  if (pv.metricsLoading) return;
  pv.metricsLoading = true;
  const ids = pv.items.map((p) => p.id).filter((id) => !pv.metrics[id]);
  await Promise.all(ids.map(async (id) => {
    try {
      const r = await apiGet(`/api/profiles/${encodeURIComponent(id)}/metrics`);
      pv.metrics[id] = r.ok && r.data ? (r.data.metrics || r.data) : null;
    } catch (e) { pv.metrics[id] = null; }
  }));
  pv.metricsLoading = false;
  $$("#pvBody tr[data-pid]").forEach((tr) => {
    const cell = tr.querySelector('[data-cell="metrics"]');
    if (cell && cell.querySelector(".metric-pending")) cell.innerHTML = metricCell(pv.metrics[tr.dataset.pid]);
  });
}

function updateBulkBar() {
  const n = pv.selected.size;
  const cnt = $("#selCount"), bulk = $("#bulkBtns");
  if (!cnt || !bulk) return;
  cnt.hidden = n === 0; bulk.hidden = n === 0;
  cnt.textContent = n + " selected";
}
function updatePager() {
  const pager = $("#pvPager");
  if (!pager) return;
  const hasPrev = pv.useCursor ? pv.cursorStack && pv.cursorStack.length > 0 : pv.offset > 0;
  const hasNext = pv.useCursor ? !!pv.nextCursor : (pv.total == null || pv.offset + pv.items.length < pv.total);
  pager.hidden = !hasPrev && !hasNext && pv.total == null;
  $("#pvPrev").disabled = !hasPrev;
  $("#pvNext").disabled = !hasNext;
  const info = [];
  if (pv.total != null) {
    const from = pv.offset + 1, to = pv.offset + pv.items.length;
    info.push(`${from}–${to} of ${pv.total}`);
  } else if (pv.items.length) {
    info.push(`${pv.items.length} shown`);
  }
  info.push(`${PAGE_SIZE}/page`);
  $("#pvPageInfo").textContent = info.join(" · ");
}
async function pvPrevPage() {
  if (pv.useCursor && pv.cursorStack && pv.cursorStack.length) {
    pv.cursor = pv.cursorStack.pop();
  } else {
    pv.offset = Math.max(0, pv.offset - PAGE_SIZE);
  }
  pv.selected = new Set(); pv.metrics = {};
  await loadProfilesPage();
}
async function pvNextPage() {
  if (pv.useCursor) {
    pv.cursorStack = pv.cursorStack || [];
    pv.cursorStack.push(pv.cursor);
    pv.cursor = pv.nextCursor;
  } else {
    pv.offset += PAGE_SIZE;
  }
  pv.selected = new Set(); pv.metrics = {};
  await loadProfilesPage();
}
/* Used by action buttons rendered in HTML strings */
async function refreshProfilesTable(keepSelection) {
  if (currentRoute && currentRoute.page === "profiles" && pv.initialized) {
    if (!keepSelection) pv.selected = new Set();
    await loadProfilesPage();
  }
}
/* Live-update a single row in place (status events) */
function patchProfileRow(id, patch) {
  const tr = document.querySelector(`#pvBody tr[data-pid="${CSS.escape(id)}"]`);
  if (!tr) return;
  const idx = pv.items.findIndex((p) => p.id === id);
  if (idx >= 0) pv.items[idx] = Object.assign({}, pv.items[idx], patch);
  const p = idx >= 0 ? pv.items[idx] : Object.assign({ id }, patch);
  const statusCell = tr.querySelector('[data-cell="status"]');
  if (statusCell) statusCell.innerHTML = runDot(p);
  if (currentRoute && currentRoute.page === "profiles") updatePager();
}

/* ============================== Detail helpers ============================== */
async function loadFp(profileId, force) {
  if (!force && cache.fp[profileId]) return cache.fp[profileId];
  const r = await apiGet(`/api/profiles/${encodeURIComponent(profileId)}/fingerprint`);
  cache.fp[profileId] = r.ok ? r.data : null;
  return cache.fp[profileId];
}
async function loadNetHealth(profileId, force) {
  if (!force && cache.netHealth[profileId]) return cache.netHealth[profileId];
  const r = await apiGet(`/api/profiles/${encodeURIComponent(profileId)}/network-health`);
  cache.netHealth[profileId] = r.ok ? r.data : null;
  return cache.netHealth[profileId];
}
async function loadJobs(force) {
  if (!force && cache.jobs) return cache.jobs;
  const r = await apiGet("/api/jobs?limit=100");
  cache.jobs = (r.ok && r.data && Array.isArray(r.data.jobs)) ? r.data.jobs : [];
  return cache.jobs;
}

/* ============================== Fingerprint inspector ============================== */
/* Renders diagnostic sections. The backend returns `components`: an array of
 * section objects {name, expected, runtime, status, note}. When the profile is
 * not running, runtime is null and we show an honest "Not running" state. */
function flattenFp(obj, prefix, out) {
  out = out || [];
  if (obj == null) return out;
  if (Array.isArray(obj)) {
    const s = obj.map((x) => (typeof x === "object" && x !== null)
      ? Object.entries(x).map(([k, v]) => `${k}=${v}`).join(", ")
      : String(x)).join("; ");
    out.push([prefix, s || "—"]);
    return out;
  }
  if (typeof obj !== "object") { out.push([prefix, String(obj)]); return out; }
  const keys = Object.keys(obj);
  if (!keys.length) { out.push([prefix, "—"]); return out; }
  keys.forEach((k) => {
    const v = obj[k];
    const path = prefix ? prefix + "." + k : k;
    if (v != null && typeof v === "object") flattenFp(v, path, out);
    else out.push([path, v]);
  });
  return out;
}
function lookupPath(obj, path) {
  if (!obj || !path) return undefined;
  let cur = obj;
  for (const part of path.split(".")) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = cur[part];
  }
  return cur;
}
function fmtFpVal(v) {
  if (v === undefined || v === null || v === "") return '<span class="muted">—</span>';
  if (typeof v === "object") return esc(JSON.stringify(v));
  return esc(String(v));
}
function fpSections(fp, components) {
  const sec = (title, rowsHtml, note) => `<div class="fp-sec"><h4>${esc(title)}</h4>
    ${note ? `<p class="note" style="margin:0 0 8px">${esc(note)}</p>` : ""}
    <div class="table-card"><div class="table-scroll"><table class="fp-table">
    <tbody>${rowsHtml}</tbody></table></div></div></div>`;

  if (Array.isArray(components) && components.length) {
    return components.map((c) => {
      const rows = flattenFp(c.expected, "");
      const rt = c.runtime;
      const body = rows.length ? rows.map(([prop, exp]) =>
        `<tr><td>${esc(prop)}</td><td>${fmtFpVal(exp)}</td>` +
        `<td>${rt ? fmtFpVal(lookupPath(rt, prop)) : '<span class="muted">Not running</span>'}</td>` +
        `<td>${statusBadge(c.status)}</td></tr>`).join("")
        : `<tr><td colspan="4" class="muted">No expected values exposed for this section.</td></tr>`;
      return sec(c.name || "Section", `<tr><th style="text-align:left">Property</th><th style="text-align:left">Expected</th><th style="text-align:left">Runtime</th><th style="text-align:left">Status</th></tr>` + body, c.note);
    }).join("");
  }
  /* Legacy fallback: flat fingerprint dict without section components. */
  const f = fp || {};
  const scr = f.screen || {};
  const ch = f.clientHints || {};
  const gpu = f.gpu || {};
  const media = f.mediaDeviceIds || {};
  const row = (prop, expected) =>
    `<tr><td>${esc(prop)}</td><td>${fmtFpVal(expected)}</td>` +
    `<td><span class="muted">Not running</span></td><td>${statusBadge("UNKNOWN")}</td></tr>`;
  const legacy = (title, rowsHtml) => sec(title, rowsHtml, "Legacy report format — runtime values unavailable.");
  return (
    legacy("Navigator", [
      row("userAgent", f.userAgent), row("platform", f.platform),
      row("isMobile", f.isMobile), row("hardwareConcurrency", f.cores),
      row("deviceMemory (GB)", f.memory),
    ].join("")) +
    legacy("Screen", [
      row("width × height", scr.w != null ? `${scr.w}×${scr.h}` : null),
      row("availHeight", scr.availH), row("devicePixelRatio", scr.dpr),
      row("touch points", f.touch),
    ].join("")) +
    legacy("Client Hints", [
      row("platform", ch.platform || f.ch_platform),
      row("mobile", ch.mobile != null ? ch.mobile : (f.touch > 0)),
      row("architecture", ch.architecture || f.architecture),
      row("bitness", ch.bitness || f.bitness),
      row("platformVersion", ch.platformVersion || f.platformVersion),
      row("model", ch.model || f.model),
      row("uaFullVersion", ch.uaFullVersion),
      row("brands", (ch.brands || f.ch_brands || []).map((b) => `${b.brand} ${b.version}`).join("; ")),
    ].join("")) +
    legacy("Canvas", [row("seed", f.seed)].join("")) +
    legacy("WebGL", [row("vendor", gpu.vendor), row("renderer", gpu.renderer)].join("")) +
    legacy("Audio / Media", [
      row("audioinput id", media.audioinput), row("audiooutput id", media.audiooutput),
      row("videoinput id", media.videoinput),
    ].join("")) +
    legacy("Network", [row("webrtc policy", f.webrtcPolicy || f.webrtcMode)].join("")) +
    legacy("Timezone", [row("timezone", f.timezone)].join("")) +
    legacy("Language", [
      row("language", f.language), row("languages", (f.languages || []).join(", ")),
    ].join("")) +
    legacy("Hardware", [
      row("cores", f.cores), row("memory (GB)", f.memory),
      row("gpu vendor", gpu.vendor), row("gpu renderer", gpu.renderer),
    ].join(""))
  );
}

async function renderFpInspector(container, profileId) {
  container.innerHTML = `<div class="skel" style="height:120px">&nbsp;</div>`;
  const rep = await loadFp(profileId);
  if (!rep) {
    container.innerHTML = emptyState("Fingerprint unavailable",
      "GET /api/profiles/" + profileId + "/fingerprint did not return a report.");
    return;
  }
  const fp = rep.fingerprint || {};
  const warnings = rep.warnings || [];
  const components = Array.isArray(rep.components) ? rep.components : null;
  container.innerHTML = `
    ${warnings.length ? `<div class="notice">
      <div><strong>${warnings.length} warning${warnings.length > 1 ? "s" : ""}:</strong><br>${warnings.map((w) => "• " + esc(w)).join("<br>")}</div></div>` : ""}
    <p class="note">${esc(rep.summary || "")}${rep.generated_at ? " · generated " + esc(rep.generated_at) : ""}</p>
    ${fpSections(fp, components)}`;
}

/* ============================== Profile detail ============================== */
const DETAIL_TABS = ["overview", "network", "fingerprint", "device", "cookies", "storage", "runtime", "health", "automation", "audit"];

async function vProfileDetail(view, id, query) {
  const r = await apiGet("/api/profiles?limit=500");
  const profiles = r.ok ? (Array.isArray(r.data) ? r.data : (r.data.items || [])) : [];
  const p = profiles.find((x) => x.id === id);
  if (!p) {
    view.innerHTML = emptyState("Profile not found", "It may have been deleted.",
      `<a class="btn btn-primary" href="#/profiles">Back to profiles</a>`);
    return;
  }
  const tab = DETAIL_TABS.includes(query.tab) ? query.tab : "overview";
  const running = isRunning(p);
  view.innerHTML = `
    <div class="detail-head">
      <a class="btn btn-sm btn-ghost" href="#/profiles">‹ Profiles</a>
      <div><h2>${esc(p.name || p.id)}</h2>
        <span class="mono muted small">${esc(p.id)}</span></div>
      <span class="spacer"></span>
      ${statusBadge(p.status || (running ? "running" : "stopped"))}
      <div class="btn-row">
        ${running
          ? `<button class="btn btn-sm" id="dStop">${IC.stop} Stop</button>
             <button class="btn btn-sm" id="dRestart">${IC.restart} Restart</button>`
          : `<button class="btn btn-sm" id="dLaunch">${IC.play} Launch</button>`}
        <button class="btn btn-sm" id="dEdit">Edit</button>
        <button class="btn btn-sm" id="dClone">Clone</button>
        <button class="btn btn-sm btn-danger" id="dDelete">${IC.trash} Delete</button>
      </div>
    </div>
    <div class="tabs">${DETAIL_TABS.map((t) =>
      `<button class="tab ${t === tab ? "active" : ""}" data-tab="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</div>
    <div id="tabBody"></div>`;

  $$("[data-tab]", view).forEach((b) => {
    b.onclick = () => { location.hash = `#/profile/${encodeURIComponent(id)}?tab=${b.dataset.tab}`; };
  });
  const dl = $("#dLaunch"); if (dl) dl.onclick = () => actLaunch([id]);
  const ds = $("#dStop"); if (ds) ds.onclick = () => actStop([id]);
  const dr = $("#dRestart"); if (dr) dr.onclick = () => actRestart(id);
  $("#dEdit").onclick = () => openProfileEdit(p);
  $("#dClone").onclick = () => openCloneModal([id]);
  $("#dDelete").onclick = () => actDelete([id]);

  const body = $("#tabBody");
  body.innerHTML = `<div class="skel" style="height:160px">&nbsp;</div>`;
  const tabs = {
    overview: () => dOverview(body, p),
    network: () => dNetwork(body, p),
    fingerprint: () => dFingerprint(body, p),
    device: () => dDevice(body, p),
    cookies: () => dCookies(body, p),
    storage: () => dStorage(body, p),
    runtime: () => dRuntime(body, p),
    health: () => dHealth(body, p),
    automation: () => dAutomation(body, p),
    audit: () => dAudit(body, p),
  };
  await tabs[tab]();
}

function kvRows(pairs) {
  return `<dl class="kv">${pairs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
}
async function dOverview(body, p) {
  await loadClients();
  body.innerHTML = `<div class="form-grid">
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Identity</h3>${kvRows([
      ["Name", val(p.name)], ["ID", `<span class="mono">${esc(p.id)}</span>`],
      ["Client", val(clientName(p.clientId) || p.clientId)],
      ["Tags", (p.tags || []).length ? (p.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("") : NA],
      ["Notes", val(p.notes)], ["Start URL", val(p.startUrl)],
      ["Created", val(p.createdAt)], ["Color", p.color ? `<span class="mono">${esc(p.color)}</span>` : NA],
    ])}</div></div>
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Configuration</h3>${kvRows([
      ["Device", p.deviceId ? `${esc(p.deviceId)} ${supportBadge((deviceById(p.deviceId) || {}).support)}` : NA],
      ["Network mode", val(p.networkMode)],
      ["Proxy", proxyLabel(p)], ["Language", val(p.language)],
      ["Timezone", val(p.timezone)], ["WebRTC", val(p.webrtcMode)],
      ["Seed", p.seed != null ? `<span class="mono">${esc(p.seed)}</span>` : NA],
    ])}</div></div>
  </div>`;
}
async function dNetwork(body, p) {
  body.innerHTML = `<div class="skel" style="height:100px">&nbsp;</div>`;
  const nh = await loadNetHealth(p.id, true);
  /* New contract: POST /api/profiles/<id>/network/test (tolerate 404) */
  let nt = null;
  const r = await apiPost(`/api/profiles/${encodeURIComponent(p.id)}/network/test`, {});
  if (r.ok) nt = r.data;

  const ph = nh && nh.proxyHealth;
  body.innerHTML = `
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Connection</h3>${kvRows([
      ["Mode", val(nh && nh.mode)],
      ["Status", nh ? (nh.status === "DIRECT" ? badge("Direct", "blue") : (nh.ok ? badge("OK", "green") : badge("Failed", "red"))) : NA],
      ["Egress IP", ph ? val(ph.egress_ip || ph.egressIp) : NA],
      ["Latency", ph && ph.latency_ms != null ? `<span class="num">${esc(ph.latency_ms)} ms</span>` : NA],
      ["Message", val(nh && nh.message)],
    ])}
    <p class="note">Source: GET /api/profiles/:id/network-health${nt ? " + POST /api/profiles/:id/network/test" : " (new network/test endpoint not available — showing existing network-health)"}.</p>
    <div class="btn-row" style="margin-top:10px">
      <button class="btn btn-sm" id="nTest">Re-check</button>
      ${p.proxyHost ? `<button class="btn btn-sm" id="nAdhoc">Test proxy config</button>` : ""}
    </div></div></div>
    ${nt ? `<div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Extended network test</h3>${kvRows([
      ["Health", nt.health ? statusBadge(nt.health) : NA],
      ["Egress IP", val(nt.egress_ip)], ["Country", val(nt.country)],
      ["Timezone", val(nt.timezone)], ["Latency", nt.latency_ms != null ? `<span class="num">${esc(nt.latency_ms)} ms</span>` : NA],
      ["DNS", nt.dns ? statusBadge(nt.dns.status) + " " + val(nt.dns.detail) : NA],
      ["IPv6", nt.ipv6 ? statusBadge(nt.ipv6.status) + " " + val(nt.ipv6.detail) : NA],
      ["Error", val(nt.error)],
    ])}</div></div>` : ""}`;
  $("#nTest").onclick = () => dNetwork(body, p);
  const adhoc = $("#nAdhoc");
  if (adhoc) adhoc.onclick = async () => {
    const t = await apiPost("/api/proxy/test", { host: p.proxyHost, port: p.proxyPort, username: p.proxyUser, password: p.proxyPass, protocol: p.proxyType || "http" });
    toast(t.ok && t.data && (t.data.ok || t.data.success) ? "Proxy test passed" : "Proxy test failed: " + apiErr(t), t.ok ? "ok" : "err");
  };
}
async function dFingerprint(body, p) { await renderFpInspector(body, p.id); }
async function dDevice(body, p) {
  body.innerHTML = `<div class="skel" style="height:80px">&nbsp;</div>`;
  let detail = deviceById(p.deviceId), failed = false;
  if (p.deviceId && !detail) {
    const r = await apiGet(`/api/devices/${encodeURIComponent(p.deviceId)}`);
    if (r.ok) detail = r.data; else failed = true;
  }
  body.innerHTML = `
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Assigned device</h3>${kvRows([
      ["Device ID", val(p.deviceId)],
      ["Support", detail ? supportBadge(detail.support) : NA],
      ["Validation date", detail ? val(detail.validation_date) : NA],
    ])}
    ${!p.deviceId ? `<p class="note">No device preset assigned to this profile.</p>` : ""}
    ${failed ? `<p class="note">GET /api/devices/${esc(p.deviceId)} failed — device library may be unavailable.</p>` : ""}
    </div></div>
    ${detail ? deviceDetailHtml(detail) : ""}`;
}
function deviceDetailHtml(d) {
  const cm = d.capability_matrix || {};
  const rows = Object.entries(cm).map(([k, v]) =>
    `<tr><td class="prop">${esc(k)}</td><td>${statusBadge(v.status)}</td><td class="muted">${esc(v.note || "—")}</td></tr>`).join("");
  return `<div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Capability matrix</h3>
    ${rows ? `<div class="table-card"><table class="grid"><thead><tr><th>Component</th><th>Status</th><th>Note</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="muted">No capability data.</p>`}
    </div></div>
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Limitations</h3>
    ${(d.limitations || []).length ? "<ul>" + d.limitations.map((l) => `<li>${esc(l)}</li>`).join("") + "</ul>" : `<p class="muted">None reported.</p>`}
    </div></div>
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Specification</h3>${kvRows([
      ["Manufacturer", val(d.manufacturer)], ["Model", val(d.model)],
      ["Platform", val(d.platform || d.os)], ["Category", val(d.category)],
      ["Viewport", d.viewport ? `<span class="mono">${esc(d.viewport.width)}×${esc(d.viewport.height)}</span>` : NA],
      ["Device scale factor", val(d.device_scale_factor)],
      ["Touch", d.touch == null ? NA : (d.touch ? "yes" : "no")],
      ["Mobile", d.mobile == null ? NA : (d.mobile ? "yes" : "no")],
    ])}</div></div>`;
}
async function dCookies(body, p) {
  body.innerHTML = `<div class="skel" style="height:100px">&nbsp;</div>`;
  const r = await apiGet(`/api/profiles/${encodeURIComponent(p.id)}/cookies`);
  const cookies = (r.ok && Array.isArray(r.data)) ? r.data : (r.ok && r.data && Array.isArray(r.data.cookies)) ? r.data.cookies : [];
  body.innerHTML = `
    <div class="toolbar">
      <div class="spacer"></div>
      <button class="btn btn-sm" id="ckExport">Export</button>
      <button class="btn btn-sm" id="ckImport">Import</button>
    </div>
    ${cookies.length ? `<div class="table-card"><div class="table-scroll"><table class="grid"><thead><tr>
      <th>Name</th><th>Value</th><th>Domain</th><th>Path</th><th>Expires</th><th>Secure</th><th>HttpOnly</th></tr></thead>
      <tbody>${cookies.map((c) => `<tr>
        <td class="mono">${esc(c.name || "")}</td>
        <td class="mono muted">${esc(String(c.value || "").slice(0, 40))}${String(c.value || "").length > 40 ? "…" : ""}</td>
        <td class="mono">${esc(c.domain || "")}</td><td class="mono">${esc(c.path || "")}</td>
        <td class="muted">${esc(c.expires || c.expirationDate || "")}</td>
        <td>${c.secure ? "yes" : "—"}</td><td>${c.httpOnly || c.httponly ? "yes" : "—"}</td>
      </tr>`).join("")}</tbody></table></div></div>`
      : emptyState("No cookies", "This profile has no stored cookies, or the endpoint returned none.")}
    <p class="note">Source: GET /api/profiles/:id/cookies. Cookie values are truncated in this view.</p>`;
  $("#ckExport").onclick = async () => {
    const e = await apiGet(`/api/profiles/${encodeURIComponent(p.id)}/cookies/export`);
    if (e.ok) {
      const blob = new Blob([JSON.stringify(e.data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = `${p.id}-cookies.json`; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    } else toast("Export failed", "err");
  };
  $("#ckImport").onclick = () => {
    openModal("Import cookies", `
      <label class="field"><span class="field-label">Paste cookies JSON (array)</span>
        <textarea id="ckJson" rows="8" placeholder='[{"name":"...","value":"...","domain":"..."}]'></textarea></label>`,
      `<button class="btn" id="mCancel">Cancel</button><button class="btn btn-primary" id="mImport">Import</button>`);
    $("#mCancel").onclick = closeModal;
    $("#mImport").onclick = async () => {
      let arr;
      try { arr = JSON.parse($("#ckJson").value); } catch (e) { toast("Invalid JSON", "err"); return; }
      const im = await apiPost(`/api/profiles/${encodeURIComponent(p.id)}/cookies/import`, { cookies: arr });
      closeModal();
      toast(im.ok ? "Cookies imported" : "Import failed", im.ok ? "ok" : "err");
      dCookies(body, p);
    };
  };
}
async function dStorage(body, p) {
  const h = (await loadHealthMap())[p.id];
  body.innerHTML = `<div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Storage</h3>${kvRows([
    ["User data dir", h && h.user_data_dir ? `<span class="mono">${esc(h.user_data_dir)}</span>` : NA],
    ["Storage accessible", h ? (h.storage_accessible ? badge("yes", "green") : badge("no", "red")) : NA],
  ])}
  <p class="note">The backend does not expose per-profile disk usage; only accessibility and the data directory are available.</p></div></div>`;
}
async function dRuntime(body, p) {
  const h = (await loadHealthMap(true))[p.id];
  body.innerHTML = `<div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Runtime</h3>${kvRows([
    ["Launch state", h ? badge(h.launch_state || "—", h.launch_state === "RUNNING" ? "green" : "grey") : NA],
    ["Process alive", h ? (h.process_alive ? badge("yes", "green") : badge("no", "grey")) : NA],
    ["PID", h ? num(h.pid) : NA],
    ["Uptime", h ? val(fmtDur(h.uptime_s)) : NA],
    ["CDP alive", h ? (h.cdp_alive ? badge("yes", "green") : badge("no", "grey")) : NA],
    ["CDP port", h ? num(h.cdp_port) : NA],
    ["Last launch", h ? val(timeAgo(h.last_launch ? h.last_launch * 1000 : null)) : NA],
    ["Last error", h ? val(h.last_error) : NA],
    ["Checked at", h && h.checked_at ? esc(new Date(h.checked_at * 1000).toLocaleString()) : NA],
  ])}
  <p class="note">Source: GET /api/profiles/:id/health.</p></div></div>`;
}
async function dHealth(body, p) {
  body.innerHTML = `<div class="skel" style="height:100px">&nbsp;</div>`;
  const r = await apiGet(`/api/profiles/${encodeURIComponent(p.id)}/health`);
  const h = r.ok ? r.data : null;
  /* New contract: GET /api/profiles/<id>/consistency (tolerate 404) */
  const cr = await apiGet(`/api/profiles/${encodeURIComponent(p.id)}/consistency`);
  const cons = cr.ok ? cr.data : null;
  body.innerHTML = `
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Health snapshot</h3>${h ? kvRows([
      ["Overall", h.overallOk ? badge("healthy", "green") : badge(h.status || "unknown", h.status === "proxy_failed" ? "red" : "amber")],
      ["Network mode", val(h.network_mode)],
      ["Proxy OK", h.proxy_ok == null ? NA : (h.proxy_ok ? badge("yes", "green") : badge("no", "red"))],
      ["Egress IP", val(h.egress_ip)],
      ["Fingerprint loaded", h.fingerprint_loaded ? badge("yes", "green") : badge("no", "amber")],
      ["Extension loaded", h.extension_loaded == null ? NA : (h.extension_loaded ? badge("yes", "green") : badge("no", "amber"))],
    ]) : `<p class="muted">Health endpoint did not return data.</p>`}
    <div class="btn-row" style="margin-top:10px"><button class="btn btn-sm" id="hRefresh">Refresh</button></div></div></div>
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Consistency checks</h3>
    ${cons && Array.isArray(cons.checks) ? `
      <div style="margin-bottom:10px">${statusBadge(cons.status)} <span class="muted">overall</span></div>
      <div class="table-card"><table class="grid"><thead><tr><th>Domain</th><th>Status</th><th>Expected</th><th>Actual</th><th>Message</th></tr></thead>
      <tbody>${cons.checks.map((c) => `<tr><td class="prop">${esc(c.domain)}</td><td>${statusBadge(c.status)}</td>
        <td>${fmtFpVal(c.expected)}</td><td>${fmtFpVal(c.actual)}</td><td>${esc(c.message || "—")}</td></tr>`).join("")}</tbody></table></div>
      <p class="note">Source: GET /api/profiles/:id/consistency.</p>`
      : `<p class="muted">Consistency endpoint not available for this profile (GET /api/profiles/:id/consistency returned ${cr.status}).</p>`}
    </div></div>`;
  $("#hRefresh").onclick = () => dHealth(body, p);
}
async function dAutomation(body, p) {
  const jobs = (await loadJobs(true)).filter((j) => (j.profileIds || []).includes(p.id));
  body.innerHTML = jobs.length ? `<div class="table-card"><div class="table-scroll"><table class="grid"><thead><tr>
    <th>Job</th><th>Type</th><th>Status</th><th>Progress</th><th>Created</th><th></th></tr></thead><tbody>
    ${jobs.map((j) => `<tr><td class="mono">${esc(j.id)}</td><td>${esc(j.type || "")}</td>
      <td>${jobStatusBadge(j.status)}</td>
      <td>${j.progress != null ? `<div class="progress" style="min-width:90px"><i style="width:${Math.min(100, j.progress)}%"></i></div>` : NA}</td>
      <td class="muted">${esc(timeAgo(j.created_at || j.createdAt))}</td>
      <td><button class="btn btn-xs btn-danger" data-cancel="${esc(j.id)}">Cancel</button></td></tr>`).join("")}
    </tbody></table></div></div>`
    : emptyState("No jobs for this profile", "Queue a launch or stop from the Profiles page, or create one below.");
  body.insertAdjacentHTML("beforeend", `
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Queue a job</h3>
    <div class="btn-row">
      <button class="btn btn-sm" id="aLaunch">${IC.play} Launch job</button>
      <button class="btn btn-sm" id="aStop">${IC.stop} Stop job</button>
    </div></div></div>`);
  $$("[data-cancel]", body).forEach((b) => b.onclick = async () => {
    const r = await apiPost(`/api/jobs/${encodeURIComponent(b.dataset.cancel)}/cancel`, {});
    toast(r.ok ? "Job cancelled" : "Cancel failed", r.ok ? "ok" : "err");
    dAutomation(body, p);
  });
  $("#aLaunch").onclick = async () => { await actLaunch([p.id]); dAutomation(body, p); };
  $("#aStop").onclick = async () => { await actStop([p.id]); dAutomation(body, p); };
}
async function dAudit(body, p) {
  const r = await apiGet("/api/audit?limit=200");
  const items = (r.ok && r.data && Array.isArray(r.data.items)) ? r.data.items
    .filter((e) => e.profileId === p.id || (e.detail && JSON.stringify(e.detail).includes(p.id))) : [];
  body.innerHTML = items.length ? `<div class="table-card"><div class="table-scroll"><table class="grid"><thead><tr>
    <th>Time</th><th>Action</th><th>Result</th><th>Detail</th></tr></thead><tbody>
    ${items.map((e) => `<tr><td class="muted small" style="white-space:nowrap">${esc(e.ts || "")}</td>
      <td class="mono">${esc(e.action || "")}</td>
      <td>${e.ok ? badge("ok", "green") : badge("fail", "red")}</td>
      <td class="mono muted small">${esc(JSON.stringify(e.detail ?? "")).slice(0, 120)}</td></tr>`).join("")}
    </tbody></table></div></div>`
    : emptyState("No audit entries", "No recorded actions reference this profile.");
}
function openProfileEdit(p) {
  openModal("Edit profile — " + (p.name || p.id), `
    <div class="form-grid">
      <label class="field"><span class="field-label">Name</span><input type="text" id="eName" value="${esc(p.name || "")}"></label>
      <label class="field"><span class="field-label">Start URL</span><input type="url" id="eUrl" value="${esc(p.startUrl || "")}"></label>
      <label class="field"><span class="field-label">Tags (comma separated)</span><input type="text" id="eTags" value="${esc((p.tags || []).join(", "))}"></label>
      <label class="field"><span class="field-label">Notes</span><input type="text" id="eNotes" value="${esc(p.notes || "")}"></label>
      <label class="field"><span class="field-label">Device ID</span><input type="text" id="eDevice" value="${esc(p.deviceId || "")}"></label>
      <label class="field"><span class="field-label">Language</span><input type="text" id="eLang" value="${esc(p.language || "")}"></label>
      <label class="field"><span class="field-label">Timezone</span><input type="text" id="eTz" value="${esc(p.timezone || "")}"></label>
    </div>`,
    `<button class="btn" id="mCancel">Cancel</button><button class="btn btn-primary" id="mSave">Save</button>`);
  $("#mCancel").onclick = closeModal;
  $("#mSave").onclick = async () => {
    const payload = {
      name: $("#eName").value.trim(),
      startUrl: $("#eUrl").value.trim(),
      tags: $("#eTags").value.split(",").map((s) => s.trim()).filter(Boolean),
      notes: $("#eNotes").value, deviceId: $("#eDevice").value.trim(),
      language: $("#eLang").value.trim(), timezone: $("#eTz").value.trim(),
    };
    const r = await apiPut(`/api/profiles/${encodeURIComponent(p.id)}`, payload);
    closeModal();
    if (r.ok && r.data && r.data.ok) {
      toast("Profile updated" + ((r.data.warnings || []).length ? ` (${r.data.warnings.length} fingerprint warning(s))` : ""), "ok");
      invalidate("profiles"); route();
    } else toast("Update failed: " + apiErr(r), "err");
  };
}
async function openCloneModal(ids) {
  openModal("Clone profile",
    `<label class="field"><span class="field-label">New name</span><input type="text" id="clName" placeholder="Copy of …"></label>
     <label class="checkbox"><input type="checkbox" id="clCookies"> Copy stored cookies</label>`,
    `<button class="btn" id="mCancel">Cancel</button><button class="btn btn-primary" id="mClone">Clone</button>`);
  $("#mCancel").onclick = closeModal;
  $("#mClone").onclick = async () => {
    const name = $("#clName").value.trim();
    const copyCookies = $("#clCookies").checked;
    closeModal();
    for (const id of ids) {
      const r = await apiPost(`/api/profiles/${encodeURIComponent(id)}/duplicate`, { name, copyCookies });
      if (!(r.ok && r.data && r.data.ok)) toast("Clone failed for " + id + ": " + apiErr(r), "err");
    }
    toast("Cloned", "ok");
    invalidate("profiles");
    if (currentRoute && currentRoute.page === "profiles") refreshProfilesTable(true);
  };
}

/* ============================== Profile creation wizard ============================== */
const WIZ_STEPS = ["Basic", "Device", "Network", "Fingerprint", "Consistency", "Review"];
let wiz = null;

function openWizard() {
  wiz = {
    step: 0,
    basic: { name: "", clientId: "", tags: "", notes: "", startUrl: "https://example.com" },
    deviceTab: "desktop", deviceId: null,
    net: { mode: "direct", host: "", port: "", user: "", pass: "", gatewayId: "" },
    fpMode: "stable",
    netTest: null,
  };
  renderWizard();
}
function wizardShell(inner, foot) {
  const steps = WIZ_STEPS.map((s, i) =>
    `<div class="wiz-step ${i === wiz.step ? "now" : i < wiz.step ? "done" : ""}"><span class="n">${i + 1}</span>${s}</div>`).join("");
  openModal("New Profile", `<div class="wizard-steps">${steps}</div><div id="wizBody">${inner}</div>`, foot, true);
}
function wizFoot(back, nextLabel) {
  return `${back ? `<button class="btn" id="wBack">‹ Back</button>` : ""}
    <button class="btn" id="wCancel">Cancel</button>
    <button class="btn btn-primary" id="wNext">${esc(nextLabel)}</button>`;
}
function bindWizFoot(back, nextFn) {
  $("#wCancel").onclick = () => { closeModal(); wiz = null; };
  if (back) $("#wBack").onclick = () => { wiz.step--; renderWizard(); };
  $("#wNext").onclick = nextFn;
}
function renderWizard() {
  const s = wiz.step;
  if (s === 0) wBasic();
  else if (s === 1) wDevice();
  else if (s === 2) wNetwork();
  else if (s === 3) wFingerprint();
  else if (s === 4) wConsistency();
  else wReview();
}

/* Step 1 — Basic */
async function wBasic() {
  const clients = await loadClients();
  wizardShell(`
    <div class="form-grid">
      <label class="field"><span class="field-label">Profile name *</span><input type="text" id="wbName" value="${esc(wiz.basic.name)}" placeholder="e.g. shopper-01"></label>
      <label class="field"><span class="field-label">Client</span><select id="wbClient"><option value="">— none —</option>
        ${clients.map((c) => `<option value="${esc(c.id)}" ${wiz.basic.clientId === c.id ? "selected" : ""}>${esc(c.name || c.id)}</option>`).join("")}</select></label>
      <label class="field"><span class="field-label">Tags (comma separated)</span><input type="text" id="wbTags" value="${esc(wiz.basic.tags)}"></label>
      <label class="field"><span class="field-label">Start URL</span><input type="url" id="wbUrl" value="${esc(wiz.basic.startUrl)}"></label>
      <label class="field" style="grid-column:1/-1"><span class="field-label">Notes</span><textarea id="wbNotes" rows="2">${esc(wiz.basic.notes)}</textarea></label>
    </div>`,
    wizFoot(false, "Next: Device ›"));
  bindWizFoot(false, () => {
    const name = $("#wbName").value.trim();
    if (!name) { toast("Profile name is required", "err"); return; }
    wiz.basic = {
      name, clientId: $("#wbClient").value,
      tags: $("#wbTags").value, notes: $("#wbNotes").value,
      startUrl: $("#wbUrl").value.trim() || "https://example.com",
    };
    wiz.step = 1; renderWizard();
  });
}

/* Step 2 — Device (with support badges) */
async function wDevice() {
  wizardShell(`<div class="skel" style="height:160px">&nbsp;</div>`, wizFoot(true, "Next: Network ›"));
  bindWizFoot(true, () => { wiz.step = 2; renderWizard(); });

  const devices = await loadDevices();
  if (!devices.length) {
    $("#wizBody").innerHTML = emptyState("Device library unavailable",
      "GET /api/devices failed or returned no presets. You can continue without a device preset — the backend will generate a default fingerprint.");
    return;
  }
  const tab = wiz.deviceTab;
  const inTab = (d) => {
    const c = String(d.category || "").toLowerCase();
    if (tab === "desktop") return c.includes("desktop") || (!c.includes("mobile") && !c.includes("tablet"));
    if (tab === "mobile") return c.includes("mobile") && !c.includes("tablet");
    return c.includes("tablet");
  };
  const list = devices.filter(inTab);
  $("#wizBody").innerHTML = `
    <div class="tabs">
      ${["desktop", "mobile", "tablet"].map((t) =>
        `<button class="tab ${t === tab ? "active" : ""}" data-dt="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}
    </div>
    ${list.length ? `<div class="device-pick">${list.map((d) => `
      <div class="device-pick-card ${wiz.deviceId === d.id ? "picked" : ""}" data-dev="${esc(d.id)}">
        <div class="nm">${esc(d.name || d.model || d.id)}</div>
        <div class="meta">${esc(d.manufacturer || "")} ${esc(d.model || "")}<br>
          ${d.viewport ? `${esc(d.viewport.width)}×${esc(d.viewport.height)}` : "?"} · DPR ${esc(d.device_scale_factor ?? "?")} · ${d.touch ? "touch" : "no touch"}<br>
          ${esc(d.platform || d.os || "")}</div>
        <div style="margin-top:6px">${supportBadge(d.support)}</div>
      </div>`).join("")}</div>`
      : `<p class="muted">No presets in this category.</p>`}
    <p class="note">Device is optional. Skipping assigns no device preset; the backend builds a default fingerprint.</p>`;
  $$("[data-dt]", $("#wizBody")).forEach((b) => b.onclick = () => { wiz.deviceTab = b.dataset.dt; wDevice(); });
  $$("[data-dev]", $("#wizBody")).forEach((c) => c.onclick = () => {
    wiz.deviceId = wiz.deviceId === c.dataset.dev ? null : c.dataset.dev;
    wDevice();
  });
}

/* Step 3 — Network (with connection test) */
async function wNetwork() {
  const gateways = await loadGateways();
  const n = wiz.net;
  const modes = [
    ["direct", "Direct", "No proxy — fastest"],
    ["http", "HTTP", "HTTP proxy"],
    ["https", "HTTPS", "HTTPS proxy"],
    ["socks5", "SOCKS5", "SOCKS5 proxy"],
    ["gateway", "Gateway", "Route via a configured gateway"],
  ];
  wizardShell(`
    <div class="radio-cards">
      ${modes.map(([m, label, sub]) => `
        <div class="radio-card ${n.mode === m ? "selected" : ""}" data-mode="${m}">
          <strong>${label}</strong><span>${sub}</span></div>`).join("")}
    </div>
    <div id="netFields" style="margin-top:14px"></div>
    <div id="netTestOut" style="margin-top:12px"></div>`,
    wizFoot(true, "Next: Fingerprint ›"));
  bindWizFoot(true, () => { wiz.step = 3; renderWizard(); });

  const drawFields = () => {
    const f = $("#netFields");
    if (n.mode === "direct") { f.innerHTML = `<p class="note">Direct connection — no proxy fields needed.</p>`; return; }
    if (n.mode === "gateway") {
      f.innerHTML = `<label class="field"><span class="field-label">Gateway</span><select id="wnGw">
        ${gateways.map((g) => `<option value="${esc(g.id)}" ${n.gatewayId === g.id ? "selected" : ""}>${esc(g.name || g.id)} (${esc(g.providerType || "?")})</option>`).join("")}
        </select></label>
        ${gateways.length ? "" : `<p class="note">No gateways configured. Add one under Network › Gateways first.</p>`}`;
      const sel = $("#wnGw");
      if (sel) sel.onchange = (e) => { n.gatewayId = e.target.value; };
      return;
    }
    f.innerHTML = `<div class="form-grid">
      <label class="field"><span class="field-label">Host *</span><input type="text" id="wnHost" value="${esc(n.host)}" placeholder="203.0.113.10"></label>
      <label class="field"><span class="field-label">Port *</span><input type="number" id="wnPort" value="${esc(n.port)}" placeholder="8080"></label>
      <label class="field"><span class="field-label">Username</span><input type="text" id="wnUser" value="${esc(n.user)}" autocomplete="off"></label>
      <label class="field"><span class="field-label">Password</span><input type="password" id="wnPass" value="${esc(n.pass)}" autocomplete="off"></label>
    </div>
    <div class="btn-row" style="margin-top:10px">
      <button class="btn btn-sm" id="wnTest">Test connection</button>
    </div>
    <p class="note">Uses POST /api/proxy/test for ad-hoc checks. Passwords are never displayed or logged.</p>`;
    $("#wnHost").oninput = (e) => { n.host = e.target.value; wiz.netTest = null; };
    $("#wnPort").oninput = (e) => { n.port = e.target.value; wiz.netTest = null; };
    $("#wnUser").oninput = (e) => { n.user = e.target.value; wiz.netTest = null; };
    $("#wnPass").oninput = (e) => { n.pass = e.target.value; wiz.netTest = null; };
    $("#wnTest").onclick = async () => {
      if (!n.host || !n.port) { toast("Host and port are required", "err"); return; }
      $("#netTestOut").innerHTML = `<div class="skel" style="height:60px">&nbsp;</div>`;
      const t = await apiPost("/api/proxy/test", {
        host: n.host, port: Number(n.port), username: n.user, password: n.pass, protocol: n.mode, timeout: 10,
      });
      wiz.netTest = t.ok ? t.data : { ok: false, error: t.error || ("HTTP " + t.status) };
      drawTestOut();
    };
  };
  const drawTestOut = () => {
    const t = wiz.netTest, out = $("#netTestOut");
    if (!t) { out.innerHTML = ""; return; }
    const ok = t.ok || t.success;
    out.innerHTML = `<div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Test result ${ok ? badge("pass", "green") : badge("fail", "red")}</h3>${kvRows([
      ["Egress IP", val(t.egress_ip || t.egressIp)],
      ["Country", val(t.country)],
      ["Latency", t.latency_ms != null ? `<span class="num">${esc(t.latency_ms)} ms</span>` : NA],
      ["DNS", t.dns ? esc(JSON.stringify(t.dns)) : NA],
      ["IPv6", t.ipv6 ? esc(JSON.stringify(t.ipv6)) : NA],
      ["Error", val(t.error)],
    ])}</div></div>`;
  };
  drawFields(); drawTestOut();
  $$("[data-mode]", $("#wizBody")).forEach((c) => c.onclick = () => {
    n.mode = c.dataset.mode; wiz.netTest = null;
    $$("[data-mode]", $("#wizBody")).forEach((x) => x.classList.toggle("selected", x === c));
    drawFields(); drawTestOut();
  });
}

/* Step 4 — Fingerprint */
async function wFingerprint() {
  let caps = null;
  if (wiz.deviceId) {
    const r = await apiGet(`/api/devices/${encodeURIComponent(wiz.deviceId)}`);
    if (r.ok) caps = r.data.capability_matrix || null;
  }
  const modes = [
    ["stable", "Stable profile", "Deterministic fingerprint from a fixed seed"],
    ["preset", "Preset", "Pick a curated fingerprint preset"],
    ["custom", "Custom", "Tune individual components (stored with profile)"],
  ];
  wizardShell(`
    <div class="radio-cards">
      ${modes.map(([m, label, sub]) => `
        <div class="radio-card ${wiz.fpMode === m ? "selected" : ""}" data-fpm="${m}">
          <strong>${label}</strong><span>${sub}</span></div>`).join("")}
    </div>
    <div class="section-head"><h2>Capability status${wiz.deviceId ? " — " + esc(wiz.deviceId) : ""}</h2></div>
    <div id="capList"></div>`,
    wizFoot(true, "Next: Consistency ›"));
  bindWizFoot(true, () => { wiz.step = 4; renderWizard(); });

  $$("[data-fpm]", $("#wizBody")).forEach((c) => c.onclick = () => {
    wiz.fpMode = c.dataset.fpm;
    $$("[data-fpm]", $("#wizBody")).forEach((x) => x.classList.toggle("selected", x === c));
  });
  const cl = $("#capList");
  if (caps && Object.keys(caps).length) {
    cl.innerHTML = `<div class="table-card"><table class="grid"><thead><tr><th>Component</th><th>Status</th><th>Note</th></tr></thead><tbody>
      ${Object.entries(caps).map(([k, v]) => `<tr><td class="prop">${esc(k)}</td><td>${statusBadge(v.status)}</td><td class="muted">${esc(v.note || "—")}</td></tr>`).join("")}
      </tbody></table></div><p class="note">Source: GET /api/devices/${esc(wiz.deviceId)} capability_matrix.</p>`;
  } else {
    cl.innerHTML = `<p class="muted">${wiz.deviceId
      ? "No capability data for this device (endpoint missing or empty)."
      : "Select a device preset in step 2 to see its capability status here."}</p>`;
  }
}

/* Step 5 — Consistency (local pre-flight; server check runs after creation) */
function wConsistency() {
  const checks = [];
  const dev = (cache.devices || []).find((d) => d.id === wiz.deviceId);
  const mobileDev = dev && /mobile|android|iphone/i.test(String(dev.category || "") + String(dev.model || ""));
  checks.push({
    domain: "device/network", status: "PASS",
    expected: "n/a (draft)", actual: "n/a (draft)",
    message: wiz.net.mode === "direct"
      ? "Direct connection selected — no proxy/DNS consistency to verify."
      : `${wiz.net.mode.toUpperCase()} proxy configured; egress will be verified after creation.`,
  });
  checks.push({
    domain: "device/fingerprint",
    status: mobileDev ? "PASS" : (wiz.deviceId ? "PASS" : "WARNING"),
    expected: mobileDev ? "mobile profile" : "desktop profile",
    actual: mobileDev ? "mobile preset" : "no preset",
    message: wiz.deviceId
      ? `Device preset "${wiz.deviceId}" selected; fingerprint will be derived from it.`
      : "No device preset — backend will generate a default (desktop-class) fingerprint.",
  });
  if (wiz.net.mode !== "direct" && wiz.netTest) {
    const ok = wiz.netTest.ok || wiz.netTest.success;
    checks.push({
      domain: "network/reachability",
      status: ok ? "PASS" : "ERROR",
      expected: "reachable proxy",
      actual: ok ? `egress ${wiz.netTest.egress_ip || wiz.netTest.egressIp || "?"}` : "unreachable",
      message: ok ? `Proxy responded in ${wiz.netTest.latency_ms ?? "?"} ms.` : `Test failed: ${wiz.netTest.error || "unknown error"}`,
    });
  }
  const overall = checks.some((c) => c.status === "ERROR") ? "ERROR"
    : checks.some((c) => c.status === "WARNING") ? "WARNING" : "PASS";

  wizardShell(`
    <p class="note"><strong>Local pre-flight</strong> — computed from the draft on this page.
    The server-side consistency check (<span class="mono">GET /api/profiles/:id/consistency</span>)
    runs after the profile is created and is shown in the profile's Health tab.</p>
    <div style="margin-bottom:10px">${statusBadge(overall)} <span class="muted">pre-flight overall</span></div>
    <div class="table-card"><table class="grid"><thead><tr><th>Domain</th><th>Status</th><th>Expected</th><th>Actual</th><th>Message</th></tr></thead>
    <tbody>${checks.map((c) => `<tr><td class="prop">${esc(c.domain)}</td><td>${statusBadge(c.status)}</td>
      <td>${esc(c.expected)}</td><td>${esc(c.actual)}</td>
      <td>${esc(c.message)}</td></tr>`).join("")}</tbody></table></div>`,
    wizFoot(true, "Next: Review ›"));
  bindWizFoot(true, () => {
    if (overall === "ERROR" && !confirm("Pre-flight found errors. Continue anyway?")) return;
    wiz.step = 5; renderWizard();
  });
}

/* Step 6 — Review + create */
function wReview() {
  const b = wiz.basic, n = wiz.net;
  const dev = (cache.devices || []).find((d) => d.id === wiz.deviceId);
  const netSummary = n.mode === "direct" ? "Direct" :
    n.mode === "gateway" ? `Gateway (${n.gatewayId || "none selected"})` :
    `${n.mode.toUpperCase()} ${n.host}:${n.port}${n.user ? " (auth •••)" : ""}`;
  wizardShell(`
    <div class="review-block"><h4>Basic</h4>${kvRows([
      ["Name", val(b.name)], ["Client", val(clientName(b.clientId) || b.clientId)],
      ["Tags", val(b.tags)], ["Start URL", val(b.startUrl)], ["Notes", val(b.notes)],
    ])}</div>
    <div class="review-block"><h4>Device</h4>${kvRows([
      ["Preset", dev ? `${esc(dev.name || dev.model)} (${esc(dev.id)})` : "none — backend default"],
      ["Support", dev ? supportBadge(dev.support) : NA],
    ])}</div>
    <div class="review-block"><h4>Network</h4>${kvRows([["Mode", esc(netSummary)]])}</div>
    <div class="review-block"><h4>Fingerprint</h4>${kvRows([["Strategy", esc({ stable: "Stable profile", preset: "Preset", custom: "Custom" }[wiz.fpMode])]])}</div>`,
    `<button class="btn" id="wBack2">‹ Back</button>
     <button class="btn" id="wCancel2">Cancel</button>
     <button class="btn btn-primary" id="wCreate">Create profile</button>`);
  $("#wBack2").onclick = () => { wiz.step = 4; renderWizard(); };
  $("#wCancel2").onclick = () => { closeModal(); wiz = null; };
  $("#wCreate").onclick = async () => {
    const btn = $("#wCreate");
    btn.disabled = true; btn.textContent = "Creating…";
    const payload = {
      name: b.name,
      clientId: b.clientId || undefined,
      tags: b.tags.split(",").map((s) => s.trim()).filter(Boolean),
      notes: b.notes, startUrl: b.startUrl,
      deviceId: wiz.deviceId || undefined,
      fingerprintStrategy: wiz.fpMode,
    };
    if (n.mode === "direct") payload.networkMode = "direct";
    else if (n.mode === "gateway") { payload.networkMode = "gateway"; payload.gatewayId = n.gatewayId || undefined; }
    else {
      payload.networkMode = n.mode;
      payload.proxyHost = n.host; payload.proxyPort = n.port ? Number(n.port) : undefined;
      payload.proxyUser = n.user || undefined; payload.proxyPass = n.pass || undefined;
      payload.proxyType = n.mode;
    }
    const r = await apiPost("/api/profiles", payload);
    if (r.ok && r.data && r.data.ok && r.data.profile) {
      const np = r.data.profile;
      closeModal(); wiz = null;
      toast(`Profile "${np.name || np.id}" created` +
        ((r.data.warnings || []).length ? ` — ${r.data.warnings.length} fingerprint warning(s)` : ""), "ok");
      invalidate("profiles");
      location.hash = `#/profile/${encodeURIComponent(np.id)}`;
    } else {
      btn.disabled = false; btn.textContent = "Create profile";
      toast("Create failed: " + apiErr(r), "err");
    }
  };
}

/* ============================== Device Library ============================== */
const devView = { q: "", cat: "", support: "" };

async function loadAllProfiles(force) {
  if (!force && cache.profiles) return cache.profiles;
  const r = await apiGet("/api/profiles?limit=500");
  if (!r.ok) { cache.profiles = []; return []; }
  cache.profiles = Array.isArray(r.data) ? r.data : (r.data.items || []);
  return cache.profiles;
}

async function vDevices(view, query) {
  const devices = await loadDevices(true);
  if (!devices.length) {
    view.innerHTML = emptyState("Device library unavailable",
      "GET /api/devices failed or returned no presets. The device preset service may not be implemented yet.",
      `<button class="btn btn-primary" onclick="location.reload()">Retry</button>`);
    return;
  }
  const q = devView.q.trim().toLowerCase();
  const rows = devices.filter((d) => {
    if (q) {
      const hay = [d.name, d.manufacturer, d.model, d.platform, d.category, d.id].filter(Boolean).join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (devView.cat) {
      const hay = (String(d.category || "") + " " + String(d.platform || "")).toLowerCase();
      if (devView.cat === "ios" && !/ios|iphone|ipad/.test(hay)) return false;
      else if (!hay.includes(devView.cat)) return false;
    }
    if (devView.support) {
      if (String(d.support || "").toUpperCase() !== devView.support) return false;
    }
    return true;
  });

  view.innerHTML = `
    <div class="action-bar">
      <span class="bar-title">Device Library</span>
      <span class="spacer"></span>
      <div class="filter-row">
        <input type="search" id="dvQ" placeholder="Search devices…" value="${esc(devView.q)}" aria-label="Search devices">
        <select id="dvCat" aria-label="Category filter">
          <option value="">All categories</option>
          ${["android", "ios", "tablet", "mobile", "desktop"].map((c) =>
            `<option value="${c}" ${devView.cat === c ? "selected" : ""}>${c[0].toUpperCase() + c.slice(1)}</option>`).join("")}
        </select>
        <select id="dvSup" aria-label="Support filter">
          <option value="">All support levels</option>
          ${["VALIDATED", "PARTIAL", "PENDING", "UNSUPPORTED"].map((s) =>
            `<option value="${s}" ${devView.support === s ? "selected" : ""}>${s[0] + s.slice(1).toLowerCase()}</option>`).join("")}
        </select>
      </div>
    </div>
    <p class="note" style="margin:0 0 12px">${rows.length} of ${devices.length} presets</p>
    ${rows.length ? `<div class="device-grid">${rows.map((d) => `
      <div class="device-card" data-dev="${esc(d.id)}" role="button" tabindex="0" aria-label="${esc(d.name || d.id)}">
        <div class="nm">${esc(d.name || d.model || d.id)} ${supportBadge(d.support)}</div>
        <div class="meta">
          ${esc(d.manufacturer || "")} ${esc(d.model || "")}<br>
          ${esc(d.platform || d.os || "")} · ${esc(d.category || "—")}<br>
          ${d.viewport ? `${esc(d.viewport.width)}×${esc(d.viewport.height)}` : "—"} · DPR ${esc(d.device_scale_factor ?? "—")} · ${d.touch ? "touch" : "no touch"}
        </div>
        <div class="badges">${badge(d.mobile ? "Mobile" : "Desktop", d.mobile ? "blue" : "grey")}</div>
      </div>`).join("")}</div>`
      : emptyState("No devices match", "Adjust the search or filters.")}`;

  const deb = debounce((v) => { devView.q = v; vDevices(view, query); }, 280);
  $("#dvQ").addEventListener("input", (e) => deb(e.target.value));
  $("#dvCat").onchange = (e) => { devView.cat = e.target.value; vDevices(view, query); };
  $("#dvSup").onchange = (e) => { devView.support = e.target.value; vDevices(view, query); };
  $$("[data-dev]", view).forEach((c) => {
    c.onclick = () => openDeviceDrawer(c.dataset.dev);
    c.onkeydown = (e) => { if (e.key === "Enter") openDeviceDrawer(c.dataset.dev); };
  });
}

async function openDeviceDrawer(id) {
  openDrawer("Device preset", `<div class="skel" style="height:120px">&nbsp;</div>`);
  let d = (cache.devices || []).find((x) => x.id === id);
  if (!d) {
    const r = await apiGet(`/api/devices/${encodeURIComponent(id)}`);
    if (r.ok) d = r.data;
  }
  if (!d) {
    $("#drawerBody").innerHTML = emptyState("Not found", "Device preset could not be loaded.");
    return;
  }
  $("#drawerBody").innerHTML = `
    <div class="toolbar-row"><h3 style="margin:0">${esc(d.name || d.model || d.id)}</h3><span class="spacer"></span>${supportBadge(d.support)}</div>
    ${deviceDetailHtml(d)}
    <div class="btn-row" style="margin-top:14px">
      <button class="btn btn-primary" id="dvUse">Use in new profile</button>
    </div>`;
  $("#dvUse").onclick = () => {
    closeDrawer();
    openWizard();
    setTimeout(() => { wiz.deviceId = id; wiz.deviceTab = "mobile"; wiz.step = 1; renderWizard(); }, 60);
  };
}

/* ============================== Fingerprint page ============================== */
async function vFingerprint(view, query) {
  const profiles = await loadAllProfiles();
  if (!profiles.length) {
    view.innerHTML = emptyState("No profiles", "Create a profile to inspect its fingerprint.",
      `<button class="btn btn-primary" onclick="openWizard()">New profile</button>`);
    return;
  }
  const selId = (query.id && profiles.some((p) => p.id === query.id)) ? query.id : profiles[0].id;
  view.innerHTML = `
    <div class="action-bar">
      <span class="bar-title">Profile</span>
      <select id="fpSel" aria-label="Select profile" style="min-width:260px">
        ${profiles.map((p) => `<option value="${esc(p.id)}" ${p.id === selId ? "selected" : ""}>${esc(p.name || p.id)}</option>`).join("")}
      </select>
      <span class="spacer"></span>
      <a class="btn btn-sm" id="fpOpen" href="#/profile/${encodeURIComponent(selId)}?tab=fingerprint">Open in profile view</a>
    </div>
    <div id="fpBody"></div>`;
  const render = async (id) => {
    $("#fpOpen").href = `#/profile/${encodeURIComponent(id)}?tab=fingerprint`;
    await renderFpInspector($("#fpBody"), id);
  };
  $("#fpSel").onchange = (e) => { render(e.target.value); };
  await render(selId);
}

/* ============================== Network ============================== */
const netView = { tab: "proxies" };

async function vNetwork(view, query) {
  const tab = query.tab || netView.tab || "proxies";
  netView.tab = tab;
  view.innerHTML = `
    <div class="tabs">
      ${[["proxies", "Proxies"], ["gateways", "Gateways"], ["diagnostics", "Diagnostics"]].map(([t, label]) =>
        `<button class="tab ${t === tab ? "active" : ""}" data-ntab="${t}">${label}</button>`).join("")}
    </div>
    <div id="netBody"></div>`;
  $$("[data-ntab]", view).forEach((b) => b.onclick = () => { location.hash = `#/network?tab=${b.dataset.ntab}`; });
  const body = $("#netBody");
  if (tab === "proxies") await netProxies(body);
  else if (tab === "gateways") await netGateways(body);
  else await netDiagnostics(body);
}

async function netProxies(body) {
  const profiles = await loadAllProfiles();
  const withProxy = profiles.filter((p) => p.proxyHost);
  body.innerHTML = `
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Test a proxy</h3>
      <div class="form-grid">
        <label class="field"><span class="field-label">Protocol</span><select id="pxProto">
          <option value="http">HTTP</option><option value="https">HTTPS</option><option value="socks5">SOCKS5</option></select></label>
        <label class="field"><span class="field-label">Host *</span><input type="text" id="pxHost" placeholder="203.0.113.10"></label>
        <label class="field"><span class="field-label">Port *</span><input type="number" id="pxPort" placeholder="8080"></label>
        <label class="field"><span class="field-label">Username</span><input type="text" id="pxUser" autocomplete="off"></label>
        <label class="field"><span class="field-label">Password</span><input type="password" id="pxPass" autocomplete="off"></label>
      </div>
      <div class="btn-row" style="margin-top:12px">
        <button class="btn btn-primary" id="pxGo">Test connection</button>
      </div>
      <div id="pxOut" style="margin-top:12px"></div>
      <p class="note">Uses POST /api/proxy/test. Passwords are never displayed or logged.</p>
    </div></div>
    <h3 class="section-title">Profiles using proxies <span class="muted small">${withProxy.length} of ${profiles.length}</span></h3>
    ${withProxy.length ? `<div class="table-card"><div class="table-scroll"><table class="grid"><thead><tr>
      <th>Profile</th><th>Proxy</th><th>Health</th><th>Egress IP</th></tr></thead><tbody>
      ${withProxy.map((p) => `<tr>
        <td><span class="profile-name"><a href="#/profile/${encodeURIComponent(p.id)}">${esc(p.name || p.id)}</a></span></td>
        <td>${proxyLabel(p)}</td>
        <td class="c-ph" data-ph="${esc(p.id)}"><span class="muted">…</span></td>
        <td class="c-ip" data-ip="${esc(p.id)}"><span class="muted">…</span></td>
      </tr>`).join("")}</tbody></table></div></div>`
      : emptyState("No proxies in use", "No profiles currently have a proxy configured.")}`;

  $("#pxGo").onclick = async () => {
    const host = $("#pxHost").value.trim(), port = $("#pxPort").value.trim();
    if (!host || !port) { toast("Host and port are required", "err"); return; }
    $("#pxOut").innerHTML = `<div class="skel" style="height:70px">&nbsp;</div>`;
    const t = await apiPost("/api/proxy/test", {
      host, port: Number(port), protocol: $("#pxProto").value,
      username: $("#pxUser").value, password: $("#pxPass").value, timeout: 10,
    });
    const d = t.ok ? t.data : { ok: false, error: t.error || ("HTTP " + t.status) };
    const ok = d.ok || d.success;
    $("#pxOut").innerHTML = `<div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Result ${ok ? badge("pass", "green") : badge("fail", "red")}</h3>${kvRows([
      ["Egress IP", val(d.egress_ip || d.egressIp)], ["Country", val(d.country)],
      ["Latency", d.latency_ms != null ? `<span class="num">${esc(d.latency_ms)} ms</span>` : NA],
      ["Error", val(d.error)],
    ])}</div></div>`;
  };

  /* lazy health + egress for proxy profiles */
  withProxy.forEach(async (p) => {
    const nh = await loadNetHealth(p.id);
    const ph = nh && nh.proxyHealth;
    const hc = document.querySelector(`[data-ph="${CSS.escape(p.id)}"]`);
    const ic = document.querySelector(`[data-ip="${CSS.escape(p.id)}"]`);
    if (hc) hc.innerHTML = nh ? (nh.ok === false ? badge("failed", "red") : badge("ok", "green")) : NA;
    if (ic) ic.innerHTML = ph && (ph.egress_ip || ph.egressIp) ? `<span class="mono">${esc(ph.egress_ip || ph.egressIp)}</span>` : NA;
  });
}

async function netGateways(body) {
  const gateways = await loadGateways(true);
  body.innerHTML = `
    <div class="toolbar"><div class="spacer"></div>
      <button class="btn btn-sm btn-primary" id="gwAdd">Add gateway</button></div>
    ${gateways.length ? `<div class="table-card"><div class="table-scroll"><table class="grid"><thead><tr>
      <th>Name</th><th>Provider</th><th>Health</th><th>ID</th><th></th></tr></thead><tbody>
      ${gateways.map((g) => `<tr>
        <td><strong>${esc(g.name || g.id)}</strong></td>
        <td>${val(g.providerType || g.provider)}</td>
        <td class="c-gh" data-gh="${esc(g.id)}"><span class="muted">…</span></td>
        <td class="mono muted">${esc(g.id)}</td>
        <td><div class="row-actions">
          <button class="btn btn-xs" data-gw="health" data-id="${esc(g.id)}">Health</button>
          <button class="btn btn-xs" data-gw="provision" data-id="${esc(g.id)}">Provision</button>
          <button class="btn btn-xs" data-gw="edit" data-id="${esc(g.id)}">Edit</button>
          <button class="btn btn-xs btn-danger" data-gw="del" data-id="${esc(g.id)}">Delete</button>
        </div></td></tr>`).join("")}</tbody></table></div></div>`
      : emptyState("No gateways", "Add a gateway provider to route profile traffic through it.")}
    <p class="note">Provider secrets are stored server-side and never returned by the API.</p>`;

  $("#gwAdd").onclick = () => openGatewayEdit(null);
  $$("[data-gw]", body).forEach((b) => b.onclick = async () => {
    const id = b.dataset.id, kind = b.dataset.gw;
    if (kind === "health") {
      const cell = document.querySelector(`[data-gh="${CSS.escape(id)}"]`);
      if (cell) cell.innerHTML = `<span class="muted">checking…</span>`;
      const r = await apiGet(`/api/gateways/${encodeURIComponent(id)}/health`);
      const ok = r.ok && r.data && (r.data.ok || (r.data.health && r.data.health.ok));
      if (cell) cell.innerHTML = ok ? badge("healthy", "green") : badge("unhealthy", "red");
      if (!r.ok) toast("Health check failed", "err");
    } else if (kind === "provision") {
      const r = await apiPost(`/api/gateways/${encodeURIComponent(id)}/provision`, {});
      toast(r.ok && r.data && r.data.ok ? "Provisioned" : "Provision failed: " + apiErr(r), r.ok ? "ok" : "err");
    } else if (kind === "edit") {
      openGatewayEdit(gateways.find((g) => g.id === id));
    } else if (kind === "del") {
      if (!confirm("Delete this gateway?")) return;
      const r = await apiDel(`/api/gateways/${encodeURIComponent(id)}`);
      toast(r.ok ? "Deleted" : "Delete failed", r.ok ? "ok" : "err");
      if (r.ok) { invalidate("gateways"); route(); }
    }
  });

  gateways.forEach(async (g) => {
    const r = await apiGet(`/api/gateways/${encodeURIComponent(g.id)}/health`);
    const cell = document.querySelector(`[data-gh="${CSS.escape(g.id)}"]`);
    if (!cell) return;
    const ok = r.ok && r.data && (r.data.ok || (r.data.health && r.data.health.ok));
    cell.innerHTML = r.ok ? (ok ? badge("healthy", "green") : badge("unhealthy", "red")) : NA;
  });
}
function openGatewayEdit(g) {
  const isNew = !g;
  openModal((isNew ? "Add" : "Edit") + " gateway", `
    <div class="form-grid">
      <label class="field"><span class="field-label">Name *</span><input type="text" id="gwName" value="${esc((g && g.name) || "")}"></label>
      <label class="field"><span class="field-label">Provider type</span><input type="text" id="gwType" value="${esc((g && (g.providerType || g.provider)) || "")}" placeholder="e.g. ssh, wireguard"></label>
      <label class="field" style="grid-column:1/-1"><span class="field-label">Config JSON (no secrets in this form — manage secrets server-side)</span>
        <textarea id="gwCfg" rows="5" placeholder='{"host":"...","port":22}'>${esc(g && g.config ? JSON.stringify(g.config, null, 2) : "")}</textarea></label>
    </div>
    <p class="note">Secrets are never displayed: the API returns a redacted (safe) view.</p>`,
    `<button class="btn" id="mCancel">Cancel</button><button class="btn btn-primary" id="mSave">Save</button>`);
  $("#mCancel").onclick = closeModal;
  $("#mSave").onclick = async () => {
    const name = $("#gwName").value.trim();
    if (!name) { toast("Name is required", "err"); return; }
    let config = {};
    try { config = JSON.parse($("#gwCfg").value || "{}"); }
    catch (e) { toast("Config is not valid JSON", "err"); return; }
    const payload = { name, providerType: $("#gwType").value.trim(), config };
    const r = isNew ? await apiPost("/api/gateways", payload)
      : await apiPut(`/api/gateways/${encodeURIComponent(g.id)}`, payload);
    closeModal();
    toast(r.ok ? "Saved" : "Save failed", r.ok ? "ok" : "err");
    if (r.ok) { invalidate("gateways"); route(); }
  };
}

/* Diagnostics tab: per-profile network check with honest endpoint availability */
async function netDiagnostics(body) {
  const profiles = await loadAllProfiles();
  body.innerHTML = `
    <div class="action-bar">
      <span class="bar-title">Profile</span>
      <select id="dgSel" aria-label="Select profile" style="min-width:260px">
        ${profiles.map((p) => `<option value="${esc(p.id)}">${esc(p.name || p.id)}</option>`).join("")}
      </select>
      <button class="btn btn-primary btn-sm" id="dgGo">Run diagnostics</button>
    </div>
    <div id="dgOut">${emptyState("Network diagnostics", "Pick a profile and run the check.")}</div>`;
  if (!profiles.length) {
    body.innerHTML = emptyState("No profiles", "Create a profile first.");
    return;
  }
  $("#dgGo").onclick = async () => {
    const id = $("#dgSel").value;
    $("#dgOut").innerHTML = `<div class="skel" style="height:120px">&nbsp;</div>`;
    const r = await apiPost(`/api/profiles/${encodeURIComponent(id)}/network/test`, {});
    if (r.status === 404) {
      $("#dgOut").innerHTML = `<div class="notice info">The extended network test endpoint (POST /api/profiles/:id/network/test) is not available on this backend. Basic health is shown on the profile's Network tab instead.</div>`;
      return;
    }
    if (!r.ok || !r.data) {
      $("#dgOut").innerHTML = `<div class="notice err">Diagnostics failed: ${apiErr(r)}</div>`;
      return;
    }
    const nt = r.data;
    $("#dgOut").innerHTML = `<div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Diagnostics result</h3>${kvRows([
      ["Health", nt.health ? statusBadge(nt.health) : NA],
      ["Egress IP", val(nt.egress_ip)], ["Country", val(nt.country)],
      ["Timezone", val(nt.timezone)], ["Latency", nt.latency_ms != null ? `<span class="num">${esc(nt.latency_ms)} ms</span>` : NA],
      ["DNS", nt.dns ? statusBadge(nt.dns.status) + " " + val(nt.dns.detail) : NA],
      ["IPv6", nt.ipv6 ? statusBadge(nt.ipv6.status) + " " + val(nt.ipv6.detail) : NA],
      ["Error", val(nt.error)],
    ])}</div></div>`;
  };
}

/* ============================== Jobs ============================== */
const jobsView = { cursor: null, useCursor: false, offset: 0, cache: [], nextCursor: null, unavailable: false };

async function vJobs(view, query) {
  jobsView.cursor = null; jobsView.useCursor = false; jobsView.offset = 0; jobsView.cache = [];

  view.innerHTML = `
    <div class="card"><div class="card-body">
      <h3 class="section-title" style="margin-top:0">Queue a bulk job</h3>
      <div class="form-grid">
        <label class="field"><span class="field-label">Job type</span><select id="jbType">
          <option value="bulk_launch">Bulk launch</option>
          <option value="bulk_stop">Bulk stop</option>
          <option value="launch_profile">Launch (single)</option>
          <option value="stop_profile">Stop (single)</option>
        </select></label>
        <label class="field"><span class="field-label">Start URL (optional)</span><input type="url" id="jbUrl" placeholder="https://…"></label>
        <label class="field"><span class="field-label">Profile IDs (comma separated)</span><input type="text" id="jbIds" placeholder="profile_xxx, profile_yyy"></label>
        <label class="field"><span class="field-label">Options</span>
          <select id="jbDirect"><option value="">Default launch</option><option value="1">Direct (fast)</option></select></label>
      </div>
      <div class="btn-row" style="margin-top:12px">
        <button class="btn btn-primary" id="jbGo">Queue job</button>
      </div>
      <p class="note">Job types come from the backend (<span class="mono">launch_profile, stop_profile, bulk_launch, bulk_stop</span>).</p>
    </div></div>
    <div class="toolbar-row"><h3 class="section-title" style="margin:0">Job queue</h3><span class="spacer"></span>
      <button class="btn btn-sm" id="jbRefresh">Refresh</button></div>
    <div id="jbList">${skelTable(8, 6)}</div>
    <div class="pager" id="jbPager" hidden>
      <button class="btn btn-sm" id="jbPrev">← Prev</button>
      <button class="btn btn-sm" id="jbNext">Next →</button>
      <span class="spacer"></span><span id="jbInfo"></span>
    </div>`;

  $("#jbGo").onclick = async () => {
    const ids = $("#jbIds").value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!ids.length) { toast("Enter at least one profile ID", "err"); return; }
    const r = await apiPost("/api/jobs", {
      type: $("#jbType").value, profileIds: ids,
      url: $("#jbUrl").value.trim() || undefined,
      direct: $("#jbDirect").value === "1",
    });
    if (r.ok && r.data && r.data.ok) {
      toast(`Job ${r.data.job.id} queued`, "ok");
      invalidate("jobs"); loadJobsPage();
    } else toast("Queue failed: " + apiErr(r), "err");
  };
  $("#jbRefresh").onclick = () => { invalidate("jobs"); jobsView.offset = 0; jobsView.cursor = null; loadJobsPage(); };
  $("#jbPrev").onclick = () => { if (jobsView.useCursor) { /* cursor stack not kept; fall back to offset */ } jobsView.offset = Math.max(0, jobsView.offset - PAGE_SIZE); loadJobsPage(); };
  $("#jbNext").onclick = () => { jobsView.offset += PAGE_SIZE; loadJobsPage(); };

  loadJobsPage();
}

async function jobsFetchPage() {
  const p = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (jobsView.useCursor && jobsView.cursor) p.set("cursor", jobsView.cursor);
  const r = await apiGet("/api/jobs?" + p.toString());
  if (r.status === 503) return { unavailable: true };
  if (!r.ok) return { error: "GET /api/jobs failed (" + r.status + ")" };
  const d = r.data || {};
  const jobs = d.jobs || [];
  if (typeof d.next_cursor === "string") {
    jobsView.useCursor = true;
    jobsView.nextCursor = d.next_cursor || null;
    return { jobs, total: d.total };
  }
  /* No cursor contract from the backend yet — page client-side over a larger fetch */
  if (!jobsView.cache.length) {
    const full = await apiGet("/api/jobs?limit=500");
    jobsView.cache = (full.ok && full.data && full.data.jobs) ? full.data.jobs : jobs;
  }
  const total = jobsView.cache.length;
  const page = jobsView.cache.slice(jobsView.offset, jobsView.offset + PAGE_SIZE);
  return { jobs: page, total };
}

function jobRows(jobs) {
  return jobs.map((j) => {
    const pct = j.progress != null ? Math.min(100, Math.max(0, Number(j.progress))) : null;
    const running = ["queued", "pending", "running", "in_progress"].includes(String(j.status || "").toLowerCase());
    return `<tr data-job="${esc(j.id)}">
      <td class="mono">${esc(j.id)}</td><td>${esc(j.type || "")}</td>
      <td data-jcell="status">${jobStatusBadge(j.status)}</td>
      <td style="min-width:120px" data-jcell="progress">${pct != null ? `<div class="progress"><i style="width:${pct}%"></i></div>` : NA}</td>
      <td class="num">${(j.profileIds || []).length}</td>
      <td class="muted small">${esc(timeAgo(j.created_at || j.createdAt))}</td>
      <td>${j.error ? `<span class="muted small">${esc(String(j.error).slice(0, 80))}</span>` : NA}</td>
      <td>${running ? `<button class="btn btn-xs btn-danger" data-jcancel="${esc(j.id)}">Cancel</button>` : ""}</td></tr>`;
  }).join("");
}

async function loadJobsPage() {
  const list = $("#jbList");
  if (!list) return;
  const res = await jobsFetchPage();
  if (res.unavailable) {
    list.innerHTML = `<div class="notice">The job system is not initialized on this backend (GET /api/jobs returned 503). Jobs queued from this console will not run until the job service is started.</div>`;
    $("#jbPager").hidden = true;
    return;
  }
  if (res.error) {
    list.innerHTML = emptyState("Couldn't load jobs", res.error, `<button class="btn" onclick="loadJobsPage()">Retry</button>`);
    $("#jbPager").hidden = true;
    return;
  }
  const jobs = res.jobs;
  list.innerHTML = jobs.length
    ? `<div class="table-card"><div class="table-scroll"><table class="grid"><thead><tr>
        <th>Job</th><th>Type</th><th>Status</th><th>Progress</th><th>Profiles</th><th>Created</th><th>Error</th><th></th></tr></thead>
        <tbody>${jobRows(jobs)}</tbody></table></div></div>`
    : emptyState("No jobs", "Queue a bulk job above, or start/stop profiles from the Profiles page.");
  $$("[data-jcancel]", list).forEach((b) => b.onclick = async () => {
    const r = await apiPost(`/api/jobs/${encodeURIComponent(b.dataset.jcancel)}/cancel`, {});
    toast(r.ok ? "Job cancelled" : "Cancel failed", r.ok ? "ok" : "err");
    invalidate("jobs"); jobsView.cache = []; loadJobsPage();
  });
  /* pager */
  const pager = $("#jbPager");
  if (jobsView.useCursor) {
    const hasNext = !!jobsView.nextCursor;
    pager.hidden = !hasNext;
    $("#jbNext").disabled = !hasNext;
    $("#jbInfo").textContent = `${PAGE_SIZE}/page`;
  } else {
    const total = res.total || 0;
    const hasPrev = jobsView.offset > 0, hasNext = jobsView.offset + jobs.length < total;
    pager.hidden = !hasPrev && !hasNext;
    $("#jbPrev").disabled = !hasPrev;
    $("#jbNext").disabled = !hasNext;
    $("#jbInfo").textContent = total ? `${jobsView.offset + 1}–${jobsView.offset + jobs.length} of ${total}` : "";
  }
}
/* Live-update a job row in place */
function patchJobRow(job) {
  const tr = document.querySelector(`#jbList tr[data-job="${CSS.escape(job.id)}"]`);
  if (!tr) return;
  const sc = tr.querySelector('[data-jcell="status"]');
  if (sc) sc.innerHTML = jobStatusBadge(job.status);
  const pc = tr.querySelector('[data-jcell="progress"]');
  if (pc && job.progress != null) pc.innerHTML = `<div class="progress"><i style="width:${Math.min(100, job.progress)}%"></i></div>`;
}

/* ============================== Audit Log ============================== */
const auditView = { q: "", onlyFail: false, unavailable: false };

async function vAudit(view, query) {
  view.innerHTML = skelTable(5, 10);
  const r = await apiGet("/api/audit?limit=200");
  if (r.status === 404) {
    view.innerHTML = emptyState("Audit log unavailable",
      "This backend does not expose GET /api/audit yet. Actions are not being recorded for display.");
    return;
  }
  const items = (r.ok && r.data && Array.isArray(r.data.items)) ? r.data.items : [];
  if (!r.ok) {
    view.innerHTML = emptyState("Couldn't load audit log", "GET /api/audit failed (" + r.status + ").",
      `<button class="btn" onclick="route()">Retry</button>`);
    return;
  }
  const q = auditView.q.trim().toLowerCase();
  const rows = items.filter((e) => {
    if (auditView.onlyFail && e.ok !== false) return false;
    if (!q) return true;
    return [e.action, e.profileId, e.ts, JSON.stringify(e.detail || "")].join(" ").toLowerCase().includes(q);
  }).reverse();

  view.innerHTML = `
    <div class="action-bar">
      <span class="bar-title">Audit Log</span>
      <input type="search" id="auQ" placeholder="Filter action, profile, detail…" value="${esc(auditView.q)}" style="width:260px" aria-label="Filter audit log">
      <label class="checkbox"><input type="checkbox" id="auFail" ${auditView.onlyFail ? "checked" : ""}> failures only</label>
      <span class="spacer"></span><span class="muted small">${rows.length} entries</span>
    </div>
    ${rows.length ? `<div class="table-card"><div class="table-scroll"><table class="grid"><thead><tr>
      <th>Time</th><th>Action</th><th>Profile</th><th>Result</th><th>Detail</th></tr></thead><tbody>
      ${rows.map((e) => `<tr>
        <td class="muted small" style="white-space:nowrap">${esc(e.ts || "")}</td>
        <td class="mono">${esc(e.action || "")}</td>
        <td>${e.profileId ? `<a class="mono" href="#/profile/${encodeURIComponent(e.profileId)}">${shortId(e.profileId)}</a>` : NA}</td>
        <td>${e.ok ? badge("ok", "green") : badge("fail", "red")}</td>
        <td class="mono muted small" style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(JSON.stringify(e.detail ?? ""))}">${esc(JSON.stringify(e.detail ?? "").slice(0, 140))}</td>
      </tr>`).join("")}</tbody></table></div></div>`
      : emptyState("No audit entries", "Actions are recorded here as they happen.")}
    <p class="note">Source: GET /api/audit.</p>`;

  const deb = debounce((v) => { auditView.q = v; vAudit(view, query); }, 280);
  $("#auQ").addEventListener("input", (e) => deb(e.target.value));
  $("#auFail").onchange = (e) => { auditView.onlyFail = e.target.checked; vAudit(view, query); };
}

/* ============================== Settings ============================== */
async function vSettings(view, query) {
  const tab = query.tab || "general";
  const sys = cache.sysHealth;
  view.innerHTML = `
    <div class="tabs">
      ${[["general", "General"], ["groups", "Clients & Templates"], ["backups", "Backups"], ["extensions", "Extensions"]].map(([t, label]) =>
        `<button class="tab ${t === tab ? "active" : ""}" data-stab="${t}">${label}</button>`).join("")}
    </div>
    <div id="setBody"></div>`;
  $$("[data-stab]", view).forEach((b) => b.onclick = () => { location.hash = `#/settings?tab=${b.dataset.stab}`; });
  const body = $("#setBody");
  if (tab === "general") setGeneral(body, sys);
  else if (tab === "groups") await setGroups(body);
  else if (tab === "backups") await setBackups(body);
  else await setExtensions(body);
}

function setGeneral(body, sys) {
  body.innerHTML = `
    <div class="form-grid">
      <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">API access</h3>
        <label class="field"><span class="field-label">Bearer token (stored in this browser's localStorage)</span>
          <input type="password" id="sToken" value="${esc(apiToken)}" autocomplete="off"></label>
        <div class="btn-row" style="margin-top:12px">
          <button class="btn btn-primary" id="sSave">Save token</button>
          <button class="btn" id="sClear">Clear</button>
        </div>
        <p class="note">The backend only enforces a token when <span class="mono">MULTIPROFILE_API_TOKEN</span> is set server-side.
        If unset, all endpoints are open and no token is needed.</p>
      </div></div>
      <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Maintenance</h3>
        <div class="btn-row">
          <button class="btn" id="sReap">Reap orphaned browsers</button>
          <button class="btn" id="sUpd">Check update status</button>
        </div>
        <div id="sOut" style="margin-top:12px"></div>
      </div></div>
    </div>
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Backend health (GET /api/health)</h3>
      <pre class="mono small" style="white-space:pre-wrap">${sys ? esc(JSON.stringify(sys, null, 2)) : "unavailable"}</pre>
    </div></div>`;

  $("#sSave").onclick = () => {
    apiToken = $("#sToken").value.trim();
    if (apiToken) storeSet(TOKEN_KEY, apiToken); else storeDel(TOKEN_KEY);
    authFailed = false;
    toast("Token saved", "ok");
    refreshHealthPill();
  };
  $("#sClear").onclick = () => { apiToken = ""; storeDel(TOKEN_KEY); $("#sToken").value = ""; toast("Token cleared"); };
  $("#sReap").onclick = async () => {
    const r = await apiPost("/api/maintenance/reap", {});
    $("#sOut").innerHTML = `<p class="note">${r.ok ? `Reaped ${r.data && r.data.count != null ? r.data.count : "?"} orphan(s).` : "Reap failed: " + apiErr(r)}</p>`;
  };
  $("#sUpd").onclick = async () => {
    const r = await apiGet("/api/update/status");
    $("#sOut").innerHTML = `<p class="note">Update status: <span class="mono">${esc(r.ok ? JSON.stringify(r.data) : "unavailable (" + r.status + ")")}</span></p>`;
  };
}

async function setGroups(body) {
  const [clients, templates] = await Promise.all([loadClients(true), loadTemplates(true)]);
  const crud = (kind, items, fields) => `
    <div class="toolbar"><div class="spacer"></div>
      <button class="btn btn-sm btn-primary" data-add="${kind}">Add ${kind === "clients" ? "client" : "template"}</button></div>
    ${items.length ? `<div class="table-card"><div class="table-scroll"><table class="grid"><thead><tr>
      <th>Name</th>${fields.map((f) => `<th>${esc(f[1])}</th>`).join("")}<th>ID</th><th></th></tr></thead><tbody>
      ${items.map((it) => `<tr><td><strong>${esc(it.name || it.id)}</strong></td>
        ${fields.map(([k]) => `<td class="muted">${esc(it[k] ?? "—")}</td>`).join("")}
        <td class="mono muted">${esc(it.id)}</td>
        <td><div class="row-actions">
          <button class="btn btn-xs" data-edit="${kind}:${esc(it.id)}">Edit</button>
          <button class="btn btn-xs btn-danger" data-del="${kind}:${esc(it.id)}">Delete</button>
        </div></td></tr>`).join("")}</tbody></table></div></div>`
      : emptyState(`No ${kind}`, `No ${kind} defined yet.`) }`;

  body.innerHTML = `
    <h3 class="section-title">Clients</h3>
    <div id="grpClients">${crud("clients", clients, [["description", "Description"]])}</div>
    <h3 class="section-title">Templates</h3>
    <div id="grpTemplates">${crud("templates", templates, [["description", "Description"]])}</div>
    <p class="note">Groups are organized through clients and templates — the backend exposes these via
    <span class="mono">/api/clients</span> and <span class="mono">/api/templates</span>. Profiles reference a client via <span class="mono">clientId</span>.</p>`;

  const ep = (kind) => `/api/${kind}`;
  $$("[data-add]", body).forEach((b) => b.onclick = () => openGroupEdit(b.dataset.add, null));
  $$("[data-edit]", body).forEach((b) => b.onclick = () => {
    const [kind, id] = b.dataset.edit.split(/:(.+)/);
    const list = kind === "clients" ? clients : templates;
    openGroupEdit(kind, list.find((x) => x.id === id));
  });
  $$("[data-del]", body).forEach((b) => b.onclick = async () => {
    const [kind, id] = b.dataset.del.split(/:(.+)/);
    if (!confirm(`Delete this ${kind.slice(0, -1)}?`)) return;
    const r = await apiDel(`${ep(kind)}/${encodeURIComponent(id)}`);
    toast(r.ok ? "Deleted" : "Delete failed", r.ok ? "ok" : "err");
    if (r.ok) { invalidate(kind); route(); }
  });

  function openGroupEdit(kind, item) {
    const isNew = !item;
    openModal((isNew ? "Add " : "Edit ") + kind.slice(0, -1), `
      <div class="form-grid">
        <label class="field"><span class="field-label">Name *</span><input type="text" id="gName" value="${esc((item && item.name) || "")}"></label>
        <label class="field"><span class="field-label">Description</span><input type="text" id="gDesc" value="${esc((item && item.description) || "")}"></label>
      </div>`,
      `<button class="btn" id="mCancel">Cancel</button><button class="btn btn-primary" id="mSave">Save</button>`);
    $("#mCancel").onclick = closeModal;
    $("#mSave").onclick = async () => {
      const name = $("#gName").value.trim();
      if (!name) { toast("Name is required", "err"); return; }
      const payload = { name, description: $("#gDesc").value.trim() };
      const r = isNew ? await apiPost(ep(kind), payload)
        : await apiPut(`${ep(kind)}/${encodeURIComponent(item.id)}`, payload);
      closeModal();
      toast(r.ok ? "Saved" : "Save failed", r.ok ? "ok" : "err");
      if (r.ok) { invalidate(kind); route(); }
    };
  }
}

async function setBackups(body) {
  const r = await apiGet("/api/backups");
  const backups = (r.ok && r.data && Array.isArray(r.data.backups)) ? r.data.backups : [];
  body.innerHTML = `
    ${backups.length ? `<div class="table-card"><div class="table-scroll"><table class="grid"><thead><tr>
      <th>Backup</th><th>Profile</th><th>Created</th><th>Size</th><th></th></tr></thead><tbody>
      ${backups.map((b) => {
        const id = b.id || b.backupId || b.path || b.backupPath;
        return `<tr>
          <td class="mono">${esc(b.name || id || "backup")}</td>
          <td>${val(b.profileId || b.profile_id)}</td>
          <td class="muted">${esc(b.createdAt || b.created_at || b.ts || "—")}</td>
          <td class="num">${b.size != null ? esc(b.size) : "—"}</td>
          <td><button class="btn btn-xs" data-restore="${esc(id || "")}" ${id ? "" : "disabled"}>Restore</button></td>
        </tr>`;
      }).join("")}</tbody></table></div></div>`
      : emptyState("No backups", "Backups appear here after using Backup on a profile.")}
    <p class="note">Source: GET /api/backups. Restore uses POST /api/backups/restore with backupId/backupPath + optional profileId + passphrase. Passphrases are consumed by the endpoint and never logged.</p>`;

  $$("[data-restore]", body).forEach((b) => b.onclick = () => {
    openModal("Restore backup", `
      <label class="field"><span class="field-label">Target profile ID (optional — defaults to the backup's profile)</span>
        <input type="text" id="rsPid" placeholder="profile_…"></label>
      <label class="field"><span class="field-label">Passphrase (if encrypted)</span><input type="password" id="rsPass" autocomplete="off"></label>
      <p class="note">The passphrase is sent once to the restore endpoint and is never stored or logged by the UI.</p>`,
      `<button class="btn" id="mCancel">Cancel</button><button class="btn btn-primary" id="mGo">Restore</button>`);
    $("#mCancel").onclick = closeModal;
    $("#mGo").onclick = async () => {
      const payload = { backupId: b.dataset.restore, profileId: $("#rsPid").value.trim() || undefined };
      const pass = $("#rsPass").value;
      if (pass) payload.passphrase = pass;
      $("#rsPass").value = "";
      const rr = await apiPost("/api/backups/restore", payload);
      closeModal();
      toast(rr.ok && rr.data && rr.data.ok ? "Restore completed" : "Restore failed: " + apiErr(rr), rr.ok ? "ok" : "err");
    };
  });
}

async function setExtensions(body) {
  const sys = cache.sysHealth || (await apiGet("/api/health")).data;
  const extOk = sys && sys.extension_ok;
  body.innerHTML = `
    <div class="card"><div class="card-body"><h3 class="section-title" style="margin-top:0">Bundled extension</h3>${kvRows([
      ["Manifest present", extOk ? badge("yes", "green") : badge("no", "amber")],
      ["Note", `<span class="muted">The backend ships one bundled extension used for fingerprint/CDP injection. It is loaded automatically when profiles launch.</span>`],
    ])}
    <p class="note">Source: GET /api/health → extension_ok.</p></div></div>
    ${emptyState("No extension management API",
      "The current backend does not expose per-profile extension install/uninstall endpoints, so there is nothing to list or manage here.")}`;
}

/* ============================== Live updates ==============================
 * Chain: WebSocket /ws/events → SSE /api/events/stream → poll every 15s.
 * Everything degrades gracefully; failures never break the console. */
const live = { mode: "idle", ws: null, es: null, pollTimer: null, started: false };

function handleEvent(ev) {
  if (!ev || typeof ev !== "object") return;
  const type = String(ev.type || ev.event || "");
  if (type === "profile.status" || type === "profile_status") {
    const id = ev.profile_id || ev.profileId || ev.id;
    const status = ev.status || ev.state;
    if (id && status) patchProfileRow(id, { status });
    if (currentRoute && currentRoute.page === "dashboard") { /* dashboard refreshes on next visit */ }
  } else if (type === "job.progress" || type === "job_progress" || type === "job.status") {
    const job = { id: ev.job_id || ev.jobId || ev.id, status: ev.status, progress: ev.progress };
    if (job.id) patchJobRow(job);
  } else if (type === "profiles.changed" || type === "profiles_changed") {
    invalidate("profiles");
    if (currentRoute && currentRoute.page === "profiles") refreshProfilesTable(true);
  } else if (type === "jobs.changed" || type === "jobs_changed") {
    invalidate("jobs"); jobsView.cache = [];
    if (currentRoute && currentRoute.page === "jobs") loadJobsPage();
  }
}

function startPolling() {
  if (live.pollTimer) return;
  live.mode = "poll";
  const tick = async () => {
    try {
      refreshHealthPill();
      if (currentRoute && currentRoute.page === "profiles" && pv.initialized) {
        const r = await apiGet("/api/profiles?" + new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(pv.offset) }).toString());
        if (r.ok && r.data) {
          const items = Array.isArray(r.data) ? r.data : (r.data.items || []);
          items.forEach((p) => patchProfileRow(p.id, p));
        }
      }
    } catch (e) { /* poll failures are silent */ }
  };
  tick();
  live.pollTimer = setInterval(tick, 15000);
}

function startSSE() {
  try {
    const es = new EventSource("/api/events/stream");
    live.es = es;
    live.mode = "sse";
    es.onmessage = (m) => {
      try { handleEvent(JSON.parse(m.data)); } catch (e) { /* ignore malformed */ }
    };
    es.onerror = () => {
      try { es.close(); } catch (e) {}
      live.es = null;
      startPolling();
    };
  } catch (e) {
    startPolling();
  }
}

function startLive() {
  if (live.started) return;
  live.started = true;
  let ws;
  try {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/ws/events");
  } catch (e) {
    startSSE();
    return;
  }
  live.ws = ws;
  live.mode = "ws";
  let opened = false;
  const fallback = () => {
    if (opened) return;
    opened = true;
    try { ws.close(); } catch (e) {}
    live.ws = null;
    startSSE();
  };
  ws.onopen = () => { opened = true; };
  ws.onmessage = (m) => {
    try { handleEvent(JSON.parse(m.data)); } catch (e) { /* ignore malformed */ }
  };
  ws.onerror = fallback;
  ws.onclose = () => {
    if (!opened) { startSSE(); return; }
    /* Reconnect once; if that fails, fall back down the chain */
    live.ws = null;
    setTimeout(() => {
      if (live.mode !== "ws") return;
      live.started = false;
      startLive();
    }, 10000);
  };
  /* If the socket never opens, move on to SSE after 5s */
  setTimeout(() => { if (!opened && live.mode === "ws") fallback(); }, 5000);
}

/* ============================== Global search ============================== */
function initSearch() {
  const input = $("#globalSearch"), box = $("#searchResults");
  const run = debounce(async () => {
    const q = input.value.trim().toLowerCase();
    if (q.length < 2) { box.hidden = true; return; }
    const profiles = await loadAllProfiles();
    const hits = profiles.filter((p) =>
      [p.name, p.id, (p.tags || []).join(" ")].filter(Boolean).join(" ").toLowerCase().includes(q)).slice(0, 8);
    if (!hits.length) { box.innerHTML = `<div style="padding:10px 12px" class="muted">No matches</div>`; box.hidden = false; return; }
    box.innerHTML = hits.map((p) =>
      `<div class="search-result" data-sid="${esc(p.id)}">${runDot(p)}
       <span><strong>${esc(p.name || p.id)}</strong></span>
       <span class="sub mono">${shortId(p.id)}</span></div>`).join("");
    box.hidden = false;
    $$("[data-sid]", box).forEach((el) => el.onclick = () => {
      location.hash = "#/profile/" + encodeURIComponent(el.dataset.sid);
      box.hidden = true; input.value = "";
    });
  }, 220);
  input.addEventListener("input", run);
  input.addEventListener("blur", () => setTimeout(() => { box.hidden = true; }, 200));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { location.hash = "#/profiles?q=" + encodeURIComponent(input.value.trim()); box.hidden = true; }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement.tagName)) {
      e.preventDefault(); input.focus();
    }
  });
}

/* ============================== Init ============================== */
function init() {
  renderNav("dashboard");
  $("#btnNewProfile").onclick = openWizard;
  $("#btnRefresh").onclick = () => {
    invalidate("profiles", "healthMap", "jobs", "gateways", "clients", "templates", "devices");
    cache.fp = {}; cache.netHealth = {};
    route();
  };
  $("#navCollapse").onclick = () => toggleSidebar(true);
  $("#navExpand").onclick = () => toggleSidebar(false);
  $("#modalClose").onclick = closeModal;
  $("#modalBackdrop").addEventListener("click", (e) => { if (e.target.id === "modalBackdrop") closeModal(); });
  $("#drawerClose").onclick = closeDrawer;
  $("#drawerBackdrop").onclick = closeDrawer;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeModal(); closeDrawer(); }
  });
  window.addEventListener("hashchange", route);
  initAuth();
  initTheme();
  initSearch();
  refreshHealthPill();
  setInterval(refreshHealthPill, 30000);
  if (storeGet("mp_nav_collapsed") === "1") toggleSidebar(true);
  if (!location.hash) location.hash = "#/dashboard";
  hideLoginGate();
  $("#app").hidden = false;
  route();
  startLive();
}
document.addEventListener("DOMContentLoaded", init);
