/* Multiprofile Operations Console — vanilla JS SPA.
 * All numbers/badges come from live API responses. Unreachable endpoints
 * render as "—", never fabricated values. Secrets are never displayed.
 */
"use strict";

/* ============================== Utilities ============================== */
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const esc = (v) => String(v == null ? "" : v)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const cssEsc = (typeof CSS !== "undefined" && CSS.escape)
  ? (v) => CSS.escape(v)
  : (v) => String(v).replace(/[^a-zA-Z0-9_-]/g, (c) => "\\" + c);
const NA = '<span class="muted">—</span>';
const val = (v) => (v === undefined || v === null || v === "") ? NA : esc(v);
const num = (v) => (v === undefined || v === null || v === "") ? NA
  : `<span class="num">${esc(v)}</span>`;

function debounce(fn, ms) {
  let t = null;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
function timeAgo(ts) {
  if (!ts) return null;
  const d = new Date(ts);
  if (isNaN(d)) return null;
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  const days = Math.floor(s / 86400);
  return days + "d ago";
}
function fmtDur(sec) {
  if (sec == null) return null;
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${sec}s`;
}
function shortId(id) {
  if (!id) return "—";
  const s = String(id);
  return esc(s.length > 18 ? s.slice(0, 16) + "…" : s);
}

/* ============================== API client ============================= */
const TOKEN_KEY = "mp_api_token";
let apiToken = localStorage.getItem(TOKEN_KEY) || "";
let authFailed = false;

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (apiToken) headers["Authorization"] = "Bearer " + apiToken;
  let res;
  try {
    res = await fetch(path, Object.assign({}, opts, { headers }));
  } catch (e) {
    return { ok: false, status: 0, networkError: true, error: String(e && e.message || e) };
  }
  if (res.status === 401) {
    authFailed = true;
    return { ok: false, status: 401, error: "unauthorized" };
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON */ }
  return { ok: res.ok, status: res.status, data };
}
const apiGet = (p) => api(p, { method: "GET" });
const apiPost = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });
const apiPut = (p, body) => api(p, { method: "PUT", body: JSON.stringify(body || {}) });
const apiDel = (p) => api(p, { method: "DELETE" });

/* ============================== Cache ============================== */
const cache = {
  profiles: null, health: null, clients: null, templates: null,
  devices: null, deviceDetail: {}, gateways: null, jobs: null,
  backups: null, audit: null, ua: null, sysHealth: null,
  fp: {},          // profileId -> fingerprint report
  netHealth: {},   // profileId -> network-health
};
function invalidate(...keys) { keys.forEach((k) => { cache[k] = null; }); }

async function loadProfiles(force) {
  if (!force && cache.profiles) return cache.profiles;
  const r = await apiGet("/api/profiles");
  let items = [];
  if (r.ok && r.data) {
    if (Array.isArray(r.data)) items = r.data;
    else if (Array.isArray(r.data.items)) items = r.data.items;
  }
  cache.profiles = items;
  cache._profilesFailed = !r.ok;
  return items;
}
async function loadHealthMap(force) {
  if (!force && cache.health) return cache.health;
  const r = await apiGet("/api/health/profiles");
  const map = {};
  if (r.ok && r.data && Array.isArray(r.data.profiles)) {
    r.data.profiles.forEach((h) => { map[h.profile_id || h.profileId] = h; });
  }
  cache.health = map;
  cache._healthFailed = !r.ok;
  return map;
}
async function loadClients(force) {
  if (!force && cache.clients) return cache.clients;
  const r = await apiGet("/api/clients");
  cache.clients = (r.ok && Array.isArray(r.data)) ? r.data : [];
  return cache.clients;
}
async function loadTemplates(force) {
  if (!force && cache.templates) return cache.templates;
  const r = await apiGet("/api/templates");
  cache.templates = (r.ok && Array.isArray(r.data)) ? r.data : [];
  return cache.templates;
}
async function loadDevices(force) {
  if (!force && cache.devices) return cache.devices;
  const r = await apiGet("/api/devices");
  if (r.ok && r.data && Array.isArray(r.data.devices)) {
    cache.devices = r.data.devices;
    cache._devicesFailed = false;
  } else {
    cache.devices = null;
    cache._devicesFailed = true;
  }
  return cache.devices;
}
async function loadGateways(force) {
  if (!force && cache.gateways) return cache.gateways;
  const r = await apiGet("/api/gateways");
  cache.gateways = (r.ok && r.data && Array.isArray(r.data.gateways)) ? r.data.gateways : [];
  cache._gatewaysFailed = !r.ok;
  return cache.gateways;
}
async function loadJobs(force) {
  if (!force && cache.jobs) return cache.jobs;
  const r = await apiGet("/api/jobs?limit=100");
  cache.jobs = (r.ok && r.data && Array.isArray(r.data.jobs)) ? r.data.jobs : [];
  cache._jobsFailed = !r.ok;
  return cache.jobs;
}
async function loadFp(profileId, force) {
  if (!force && cache.fp[profileId]) return cache.fp[profileId];
  const r = await apiGet(`/api/profiles/${encodeURIComponent(profileId)}/fingerprint`);
  cache.fp[profileId] = r.ok ? r.data : null;
  return cache.fp[profileId];
}
async function loadNetHealth(profileId, force) {
  if (!force && cache.netHealth[profileId] !== undefined) return cache.netHealth[profileId];
  const r = await apiGet(`/api/profiles/${encodeURIComponent(profileId)}/network-health`);
  cache.netHealth[profileId] = r.ok ? r.data : null;
  return cache.netHealth[profileId];
}
function clientName(id) {
  const c = (cache.clients || []).find((x) => x.id === id);
  return c ? (c.name || c.id) : null;
}
function isRunning(p) {
  const s = String(p.launchState || "").toUpperCase();
  return s === "RUNNING" || s === "VERIFIED" || s === "CDP_READY" || s === "WINDOW_FOUND" || s === "POSITIONING";
}

/* ============================== Components ============================== */
function toast(msg, type) {
  const t = document.createElement("div");
  t.className = "toast" + (type ? " " + type : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; }, 3400);
  setTimeout(() => t.remove(), 3800);
}
function badge(text, kind) {
  return `<span class="badge ${kind || ""}">${esc(text)}</span>`;
}
function healthBadge(status) {
  const s = String(status || "").toLowerCase();
  if (s === "healthy") return badge("Healthy", "green");
  if (s === "proxy_failed") return badge("Proxy failed", "red");
  if (s === "stopped") return badge("Stopped", "grey");
  if (s === "degraded") return badge("Degraded", "amber");
  return NA;
}
function runBadge(p) {
  return isRunning(p)
    ? `<span class="badge green"><span class="dot-run"></span>&nbsp;Running</span>`
    : `<span class="badge grey"><span class="dot-stop"></span>&nbsp;Stopped</span>`;
}
function supportBadge(s) {
  const v = String(s || "").toUpperCase();
  if (v === "VALIDATED") return badge("✓ Validated", "green");
  if (v === "PARTIAL") return badge("⚠ Partial", "amber");
  if (v === "UNSUPPORTED") return badge("✕ Unsupported", "red");
  if (v === "PENDING") return badge("… Pending", "grey");
  return NA;
}
function statusBadge(s) {
  const v = String(s || "").toUpperCase();
  if (v === "PASS" || v === "SUPPORTED") return badge(v, "green");
  if (v === "WARNING" || v === "PARTIAL") return badge(v, "amber");
  if (v === "ERROR" || v === "FAIL" || v === "UNAVAILABLE" || v === "UNSUPPORTED") return badge(v, "red");
  return badge(v || "—", "grey");
}
function emptyState(icon, title, text, actionHtml) {
  return `<div class="empty"><div class="big">${icon}</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${actionHtml || ""}</div>`;
}
function skelRow(cols) {
  return `<tr>${Array.from({ length: cols }, () => '<td><div class="skel">&nbsp;</div></td>').join("")}</tr>`;
}

/* Modal */
function openModal(title, bodyHtml, footHtml) {
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = bodyHtml;
  const foot = $("#modalFoot");
  if (footHtml) { foot.innerHTML = footHtml; foot.hidden = false; }
  else { foot.innerHTML = ""; foot.hidden = true; }
  $("#modalBackdrop").hidden = false;
}
function closeModal() { $("#modalBackdrop").hidden = true; $("#modalBody").innerHTML = ""; }

/* Drawer */
function openDrawer(title, bodyHtml) {
  $("#drawerTitle").textContent = title;
  $("#drawerBody").innerHTML = bodyHtml;
  $("#drawerBackdrop").hidden = false;
  $("#drawer").hidden = false;
}
function closeDrawer() {
  $("#drawerBackdrop").hidden = true;
  $("#drawer").hidden = true;
  $("#drawerBody").innerHTML = "";
}

/* Masked proxy display — never show passwords */
function proxyLabel(p) {
  const host = p.proxyHost || p.proxy_host;
  if (!host) return NA;
  const proto = (p.proxyType || p.networkMode || "http");
  const port = p.proxyPort ? ":" + p.proxyPort : "";
  const auth = (p.proxyUser || p.hasProxyAuth) ? " •••@" : "";
  return `<span class="mono">${esc(String(proto).toLowerCase())}://${auth}${esc(host)}${esc(port)}</span>`;
}

/* ============================== Navigation ============================== */
const NAV = [
  { id: "dashboard", label: "Dashboard", icon: "▦", route: "#/dashboard" },
  { id: "profiles", label: "Profiles", icon: "◉", route: "#/profiles" },
  { id: "groups", label: "Groups", icon: "🏷", route: "#/groups" },
  { id: "proxies", label: "Proxies", icon: "🌐", route: "#/proxies" },
  { id: "devices", label: "Devices", icon: "📱", route: "#/devices" },
  { id: "fingerprints", label: "Fingerprints", icon: "🧬", route: "#/fingerprints" },
  { id: "automation", label: "Automation", icon: "⚙", route: "#/automation" },
  { id: "tasks", label: "Tasks", icon: "✓", route: "#/tasks" },
  { id: "gateways", label: "Gateways", icon: "🛰", route: "#/gateways" },
  { id: "extensions", label: "Extensions", icon: "🧩", route: "#/extensions" },
  { id: "backups", label: "Backups", icon: "💾", route: "#/backups" },
  { id: "audit", label: "Audit Logs", icon: "📜", route: "#/audit" },
  { id: "settings", label: "Settings", icon: "🔧", route: "#/settings" },
];
function renderNav(activeId, counts) {
  $("#mainNav").innerHTML = NAV.map((n) => {
    const c = counts && counts[n.id] != null ? `<span class="count">${esc(counts[n.id])}</span>` : "";
    return `<a href="${n.route}" class="${n.id === activeId ? "active" : ""}" data-nav="${n.id}">
      <span class="nav-ico">${n.icon}</span><span>${esc(n.label)}</span>${c}</a>`;
  }).join("");
}

/* ============================== Router ============================== */
const TITLES = {
  dashboard: ["Dashboard", "Live operational overview"],
  profiles: ["Profiles", "Browser profiles — launch, monitor, manage"],
  groups: ["Groups", "Clients and templates"],
  proxies: ["Proxies", "Test and manage proxy configurations"],
  devices: ["Device Library", "Device presets for profile creation"],
  fingerprints: ["Fingerprints", "Per-profile fingerprint inspection"],
  automation: ["Automation", "Job queue and bulk operations"],
  tasks: ["Tasks", "Background job monitor"],
  gateways: ["Gateways", "Proxy gateway providers"],
  extensions: ["Extensions", "Browser extension status"],
  backups: ["Backups", "Profile backups and restore"],
  audit: ["Audit Logs", "Action history"],
  settings: ["Settings", "Console and backend configuration"],
  login: ["Sign in", "API access token required"],
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
  if (authFailed) { renderLogin(); return; }
  const { page, param, query } = parseHash();
  currentRoute = { page, param, query };
  const title = TITLES[page] || TITLES.dashboard;
  $("#pageTitle").textContent = title[0];
  $("#pageSub").textContent = title[1];
  renderNav(page === "profile" ? "profiles" : page);
  $("#sidebar").classList.remove("open");
  const view = $("#view");
  view.innerHTML = `<div class="grid cards-4">${"<div class='card'><div class='skel'>&nbsp;</div></div>".repeat(4)}</div>`;
  try {
    switch (page) {
      case "dashboard": await vDashboard(view); break;
      case "profiles": await vProfiles(view, query); break;
      case "profile": await vProfileDetail(view, param, query); break;
      case "groups": await vGroups(view); break;
      case "proxies": await vProxies(view); break;
      case "devices": await vDevices(view); break;
      case "fingerprints": await vFingerprints(view, query); break;
      case "automation": await vAutomation(view); break;
      case "tasks": await vTasks(view); break;
      case "gateways": await vGateways(view); break;
      case "extensions": await vExtensions(view); break;
      case "backups": await vBackups(view); break;
      case "audit": await vAudit(view); break;
      case "settings": await vSettings(view); break;
      default: await vDashboard(view);
    }
  } catch (e) {
    view.innerHTML = emptyState("⚠", "View failed to render",
      "An unexpected error occurred: " + String(e && e.message || e));
  }
  view.scrollTop = 0;
  $("#app").setAttribute("aria-busy", "false");
}

function renderLogin() {
  renderNav("login");
  $("#pageTitle").textContent = "Sign in";
  $("#pageSub").textContent = "API access token required";
  $("#view").innerHTML = `
    <div class="login-wrap"><div class="card">
      <h3>API token required</h3>
      <p class="note">The backend rejected the request (401). Paste the value of
      <span class="mono">MULTIPROFILE_API_TOKEN</span> — it is stored only in this
      browser's localStorage.</p>
      <label class="fld">API token
        <input type="password" id="loginToken" value="${esc(apiToken)}" autocomplete="off">
      </label>
      <div style="margin-top:12px;display:flex;gap:8px;justify-content:flex-end">
        <button class="btn" id="loginClear">Clear</button>
        <button class="btn btn-primary" id="loginSave">Save &amp; retry</button>
      </div>
    </div></div>`;
  $("#loginSave").onclick = () => {
    apiToken = $("#loginToken").value.trim();
    if (apiToken) localStorage.setItem(TOKEN_KEY, apiToken);
    else localStorage.removeItem(TOKEN_KEY);
    authFailed = false;
    invalidate("profiles", "health", "clients", "templates", "devices", "gateways", "jobs", "backups", "audit");
    route();
  };
  $("#loginClear").onclick = () => {
    apiToken = ""; localStorage.removeItem(TOKEN_KEY); $("#loginToken").value = "";
    toast("Token cleared");
  };
}

/* ============================== Shared actions ============================== */
async function actLaunch(ids, opts) {
  const r = await apiPost("/api/jobs", Object.assign({ type: ids.length > 1 ? "bulk_launch" : "launch_profile", profileIds: ids }, opts || {}));
  if (r.ok && r.data && r.data.ok) {
    toast(`Launch job queued (${ids.length} profile${ids.length > 1 ? "s" : ""})`, "ok");
    invalidate("jobs");
  } else toast("Launch failed: " + esc((r.data && (r.data.error || r.data.message)) || r.error || "unknown"), "err");
}
async function actStop(ids) {
  const r = await apiPost("/api/jobs", { type: ids.length > 1 ? "bulk_stop" : "stop_profile", profileIds: ids });
  if (r.ok && r.data && r.data.ok) { toast(`Stop job queued (${ids.length})`, "ok"); invalidate("jobs"); }
  else toast("Stop failed: " + esc((r.data && (r.data.error || r.data.message)) || r.error || "unknown"), "err");
}
async function actDelete(id) {
  const ok = confirm("Delete this profile? Its browser storage will be purged.");
  if (!ok) return;
  const r = await apiDel(`/api/profiles/${encodeURIComponent(id)}`);
  if (r.ok && r.data && r.data.ok) {
    toast("Profile deleted", "ok");
    invalidate("profiles"); delete cache.fp[id]; delete cache.netHealth[id];
    route();
  } else toast("Delete failed: " + esc((r.data && r.data.error) || r.error || "unknown"), "err");
}
function exportProfiles(profiles) {
  const safe = profiles.map((p) => {
    const c = Object.assign({}, p);
    delete c.proxyPass; delete c.proxy_password;
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
    $("#versionText").textContent = `multiprofile-native ${r.data.version || ""} · chrome ${r.data.chrome_ok ? "ok" : "missing"}`;
    cache.sysHealth = r.data;
  } else if (r.status === 401) {
    dot.className = "dot bad"; txt.textContent = "Auth required";
  } else {
    dot.className = "dot bad";
    txt.textContent = r.networkError ? "Unreachable" : "Error " + r.status;
  }
}

/* ============================== Dashboard ============================== */
function statCard(label, valueHtml, hint, src, tone) {
  return `<div class="card stat-card ${tone || ""}">
    <div class="label">${esc(label)}</div>
    <div class="value">${valueHtml}</div>
    <div class="hint">${esc(hint || "")}</div>
    ${src ? `<div class="src">${esc(src)}</div>` : ""}
  </div>`;
}
const DASH_NA = statCard;

async function vDashboard(view) {
  const [profiles, healthMap] = await Promise.all([loadProfiles(), loadHealthMap()]);
  const profFailed = cache._profilesFailed, healthFailed = cache._healthFailed;

  const total = profFailed ? null : profiles.length;
  const running = profFailed ? null : profiles.filter(isRunning).length;
  const stopped = (total == null || running == null) ? null : total - running;

  let healthy = null, netErr = null;
  if (!healthFailed) {
    const vals = Object.values(healthMap);
    healthy = vals.filter((h) => h.overallOk).length;
    netErr = vals.filter((h) => h.proxy_ok === false || h.status === "proxy_failed").length;
  }

  const activeProxies = profFailed ? null
    : profiles.filter((p) => p.proxyHost || String(p.networkMode || "").toUpperCase() !== "DIRECT" && p.networkMode).length;

  const jobs = await loadJobs();
  const jobsFailed = cache._jobsFailed;
  const activeTasks = jobsFailed ? null
    : jobs.filter((j) => ["queued", "running", "in_progress"].includes(String(j.status).toLowerCase())).length;

  const card = (label, v, hint, src, tone) =>
    statCard(label, v == null ? '<span class="na">—</span>' : esc(v), hint, src, tone);

  view.innerHTML = `
    <div class="grid cards-4">
      ${card("Total Profiles", total, "from GET /api/profiles", "/api/profiles")}
      ${card("Running", running, "launchState is a running state", "/api/profiles", "good")}
      ${card("Stopped", stopped, "total minus running", "/api/profiles")}
      ${card("Healthy", healthy, "overallOk across profiles", "/api/health/profiles", healthy ? "good" : "")}
      ${card("Network Errors", netErr, "proxy check failed", "/api/health/profiles", netErr ? "bad" : "")}
      ${card("Fingerprint Warnings", '<span class="na" id="dashFpWarn">—</span>', "profiles with fingerprint warnings", "/api/profiles/:id/fingerprint", "warn")}
      ${card("Active Proxies", activeProxies, "profiles with proxy configured", "/api/profiles")}
      ${card("Active Tasks", activeTasks, "jobs queued or running", "/api/jobs")}
    </div>

    <div class="section-head"><h2>Fleet snapshot</h2><div class="spacer"></div>
      <button class="btn btn-sm" id="dashRefresh">⟳ Refresh</button></div>
    <div class="grid cards-2">
      <div class="card"><h3>Runtime states</h3><div id="dashStates"><div class="skel">&nbsp;</div></div></div>
      <div class="card"><h3>Recent jobs</h3><div id="dashJobs"><div class="skel">&nbsp;</div></div></div>
    </div>
    <div class="section-head"><h2>System</h2></div>
    <div class="card" id="dashSys"><div class="skel">&nbsp;</div></div>`;

  $("#dashRefresh").onclick = () => {
    invalidate("profiles", "health", "jobs");
    cache.fp = {}; cache.netHealth = {};
    route();
  };

  // Fingerprint warnings — fetched per profile in the background; "—" until known.
  (async () => {
    if (profFailed) return;
    const settled = await Promise.allSettled(profiles.map((p) => loadFp(p.id)));
    const el = $("#dashFpWarn");
    if (!el) return;
    let n = 0;
    settled.forEach((s) => {
      if (s.status === "fulfilled" && s.value && Array.isArray(s.value.warnings) && s.value.warnings.length) n++;
    });
    el.textContent = String(n);
    el.classList.remove("na");
  })();

  // Runtime state breakdown
  const states = {};
  profiles.forEach((p) => {
    const s = p.launchState || "IDLE";
    states[s] = (states[s] || 0) + 1;
  });
  $("#dashStates").innerHTML = Object.keys(states).length
    ? Object.entries(states).sort((a, b) => b[1] - a[1])
        .map(([s, n]) => `<div class="check-row"><span style="flex:1">${badge(s, s === "RUNNING" ? "green" : s.includes("FAIL") ? "red" : "grey")}</span><span class="num">${n}</span></div>`).join("")
    : `<p class="muted">No profiles.</p>`;

  $("#dashJobs").innerHTML = jobs.length
    ? jobs.slice(0, 5).map((j) => `<div class="check-row"><span style="flex:1">
        <span class="mono">${esc(j.id)}</span> <span class="muted">${esc(j.type || "")}</span></span>
        ${badge(String(j.status || "unknown"), String(j.status).toLowerCase() === "done" || String(j.status).toLowerCase() === "completed" ? "green" : String(j.status).toLowerCase().includes("fail") ? "red" : "cyan")}
      </div>`).join("")
    : `<p class="muted">No jobs recorded.</p>`;

  const sys = cache.sysHealth;
  $("#dashSys").innerHTML = sys ? `
    <dl class="kv">
      <dt>App</dt><dd class="mono">${esc(sys.app || "—")} ${esc(sys.version || "")}</dd>
      <dt>Chrome</dt><dd>${sys.chrome_ok ? badge("found", "green") : badge("missing", "red")} <span class="mono muted">${esc(sys.chrome_path || "")}</span></dd>
      <dt>Profiles dir writable</dt><dd>${sys.profiles_writable ? "yes" : "no"}</dd>
      <dt>Slots</dt><dd class="mono">${esc(JSON.stringify(sys.slots ?? "—"))}</dd>
      <dt>Max concurrent</dt><dd class="num">${esc(sys.maxConcurrentProfiles ?? "—")}</dd>
      <dt>Sandbox</dt><dd class="mono">${esc(sys.sandbox ? (sys.sandbox.ok ? "ok" : sys.sandbox.reason || "not ok") : "—")}</dd>
    </dl>
    <p class="note">Source: GET /api/health (unauthenticated by design).</p>`
    : `<p class="muted">System info unavailable.</p>`;
}

/* ============================== Profiles ============================== */
const profView = {
  q: "", fClient: "", fTag: "", fDevice: "", fNetwork: "", fHealth: "", fFp: "",
  sort: "name", dir: 1, page: 0, per: 25, selected: new Set(),
};
function fpWarnCount(p) {
  const r = cache.fp[p.id];
  if (!r) return null;
  return Array.isArray(r.warnings) ? r.warnings.length : 0;
}

async function vProfiles(view, query) {
  if (query.q) profView.q = query.q;
  const [profiles, healthMap, clients] = await Promise.all([loadProfiles(), loadHealthMap(), loadClients()]);
  if (cache._profilesFailed) {
    view.innerHTML = emptyState("⚠", "Could not load profiles", "GET /api/profiles failed. Check the backend and retry.",
      `<button class="btn btn-primary" onclick="location.reload()">Retry</button>`);
    return;
  }
  renderProfilesTable(view, profiles, healthMap, clients);

  // Lazy per-row enrichment: fingerprint warnings + network egress (visible rows only)
  const visible = currentRows(profiles, healthMap).slice(
    profView.page * profView.per, profView.page * profView.per + profView.per);
  visible.forEach(async (p) => {
    const [fp, nh] = await Promise.all([loadFp(p.id), loadNetHealth(p.id)]);
    if (!document.body.contains($("#view"))) return;
    const row = $(`tr[data-pid="${cssEsc(p.id)}"]`);
    if (!row) return;
    const fpCell = row.querySelector(".c-fp");
    const ipCell = row.querySelector(".c-ip");
    if (fpCell && fp) {
      const w = Array.isArray(fp.warnings) ? fp.warnings.length : 0;
      fpCell.innerHTML = w ? badge(w + " warning" + (w > 1 ? "s" : ""), "amber") : badge("clean", "green");
    }
    if (ipCell && nh) {
      const ip = nh.proxyHealth && nh.proxyHealth.egress_ip || nh.egressIp || (nh.proxyHealth && nh.proxyHealth.egressIp);
      ipCell.innerHTML = ip ? `<span class="mono">${esc(ip)}</span>` : NA;
    }
  });
}

function currentRows(profiles, healthMap) {
  const q = profView.q.trim().toLowerCase();
  let rows = profiles.filter((p) => {
    if (q) {
      const hay = [p.name, p.id, p.clientId, (p.tags || []).join(" "), p.proxyHost, p.deviceId, p.notes]
        .filter(Boolean).join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (profView.fClient && p.clientId !== profView.fClient) return false;
    if (profView.fTag && !(p.tags || []).includes(profView.fTag)) return false;
    if (profView.fDevice) {
      const d = String(p.deviceId || "").toLowerCase();
      if (profView.fDevice === "mobile" && !/mobile|android|iphone/i.test(d + " " + (p.userAgent || ""))) return false;
      if (profView.fDevice === "desktop" && /mobile|android|iphone/i.test(d + " " + (p.userAgent || ""))) return false;
      if (profView.fDevice === "set" && !p.deviceId) return false;
      if (profView.fDevice === "unset" && p.deviceId) return false;
    }
    if (profView.fNetwork) {
      const hasProxy = !!p.proxyHost;
      if (profView.fNetwork === "proxy" && !hasProxy) return false;
      if (profView.fNetwork === "direct" && hasProxy) return false;
    }
    if (profView.fHealth) {
      const h = healthMap[p.id];
      const st = h ? String(h.status || "").toLowerCase() : "unknown";
      if (profView.fHealth === "healthy" && st !== "healthy") return false;
      if (profView.fHealth === "error" && !(st === "proxy_failed" || st === "degraded")) return false;
    }
    if (profView.fFp) {
      const w = fpWarnCount(p);
      if (profView.fFp === "warn" && !(w > 0)) return false;
      if (profView.fFp === "clean" && w !== 0) return false;
    }
    return true;
  });
  const key = profView.sort, dir = profView.dir;
  rows.sort((a, b) => {
    let x, y;
    switch (key) {
      case "name": x = (a.name || "").toLowerCase(); y = (b.name || "").toLowerCase(); break;
      case "runtime": x = isRunning(a) ? 1 : 0; y = isRunning(b) ? 1 : 0; break;
      case "health": x = (healthMap[a.id] || {}).status || ""; y = (healthMap[b.id] || {}).status || ""; break;
      case "lastActive": x = a.lastActiveAt || a.updatedAt || a.createdAt || ""; y = b.lastActiveAt || b.updatedAt || b.createdAt || ""; break;
      default: x = String(a[key] || ""); y = String(b[key] || "");
    }
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  });
  return rows;
}

function renderProfilesTable(view, profiles, healthMap, clients) {
  const rows = currentRows(profiles, healthMap);
  const pages = Math.max(1, Math.ceil(rows.length / profView.per));
  if (profView.page >= pages) profView.page = pages - 1;
  const slice = rows.slice(profView.page * profView.per, profView.page * profView.per + profView.per);
  const sel = profView.selected;
  const allTags = [...new Set(profiles.flatMap((p) => p.tags || []))].sort();

  const th = (label, key) => {
    const arrow = profView.sort === key ? (profView.dir === 1 ? " ▲" : " ▼") : "";
    return `<th class="sortable" data-sort="${key}">${esc(label)}${arrow}</th>`;
  };
  const opt = (v, cur, label) => `<option value="${esc(v)}" ${cur === v ? "selected" : ""}>${esc(label)}</option>`;

  view.innerHTML = `
    <div class="toolbar">
      <input type="search" id="pfQ" placeholder="Search name, id, tags, proxy…" value="${esc(profView.q)}" style="width:260px" aria-label="Search profiles">
      <select id="pfClient" aria-label="Filter by client"><option value="">All clients</option>
        ${clients.map((c) => opt(c.id, profView.fClient, c.name || c.id)).join("")}</select>
      <select id="pfTag" aria-label="Filter by tag"><option value="">All tags</option>
        ${allTags.map((t) => opt(t, profView.fTag, t)).join("")}</select>
      <select id="pfDevice" aria-label="Filter by device">
        ${opt("", profView.fDevice, "Any device")}${opt("set", profView.fDevice, "Device set")}${opt("unset", profView.fDevice, "No device")}${opt("mobile", profView.fDevice, "Mobile-like")}${opt("desktop", profView.fDevice, "Desktop-like")}
      </select>
      <select id="pfNetwork" aria-label="Filter by network">
        ${opt("", profView.fNetwork, "Any network")}${opt("proxy", profView.fNetwork, "Proxy")}${opt("direct", profView.fNetwork, "Direct")}
      </select>
      <select id="pfHealth" aria-label="Filter by health">
        ${opt("", profView.fHealth, "Any health")}${opt("healthy", profView.fHealth, "Healthy")}${opt("error", profView.fHealth, "Errors")}
      </select>
      <select id="pfFp" aria-label="Filter by fingerprint">
        ${opt("", profView.fFp, "Any fingerprint")}${opt("warn", profView.fFp, "Has warnings")}${opt("clean", profView.fFp, "Clean")}
      </select>
      <div class="spacer"></div>
      <span class="muted">${rows.length} of ${profiles.length}</span>
    </div>

    <div class="bulk-bar" id="bulkBar" ${sel.size ? "" : "hidden"}>
      <span><span class="sel-count">${sel.size}</span> selected</span>
      <button class="btn btn-sm" data-bulk="launch">▶ Launch</button>
      <button class="btn btn-sm" data-bulk="stop">■ Close</button>
      <button class="btn btn-sm" data-bulk="restart">↻ Restart</button>
      <button class="btn btn-sm" data-bulk="tile">▦ Tile</button>
      <button class="btn btn-sm" data-bulk="clone">⧉ Clone</button>
      <button class="btn btn-sm" data-bulk="backup">💾 Backup</button>
      <button class="btn btn-sm" data-bulk="health">🩺 Health check</button>
      <button class="btn btn-sm" data-bulk="export">⇩ Export</button>
      <button class="btn btn-sm btn-ghost" data-bulk="clear">Clear</button>
    </div>

    <div class="tbl-wrap"><table class="tbl" aria-label="Profiles">
      <thead><tr>
        <th style="width:34px"><input type="checkbox" id="pfAll" aria-label="Select all on page"></th>
        ${th("Profile", "name")}${th("Group", "clientId")}<th>Device</th><th>OS</th><th>IP</th><th>Country</th>
        <th>Proxy</th><th>Fingerprint</th>${th("Health", "health")}${th("Runtime", "runtime")}${th("Last active", "lastActive")}<th>Actions</th>
      </tr></thead>
      <tbody>
      ${slice.length ? slice.map((p) => profileRow(p, healthMap, sel.has(p.id))).join("") : `<tr><td colspan="14">${emptyState("◉", "No profiles match", "Adjust the search or filters.")}</td></tr>`}
      </tbody>
    </table>
    <div class="pager">
      <button class="btn btn-sm" id="pgPrev" ${profView.page === 0 ? "disabled" : ""}>‹ Prev</button>
      <span>Page ${profView.page + 1} of ${pages}</span>
      <button class="btn btn-sm" id="pgNext" ${profView.page >= pages - 1 ? "disabled" : ""}>Next ›</button>
      <div class="spacer"></div><span>25 per page</span>
    </div></div>`;

  const rerender = () => renderProfilesTable(view, profiles, healthMap, clients);
  const debQ = debounce((v) => { profView.q = v; profView.page = 0; rerender(); }, 280);
  $("#pfQ").addEventListener("input", (e) => debQ(e.target.value));
  $("#pfClient").onchange = (e) => { profView.fClient = e.target.value; profView.page = 0; rerender(); };
  $("#pfTag").onchange = (e) => { profView.fTag = e.target.value; profView.page = 0; rerender(); };
  $("#pfDevice").onchange = (e) => { profView.fDevice = e.target.value; profView.page = 0; rerender(); };
  $("#pfNetwork").onchange = (e) => { profView.fNetwork = e.target.value; profView.page = 0; rerender(); };
  $("#pfHealth").onchange = (e) => { profView.fHealth = e.target.value; profView.page = 0; rerender(); };
  $("#pfFp").onchange = (e) => { profView.fFp = e.target.value; profView.page = 0; rerender(); };
  $$("th.sortable", view).forEach((thEl) => {
    thEl.onclick = () => {
      const k = thEl.dataset.sort;
      if (profView.sort === k) profView.dir *= -1; else { profView.sort = k; profView.dir = 1; }
      rerender();
    };
  });
  $("#pgPrev").onclick = () => { if (profView.page > 0) { profView.page--; rerender(); } };
  $("#pgNext").onclick = () => { if (profView.page < pages - 1) { profView.page++; rerender(); } };

  const allBox = $("#pfAll");
  allBox.checked = slice.length > 0 && slice.every((p) => sel.has(p.id));
  allBox.onchange = () => {
    slice.forEach((p) => allBox.checked ? sel.add(p.id) : sel.delete(p.id));
    rerender();
  };
  $$('input[data-sel]', view).forEach((cb) => {
    cb.onchange = () => { cb.checked ? sel.add(cb.dataset.sel) : sel.delete(cb.dataset.sel); rerender(); };
  });
  $$("[data-bulk]", view).forEach((b) => { b.onclick = () => bulkAction(b.dataset.bulk, profiles); });
  $$("[data-row]", view).forEach((b) => {
    b.onclick = (e) => { e.stopPropagation(); rowAction(b.dataset.row, b.dataset.pid, profiles); };
  });
}

function profileRow(p, healthMap, checked) {
  const h = healthMap[p.id];
  const osGuess = osFromProfile(p);
  const country = NA; // filled lazily? network-health has no country; keep honest "—"
  return `<tr data-pid="${esc(p.id)}" class="${checked ? "selected" : ""}">
    <td><input type="checkbox" data-sel="${esc(p.id)}" ${checked ? "checked" : ""} aria-label="Select ${esc(p.name || p.id)}"></td>
    <td><a class="rowlink" href="#/profile/${esc(p.id)}">${esc(p.name || p.id)}</a>
      <div class="muted" style="font-size:11px"><span class="mono">${shortId(p.id)}</span></div></td>
    <td>${p.clientId ? esc(clientName(p.clientId) || p.clientId) : NA}</td>
    <td>${p.deviceId ? `<span class="mono">${esc(p.deviceId)}</span>` : NA}</td>
    <td>${osGuess}</td>
    <td class="c-ip">${NA}</td>
    <td>${country}</td>
    <td>${proxyLabel(p)}</td>
    <td class="c-fp"><span class="muted">…</span></td>
    <td>${h ? healthBadge(h.status) : NA}</td>
    <td>${runBadge(p)}${p.cdpPort ? ` <span class="mono muted">:${esc(p.cdpPort)}</span>` : ""}</td>
    <td class="muted">${esc(timeAgo(p.lastActiveAt || p.updatedAt) || "—")}</td>
    <td><div class="inline-actions">
      <button class="btn btn-sm" data-row="launch" data-pid="${esc(p.id)}" title="Launch">▶</button>
      <button class="btn btn-sm" data-row="stop" data-pid="${esc(p.id)}" title="Stop">■</button>
      <button class="btn btn-sm" data-row="clone" data-pid="${esc(p.id)}" title="Clone">⧉</button>
      <button class="btn btn-sm" data-row="backup" data-pid="${esc(p.id)}" title="Backup">💾</button>
      <button class="btn btn-sm btn-danger" data-row="delete" data-pid="${esc(p.id)}" title="Delete">🗑</button>
    </div></td>
  </tr>`;
}

function osFromProfile(p) {
  const fp = cache.fp[p.id];
  const plat = fp && fp.fingerprint && fp.fingerprint.platform;
  if (plat) return esc(plat);
  const ua = p.userAgent || "";
  if (/Windows/i.test(ua)) return "Win32";
  if (/Macintosh|Mac OS X/i.test(ua)) return "MacIntel";
  if (/Android/i.test(ua)) return "Android";
  if (/iPhone|iPad/i.test(ua)) return "iOS";
  if (/Linux/i.test(ua)) return "Linux";
  return NA;
}

async function bulkAction(kind, profiles) {
  const ids = [...profView.selected];
  if (!ids.length && kind !== "clear") { toast("Nothing selected", "err"); return; }
  switch (kind) {
    case "clear": profView.selected.clear(); route(); break;
    case "launch": await actLaunch(ids); break;
    case "stop": await actStop(ids); break;
    case "restart": {
      toast("Restarting: stopping first…");
      const s = await apiPost("/api/jobs", { type: ids.length > 1 ? "bulk_stop" : "stop_profile", profileIds: ids });
      if (!(s.ok && s.data && s.data.ok)) { toast("Stop step failed", "err"); break; }
      await waitForJob(s.data.job.id);
      await actLaunch(ids);
      break;
    }
    case "tile": {
      const r = await apiPost("/api/windows/tile", { profileIds: ids });
      toast(r.ok && r.data && r.data.ok ? "Tile request sent" : "Tile failed: " + esc((r.data && r.data.error) || r.error || "?"), r.ok ? "ok" : "err");
      break;
    }
    case "clone": openCloneModal(ids); break;
    case "backup": {
      for (const id of ids) await apiPost(`/api/profiles/${encodeURIComponent(id)}/backup`, { includeUserData: true });
      toast(`Backup requested for ${ids.length} profile(s)`, "ok");
      invalidate("backups");
      break;
    }
    case "health": {
      toast("Refreshing health…");
      for (const id of ids) { await apiGet(`/api/profiles/${encodeURIComponent(id)}/health`); await loadNetHealth(id, true); }
      invalidate("health"); route();
      break;
    }
    case "export": exportProfiles(profiles.filter((p) => profView.selected.has(p.id))); break;
  }
}
async function waitForJob(jobId, tries = 30) {
  for (let i = 0; i < tries; i++) {
    const r = await apiGet(`/api/jobs/${encodeURIComponent(jobId)}`);
    const st = String(r.data && r.data.job && r.data.job.status || "").toLowerCase();
    if (["done", "completed", "failed", "cancelled", "canceled"].includes(st)) return st;
    await new Promise((r2) => setTimeout(r2, 1000));
  }
  return "timeout";
}
async function rowAction(kind, id, profiles) {
  switch (kind) {
    case "launch": await actLaunch([id]); break;
    case "stop": await actStop([id]); break;
    case "clone": openCloneModal([id]); break;
    case "backup": {
      const r = await apiPost(`/api/profiles/${encodeURIComponent(id)}/backup`, { includeUserData: true });
      toast(r.ok ? "Backup requested" : "Backup failed", r.ok ? "ok" : "err");
      invalidate("backups");
      break;
    }
    case "delete": await actDelete(id); break;
  }
  setTimeout(() => { invalidate("profiles", "health"); }, 1200);
}
function openCloneModal(ids) {
  openModal(`Clone ${ids.length} profile${ids.length > 1 ? "s" : ""}`, `
    <label class="fld">Clone mode
      <select id="cloneMode">
        <option value="config_only">Config only — settings, no cookies or session</option>
        <option value="config_and_cookies">Config + cookies — settings and stored cookies</option>
        <option value="full_session">Full session — complete copy including browser data</option>
      </select></label>
    <p class="note">Uses POST /api/profiles/:id/clone with the selected mode.</p>`,
    `<button class="btn" id="mCancel">Cancel</button>
     <button class="btn btn-primary" id="mClone">Clone</button>`);
  $("#mCancel").onclick = closeModal;
  $("#mClone").onclick = async () => {
    const mode = $("#cloneMode").value;
    let n = 0;
    for (const id of ids) {
      const r = await apiPost(`/api/profiles/${encodeURIComponent(id)}/clone`, { mode });
      if (r.ok && r.data && r.data.ok) n++;
    }
    closeModal();
    toast(`Cloned ${n}/${ids.length}`, n === ids.length ? "ok" : "err");
    invalidate("profiles"); route();
  };
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
  const sec = (title, rowsHtml, note) => `<div class="panel fp-sec"><h3>${esc(title)}</h3>
    ${note ? `<p class="note" style="margin:0 0 8px">${esc(note)}</p>` : ""}
    <table class="compare-table"><thead><tr><th>Property</th><th>Expected</th><th>Runtime</th><th>Status</th></tr></thead>
    <tbody>${rowsHtml}</tbody></table></div>`;

  if (Array.isArray(components) && components.length) {
    return components.map((c) => {
      const rows = flattenFp(c.expected, "");
      const rt = c.runtime;
      const body = rows.length ? rows.map(([prop, exp]) => {
        const rv = rt ? lookupPath(rt, prop) : undefined;
        return `<tr><td class="prop">${esc(prop)}</td><td>${fmtFpVal(exp)}</td>` +
          `<td>${rt ? fmtFpVal(rv) : '<span class="muted">Not running</span>'}</td>` +
          `<td>${statusBadge(c.status)}</td></tr>`;
      }).join("") : `<tr><td colspan="4" class="muted">No expected values exposed for this section.</td></tr>`;
      return sec(c.name || "Section", body, c.note);
    }).join("");
  }
  /* Legacy fallback: flat fingerprint dict without section components. */
  const f = fp || {};
  const scr = f.screen || {};
  const ch = f.clientHints || {};
  const gpu = f.gpu || {};
  const media = f.mediaDeviceIds || {};
  const row = (prop, expected) =>
    `<tr><td class="prop">${esc(prop)}</td><td>${fmtFpVal(expected)}</td>` +
    `<td><span class="muted">Not running</span></td><td>${statusBadge("UNKNOWN")}</td></tr>`;
  const legacy = (title, rowsHtml) => sec(title, rowsHtml,
    "Legacy report format — runtime values unavailable.");
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
    container.innerHTML = emptyState("🧬", "Fingerprint unavailable",
      "GET /api/profiles/" + profileId + "/fingerprint did not return a report.");
    return;
  }
  const fp = rep.fingerprint || {};
  const warnings = rep.warnings || [];
  const components = Array.isArray(rep.components) ? rep.components : null;
  container.innerHTML = `
    ${warnings.length ? `<div class="note" style="border-color:rgba(251,191,36,.4);color:var(--amber)">
      <strong>${warnings.length} warning${warnings.length > 1 ? "s" : ""}:</strong><br>${warnings.map((w) => "• " + esc(w)).join("<br>")}</div>` : ""}
    <p class="note">${esc(rep.summary || "")}${rep.generated_at ? " · generated " + esc(rep.generated_at) : ""}</p>
    ${fpSections(fp, components)}`;
}

/* ============================== Profile detail ============================== */
const DETAIL_TABS = ["overview", "network", "fingerprint", "device", "cookies", "storage", "runtime", "health", "automation", "audit"];
async function vProfileDetail(view, id, query) {
  const profiles = await loadProfiles();
  const p = profiles.find((x) => x.id === id);
  if (!p) {
    view.innerHTML = emptyState("◉", "Profile not found", "It may have been deleted.",
      `<a class="btn btn-primary" href="#/profiles">Back to profiles</a>`);
    return;
  }
  const tab = DETAIL_TABS.includes(query.tab) ? query.tab : "overview";
  view.innerHTML = `
    <div class="toolbar">
      <a class="btn btn-sm btn-ghost" href="#/profiles">‹ Profiles</a>
      <div><strong style="font-size:16px">${esc(p.name || p.id)}</strong>
        <span class="mono muted" style="font-size:12px">${esc(p.id)}</span></div>
      <div class="spacer"></div>
      ${runBadge(p)}
      <div class="inline-actions">
        <button class="btn btn-sm" id="dLaunch">▶ Launch</button>
        <button class="btn btn-sm" id="dStop">■ Stop</button>
        <button class="btn btn-sm" id="dEdit">✎ Edit</button>
        <button class="btn btn-sm" id="dClone">⧉ Clone</button>
        <button class="btn btn-sm btn-danger" id="dDelete">🗑 Delete</button>
      </div>
    </div>
    <div class="tabs">${DETAIL_TABS.map((t) =>
      `<button data-tab="${t}" class="${t === tab ? "active" : ""}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</div>
    <div id="tabBody"></div>`;

  $$("[data-tab]", view).forEach((b) => {
    b.onclick = () => { location.hash = `#/profile/${encodeURIComponent(id)}?tab=${b.dataset.tab}`; };
  });
  $("#dLaunch").onclick = () => actLaunch([id]);
  $("#dStop").onclick = () => actStop([id]);
  $("#dEdit").onclick = () => openProfileEdit(p);
  $("#dClone").onclick = () => openCloneModal([id]);
  $("#dDelete").onclick = () => actDelete(id);

  const body = $("#tabBody");
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
  const clients = await loadClients();
  body.innerHTML = `<div class="grid cards-2">
    <div class="panel"><h3>Identity</h3>${kvRows([
      ["Name", val(p.name)], ["ID", `<span class="mono">${esc(p.id)}</span>`],
      ["Client", val(clientName(p.clientId) || p.clientId)],
      ["Tags", (p.tags || []).length ? (p.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("") : NA],
      ["Notes", val(p.notes)], ["Start URL", val(p.startUrl)],
      ["Created", val(p.createdAt)], ["Color", p.color ? `<span class="mono">${esc(p.color)}</span>` : NA],
    ])}</div>
    <div class="panel"><h3>Configuration</h3>${kvRows([
      ["Device", val(p.deviceId)], ["Network mode", val(p.networkMode)],
      ["Proxy", proxyLabel(p)], ["Language", val(p.language)],
      ["Timezone", val(p.timezone)], ["WebRTC", val(p.webrtcMode)],
      ["Seed", p.seed != null ? `<span class="mono">${esc(p.seed)}</span>` : NA],
    ])}</div>
  </div>`;
}
async function dNetwork(body, p) {
  body.innerHTML = `<div class="skel" style="height:100px">&nbsp;</div>`;
  const nh = await loadNetHealth(p.id, true);
  // New contract: POST /api/profiles/<id>/network/test (tolerate 404)
  let nt = null;
  const r = await apiPost(`/api/profiles/${encodeURIComponent(p.id)}/network/test`, {});
  if (r.ok) nt = r.data;

  const ph = nh && nh.proxyHealth;
  body.innerHTML = `
    <div class="panel"><h3>Connection</h3>${kvRows([
      ["Mode", val(nh && nh.mode)],
      ["Status", nh ? (nh.status === "DIRECT" ? badge("Direct", "cyan") : (nh.ok ? badge("OK", "green") : badge("Failed", "red"))) : NA],
      ["Egress IP", ph ? val(ph.egress_ip || ph.egressIp) : NA],
      ["Latency", ph && ph.latency_ms != null ? `<span class="num">${esc(ph.latency_ms)} ms</span>` : NA],
      ["Message", val(nh && nh.message)],
    ])}
    <p class="note">Source: GET /api/profiles/:id/network-health${nt ? " + POST /api/profiles/:id/network/test" : " (new network/test endpoint not available — showing existing network-health)"}.</p>
    <div class="inline-actions">
      <button class="btn btn-sm" id="nTest">↻ Re-check</button>
      ${p.proxyHost ? `<button class="btn btn-sm" id="nAdhoc">Test proxy config</button>` : ""}
    </div></div>
    ${nt ? `<div class="panel"><h3>Extended network test</h3>${kvRows([
      ["Health", nt.health ? statusBadge(nt.health) : NA],
      ["Egress IP", val(nt.egress_ip)], ["Country", val(nt.country)],
      ["Timezone", val(nt.timezone)], ["Latency", nt.latency_ms != null ? `<span class="num">${esc(nt.latency_ms)} ms</span>` : NA],
      ["DNS", nt.dns ? statusBadge(nt.dns.status) + " " + val(nt.dns.detail) : NA],
      ["IPv6", nt.ipv6 ? statusBadge(nt.ipv6.status) + " " + val(nt.ipv6.detail) : NA],
      ["Error", val(nt.error)],
    ])}</div>` : ""}`;
  $("#nTest").onclick = () => dNetwork(body, p);
  const adhoc = $("#nAdhoc");
  if (adhoc) adhoc.onclick = async () => {
    const t = await apiPost("/api/proxy/test", { host: p.proxyHost, port: p.proxyPort, username: p.proxyUser, password: p.proxyPass, protocol: p.proxyType || "http" });
    toast(t.ok && t.data && (t.data.ok || t.data.success) ? "Proxy test passed" : "Proxy test failed: " + esc((t.data && t.data.error) || t.error || "?"), t.ok ? "ok" : "err");
  };
}
async function dFingerprint(body, p) { await renderFpInspector(body, p.id); }
async function dDevice(body, p) {
  body.innerHTML = `<div class="skel" style="height:80px">&nbsp;</div>`;
  let detail = null, failed = false;
  if (p.deviceId) {
    const r = await apiGet(`/api/devices/${encodeURIComponent(p.deviceId)}`);
    if (r.ok) detail = r.data; else failed = true;
  }
  body.innerHTML = `
    <div class="panel"><h3>Assigned device</h3>${kvRows([
      ["Device ID", val(p.deviceId)],
      ["Support", detail ? supportBadge(detail.support) : NA],
      ["Validation date", detail ? val(detail.validation_date) : NA],
    ])}
    ${!p.deviceId ? `<p class="note">No device preset assigned to this profile.</p>` : ""}
    ${failed ? `<p class="note">GET /api/devices/${esc(p.deviceId)} failed — device library may be unavailable.</p>` : ""}
    </div>
    ${detail ? deviceDetailHtml(detail) : ""}`;
}
function deviceDetailHtml(d) {
  const cm = d.capability_matrix || {};
  const rows = Object.entries(cm).map(([k, v]) =>
    `<tr><td class="prop">${esc(k)}</td><td>${statusBadge(v.status)}</td><td class="muted">${esc(v.note || "—")}</td></tr>`).join("");
  return `<div class="panel"><h3>Capability matrix</h3>
    ${rows ? `<table class="compare-table"><thead><tr><th>Component</th><th>Status</th><th>Note</th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="muted">No capability data.</p>`}
    </div>
    <div class="panel"><h3>Limitations</h3>
    ${(d.limitations || []).length ? "<ul>" + d.limitations.map((l) => `<li>${esc(l)}</li>`).join("") + "</ul>" : `<p class="muted">None reported.</p>`}
    </div>
    <div class="panel"><h3>Specification</h3>${kvRows([
      ["Manufacturer", val(d.manufacturer)], ["Model", val(d.model)],
      ["Platform", val(d.platform || d.os)], ["Category", val(d.category)],
      ["Viewport", d.viewport ? `<span class="mono">${esc(d.viewport.width)}×${esc(d.viewport.height)}</span>` : NA],
      ["Device scale factor", val(d.device_scale_factor)],
      ["Touch", d.touch == null ? NA : (d.touch ? "yes" : "no")],
      ["Mobile", d.mobile == null ? NA : (d.mobile ? "yes" : "no")],
    ])}</div>`;
}
async function dCookies(body, p) {
  body.innerHTML = `<div class="skel" style="height:100px">&nbsp;</div>`;
  const r = await apiGet(`/api/profiles/${encodeURIComponent(p.id)}/cookies`);
  const cookies = (r.ok && Array.isArray(r.data)) ? r.data : (r.ok && r.data && Array.isArray(r.data.cookies)) ? r.data.cookies : [];
  body.innerHTML = `
    <div class="toolbar">
      <div class="spacer"></div>
      <button class="btn btn-sm" id="ckExport">⇩ Export</button>
      <button class="btn btn-sm" id="ckImport">⇧ Import</button>
    </div>
    ${cookies.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
      <th>Name</th><th>Value</th><th>Domain</th><th>Path</th><th>Expires</th><th>Secure</th><th>HttpOnly</th></tr></thead>
      <tbody>${cookies.map((c) => `<tr>
        <td class="mono">${esc(c.name || "")}</td>
        <td class="mono muted">${esc(String(c.value || "").slice(0, 40))}${String(c.value || "").length > 40 ? "…" : ""}</td>
        <td class="mono">${esc(c.domain || "")}</td><td class="mono">${esc(c.path || "")}</td>
        <td class="muted">${esc(c.expires || c.expirationDate || "")}</td>
        <td>${c.secure ? "✓" : "—"}</td><td>${c.httpOnly || c.httponly ? "✓" : "—"}</td>
      </tr>`).join("")}</tbody></table></div>`
      : emptyState("🍪", "No cookies", "This profile has no stored cookies, or the endpoint returned none.")}
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
      <label class="fld">Paste cookies JSON (array)
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
  body.innerHTML = `<div class="panel"><h3>Storage</h3>${kvRows([
    ["User data dir", h && h.user_data_dir ? `<span class="mono">${esc(h.user_data_dir)}</span>` : NA],
    ["Storage accessible", h ? (h.storage_accessible ? badge("yes", "green") : badge("no", "red")) : NA],
  ])}
  <p class="note">The backend does not expose per-profile disk usage; only accessibility and the data directory are available.</p></div>`;
}
async function dRuntime(body, p) {
  const h = (await loadHealthMap(true))[p.id];
  body.innerHTML = `<div class="panel"><h3>Runtime</h3>${kvRows([
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
  <p class="note">Source: GET /api/profiles/:id/health.</p></div>`;
}
async function dHealth(body, p) {
  body.innerHTML = `<div class="skel" style="height:100px">&nbsp;</div>`;
  const r = await apiGet(`/api/profiles/${encodeURIComponent(p.id)}/health`);
  const h = r.ok ? r.data : null;
  // New contract: GET /api/profiles/<id>/consistency (tolerate 404)
  const cr = await apiGet(`/api/profiles/${encodeURIComponent(p.id)}/consistency`);
  const cons = cr.ok ? cr.data : null;
  body.innerHTML = `
    <div class="panel"><h3>Health snapshot</h3>${h ? kvRows([
      ["Overall", h.overallOk ? badge("healthy", "green") : badge(h.status || "unknown", h.status === "proxy_failed" ? "red" : "amber")],
      ["Network mode", val(h.network_mode)],
      ["Proxy OK", h.proxy_ok == null ? NA : (h.proxy_ok ? badge("yes", "green") : badge("no", "red"))],
      ["Egress IP", val(h.egress_ip)],
      ["Fingerprint loaded", h.fingerprint_loaded ? badge("yes", "green") : badge("no", "amber")],
      ["Extension loaded", h.extension_loaded == null ? NA : (h.extension_loaded ? badge("yes", "green") : badge("no", "amber"))],
    ]) : `<p class="muted">Health endpoint did not return data.</p>`}
    <div class="inline-actions" style="margin-top:10px"><button class="btn btn-sm" id="hRefresh">↻ Refresh</button></div></div>
    <div class="panel"><h3>Consistency checks</h3>
    ${cons && Array.isArray(cons.checks) ? `
      <div style="margin-bottom:10px">${statusBadge(cons.status)} <span class="muted">overall</span></div>
      <table class="compare-table"><thead><tr><th>Domain</th><th>Status</th><th>Expected</th><th>Actual</th><th>Message</th></tr></thead>
      <tbody>${cons.checks.map((c) => `<tr><td class="prop">${esc(c.domain)}</td><td>${statusBadge(c.status)}</td>
        <td>${fmtFpVal(c.expected)}</td><td>${fmtFpVal(c.actual)}</td><td style="font-family:var(--font)">${esc(c.message || "—")}</td></tr>`).join("")}</tbody></table>
      <p class="note">Source: GET /api/profiles/:id/consistency.</p>`
      : `<p class="muted">Consistency endpoint not available for this profile (GET /api/profiles/:id/consistency returned ${cr.status}).</p>`}
    </div>`;
  $("#hRefresh").onclick = () => dHealth(body, p);
}
async function dAutomation(body, p) {
  const jobs = (await loadJobs(true)).filter((j) => (j.profileIds || []).includes(p.id));
  body.innerHTML = jobs.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
    <th>Job</th><th>Type</th><th>Status</th><th>Progress</th><th>Created</th><th></th></tr></thead><tbody>
    ${jobs.map((j) => `<tr><td class="mono">${esc(j.id)}</td><td>${esc(j.type || "")}</td>
      <td>${badge(String(j.status || ""), "cyan")}</td>
      <td>${j.progress != null ? `<div class="prog"><i style="width:${Math.min(100, j.progress)}%"></i></div>` : NA}</td>
      <td class="muted">${esc(timeAgo(j.created_at || j.createdAt) || "—")}</td>
      <td><button class="btn btn-sm btn-danger" data-cancel="${esc(j.id)}">Cancel</button></td></tr>`).join("")}
    </tbody></table></div>`
    : emptyState("⚙", "No jobs for this profile", "Queue a launch or stop from the Profiles page, or create one below.");
  body.insertAdjacentHTML("beforeend", `
    <div class="panel" style="margin-top:14px"><h3>Queue a job</h3>
    <div class="inline-actions">
      <button class="btn btn-sm" id="aLaunch">▶ Launch job</button>
      <button class="btn btn-sm" id="aStop">■ Stop job</button>
    </div></div>`);
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
  body.innerHTML = items.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
    <th>Time</th><th>Action</th><th>Result</th><th>Detail</th></tr></thead><tbody>
    ${items.map((e) => `<tr><td class="muted" style="white-space:nowrap">${esc(e.ts || "")}</td>
      <td class="mono">${esc(e.action || "")}</td>
      <td>${e.ok ? badge("ok", "green") : badge("fail", "red")}</td>
      <td class="mono muted">${esc(JSON.stringify(e.detail ?? "")).slice(0, 120)}</td></tr>`).join("")}
    </tbody></table></div>`
    : emptyState("📜", "No audit entries", "No recorded actions reference this profile.");
}
function openProfileEdit(p) {
  openModal("Edit profile — " + (p.name || p.id), `
    <div class="form-grid">
      <label class="fld">Name<input type="text" id="eName" value="${esc(p.name || "")}"></label>
      <label class="fld">Start URL<input type="url" id="eUrl" value="${esc(p.startUrl || "")}"></label>
      <label class="fld">Tags (comma separated)<input type="text" id="eTags" value="${esc((p.tags || []).join(", "))}"></label>
      <label class="fld">Notes<input type="text" id="eNotes" value="${esc(p.notes || "")}"></label>
      <label class="fld">Device ID<input type="text" id="eDevice" value="${esc(p.deviceId || "")}"></label>
      <label class="fld">Language<input type="text" id="eLang" value="${esc(p.language || "")}"></label>
      <label class="fld">Timezone<input type="text" id="eTz" value="${esc(p.timezone || "")}"></label>
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
    } else toast("Update failed: " + esc((r.data && r.data.error) || r.error || "?"), "err");
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
    `<div class="wstep ${i === wiz.step ? "active" : i < wiz.step ? "done" : ""}">${i + 1}. ${s}</div>`).join("");
  openModal("New Profile", `<div class="wizard-steps">${steps}</div><div id="wizBody">${inner}</div>`, foot);
}
function wizFoot(back, nextLabel, nextFn) {
  return `${back ? `<button class="btn" id="wBack">‹ Back</button>` : ""}
    <button class="btn" id="wCancel">Cancel</button>
    <button class="btn btn-primary" id="wNext">${esc(nextLabel)}</button>`;
}
function bindWizFoot(back, nextLabel, nextFn) {
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
      <label class="fld">Profile name *<input type="text" id="wbName" value="${esc(wiz.basic.name)}" placeholder="e.g. shopper-01"></label>
      <label class="fld">Client<select id="wbClient"><option value="">— none —</option>
        ${clients.map((c) => `<option value="${esc(c.id)}" ${wiz.basic.clientId === c.id ? "selected" : ""}>${esc(c.name || c.id)}</option>`).join("")}</select></label>
      <label class="fld">Tags (comma separated)<input type="text" id="wbTags" value="${esc(wiz.basic.tags)}"></label>
      <label class="fld">Start URL<input type="url" id="wbUrl" value="${esc(wiz.basic.startUrl)}"></label>
      <label class="fld" style="grid-column:1/-1">Notes<textarea id="wbNotes" rows="2">${esc(wiz.basic.notes)}</textarea></label>
    </div>`,
    wizFoot(false, "Next: Device ›", null));
  bindWizFoot(false, "Next: Device ›", () => {
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

/* Step 2 — Device */
async function wDevice() {
  wizardShell(`<div class="skel" style="height:160px">&nbsp;</div>`,
    wizFoot(true, "Next: Network ›", () => { wiz.step = 2; renderWizard(); }));
  bindWizFoot(true, "Next: Network ›", () => { wiz.step = 2; renderWizard(); });

  const devices = await loadDevices();
  if (!devices) {
    $("#wizBody").innerHTML = emptyState("📱", "Device library unavailable",
      "GET /api/devices failed. You can continue without a device preset — the backend will generate a default fingerprint.");
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
        `<button data-dt="${t}" class="${t === tab ? "active" : ""}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}
    </div>
    ${list.length ? `<div class="device-grid">${list.map((d) => `
      <div class="device-card ${wiz.deviceId === d.id ? "selected" : ""}" data-dev="${esc(d.id)}">
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

/* Step 3 — Network */
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
    wizFoot(true, "Next: Fingerprint ›", () => { wiz.step = 3; renderWizard(); }));
  bindWizFoot(true, "Next: Fingerprint ›", () => { wiz.step = 3; renderWizard(); });

  const drawFields = () => {
    const f = $("#netFields");
    if (n.mode === "direct") { f.innerHTML = `<p class="note">Direct connection — no proxy fields needed.</p>`; return; }
    if (n.mode === "gateway") {
      f.innerHTML = `<label class="fld">Gateway<select id="wnGw">
        ${gateways.map((g) => `<option value="${esc(g.id)}" ${n.gatewayId === g.id ? "selected" : ""}>${esc(g.name || g.id)} (${esc(g.providerType || "?")})</option>`).join("")}
        </select></label>
        ${gateways.length ? "" : `<p class="note">No gateways configured. Add one under Gateways first.</p>`}`;
      const sel = $("#wnGw");
      if (sel) sel.onchange = (e) => { n.gatewayId = e.target.value; };
      return;
    }
    f.innerHTML = `<div class="form-grid">
      <label class="fld">Host *<input type="text" id="wnHost" value="${esc(n.host)}" placeholder="203.0.113.10"></label>
      <label class="fld">Port *<input type="number" id="wnPort" value="${esc(n.port)}" placeholder="8080"></label>
      <label class="fld">Username<input type="text" id="wnUser" value="${esc(n.user)}" autocomplete="off"></label>
      <label class="fld">Password<input type="password" id="wnPass" value="${esc(n.pass)}" autocomplete="off"></label>
    </div>
    <div class="inline-actions" style="margin-top:10px">
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
    out.innerHTML = `<div class="panel"><h3>Test result ${ok ? badge("pass", "green") : badge("fail", "red")}</h3>${kvRows([
      ["Egress IP", val(t.egress_ip || t.egressIp)],
      ["Country", val(t.country)],
      ["Latency", t.latency_ms != null ? `<span class="num">${esc(t.latency_ms)} ms</span>` : NA],
      ["DNS", t.dns ? esc(JSON.stringify(t.dns)) : NA],
      ["IPv6", t.ipv6 ? esc(JSON.stringify(t.ipv6)) : NA],
      ["Error", val(t.error)],
    ])}</div>`;
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
    wizFoot(true, "Next: Consistency ›", () => { wiz.step = 4; renderWizard(); }));
  bindWizFoot(true, "Next: Consistency ›", () => { wiz.step = 4; renderWizard(); });

  $$("[data-fpm]", $("#wizBody")).forEach((c) => c.onclick = () => {
    wiz.fpMode = c.dataset.fpm;
    $$("[data-fpm]", $("#wizBody")).forEach((x) => x.classList.toggle("selected", x === c));
  });
  const cl = $("#capList");
  if (caps && Object.keys(caps).length) {
    cl.innerHTML = `<table class="compare-table"><thead><tr><th>Component</th><th>Status</th><th>Note</th></tr></thead><tbody>
      ${Object.entries(caps).map(([k, v]) => `<tr><td class="prop">${esc(k)}</td><td>${statusBadge(v.status)}</td><td class="muted">${esc(v.note || "—")}</td></tr>`).join("")}
      </tbody></table><p class="note">Source: GET /api/devices/${esc(wiz.deviceId)} capability_matrix.</p>`;
  } else {
    cl.innerHTML = `<p class="muted">${wiz.deviceId
      ? "No capability data for this device (endpoint missing or empty)."
      : "Select a device preset in step 2 to see its capability status here."}</p>`;
  }
}

/* Step 5 — Consistency (local pre-flight; server check runs after creation) */
function wConsistency() {
  const checks = [];
  // Device vs network
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
      ? `Device preset “${wiz.deviceId}” selected; fingerprint will be derived from it.`
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
    <table class="compare-table"><thead><tr><th>Domain</th><th>Status</th><th>Expected</th><th>Actual</th><th>Message</th></tr></thead>
    <tbody>${checks.map((c) => `<tr><td class="prop">${esc(c.domain)}</td><td>${statusBadge(c.status)}</td>
      <td>${esc(c.expected)}</td><td>${esc(c.actual)}</td>
      <td style="font-family:var(--font)">${esc(c.message)}</td></tr>`).join("")}</tbody></table>`,
    wizFoot(true, "Next: Review ›", () => {
      if (overall === "ERROR" && !confirm("Pre-flight found errors. Continue anyway?")) return;
      wiz.step = 5; renderWizard();
    }));
  bindWizFoot(true, "Next: Review ›", () => { wiz.step = 5; renderWizard(); });
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
      toast(`Profile “${np.name || np.id}” created` +
        ((r.data.warnings || []).length ? ` — ${r.data.warnings.length} fingerprint warning(s)` : ""), "ok");
      invalidate("profiles");
      location.hash = `#/profile/${encodeURIComponent(np.id)}`;
    } else {
      btn.disabled = false; btn.textContent = "Create profile";
      toast("Create failed: " + esc((r.data && (r.data.error || r.data.message)) || r.error || "unknown"), "err");
    }
  };
}

/* ============================== Fingerprints page ============================== */
async function vFingerprints(view, query) {
  const profiles = await loadProfiles();
  if (!profiles.length) {
    view.innerHTML = emptyState("🧬", "No profiles", "Create a profile to inspect its fingerprint.");
    return;
  }
  const selId = query.id || profiles[0].id;
  view.innerHTML = `
    <div class="toolbar">
      <label class="fld" style="min-width:280px">Profile
        <select id="fpSel">${profiles.map((p) =>
          `<option value="${esc(p.id)}" ${p.id === selId ? "selected" : ""}>${esc(p.name || p.id)}</option>`).join("")}</select>
      </label>
      <div class="spacer"></div>
      <a class="btn btn-sm" id="fpOpen" href="#/profile/${esc(selId)}?tab=fingerprint">Open in profile view</a>
    </div>
    <div id="fpBody"></div>`;
  const render = async (id) => {
    $("#fpOpen").href = `#/profile/${encodeURIComponent(id)}?tab=fingerprint`;
    await renderFpInspector($("#fpBody"), id);
  };
  $("#fpSel").onchange = (e) => { render(e.target.value); };
  await render(selId);
}

/* ============================== Devices page ============================== */
const devView = { q: "", cat: "" };
async function vDevices(view) {
  const devices = await loadDevices();
  if (!devices) {
    view.innerHTML = emptyState("📱", "Device library unavailable",
      "GET /api/devices failed. The device preset service may not be implemented yet.",
      `<button class="btn btn-primary" onclick="location.reload()">Retry</button>`);
    return;
  }
  const cats = [...new Set(devices.map((d) => String(d.category || "").toLowerCase()).filter(Boolean))].sort();
  const q = devView.q.trim().toLowerCase();
  const rows = devices.filter((d) => {
    if (q) {
      const hay = [d.name, d.manufacturer, d.model, d.platform, d.category, d.id].filter(Boolean).join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (devView.cat) {
      const c = String(d.category || "").toLowerCase();
      if (devView.cat === "android" && !/android/.test(c + String(d.platform || "").toLowerCase())) return false;
      if (devView.cat === "ios" && !/ios|iphone|ipad/.test(c + String(d.platform || "").toLowerCase())) return false;
      if (devView.cat === "tablet" && !/tablet/.test(c)) return false;
      if (devView.cat === "mobile" && !/mobile/.test(c)) return false;
      if (devView.cat === "desktop" && !/desktop/.test(c)) return false;
    }
    return true;
  });

  view.innerHTML = `
    <div class="toolbar">
      <input type="search" id="dvQ" placeholder="Search devices…" value="${esc(devView.q)}" style="width:240px">
      <select id="dvCat">
        <option value="">All categories</option>
        ${["android", "ios", "tablet", "mobile", "desktop"].map((c) =>
          `<option value="${c}" ${devView.cat === c ? "selected" : ""}>${c[0].toUpperCase() + c.slice(1)}</option>`).join("")}
      </select>
      <div class="spacer"></div><span class="muted">${rows.length} of ${devices.length} presets</span>
    </div>
    ${rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
      <th>Device</th><th>Manufacturer</th><th>OS</th><th>Category</th><th>Viewport</th><th>DPR</th><th>Touch</th><th>Support</th><th>Validated</th></tr></thead>
      <tbody>${rows.map((d) => `<tr data-devrow="${esc(d.id)}" style="cursor:pointer">
        <td><strong>${esc(d.name || d.model || d.id)}</strong><div class="muted mono" style="font-size:11px">${esc(d.id)}</div></td>
        <td>${val(d.manufacturer)}</td><td>${val(d.platform || d.os)}</td><td>${val(d.category)}</td>
        <td class="mono">${d.viewport ? `${esc(d.viewport.width)}×${esc(d.viewport.height)}` : "—"}</td>
        <td class="num">${esc(d.device_scale_factor ?? "—")}</td>
        <td>${d.touch == null ? "—" : (d.touch ? "✓" : "—")}</td>
        <td>${supportBadge(d.support)}</td>
        <td class="muted">${esc(d.validation_date || "—")}</td>
      </tr>`).join("")}</tbody></table></div>`
      : emptyState("📱", "No devices match", "Adjust the search or category filter.")}`;

  const deb = debounce((v) => { devView.q = v; vDevices(view); }, 280);
  $("#dvQ").addEventListener("input", (e) => deb(e.target.value));
  $("#dvCat").onchange = (e) => { devView.cat = e.target.value; vDevices(view); };
  $$("[data-devrow]", view).forEach((tr) => tr.onclick = () => openDeviceDrawer(tr.dataset.devrow));
}

async function openDeviceDrawer(id) {
  openDrawer("Device preset", `<div class="skel" style="height:120px">&nbsp;</div>`);
  let d = (cache.devices || []).find((x) => x.id === id);
  if (!d || !d.capability_matrix) {
    const r = await apiGet(`/api/devices/${encodeURIComponent(id)}`);
    if (r.ok) { d = r.data; cache.deviceDetail[id] = d; }
  }
  if (!d) { $("#drawerBody").innerHTML = emptyState("📱", "Not found", "Device preset could not be loaded."); return; }
  $("#drawerTitle").textContent = d.name || d.model || d.id;

  let checksHtml = `<p class="muted">Validation checks unavailable.</p>`;
  const vr = await apiGet(`/api/devices/${encodeURIComponent(id)}/validate`);
  if (vr.ok && vr.data) {
    const v = vr.data;
    checksHtml = `
      <div style="margin-bottom:8px">${statusBadge(v.status)} <span class="muted">validated ${esc(v.validated_at || "—")}</span></div>
      ${(v.checks || []).map((c) => `<div class="check-row"><span style="flex:1">${esc(c.name)}</span>
        ${statusBadge(c.status)}<span class="muted" style="font-size:12px">${esc(c.detail || "")}</span></div>`).join("")
        || `<p class="muted">No checks reported.</p>`}
      <p class="note">Source: GET /api/devices/${esc(id)}/validate.</p>`;
  }
  $("#drawerBody").innerHTML = `
    <div class="panel"><h3>Preset</h3>${kvRows([
      ["ID", `<span class="mono">${esc(d.id)}</span>`],
      ["Manufacturer", val(d.manufacturer)], ["Model", val(d.model)],
      ["Platform", val(d.platform || d.os)], ["Category", val(d.category)],
      ["Viewport", d.viewport ? `<span class="mono">${esc(d.viewport.width)}×${esc(d.viewport.height)}</span>` : NA],
      ["Device scale factor", val(d.device_scale_factor)],
      ["Touch", d.touch == null ? NA : (d.touch ? "yes" : "no")],
      ["Mobile", d.mobile == null ? NA : (d.mobile ? "yes" : "no")],
      ["Support", supportBadge(d.support)],
      ["Validation date", val(d.validation_date)],
    ])}</div>
    <div class="panel"><h3>Capability matrix</h3>
      ${d.capability_matrix && Object.keys(d.capability_matrix).length
        ? `<table class="compare-table"><thead><tr><th>Component</th><th>Status</th><th>Note</th></tr></thead><tbody>
          ${Object.entries(d.capability_matrix).map(([k, cv]) => `<tr><td class="prop">${esc(k)}</td><td>${statusBadge(cv.status)}</td><td class="muted">${esc(cv.note || "—")}</td></tr>`).join("")}
          </tbody></table>`
        : `<p class="muted">No capability matrix data.</p>`}
    </div>
    <div class="panel"><h3>Limitations</h3>
      ${(d.limitations || []).length ? "<ul>" + d.limitations.map((l) => `<li>${esc(l)}</li>`).join("") + "</ul>" : `<p class="muted">None reported.</p>`}
    </div>
    <div class="panel"><h3>Validation checks</h3>${checksHtml}</div>
    <div class="inline-actions">
      <button class="btn btn-primary" id="dvUse">Use in new profile</button>
    </div>`;
  $("#dvUse").onclick = () => { closeDrawer(); openWizard(); wiz.deviceId = id; wiz.step = 1; renderWizard(); };
}

/* ============================== Groups (clients + templates) ============================== */
async function vGroups(view) {
  const [clients, templates] = await Promise.all([loadClients(true), loadTemplates(true)]);
  const crud = (kind, items, fields) => `
    <div class="toolbar"><div class="spacer"></div>
      <button class="btn btn-sm btn-primary" data-add="${kind}">＋ Add ${kind === "clients" ? "client" : "template"}</button></div>
    ${items.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
      <th>Name</th>${fields.map((f) => `<th>${esc(f[1])}</th>`).join("")}<th>ID</th><th></th></tr></thead><tbody>
      ${items.map((it) => `<tr><td><strong>${esc(it.name || it.id)}</strong></td>
        ${fields.map(([k]) => `<td class="muted">${esc(it[k] ?? "—")}</td>`).join("")}
        <td class="mono muted">${esc(it.id)}</td>
        <td><div class="inline-actions">
          <button class="btn btn-sm" data-edit="${kind}:${esc(it.id)}">Edit</button>
          <button class="btn btn-sm btn-danger" data-del="${kind}:${esc(it.id)}">Delete</button>
        </div></td></tr>`).join("")}</tbody></table></div>`
      : emptyState("🏷", `No ${kind}`, `No ${kind} defined yet.`) }`;

  view.innerHTML = `
    <div class="section-head"><h2>Clients</h2></div>
    <div id="grpClients">${crud("clients", clients, [["description", "Description"]])}</div>
    <div class="section-head"><h2>Templates</h2></div>
    <div id="grpTemplates">${crud("templates", templates, [["description", "Description"]])}</div>
    <p class="note">Groups are organized through clients and templates — the backend exposes these via
    <span class="mono">/api/clients</span> and <span class="mono">/api/templates</span>. Profiles reference a client via <span class="mono">clientId</span>.</p>`;

  const ep = (kind) => `/api/${kind}`;
  $$("[data-add]", view).forEach((b) => b.onclick = () => openGroupEdit(b.dataset.add, null));
  $$("[data-edit]", view).forEach((b) => b.onclick = () => {
    const [kind, id] = b.dataset.edit.split(/:(.+)/);
    const list = kind === "clients" ? clients : templates;
    openGroupEdit(kind, list.find((x) => x.id === id));
  });
  $$("[data-del]", view).forEach((b) => b.onclick = async () => {
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
        <label class="fld">Name *<input type="text" id="gName" value="${esc((item && item.name) || "")}"></label>
        <label class="fld">Description<input type="text" id="gDesc" value="${esc((item && item.description) || "")}"></label>
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

/* ============================== Proxies ============================== */
async function vProxies(view) {
  const profiles = await loadProfiles();
  const withProxy = profiles.filter((p) => p.proxyHost);
  view.innerHTML = `
    <div class="panel"><h3>Test a proxy</h3>
      <div class="form-grid">
        <label class="fld">Protocol<select id="pxProto">
          <option value="http">HTTP</option><option value="https">HTTPS</option><option value="socks5">SOCKS5</option></select></label>
        <label class="fld">Host *<input type="text" id="pxHost" placeholder="203.0.113.10"></label>
        <label class="fld">Port *<input type="number" id="pxPort" placeholder="8080"></label>
        <label class="fld">Username<input type="text" id="pxUser" autocomplete="off"></label>
        <label class="fld">Password<input type="password" id="pxPass" autocomplete="off"></label>
      </div>
      <div class="inline-actions" style="margin-top:12px">
        <button class="btn btn-primary" id="pxGo">Test connection</button>
      </div>
      <div id="pxOut" style="margin-top:12px"></div>
      <p class="note">Uses POST /api/proxy/test. Passwords are never displayed or logged.</p>
    </div>
    <div class="section-head"><h2>Profiles using proxies</h2><div class="spacer"></div>
      <span class="muted">${withProxy.length} of ${profiles.length}</span></div>
    ${withProxy.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
      <th>Profile</th><th>Proxy</th><th>Health</th><th>Egress IP</th></tr></thead><tbody>
      ${withProxy.map((p) => `<tr>
        <td><a class="rowlink" href="#/profile/${esc(p.id)}">${esc(p.name || p.id)}</a></td>
        <td>${proxyLabel(p)}</td>
        <td class="c-ph" data-ph="${esc(p.id)}"><span class="muted">…</span></td>
        <td class="c-ip" data-ip="${esc(p.id)}"><span class="muted">…</span></td>
      </tr>`).join("")}</tbody></table></div>`
      : emptyState("🌐", "No proxies in use", "No profiles currently have a proxy configured.")}`;

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
    $("#pxOut").innerHTML = `<div class="panel"><h3>Result ${ok ? badge("pass", "green") : badge("fail", "red")}</h3>${kvRows([
      ["Egress IP", val(d.egress_ip || d.egressIp)], ["Country", val(d.country)],
      ["Latency", d.latency_ms != null ? `<span class="num">${esc(d.latency_ms)} ms</span>` : NA],
      ["Error", val(d.error)],
    ])}</div>`;
  };

  // lazy health + egress for proxy profiles
  withProxy.forEach(async (p) => {
    const nh = await loadNetHealth(p.id);
    if (!document.body.contains($("#view"))) return;
    const ph = nh && nh.proxyHealth;
    const hc = document.querySelector(`[data-ph="${cssEsc(p.id)}"]`);
    const ic = document.querySelector(`[data-ip="${cssEsc(p.id)}"]`);
    if (hc) hc.innerHTML = nh ? (nh.ok === false ? badge("failed", "red") : badge("ok", "green")) : NA;
    if (ic) ic.innerHTML = ph && (ph.egress_ip || ph.egressIp) ? `<span class="mono">${esc(ph.egress_ip || ph.egressIp)}</span>` : NA;
  });
}

/* ============================== Gateways ============================== */
async function vGateways(view) {
  const gateways = await loadGateways(true);
  view.innerHTML = `
    <div class="toolbar"><div class="spacer"></div>
      <button class="btn btn-sm btn-primary" id="gwAdd">＋ Add gateway</button></div>
    ${gateways.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
      <th>Name</th><th>Provider</th><th>Health</th><th>ID</th><th></th></tr></thead><tbody>
      ${gateways.map((g) => `<tr>
        <td><strong>${esc(g.name || g.id)}</strong></td>
        <td>${val(g.providerType || g.provider)}</td>
        <td class="c-gh" data-gh="${esc(g.id)}"><span class="muted">…</span></td>
        <td class="mono muted">${esc(g.id)}</td>
        <td><div class="inline-actions">
          <button class="btn btn-sm" data-gw="health" data-id="${esc(g.id)}">Health</button>
          <button class="btn btn-sm" data-gw="provision" data-id="${esc(g.id)}">Provision</button>
          <button class="btn btn-sm" data-gw="edit" data-id="${esc(g.id)}">Edit</button>
          <button class="btn btn-sm btn-danger" data-gw="del" data-id="${esc(g.id)}">Delete</button>
        </div></td></tr>`).join("")}</tbody></table></div>`
      : emptyState("🛰", "No gateways", "Add a gateway provider to route profile traffic through it.")}
    <p class="note">Provider secrets are stored server-side and never returned by the API (safe view).</p>`;

  $("#gwAdd").onclick = () => openGatewayEdit(null, gateways);
  $$("[data-gw]", view).forEach((b) => b.onclick = async () => {
    const id = b.dataset.id, kind = b.dataset.gw;
    if (kind === "health") {
      const cell = document.querySelector(`[data-gh="${cssEsc(id)}"]`);
      if (cell) cell.innerHTML = `<span class="muted">checking…</span>`;
      const r = await apiGet(`/api/gateways/${encodeURIComponent(id)}/health`);
      const ok = r.ok && r.data && (r.data.ok || (r.data.health && r.data.health.ok));
      if (cell) cell.innerHTML = ok ? badge("healthy", "green") : badge("unhealthy", "red");
      if (!r.ok) toast("Health check failed", "err");
    } else if (kind === "provision") {
      const r = await apiPost(`/api/gateways/${encodeURIComponent(id)}/provision`, {});
      toast(r.ok && r.data && r.data.ok ? "Provisioned" : "Provision failed: " + esc((r.data && r.data.error) || r.error || "?"), r.ok ? "ok" : "err");
    } else if (kind === "edit") {
      openGatewayEdit(gateways.find((g) => g.id === id), gateways);
    } else if (kind === "del") {
      if (!confirm("Delete this gateway?")) return;
      const r = await apiDel(`/api/gateways/${encodeURIComponent(id)}`);
      toast(r.ok ? "Deleted" : "Delete failed", r.ok ? "ok" : "err");
      if (r.ok) { invalidate("gateways"); route(); }
    }
  });

  // lazy health for all
  gateways.forEach(async (g) => {
    const r = await apiGet(`/api/gateways/${encodeURIComponent(g.id)}/health`);
    if (!document.body.contains($("#view"))) return;
    const cell = document.querySelector(`[data-gh="${cssEsc(g.id)}"]`);
    if (!cell) return;
    const ok = r.ok && r.data && (r.data.ok || (r.data.health && r.data.health.ok));
    cell.innerHTML = r.ok ? (ok ? badge("healthy", "green") : badge("unhealthy", "red")) : NA;
  });
}
function openGatewayEdit(g, gateways) {
  const isNew = !g;
  openModal((isNew ? "Add" : "Edit") + " gateway", `
    <div class="form-grid">
      <label class="fld">Name *<input type="text" id="gwName" value="${esc((g && g.name) || "")}"></label>
      <label class="fld">Provider type<input type="text" id="gwType" value="${esc((g && (g.providerType || g.provider)) || "")}" placeholder="e.g. ssh, wireguard"></label>
      <label class="fld" style="grid-column:1/-1">Config JSON (no secrets in this form — manage secrets server-side)
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

/* ============================== Automation & Tasks (jobs) ============================== */
function jobStatusBadge(s) {
  const v = String(s || "").toLowerCase();
  if (["done", "completed"].includes(v)) return badge(s || "done", "green");
  if (["failed", "error"].includes(v)) return badge(s || "failed", "red");
  if (["cancelled", "canceled"].includes(v)) return badge(s || "cancelled", "grey");
  if (["running", "in_progress"].includes(v)) return badge(s || "running", "cyan");
  return badge(s || "queued", "amber");
}
function jobRows(jobs) {
  return jobs.map((j) => {
    const pct = j.progress != null ? Math.min(100, Math.max(0, Number(j.progress))) : null;
    return `<tr><td class="mono">${esc(j.id)}</td><td>${esc(j.type || "")}</td>
      <td>${jobStatusBadge(j.status)}</td>
      <td style="min-width:120px">${pct != null ? `<div class="prog"><i style="width:${pct}%"></i></div>` : NA}</td>
      <td class="num">${(j.profileIds || []).length}</td>
      <td class="muted">${esc(timeAgo(j.created_at || j.createdAt) || "—")}</td>
      <td>${j.error ? `<span class="muted" style="font-size:12px">${esc(String(j.error).slice(0, 80))}</span>` : NA}</td>
      <td>${["queued", "running", "in_progress"].includes(String(j.status).toLowerCase())
        ? `<button class="btn btn-sm btn-danger" data-jcancel="${esc(j.id)}">Cancel</button>` : ""}</td></tr>`;
  }).join("");
}
function bindJobCancel(root, refresh) {
  $$("[data-jcancel]", root).forEach((b) => b.onclick = async () => {
    const r = await apiPost(`/api/jobs/${encodeURIComponent(b.dataset.jcancel)}/cancel`, {});
    toast(r.ok ? "Job cancelled" : "Cancel failed", r.ok ? "ok" : "err");
    invalidate("jobs"); refresh();
  });
}
async function vAutomation(view) {
  const [jobs, profiles] = await Promise.all([loadJobs(true), loadProfiles()]);
  view.innerHTML = `
    <div class="panel"><h3>Queue a bulk job</h3>
      <div class="form-grid">
        <label class="fld">Job type<select id="jbType">
          <option value="bulk_launch">Bulk launch</option>
          <option value="bulk_stop">Bulk stop</option>
          <option value="launch_profile">Launch (single)</option>
          <option value="stop_profile">Stop (single)</option>
        </select></label>
        <label class="fld">Start URL (optional)<input type="url" id="jbUrl" placeholder="https://…"></label>
        <label class="fld">Profile IDs (comma separated, or pick below)<input type="text" id="jbIds" placeholder="profile_xxx, profile_yyy"></label>
        <label class="fld">Options
          <select id="jbDirect"><option value="">Default launch</option><option value="1">Direct (fast)</option></select></label>
      </div>
      <label class="fld" style="margin-top:10px">Or select profiles
        <select id="jbPick" multiple size="6" style="min-height:120px">
          ${profiles.map((p) => `<option value="${esc(p.id)}">${esc(p.name || p.id)}</option>`).join("")}
        </select></label>
      <div class="inline-actions" style="margin-top:12px">
        <button class="btn btn-primary" id="jbGo">Queue job</button>
      </div>
      <p class="note">Job types come from the backend (<span class="mono">launch_profile, stop_profile, bulk_launch, bulk_stop</span>).
      There is no rule/trigger engine in the current API — automation here means queued bulk operations.</p>
    </div>
    <div class="section-head"><h2>Job queue</h2><div class="spacer"></div>
      <button class="btn btn-sm" id="jbRefresh">⟳ Refresh</button></div>
    <div id="jbList">${jobs.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
      <th>Job</th><th>Type</th><th>Status</th><th>Progress</th><th>Profiles</th><th>Created</th><th>Error</th><th></th></tr></thead>
      <tbody>${jobRows(jobs)}</tbody></table></div>`
      : emptyState("⚙", "No jobs", "Queue a bulk job above.")}</div>`;

  $("#jbGo").onclick = async () => {
    const typed = $("#jbIds").value.split(",").map((s) => s.trim()).filter(Boolean);
    const picked = Array.from($("#jbPick").selectedOptions).map((o) => o.value);
    const ids = [...new Set([...typed, ...picked])];
    if (!ids.length) { toast("Select at least one profile", "err"); return; }
    const r = await apiPost("/api/jobs", {
      type: $("#jbType").value, profileIds: ids,
      url: $("#jbUrl").value.trim() || undefined,
      direct: $("#jbDirect").value === "1",
    });
    if (r.ok && r.data && r.data.ok) {
      toast(`Job ${r.data.job.id} queued`, "ok");
      invalidate("jobs"); vAutomation(view);
    } else toast("Queue failed: " + esc((r.data && r.data.error) || r.error || "?"), "err");
  };
  $("#jbRefresh").onclick = () => { invalidate("jobs"); vAutomation(view); };
  bindJobCancel(view, () => vAutomation(view));
}
async function vTasks(view) {
  const jobs = await loadJobs(true);
  view.innerHTML = `
    <div class="toolbar"><div class="spacer"></div>
      <button class="btn btn-sm" id="tRefresh">⟳ Refresh</button></div>
    ${jobs.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
      <th>Job</th><th>Type</th><th>Status</th><th>Progress</th><th>Profiles</th><th>Created</th><th>Error</th><th></th></tr></thead>
      <tbody>${jobRows(jobs)}</tbody></table></div>`
      : emptyState("✓", "No tasks", "Background jobs will appear here. Create one under Automation.")}
    <p class="note">Source: GET /api/jobs. Auto-refresh is off — use ⟳ Refresh.</p>`;
  $("#tRefresh").onclick = () => { invalidate("jobs"); vTasks(view); };
  bindJobCancel(view, () => vTasks(view));
}

/* ============================== Extensions ============================== */
async function vExtensions(view) {
  const sys = cache.sysHealth || (await apiGet("/api/health")).data;
  const extOk = sys && sys.extension_ok;
  view.innerHTML = `
    <div class="panel"><h3>Bundled extension</h3>${kvRows([
      ["Manifest present", extOk ? badge("yes", "green") : badge("no", "amber")],
      ["Note", `<span class="muted">The backend ships one bundled extension used for fingerprint/CDP injection. It is loaded automatically when profiles launch.</span>`],
    ])}
    <p class="note">Source: GET /api/health → extension_ok.</p></div>
    ${emptyState("🧩", "No extension management API",
      "The current backend does not expose per-profile extension install/uninstall endpoints, so there is nothing to list or manage here. Web-store extensions are controlled by the allowWebstoreExtensions setting (" + (sys ? String(sys.allowWebstoreExtensions) : "?") + ").")}`;
}

/* ============================== Backups ============================== */
async function vBackups(view) {
  const r = await apiGet("/api/backups");
  const backups = (r.ok && r.data && Array.isArray(r.data.backups)) ? r.data.backups : [];
  view.innerHTML = `
    ${backups.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
      <th>Backup</th><th>Profile</th><th>Created</th><th>Size</th><th></th></tr></thead><tbody>
      ${backups.map((b) => {
        const id = b.id || b.backupId || b.path || b.backupPath;
        return `<tr>
          <td class="mono">${esc(b.name || id || "backup")}</td>
          <td>${val(b.profileId || b.profile_id)}</td>
          <td class="muted">${esc(b.createdAt || b.created_at || b.ts || "—")}</td>
          <td class="num">${b.size != null ? esc(b.size) : "—"}</td>
          <td><button class="btn btn-sm" data-restore="${esc(id || "")}" ${id ? "" : "disabled"}>Restore</button></td>
        </tr>`;
      }).join("")}</tbody></table></div>`
      : emptyState("💾", "No backups", "Backups appear here after using Backup on a profile.")}
    <p class="note">Source: GET /api/backups. Restore uses POST /api/backups/restore with backupId/backupPath + optional profileId + passphrase. Passphrases are consumed by the endpoint and never logged.</p>`;

  $$("[data-restore]", view).forEach((b) => b.onclick = () => {
    openModal("Restore backup", `
      <label class="fld">Target profile ID (optional — defaults to the backup's profile)
        <input type="text" id="rsPid" placeholder="profile_…"></label>
      <label class="fld">Passphrase (if encrypted)<input type="password" id="rsPass" autocomplete="off"></label>
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
      toast(rr.ok && rr.data && rr.data.ok ? "Restore completed" : "Restore failed: " + esc((rr.data && rr.data.error) || rr.error || "?"), rr.ok ? "ok" : "err");
    };
  });
}

/* ============================== Audit ============================== */
const auditView = { q: "", onlyFail: false };
async function vAudit(view) {
  const r = await apiGet("/api/audit?limit=200");
  const items = (r.ok && r.data && Array.isArray(r.data.items)) ? r.data.items : [];
  const q = auditView.q.trim().toLowerCase();
  const rows = items.filter((e) => {
    if (auditView.onlyFail && e.ok !== false) return false;
    if (!q) return true;
    return [e.action, e.profileId, e.ts, JSON.stringify(e.detail || "")].join(" ").toLowerCase().includes(q);
  }).reverse();

  view.innerHTML = `
    <div class="toolbar">
      <input type="search" id="auQ" placeholder="Filter action, profile, detail…" value="${esc(auditView.q)}" style="width:280px">
      <label style="font-size:13px;color:var(--text-1)"><input type="checkbox" id="auFail" ${auditView.onlyFail ? "checked" : ""}> failures only</label>
      <div class="spacer"></div><span class="muted">${rows.length} entries</span>
    </div>
    ${rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
      <th>Time</th><th>Action</th><th>Profile</th><th>Result</th><th>Detail</th></tr></thead><tbody>
      ${rows.map((e) => `<tr>
        <td class="muted" style="white-space:nowrap">${esc(e.ts || "")}</td>
        <td class="mono">${esc(e.action || "")}</td>
        <td>${e.profileId ? `<a class="rowlink mono" href="#/profile/${esc(e.profileId)}">${shortId(e.profileId)}</a>` : NA}</td>
        <td>${e.ok ? badge("ok", "green") : badge("fail", "red")}</td>
        <td class="mono muted" style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(JSON.stringify(e.detail ?? ""))}">${esc(JSON.stringify(e.detail ?? "").slice(0, 140))}</td>
      </tr>`).join("")}</tbody></table></div>`
      : emptyState("📜", "No audit entries", "Actions are recorded here as they happen.")}
    <p class="note">Source: GET /api/audit.</p>`;

  const deb = debounce((v) => { auditView.q = v; vAudit(view); }, 280);
  $("#auQ").addEventListener("input", (e) => deb(e.target.value));
  $("#auFail").onchange = (e) => { auditView.onlyFail = e.target.checked; vAudit(view); };
}

/* ============================== Settings ============================== */
async function vSettings(view) {
  const sys = cache.sysHealth;
  view.innerHTML = `
    <div class="grid cards-2">
      <div class="panel"><h3>API access</h3>
        <label class="fld">Bearer token (stored in this browser's localStorage)
          <input type="password" id="sToken" value="${esc(apiToken)}" autocomplete="off"></label>
        <div class="inline-actions" style="margin-top:12px">
          <button class="btn btn-primary" id="sSave">Save token</button>
          <button class="btn" id="sClear">Clear</button>
        </div>
        <p class="note">The backend only enforces a token when <span class="mono">MULTIPROFILE_API_TOKEN</span> is set server-side.
        If unset, all endpoints are open and no token is needed.</p>
      </div>
      <div class="panel"><h3>Maintenance</h3>
        <div class="inline-actions">
          <button class="btn" id="sReap">🧹 Reap orphaned browsers</button>
          <button class="btn" id="sUpd">Check update status</button>
        </div>
        <div id="sOut" style="margin-top:12px"></div>
      </div>
    </div>
    <div class="panel"><h3>Backend health (GET /api/health)</h3>
      <div class="code-block">${sys ? esc(JSON.stringify(sys, null, 2)) : "unavailable"}</div>
    </div>`;

  $("#sSave").onclick = () => {
    apiToken = $("#sToken").value.trim();
    if (apiToken) localStorage.setItem(TOKEN_KEY, apiToken); else localStorage.removeItem(TOKEN_KEY);
    authFailed = false;
    toast("Token saved", "ok");
    refreshHealthPill();
  };
  $("#sClear").onclick = () => { apiToken = ""; localStorage.removeItem(TOKEN_KEY); $("#sToken").value = ""; toast("Token cleared"); };
  $("#sReap").onclick = async () => {
    const r = await apiPost("/api/maintenance/reap", {});
    $("#sOut").innerHTML = `<p class="note">${r.ok ? `Reaped ${r.data && r.data.count != null ? r.data.count : "?"} orphan(s).` : "Reap failed: " + esc((r.data && r.data.error) || r.error || "?")}</p>`;
  };
  $("#sUpd").onclick = async () => {
    const r = await apiGet("/api/update/status");
    $("#sOut").innerHTML = `<p class="note">Update status: <span class="mono">${esc(r.ok ? JSON.stringify(r.data) : "unavailable (" + r.status + ")")}</span></p>`;
  };
}

/* ============================== Global search ============================== */
function initSearch() {
  const input = $("#globalSearch"), box = $("#searchResults");
  const run = debounce(async () => {
    const q = input.value.trim().toLowerCase();
    if (q.length < 2) { box.hidden = true; return; }
    const profiles = await loadProfiles();
    const hits = profiles.filter((p) =>
      [p.name, p.id, (p.tags || []).join(" ")].filter(Boolean).join(" ").toLowerCase().includes(q)).slice(0, 8);
    if (!hits.length) { box.innerHTML = `<div style="padding:10px 12px" class="muted">No matches</div>`; box.hidden = false; return; }
    box.innerHTML = hits.map((p) =>
      `<a href="#/profile/${esc(p.id)}">${isRunning(p) ? "🟢" : "⚪"} <strong>${esc(p.name || p.id)}</strong>
       <span class="muted mono" style="font-size:11px">${shortId(p.id)}</span></a>`).join("");
    box.hidden = false;
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
    invalidate("profiles", "health", "jobs", "gateways", "backups", "audit", "clients", "templates");
    cache.fp = {}; cache.netHealth = {};
    route();
  };
  $("#btnAuth").onclick = () => { authFailed = true; renderLogin(); };
  $("#navToggle").onclick = () => $("#sidebar").classList.toggle("open");
  $("#modalClose").onclick = closeModal;
  $("#modalBackdrop").addEventListener("click", (e) => { if (e.target.id === "modalBackdrop") closeModal(); });
  $("#drawerClose").onclick = closeDrawer;
  $("#drawerBackdrop").onclick = closeDrawer;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeModal(); closeDrawer(); }
  });
  window.addEventListener("hashchange", route);
  initSearch();
  refreshHealthPill();
  setInterval(refreshHealthPill, 30000);
  if (!location.hash) location.hash = "#/dashboard";
  route();
}
document.addEventListener("DOMContentLoaded", init);
