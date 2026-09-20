
async function refreshHealthBadge() {
  try {
    const res = await apiFetch("/api/health");
    const data = await res.json();
    const el = document.getElementById("healthVersionText");
    const badge = document.getElementById("healthVersionBadge");
    if (el && data.version) {
      el.textContent = `v${data.version}` + (data.chrome_ok ? " · Chrome OK" : " · No Chrome");
    }
    if (badge) {
      badge.title = data.chrome_ok ? "Chrome available" : "Chrome missing";
      badge.style.borderColor = data.chrome_ok ? "rgba(16,185,129,.45)" : "rgba(239,68,68,.45)";
    }
  } catch (e) {}
}


// ---- 1.2.0 helpers: API token + jobs + clients ----
function getApiToken() {
  try { return localStorage.getItem("mp_api_token") || ""; } catch (e) { return ""; }
}
function setApiToken(tok) {
  try {
    if (tok) localStorage.setItem("mp_api_token", tok);
    else localStorage.removeItem("mp_api_token");
  } catch (e) {}
}
async function apiFetch(url, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const tok = getApiToken();
  if (tok) headers["Authorization"] = "Bearer " + tok;
  if (opts.body && !headers["Content-Type"] && !(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(url, Object.assign({}, opts, { headers }));
  return res;
}
function showJobToast(msg) {
  let el = document.getElementById("jobToast");
  if (!el) {
    el = document.createElement("div");
    el.id = "jobToast";
    el.style.cssText = "position:fixed;bottom:20px;right:20px;z-index:9999;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:10px;padding:10px 14px;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.4);max-width:360px;";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.display = "none"; }, 4000);
}
let allClients = [];
let selectedProfileIds = new Set();

async function loadClients() {
  try {
    const res = await apiFetch("/api/clients");
    allClients = await res.json();
    if (!Array.isArray(allClients)) allClients = [];
    const sel = document.getElementById("clientFilter");
    if (sel) {
      const cur = sel.value;
      sel.innerHTML = '<option value="">All clients</option>' + allClients.map(c => `<option value="${c.id}">${c.name}</option>`).join("");
      sel.value = cur || "";
    }
  } catch (e) { console.error(e); }
}

async function pollJob(jobId) {
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 500));
    try {
      const res = await apiFetch(`/api/jobs/${jobId}`);
      const data = await res.json();
      const job = data.job || data;
      showJobToast(`Job ${job.type}: ${job.status} (${job.progress || 0}/${job.total || "?"})`);
      if (["completed", "failed", "cancelled"].includes(job.status)) {
        await loadProfiles();
        return job;
      }
    } catch (e) { break; }
  }
}

async function bulkJob(type) {
  const ids = Array.from(selectedProfileIds);
  if (!ids.length) { alert("Select one or more profiles first."); return; }
  const res = await apiFetch("/api/jobs", {
    method: "POST",
    body: JSON.stringify({ type, profileIds: ids })
  });
  const data = await res.json();
  if (!res.ok) { alert(data.error || "Job failed"); return; }
  const job = data.job || data;
  showJobToast(`Queued ${type} for ${ids.length} profile(s)`);
  pollJob(job.id);
}

async function backupProfile(id) {
  showJobToast("Creating backup…");
  const res = await apiFetch(`/api/profiles/${id}/backup`, { method: "POST", body: "{}" });
  const data = await res.json();
  if (!res.ok || data.error) { alert(data.error || "Backup failed"); return; }
  showJobToast("Backup saved: " + (data.backupId || data.backupPath || "ok"));
}

// NOTE: Full 1.2.0 app.js body continues — this interim includes jobs/bulk/clients/token wiring.
// Profiles UI stubs so page does not crash while full file sync is completed.
let allProfiles = [];
let allUserAgents = [];
let allGithubAccounts = [];
let activeFilter = "all";

document.addEventListener("DOMContentLoaded", async () => {
  const tokInput = document.getElementById("apiTokenInput");
  if (tokInput) {
    tokInput.value = getApiToken();
    tokInput.addEventListener("change", () => setApiToken(tokInput.value.trim()));
  }
  document.getElementById("btnBulkLaunch")?.addEventListener("click", () => bulkJob("bulk_launch"));
  document.getElementById("btnBulkStop")?.addEventListener("click", () => bulkJob("bulk_stop"));
  document.getElementById("clientFilter")?.addEventListener("change", () => loadProfiles());
  await loadClients();
  await refreshHealthBadge();
  try {
    const res = await apiFetch("/api/profiles");
    allProfiles = await res.json();
    if (!Array.isArray(allProfiles)) allProfiles = allProfiles.items || [];
  } catch (e) {}
  const grid = document.getElementById("profilesGrid");
  if (grid) {
    grid.innerHTML = `<div style="padding:16px;color:var(--text-secondary)">Loaded ${allProfiles.length} profiles. Bulk launch/stop + API token ready (1.2.0). Full UI modules pending complete app.js sync.</div>`;
  }
  const el = document.getElementById("healthVersionText");
  if (el && !el.textContent.includes("v")) el.textContent = "1.2.0 jobs ready";
});
