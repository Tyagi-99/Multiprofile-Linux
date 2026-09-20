let allProfiles = [];
let allUserAgents = [];
let allGithubAccounts = [];
let activeFilter = "all";
let _popupShownThisSession = false;  // so the periodic ad refresh never re-opens the popup
let _latestUpdate = null;
let _updateInstalling = false;
let _automaticUpdateScheduled = false;

// Auto-update notifications: default ON, user can turn off. Stored per machine in localStorage.
function isAutoUpdateEnabled() {
  try {
    return localStorage.getItem("autoUpdateEnabled") !== "false";
  } catch (e) {
    return true;
  }
}
function setAutoUpdateEnabled(on) {
  try {
    localStorage.setItem("autoUpdateEnabled", on ? "true" : "false");
  } catch (e) {}
}

document.addEventListener("DOMContentLoaded", async () => {
  // Native desktop app experience: disable default browser context menu & inspect shortcuts
  window.addEventListener("contextmenu", (e) => {
    if (e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA") {
      e.preventDefault();
    }
  });

  window.addEventListener("keydown", (e) => {
    if (
      e.key === "F12" ||
      (e.ctrlKey && e.shiftKey && (e.key === "I" || e.key === "i" || e.key === "J" || e.key === "j")) ||
      (e.ctrlKey && (e.key === "U" || e.key === "u"))
    ) {
      e.preventDefault();
    }
  });

  await loadUserAgents();
  await loadGithubAccounts();
  await loadProfiles();
  await loadAds();
  setupEventListeners();
  initBuiltInUpdater();
  initDonationFeature();

  // ── Admin detection: show Edit Wallets button only when admin panel is running ──
  detectAdminMode();

  // Auto-update toggle: reflect saved state and wire the button.
  initAutoUpdateToggle();

  // Real-time polling every 3 seconds
  setInterval(loadProfiles, 3000);

  // Re-fetch ads/updates every 5 minutes so owner broadcasts reach users without a restart.
  setInterval(loadAds, 15 * 1000);
  window.addEventListener('focus', loadAds);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) loadAds();
  });
});

function initAutoUpdateToggle() {
  const btn = document.getElementById("btnAutoUpdateToggle");
  const state = document.getElementById("autoUpdateState");
  if (!btn || !state) return;

  function paint() {
    const on = isAutoUpdateEnabled();
    state.textContent = on ? "ON" : "OFF";
    btn.style.color = on ? "#10b981" : "var(--text-muted)";
    btn.style.background = on ? "rgba(16, 185, 129, 0.1)" : "rgba(148, 163, 184, 0.08)";
    btn.style.borderColor = on ? "rgba(16, 185, 129, 0.3)" : "var(--border-color)";
    btn.title = on
      ? "Automatic update notifications are ON. Click to turn off."
      : "Automatic update notifications are OFF. Critical (mandatory) updates are still shown. Click to turn on.";
  }

  btn.addEventListener("click", () => {
    setAutoUpdateEnabled(!isAutoUpdateEnabled());
    paint();
    loadAds();  // apply immediately (show or hide the optional update bar)
  });

  paint();
}

// ==========================================
// USER AGENTS
// ==========================================
async function loadUserAgents() {
  try {
    const res = await fetch("/api/user-agents");
    allUserAgents = await res.json();
    populateUaSelect();
  } catch (err) {
    console.error("Failed to load user agents:", err);
  }
}

function populateUaSelect() {
  const select = document.getElementById("formUserAgentSelect");
  select.innerHTML = "";

  const categories = {};
  allUserAgents.forEach(item => {
    const cat = item.category || "General";
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(item);
  });

  Object.keys(categories).forEach(cat => {
    const group = document.createElement("optgroup");
    group.label = cat;
    categories[cat].forEach(ua => {
      const opt = document.createElement("option");
      opt.value = ua.id;
      opt.textContent = ua.name;
      group.appendChild(opt);
    });
    select.appendChild(group);
  });

  const customOpt = document.createElement("option");
  customOpt.value = "custom";
  customOpt.textContent = "✏️ Custom User-Agent (Paste your own)";
  select.appendChild(customOpt);
}

// ==========================================
// GITHUB ACCOUNTS
// ==========================================
async function loadGithubAccounts() {
  try {
    const res = await fetch("/api/github-accounts");
    allGithubAccounts = await res.json();
    populateGithubAccountSelect();
    renderGithubAccountsList();
  } catch (err) {
    console.error("Failed to load GitHub accounts:", err);
  }
}

function populateGithubAccountSelect() {
  const select = document.getElementById("formGithubAccountSelect");
  if (!select) return;
  select.innerHTML = "";

  if (allGithubAccounts.length === 0) {
    const opt = document.createElement("option");
    opt.value = "default";
    opt.textContent = "⚠️ No GitHub account connected (Click + to connect)";
    select.appendChild(opt);
    return;
  }

  // Default option
  const defAcc = allGithubAccounts.find(a => a.is_default) || allGithubAccounts[0];
  const defOpt = document.createElement("option");
  defOpt.value = "default";
  defOpt.textContent = `★ Default Account (@${defAcc.username}${defAcc.label ? ' - ' + defAcc.label : ''})`;
  select.appendChild(defOpt);

  allGithubAccounts.forEach(acc => {
    const opt = document.createElement("option");
    opt.value = acc.id;
    opt.textContent = `@${acc.username} (${acc.label || acc.name || 'Account'}) [${acc.repo}]`;
    select.appendChild(opt);
  });
}

function renderGithubAccountsList() {
  const container = document.getElementById("githubAccountsList");
  if (!container) return;

  if (allGithubAccounts.length === 0) {
    container.innerHTML = `
      <div style="padding: 16px; background: var(--bg-card); border-radius: var(--radius-md); text-align: center; color: var(--text-muted); font-size: 13px;">
        No GitHub accounts connected yet. Add your first token below.
      </div>
    `;
    return;
  }

  container.innerHTML = allGithubAccounts.map(acc => `
    <div style="display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; background: var(--bg-card); border: 1px solid ${acc.is_default ? 'var(--accent-cyan)' : 'var(--border-color)'}; border-radius: var(--radius-md);">
      <div style="display: flex; align-items: center; gap: 12px;">
        <img src="${acc.avatar_url || 'https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png'}" width="36" height="36" style="border-radius: 50%; border: 1px solid var(--border-highlight);" alt="avatar">
        <div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <strong style="font-size: 14px; color: var(--text-primary);">@${acc.username}</strong>
            ${acc.is_default ? '<span style="background: rgba(14, 165, 233, 0.2); color: var(--accent-cyan); font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; text-transform: uppercase;">Default</span>' : ''}
          </div>
          <div style="font-size: 12px; color: var(--text-secondary); margin-top: 2px;">
            ${acc.label ? `<span>${acc.label} &bull; </span>` : ''}
            <span style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted);">${acc.repo}</span>
            <span style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted); margin-left: 6px;">(${acc.token_masked})</span>
          </div>
        </div>
      </div>
      <div style="display: flex; align-items: center; gap: 8px;">
        <button class="btn-text" style="font-size: 11px; color: var(--text-secondary); background: rgba(255,255,255,0.06); padding: 4px 8px; border-radius: 4px; border: 1px solid var(--border-color);" onclick="testGithubAccount('${acc.id}', '${acc.username}')" title="Live test if this token is valid and has proper permissions">
          ⚡ Test Token
        </button>
        ${!acc.is_default ? `
          <button class="btn-text" style="font-size: 12px; color: var(--accent-cyan); padding: 4px 8px;" onclick="setDefaultGithubAccount('${acc.id}')">
            Set Default
          </button>
        ` : ''}
        <button class="btn-icon danger" title="Remove Account" onclick="deleteGithubAccount('${acc.id}', '${acc.username}')">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
        </button>
      </div>
    </div>
  `).join("");
}

function openGithubModal() {
  document.getElementById("githubModal").style.display = "flex";
  renderGithubAccountsList();
}

function closeGithubModal() {
  document.getElementById("githubModal").style.display = "none";
}

async function testGithubAccount(id, username) {
  try {
    const res = await fetch(`/api/github-accounts/${id}/verify`);
    if (!res.ok) {
      const txt = await res.text();
      alert(`Server error (${res.status}): ${txt}`);
      return;
    }
    const data = await res.json();
    if (data.success) {
      alert(`✅ GitHub Token Valid!\n\nAccount: @${data.username}\nScopes: ${data.scopes || 'Full Repo & Workflow'}\nStatus: Ready to launch cloud proxies!`);
    } else {
      alert(`❌ GitHub Token Error for @${username}:\n\n${data.error}\n\n👉 Solution: GitHub par jaakar naya Personal Access Token generate karein jisme 'repo' aur 'workflow' dono checkboxes ticked hon, aur yahan naya token add karein.`);
    }
  } catch (err) {
    alert(`Failed to test token: ${err.message}`);
  }
}

async function setDefaultGithubAccount(id) {
  try {
    await fetch(`/api/github-accounts/${id}/set-default`, { method: "POST" });
    await loadGithubAccounts();
  } catch (e) {
    alert("Failed to set default account: " + e.message);
  }
}

async function deleteGithubAccount(id, username) {
  if (!confirm(`Are you sure you want to remove GitHub account @${username}?`)) return;
  try {
    await fetch(`/api/github-accounts/${id}`, { method: "DELETE" });
    await loadGithubAccounts();
  } catch (e) {
    alert("Failed to remove account: " + e.message);
  }
}

// ==========================================
// PROFILES
// ==========================================
async function loadProfiles() {
  try {
    const res = await fetch("/api/profiles");
    allProfiles = await res.json();
    renderProfiles();
    updateCounts();
  } catch (err) {
    console.error("Failed to load profiles:", err);
  }
}

function updateCounts() {
  document.getElementById("countAll").textContent = allProfiles.length;
  document.getElementById("countRunning").textContent = allProfiles.filter(p => p.isRunning).length;
  document.getElementById("countMobile").textContent = allProfiles.filter(p => p.isMobile).length;
  document.getElementById("countGithub").textContent = allProfiles.filter(p => p.proxyType === "github").length;
}

function renderProfiles() {
  const grid = document.getElementById("profilesGrid");
  const empty = document.getElementById("emptyState");
  const search = document.getElementById("searchInput").value.toLowerCase().trim();

  let filtered = allProfiles.filter(p => {
    // Filter pill
    if (activeFilter === "running" && !p.isRunning) return false;
    if (activeFilter === "mobile" && !p.isMobile) return false;
    if (activeFilter === "github" && p.proxyType !== "github") return false;

    // Search
    if (search) {
      const matchName = (p.name || "").toLowerCase().includes(search);
      const matchNotes = (p.notes || "").toLowerCase().includes(search);
      const matchUa = (p.userAgent || "").toLowerCase().includes(search);
      if (!matchName && !matchNotes && !matchUa) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    grid.innerHTML = "";
    empty.style.display = "block";
    return;
  }

  empty.style.display = "none";

  const headerHtml = `
    <div class="profiles-header">
      <div class="col-serial">#</div>
      <div class="col-name">Profile</div>
      <div class="col-device">Device</div>
      <div class="col-proxy">Proxy</div>
      <div class="col-tz">Timezone</div>
      <div class="col-status">Status</div>
      <div class="col-actions">Actions</div>
    </div>`;

  const rowsHtml = filtered.map((p, idx) => {
    const uaObj = allUserAgents.find(u => u.id === p.userAgentId);
    const uaName = uaObj ? uaObj.name : (p.isMobile ? "📱 Custom Mobile" : "💻 Custom Desktop");
    const isRunning = p.isRunning || false;
    const color = p.color || "#0ea5e9";
    const serial = idx + 1;

    let proxyHtml = "";
    if (p.proxyType === "github") {
      let ghLabel = "Azure IP";
      if (p.githubAccountId && p.githubAccountId !== "default") {
        const ghAcc = allGithubAccounts.find(a => a.id === p.githubAccountId);
        if (ghAcc) ghLabel = `@${ghAcc.username}`;
      }
      proxyHtml = `<span class="cell-proxy proxy-github" title="GitHub Cloud Runner: ${ghLabel}">☁️ ${ghLabel}</span>`;
    } else if (p.proxyType === "custom") {
      proxyHtml = `<span class="cell-proxy proxy-custom" title="${p.customProxy || 'Custom Proxy'}">🔒 Custom</span>`;
    } else {
      proxyHtml = `<span class="cell-proxy proxy-direct" title="Direct Connection (no proxy)">🌐 Direct</span>`;
    }

    const statusHtml = isRunning
      ? `<span class="cell-status status-on" title="${p.allocatedIp ? 'IP: ' + p.allocatedIp : 'Running'}"><span class="pulse-dot"></span>Running</span>`
      : `<span class="cell-status status-off">Idle</span>`;

    return `
      <div class="profile-card profile-row ${isRunning ? 'active-running' : ''}" data-id="${p.id}">
        <div class="col-serial">
          <span class="row-serial" style="border-color: ${color}; color: ${color};">${serial}</span>
        </div>
        <div class="col-name">
          <span class="row-dot" style="background:${color};"></span>
          <div class="row-name-text">
            <div class="profile-name" title="${p.name || ''}">${p.name || 'Untitled'}</div>
            <div class="profile-notes" title="${p.notes || ''}">${p.notes || 'No notes'}</div>
          </div>
        </div>
        <div class="col-device"><span class="cell-device badge-ua" title="${uaName}">${uaName}</span></div>
        <div class="col-proxy">${proxyHtml}</div>
        <div class="col-tz"><span class="cell-tz" title="${p.timezone || 'America/New_York'}">🕒 ${p.timezone || 'America/New_York'}</span></div>
        <div class="col-status">${statusHtml}</div>
        <div class="col-actions">
          <div class="profile-actions">
            ${isRunning ? `
              <button class="btn-stop btn-row" onclick="stopProfile('${p.id}')" title="Stop this browser">
                <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>
                <span>Stop</span>
              </button>
            ` : `
              <button class="btn-launch btn-row" onclick="launchProfile('${p.id}')" title="Launch this profile">
                <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                <span>Launch</span>
              </button>
              ${p.proxyType === 'github' ? `
                <button class="btn-icon btn-direct" onclick="launchProfile('${p.id}', null, true)" title="Instant launch without waiting for the GitHub Cloud Runner">⚡</button>
              ` : ''}
              <button class="btn-test btn-icon" onclick="testProfile('${p.id}')" title="Test this profile's anti-detect fingerprint on CreepJS">🧪</button>
            `}
            <button class="btn-icon" title="Edit profile & proxy settings" onclick="openEditModal('${p.id}')">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
            </button>
            <button class="btn-icon" title="Open storage folder" onclick="openFolder('${p.id}')">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>
            </button>
            <button class="btn-icon danger" title="Delete profile" onclick="deleteProfile('${p.id}')">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
            </button>
          </div>
        </div>
      </div>
    `;
  }).join("");

  grid.innerHTML = headerHtml + rowsHtml;
}

// Launch Profile
async function launchProfile(id, urlOverride = null, forceDirect = false) {
  const card = document.querySelector(`.profile-card[data-id="${id}"]`);
  const btn = card ? card.querySelector(".btn-launch") : null;
  const testBtn = card ? card.querySelector(".btn-test") : null;
  const p = allProfiles.find(x => x.id === id);

  if (!forceDirect && p && p.proxyType === "github" && (!allGithubAccounts || allGithubAccounts.length === 0)) {
    const wantDirect = confirm("⚠️ GitHub Account Connected Nahi Hai!\n\nIs profile par 'GitHub Cloud Runner' proxy set hai lekin koi GitHub account connect nahi hai.\n\n👉 Kya aap is profile ko Direct Connection (with full Anti-Detect stealth) ke sath abhi kholna chahte hain?");
    if (wantDirect) {
      return launchProfile(id, urlOverride, true);
    }
    return;
  }

  if (btn) {
    if (urlOverride) {
      btn.innerHTML = `<span class="pulse-dot"></span><span>Starting CreepJS...</span>`;
    } else if (!forceDirect && p && p.proxyType === "github") {
      btn.innerHTML = `<span class="pulse-dot"></span><span>Connecting Azure IP...</span>`;
    } else {
      btn.innerHTML = `<span class="pulse-dot"></span><span>Starting...</span>`;
    }
    btn.disabled = true;
  }
  if (testBtn) testBtn.disabled = true;

  try {
    const payload = {};
    if (urlOverride) payload.url = urlOverride;
    if (forceDirect) payload.direct = true;

    const res = await fetch(`/api/profiles/${id}/launch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.error) {
      if (!forceDirect && p && p.proxyType === "github") {
        const wantDirect = confirm(
          `⚠️ GitHub Cloud Runner connect nahi ho saka:\n${data.error}\n\n👉 Kya aap is profile ko Direct Anti-Detect mode me abhi turant kholna chahte hain?\n(Full Anti-Detect protection, canvas noise, WebGL spoofing aur US Timezone 100% active rahega!)`
        );
        if (wantDirect) {
          return launchProfile(id, urlOverride, true);
        }
      } else {
        alert(`Launch failed: ${data.error}`);
      }
    }
  } catch (err) {
    alert(`Failed to launch: ${err.message}`);
  } finally {
    await loadProfiles();
  }
}

function testProfile(id) {
  launchProfile(id, "https://creepjs.org/checker#scan");
}

// Stop Profile
async function stopProfile(id) {
  try {
    await fetch(`/api/profiles/${id}/stop`, { method: "POST" });
  } catch (err) {
    console.error("Stop error:", err);
  } finally {
    await loadProfiles();
  }
}

// Open Profile Folder
async function openFolder(id) {
  try {
    await fetch(`/api/profiles/${id}/open-folder`, { method: "POST" });
  } catch (e) {}
}

// Delete Profile
async function deleteProfile(id) {
  const p = allProfiles.find(x => x.id === id);
  const name = p ? p.name : "this profile";
  if (!confirm(`Are you sure you want to delete "${name}"?`)) return;

  try {
    await fetch(`/api/profiles/${id}`, { method: "DELETE" });
    await loadProfiles();
  } catch (err) {
    alert(`Delete failed: ${err.message}`);
  }
}

// Modal handling
function openCreateModal() {
  document.getElementById("modalTitle").textContent = "Create New Profile";
  document.getElementById("formProfileId").value = "";
  document.getElementById("formName").value = "";
  document.getElementById("formNotes").value = "";
  document.getElementById("formColor").value = getRandomColor();
  document.getElementById("formUserAgentSelect").value = "win_chrome";
  document.getElementById("formCustomUaGroup").style.display = "none";
  // Check if GitHub account exists, otherwise default to Direct Connection
  const hasGh = (allGithubAccounts && allGithubAccounts.length > 0);
  if (hasGh) {
    document.querySelector('input[name="proxyType"][value="github"]').checked = true;
    document.getElementById("formGithubAccountGroup").style.display = "block";
    document.getElementById("formGithubAccountSelect").value = "default";
  } else {
    document.querySelector('input[name="proxyType"][value="none"]').checked = true;
    document.getElementById("formGithubAccountGroup").style.display = "none";
  }
  document.getElementById("formCustomProxyGroup").style.display = "none";
  document.getElementById("formCustomProxy").value = "";
  const qp = document.getElementById("formProxyQuickPaste");
  if (qp) qp.value = "";
  const ph = document.getElementById("formProxyHost");
  if (ph) ph.value = "";
  const pp = document.getElementById("formProxyPort");
  if (pp) pp.value = "";
  const pu = document.getElementById("formProxyUser");
  if (pu) pu.value = "";
  const ppw = document.getElementById("formProxyPass");
  if (ppw) ppw.value = "";
  const diagCard = document.getElementById("proxyDiagnosticCard");
  if (diagCard) diagCard.style.display = "none";
  const badge = document.getElementById("proxyTestStatusBadge");
  if (badge) badge.style.display = "none";
  document.getElementById("formTimezone").value = "America/New_York";
  document.getElementById("formStartUrl").value = "https://studio.youtube.com";

  const autoGroup = document.getElementById("formAutoLaunchGroup");
  if (autoGroup) autoGroup.style.display = "block";
  const autoCb = document.getElementById("formAutoLaunch");
  if (autoCb) autoCb.checked = true;

  const saveBtn = document.getElementById("btnSaveProfile");
  if (saveBtn) saveBtn.textContent = "+ Create Profile";

  document.getElementById("profileModal").style.display = "flex";
}

function openEditModal(id) {
  const p = allProfiles.find(x => x.id === id);
  if (!p) return;

  const autoGroup = document.getElementById("formAutoLaunchGroup");
  if (autoGroup) autoGroup.style.display = "none";

  document.getElementById("modalTitle").textContent = `✏️ Edit Profile: ${p.name || ''}`;
  const saveBtn = document.getElementById("btnSaveProfile");
  if (saveBtn) saveBtn.textContent = "💾 Save Changes";

  document.getElementById("formProfileId").value = p.id;
  document.getElementById("formName").value = p.name || "";
  document.getElementById("formNotes").value = p.notes || "";
  document.getElementById("formColor").value = p.color || "#0ea5e9";
  
  const select = document.getElementById("formUserAgentSelect");
  if (p.userAgentId) {
    select.value = p.userAgentId;
  }
  if (p.userAgentId === "custom") {
    document.getElementById("formCustomUaGroup").style.display = "flex";
    document.getElementById("formCustomUaInput").value = p.userAgent || "";
  } else {
    document.getElementById("formCustomUaGroup").style.display = "none";
  }

  const proxyRadio = document.querySelector(`input[name="proxyType"][value="${p.proxyType || 'none'}"]`);
  if (proxyRadio) proxyRadio.checked = true;

  if (p.proxyType === "github") {
    document.getElementById("formGithubAccountGroup").style.display = "block";
    document.getElementById("formGithubAccountSelect").value = p.githubAccountId || "default";
  } else {
    document.getElementById("formGithubAccountGroup").style.display = "none";
  }

  const diagCard = document.getElementById("proxyDiagnosticCard");
  if (diagCard) diagCard.style.display = "none";
  const badge = document.getElementById("proxyTestStatusBadge");
  if (badge) badge.style.display = "none";

  if (p.proxyType === "custom") {
    document.getElementById("formCustomProxyGroup").style.display = "block";
    document.getElementById("formCustomProxy").value = p.customProxy || "";
    const parsed = parseProxyClientSide(p.customProxy || "");
    if (parsed) {
      applyParsedProxyToInputs(parsed);
      const qp = document.getElementById("formProxyQuickPaste");
      if (qp) qp.value = p.customProxy || "";
    }
  } else {
    document.getElementById("formCustomProxyGroup").style.display = "none";
  }

  document.getElementById("formTimezone").value = p.timezone || "America/New_York";
  document.getElementById("formStartUrl").value = p.startUrl || "https://studio.youtube.com";
  document.getElementById("profileModal").style.display = "flex";
}

// Ensure global accessibility for inline onclick handlers
window.openEditModal = openEditModal;
window.openCreateModal = openCreateModal;
window.testProfile = testProfile;
window.launchProfile = launchProfile;
window.stopProfile = stopProfile;
window.openFolder = openFolder;
window.deleteProfile = deleteProfile;

function closeModal() {
  document.getElementById("profileModal").style.display = "none";
}

function getRandomColor() {
  const colors = ["#0ea5e9", "#10b981", "#ec4899", "#f59e0b", "#8b5cf6", "#f43f5e", "#06b6d4"];
  return colors[Math.floor(Math.random() * colors.length)];
}

function setupEventListeners() {
  document.getElementById("btnNewProfile").addEventListener("click", openCreateModal);
  document.getElementById("btnEmptyCreate").addEventListener("click", openCreateModal);
  document.getElementById("modalCloseBtn").addEventListener("click", closeModal);
  document.getElementById("btnCancelModal").addEventListener("click", closeModal);

  const switchAllBtn = document.getElementById("btnSwitchAllDirect");
  if (switchAllBtn) {
    switchAllBtn.addEventListener("click", async () => {
      if (!confirm("⚡ Switch All Profiles to Direct Connection?\n\nSabhi profiles direct anti-detect mode me switch ho jayengi aur 'Launch' click karte hi 0.2 second me turant khulengi bina kisi GitHub token ya wait ke.")) return;
      try {
        await fetch("/api/profiles/switch-all-direct", { method: "POST" });
        await loadProfiles();
        alert("✅ Sabhi profiles ab Direct Connection par set ho gayi hain!\n\nAb 'Launch' dabate hi browser turant open ho jayega.");
      } catch (err) {
        alert("Failed: " + err.message);
      }
    });
  }

  // GitHub modal events
  document.getElementById("btnGithubAccounts").addEventListener("click", openGithubModal);
  document.getElementById("githubModalCloseBtn").addEventListener("click", closeGithubModal);
  document.getElementById("btnOpenGhModalFromForm").addEventListener("click", () => {
    closeModal();
    openGithubModal();
  });

  const copyBtn = document.getElementById("btnCopyGhLink");
  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      const link = "https://github.com/settings/tokens/new?description=AntiDetect-Studio-Proxy&scopes=repo,workflow";
      navigator.clipboard.writeText(link).then(() => {
        const origText = copyBtn.innerHTML;
        copyBtn.innerHTML = "✓ Link Copied!";
        copyBtn.style.color = "var(--accent-emerald)";
        setTimeout(() => {
          copyBtn.innerHTML = origText;
          copyBtn.style.color = "var(--text-primary)";
        }, 2500);
      }).catch(err => {
        prompt("Copy this link manually:", link);
      });
    });
  }

  // Add GitHub Account Form Submit
  document.getElementById("addGhAccountForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("btnSubmitGhAccount");
    const token = document.getElementById("ghTokenInput").value.trim();
    const label = document.getElementById("ghLabelInput").value.trim();
    const isDefault = document.getElementById("ghIsDefaultInput").checked;

    btn.disabled = true;
    btn.innerHTML = `<span class="pulse-dot"></span><span>Verifying & Setting up Repo...</span>`;

    try {
      const res = await fetch("/api/github-accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, label, is_default: isDefault })
      });
      const data = await res.json();
      if (data.error) {
        alert("GitHub Connection Failed: " + data.error);
      } else {
        document.getElementById("ghTokenInput").value = "";
        document.getElementById("ghLabelInput").value = "";
        document.getElementById("ghIsDefaultInput").checked = false;
        await loadGithubAccounts();
        alert(`Successfully connected GitHub account: @${data.account.username}`);
      }
    } catch (err) {
      alert("Error connecting account: " + err.message);
    } finally {
      btn.disabled = false;
      btn.innerHTML = `<span>Verify & Connect Account</span>`;
    }
  });

  // Search input
  document.getElementById("searchInput").addEventListener("input", renderProfiles);

  // Filter pills
  document.querySelectorAll(".pill").forEach(pill => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".pill").forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
      activeFilter = pill.dataset.filter;
      renderProfiles();
    });
  });

  // Proxy radio change
  document.querySelectorAll('input[name="proxyType"]').forEach(radio => {
    radio.addEventListener("change", (e) => {
      const isCustom = e.target.value === "custom";
      const isGithub = e.target.value === "github";
      document.getElementById("formCustomProxyGroup").style.display = isCustom ? "block" : "none";
      document.getElementById("formGithubAccountGroup").style.display = isGithub ? "block" : "none";
      if (isCustom) {
        assembleProxyString();
      }
    });
  });

  // Quick Paste / Auto-Fill Proxy (ixBrowser style)
  const quickPasteInput = document.getElementById("formProxyQuickPaste");
  const autoFillBtn = document.getElementById("btnAutoFillProxy");

  function triggerProxyAutoFill() {
    const raw = quickPasteInput?.value?.trim() || "";
    if (!raw) return;
    const parsed = parseProxyClientSide(raw);
    if (parsed) {
      applyParsedProxyToInputs(parsed);
      const badge = document.getElementById("proxyTestStatusBadge");
      if (badge) {
        badge.style.display = "inline";
        badge.style.color = "var(--accent-cyan)";
        badge.textContent = "⚡ Form auto-filled!";
        setTimeout(() => { badge.style.display = "none"; }, 2000);
      }
    }
  }

  if (quickPasteInput) {
    quickPasteInput.addEventListener("input", () => {
      const val = quickPasteInput.value.trim();
      if (val.includes(":") || val.includes("@")) {
        triggerProxyAutoFill();
      }
    });
  }

  if (autoFillBtn) {
    autoFillBtn.addEventListener("click", triggerProxyAutoFill);
  }

  // Structured Proxy Inputs Change -> Reassemble
  ["formProxyProtocol", "formProxyHost", "formProxyPort", "formProxyUser", "formProxyPass"].forEach(fId => {
    const el = document.getElementById(fId);
    if (el) {
      el.addEventListener("input", assembleProxyString);
      el.addEventListener("change", assembleProxyString);
    }
  });

  // Test Proxy Connection Button
  const btnTestProxy = document.getElementById("btnTestProxyConn");
  if (btnTestProxy) {
    btnTestProxy.addEventListener("click", handleTestProxyConnection);
  }

  // User-Agent select change
  document.getElementById("formUserAgentSelect").addEventListener("change", (e) => {
    document.getElementById("formCustomUaGroup").style.display = 
      e.target.value === "custom" ? "flex" : "none";
  });

  // Randomize button in modal
  document.getElementById("btnFormRandomUa").addEventListener("click", () => {
    const randomItem = allUserAgents[Math.floor(Math.random() * allUserAgents.length)];
    document.getElementById("formUserAgentSelect").value = randomItem.id;
    document.getElementById("formCustomUaGroup").style.display = "none";
  });

  // Form Submit
  document.getElementById("profileForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    assembleProxyString();
    const id = document.getElementById("formProfileId").value;
    const uaId = document.getElementById("formUserAgentSelect").value;
    const uaObj = allUserAgents.find(u => u.id === uaId);

    let uaString = "";
    let isMobile = false;

    if (uaId === "custom") {
      uaString = document.getElementById("formCustomUaInput").value.trim();
      isMobile = uaString.includes("Mobile") || uaString.includes("Android") || uaString.includes("iPhone");
    } else if (uaObj) {
      uaString = uaObj.ua;
      isMobile = uaObj.isMobile || false;
    }

    const payload = {
      name: document.getElementById("formName").value.trim(),
      color: document.getElementById("formColor").value,
      notes: document.getElementById("formNotes").value.trim(),
      userAgentId: uaId,
      userAgent: uaString,
      isMobile: isMobile,
      proxyType: document.querySelector('input[name="proxyType"]:checked').value,
      githubAccountId: document.getElementById("formGithubAccountSelect").value || "default",
      customProxy: document.getElementById("formCustomProxy").value.trim(),
      timezone: document.getElementById("formTimezone").value || "America/New_York",
      startUrl: document.getElementById("formStartUrl").value.trim() || "https://studio.youtube.com"
    };

    if (payload.proxyType === "github" && (!allGithubAccounts || allGithubAccounts.length === 0)) {
      alert("⚠️ GitHub Account Required!\n\nAapne 'GitHub Cloud Runner' proxy chuna hai, lekin koi GitHub account connected nahi hai.\n\nYa to Settings me jaakar apna GitHub account jodiye, ya phir 'Direct Connection' ya 'Custom Proxy' select kijiye.");
      return;
    }

    let newProfileId = null;
    const shouldAutoLaunch = document.getElementById("formAutoLaunch")?.checked || false;

    if (id) {
      // Update
      await fetch(`/api/profiles/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } else {
      // Create
      const res = await fetch("/api/profiles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.profile && data.profile.id) {
        newProfileId = data.profile.id;
      }
    }

    closeModal();
    await loadProfiles();

    // Automatically launch browser if requested
    if (newProfileId && shouldAutoLaunch) {
      launchProfile(newProfileId);
    }
  });

  // Ads modal close handlers
  document.getElementById("adPopupCloseBtn")?.addEventListener("click", closeAdPopup);
  document.getElementById("adPopupDismissBtn")?.addEventListener("click", closeAdPopup);
}

function isNewerVersion(remote, local) {
  const r = (remote || "").split(".").map(n => parseInt(n) || 0);
  const l = (local || "").split(".").map(n => parseInt(n) || 0);
  for (let i = 0; i < Math.max(r.length, l.length); i++) {
    const rv = r[i] || 0;
    const lv = l[i] || 0;
    if (rv > lv) return true;
    if (rv < lv) return false;
  }
  return false;
}

function initBuiltInUpdater() {
  document.getElementById("mandatoryUpdateBtn")?.addEventListener("click", startBuiltInUpdate);
  document.getElementById("updateNoticeBtn")?.addEventListener("click", startBuiltInUpdate);
}

function renderUpdateProgress(status) {
  const wrap = document.getElementById("updateInstallProgress");
  const fill = document.getElementById("updateProgressFill");
  const text = document.getElementById("updateProgressText");
  if (wrap) wrap.hidden = false;
  if (fill) fill.style.width = `${Math.max(0, Math.min(100, status.percent || 0))}%`;
  if (text) text.textContent = status.message || "Working on the update…";
  const label = status.stage === "ready" || status.stage === "restarting"
    ? "Installing & Restarting…" : `Updating… ${status.percent || 0}%`;
  const mandatoryText = document.getElementById("mandatoryBtnText");
  const noticeText = document.getElementById("updateNoticeBtnText");
  if (mandatoryText) mandatoryText.textContent = label;
  if (noticeText) noticeText.textContent = label;
}

function updateInstallFailed(message) {
  _updateInstalling = false;
  const status = {stage: "error", percent: 0, message: `Update failed: ${message} Your current app was not changed.`};
  renderUpdateProgress(status);
  ["mandatoryUpdateBtn", "updateNoticeBtn"].forEach(id => {
    const button = document.getElementById(id);
    if (button) button.disabled = false;
  });
  const mandatoryText = document.getElementById("mandatoryBtnText");
  const noticeText = document.getElementById("updateNoticeBtnText");
  if (mandatoryText) mandatoryText.textContent = "Retry Update";
  if (noticeText) noticeText.textContent = "Retry Update";
}

async function startBuiltInUpdate() {
  if (_updateInstalling || !_latestUpdate) return;
  _updateInstalling = true;
  const modal = document.getElementById("mandatoryUpdateModal");
  document.getElementById("mandatoryTitle").textContent = _latestUpdate.title || `Update ${_latestUpdate.latestVersion}`;
  document.getElementById("mandatoryChangelog").textContent = _latestUpdate.changelog || "A new update is available.";
  if (modal) modal.style.display = "flex";
  ["mandatoryUpdateBtn", "updateNoticeBtn"].forEach(id => {
    const button = document.getElementById(id);
    if (button) button.disabled = true;
  });
  renderUpdateProgress({stage: "preparing", percent: 0, message: "Checking the signed update…"});
  try {
    const started = await fetch("/api/update/download", {
      method: "POST", headers: {"Content-Type": "application/json", "X-App-Request": "1"}, body: "{}"
    });
    const startStatus = await started.json();
    if (!started.ok) throw new Error(startStatus.error || "Could not start the update.");
    renderUpdateProgress(startStatus);
    while (_updateInstalling) {
      await new Promise(resolve => setTimeout(resolve, 500));
      const response = await fetch("/api/update/status", {cache: "no-store"});
      const status = await response.json();
      renderUpdateProgress(status);
      if (status.stage === "error") throw new Error(status.message || "Download failed.");
      if (status.stage === "ready") {
        renderUpdateProgress({stage: "restarting", percent: 100, message: "Installing update and restarting the app…"});
        let install;
        try {
          install = await fetch("/api/update/apply", {
            method: "POST", headers: {"Content-Type": "application/json", "X-App-Request": "1"}, body: "{}"
          });
        } catch (_) {
          // Once apply has been submitted, a lost connection normally means the
          // old executable exited and the replacement is starting on this port.
          renderUpdateProgress({stage: "restarting", percent: 100, message: "Update installed. Waiting for the app to restart…"});
          setTimeout(() => window.location.reload(), 4000);
          return;
        }
        let raw = "";
        try { raw = await install.text(); } catch (_) {}
        let result = !raw && install.ok ? {success: true} : {};
        try {
          result = raw ? JSON.parse(raw) : {};
        } catch (parseError) {
          // A successful apply intentionally terminates the local server. Some
          // browsers can observe that shutdown while consuming the final body.
          if (!install.ok) throw parseError;
          result = {success: true};
        }
        if (!install.ok || !result.success) throw new Error(result.error || "Could not install the update.");
        setTimeout(() => window.location.reload(), 4000);
        return;
      }
    }
  } catch (error) {
    updateInstallFailed(error.message || String(error));
  }
}

function scheduleAutomaticUpdate() {
  if (_updateInstalling || _automaticUpdateScheduled) return;
  // Never restart underneath an active browser profile. The normal update
  // prompt stays visible and automatic installation begins on a clean launch.
  if (allProfiles.some(profile => profile.isRunning)) return;
  _automaticUpdateScheduled = true;
  setTimeout(() => {
    _automaticUpdateScheduled = false;
    startBuiltInUpdate();
  }, 500);
}

// ==========================================
// REMOTE-CONTROLLED ADS & UPDATES SYSTEM
// ==========================================
async function loadAds() {
  try {
    const res = await fetch("/api/ads");
    if (!res.ok) return;
    const ads = await res.json();
    if (!ads) return;

    // 0. Check Software Updates Broadcaster
    const updates = ads.updates;
    const currentVersion = ads.currentAppVersion || "1.0.0";
    const autoUpdateOn = isAutoUpdateEnabled();
    const updateBar = document.getElementById("updateNoticeBar");
    if (!_updateInstalling) document.getElementById("mandatoryUpdateModal").style.display = "none";
    if (updates && updates.enabled && updates.latestVersion && isNewerVersion(updates.latestVersion, currentVersion)) {
      _latestUpdate = updates;
      if (updates.mandatory) {
        // Critical mandatory update: always shown (owner's force-update lever), even if the
        // user turned auto-update notifications off — this cannot be silently bypassed.
        document.getElementById("mandatoryTitle").textContent = updates.title || "Update Required to Continue";
        document.getElementById("mandatoryChangelog").textContent = updates.changelog || "New updates available.";
        if (!_updateInstalling && updates.buttonText) document.getElementById("mandatoryBtnText").textContent = updates.buttonText;
        document.getElementById("mandatoryUpdateModal").style.display = "flex";
        if (updateBar) updateBar.style.display = "none";
        scheduleAutomaticUpdate();
      } else if (autoUpdateOn) {
        // Optional announcement bar — suppressed when the user turns auto-update off.
        document.getElementById("updateNoticeTitle").textContent = updates.title || `Anti-Detect Studio v${updates.latestVersion} Available!`;
        document.getElementById("updateNoticeChangelog").textContent = updates.changelog ? updates.changelog.split('\n')[0] : "Click to get the latest features.";
        if (!_updateInstalling && updates.buttonText) document.getElementById("updateNoticeBtnText").textContent = updates.buttonText;
        if (updateBar) updateBar.style.display = "flex";
        scheduleAutomaticUpdate();
        if (_updateInstalling) document.getElementById("mandatoryUpdateModal").style.display = "flex";
      } else if (updateBar) {
        updateBar.style.display = "none";
      }
    } else {
      _latestUpdate = null;
      if (updateBar) updateBar.style.display = "none";
    }

    // 1. Render Banner Ad (Only in Profile Manager UI)
    const banner = ads.banner;
    const bannerContainer = document.getElementById("adBannerContainer");
    if (banner && banner.enabled && bannerContainer) {
      if (banner.badge) document.getElementById("adBannerBadge").textContent = banner.badge;
      if (banner.title) document.getElementById("adBannerTitle").textContent = banner.title;
      if (banner.description) document.getElementById("adBannerDesc").textContent = banner.description;
      if (banner.buttonText) document.getElementById("adBannerBtnText").textContent = banner.buttonText;
      if (banner.linkUrl) document.getElementById("adBannerBtn").href = banner.linkUrl;
      bannerContainer.style.display = "block";
    } else if (bannerContainer) {
      bannerContainer.style.display = "none";
    }

    // 2. Render Popup Ad (Only in Profile Manager UI)
    const popup = ads.popup;
    const popupModal = document.getElementById("adPopupModal");
    if (popup && popup.enabled && popupModal && !_popupShownThisSession) {
      const popupKey = "dismissed_ad_" + (popup.id || "default");
      const alreadyDismissed = sessionStorage.getItem(popupKey);
      if (!alreadyDismissed || !popup.showOncePerSession) {
        _popupShownThisSession = true;
        if (popup.badge) document.getElementById("adPopupBadge").textContent = popup.badge;
        if (popup.title) document.getElementById("adPopupTitle").textContent = popup.title;
        if (popup.message) document.getElementById("adPopupMessage").textContent = popup.message;
        if (popup.buttonText) document.getElementById("adPopupBtnText").textContent = popup.buttonText;
        if (popup.linkUrl) document.getElementById("adPopupBtn").href = popup.linkUrl;
        
        // Show popup after 1.5s delay for smooth entrance
        setTimeout(() => {
          popupModal.style.display = "flex";
        }, 1200);
      }
    }
  } catch (err) {
    console.error("Failed to load ads:", err);
  }
}

function closeAdPopup() {
  const modal = document.getElementById("adPopupModal");
  if (modal) {
    modal.style.display = "none";
    sessionStorage.setItem("dismissed_ad_popup", "true");
  }
}

// ==========================================
// DONATION / SUPPORT FEATURE
// ==========================================
let currentDonationConfig = null;

function initDonationFeature() {
  const btnDonate = document.getElementById("btnDonate");
  const donateModal = document.getElementById("donateModal");
  const btnDonateClose = document.getElementById("btnDonateClose");

  if (btnDonate) {
    btnDonate.addEventListener("click", () => {
      openDonationModal();
    });
  }

  if (btnDonateClose && donateModal) {
    btnDonateClose.addEventListener("click", () => {
      donateModal.style.display = "none";
    });
  }

  if (donateModal) {
    donateModal.addEventListener("click", (e) => {
      if (e.target === donateModal) donateModal.style.display = "none";
    });
  }

  // Tab switching
  const tabBtns = document.querySelectorAll(".donate-tab-btn");
  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      const tab = btn.getAttribute("data-tab");
      switchDonationTab(tab);
    });
  });

  // Copy buttons
  setupCopyBtn("btnCopyUsdtTrc", () => currentDonationConfig?.usdtTrc20 || document.getElementById("donateUsdtTrcText")?.textContent);
  setupCopyBtn("btnCopyUsdtBep", () => currentDonationConfig?.usdtBep20 || document.getElementById("donateUsdtBepText")?.textContent);

  // In-modal Edit Wallets Toggle
  const toggleEditBtn = document.getElementById("btnToggleDonateEdit");
  const cancelEditBtn = document.getElementById("btnCancelDonationEdit");
  const saveEditBtn = document.getElementById("btnSaveDonationEdit");
  const viewSection = document.getElementById("donateViewSection");
  const editSection = document.getElementById("donateEditSection");
  const toggleText = document.getElementById("btnToggleDonateEditText");

  function setDonationEditMode(isEdit) {
    if (viewSection) viewSection.style.display = isEdit ? "none" : "block";
    if (editSection) editSection.style.display = isEdit ? "block" : "none";
    if (toggleText) toggleText.textContent = isEdit ? "Preview" : "Edit Wallets";
    const msg = document.getElementById("donateEditMsg");
    if (msg) msg.style.display = "none";
    if (isEdit && currentDonationConfig) {
      populateDonationEditFields(currentDonationConfig);
    }
  }

  if (toggleEditBtn) {
    toggleEditBtn.addEventListener("click", () => {
      const isCurrentlyEdit = editSection && editSection.style.display !== "none";
      setDonationEditMode(!isCurrentlyEdit);
    });
  }

  if (cancelEditBtn) {
    cancelEditBtn.addEventListener("click", () => {
      setDonationEditMode(false);
    });
  }

  if (saveEditBtn) {
    saveEditBtn.addEventListener("click", async () => {
      const payload = {
        title: document.getElementById("editDonationTitle")?.value?.trim() || "Support Anti-Detect Studio",
        subtitle: document.getElementById("editDonationSubtitle")?.value?.trim() || "",
        usdtBep20: document.getElementById("editDonationUsdtBep")?.value?.trim() || "",
        usdtTrc20: document.getElementById("editDonationUsdtTrc")?.value?.trim() || "",
        coffeeUrl: document.getElementById("editDonationCoffee")?.value?.trim() || "",
        customNote: document.getElementById("editDonationNote")?.value?.trim() || "",
        showUsdtTrc20: document.getElementById("editShowUsdtTrc")?.checked !== false,
        showUsdtBep20: document.getElementById("editShowUsdtBep")?.checked !== false,
        showCoffee: document.getElementById("editShowCoffee")?.checked !== false
      };

      const msg = document.getElementById("donateEditMsg");
      if (msg) {
        msg.textContent = "Saving...";
        msg.style.color = "var(--accent-cyan)";
        msg.style.display = "inline";
      }

      try {
        const res = await fetch("/api/donation-config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const ret = await res.json();
        if (ret && ret.success) {
          currentDonationConfig = ret.donation || payload;
          renderDonationDetails(currentDonationConfig);
          populateDonationEditFields(currentDonationConfig);
          if (msg) {
            msg.textContent = "✅ Saved successfully!";
            msg.style.color = "#22c55e";
          }
          setTimeout(() => {
            setDonationEditMode(false);
          }, 800);
        } else {
          if (msg) {
            msg.textContent = "❌ Failed to save";
            msg.style.color = "#ef4444";
          }
        }
      } catch (err) {
        if (msg) {
          msg.textContent = "❌ Error: " + err.message;
          msg.style.color = "#ef4444";
        }
      }
    });
  }
}

function switchDonationTab(tabName) {
  document.querySelectorAll(".donate-tab-btn").forEach(b => {
    const active = b.getAttribute("data-tab") === tabName;
    b.classList.toggle("active", active);
    b.style.background = active ? "rgba(14,165,233,0.12)" : "rgba(255,255,255,0.03)";
    b.style.borderColor = active ? "rgba(14,165,233,0.4)" : "var(--border-color)";
    b.style.color = active ? "var(--accent-cyan)" : "var(--text-secondary)";
  });

  const cryptoTab = document.getElementById("donateTabCrypto");
  const customTab = document.getElementById("donateTabCustom");

  if (cryptoTab) cryptoTab.style.display = (tabName === "crypto") ? "block" : "none";
  if (customTab) customTab.style.display = (tabName === "custom") ? "block" : "none";
}

function setupCopyBtn(btnId, getValueFn) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const val = (getValueFn() || "").trim();
    if (!val) return;
    try {
      await navigator.clipboard.writeText(val);
      const originalText = btn.innerHTML;
      btn.innerHTML = "✅ Copied!";
      setTimeout(() => {
        btn.innerHTML = originalText;
      }, 2000);
    } catch {
      alert("Address: " + val);
    }
  });
}

async function openDonationModal() {
  const modal = document.getElementById("donateModal");
  if (!modal) return;
  modal.style.display = "flex";

  const viewSection = document.getElementById("donateViewSection");
  const editSection = document.getElementById("donateEditSection");
  const toggleText = document.getElementById("btnToggleDonateEditText");
  if (viewSection) viewSection.style.display = "block";
  if (editSection) editSection.style.display = "none";
  if (toggleText) toggleText.textContent = "Edit Wallets";

  try {
    const res = await fetch("/api/ads");
    if (res.ok) {
      const data = await res.json();
      if (data && data.donation) {
        currentDonationConfig = data.donation;
        renderDonationDetails(data.donation);
        populateDonationEditFields(data.donation);
      }
    }
  } catch (err) {
    console.error("Failed to load donation details:", err);
  }
}

function renderDonationDetails(d) {
  if (!d) return;
  const titleEl = document.getElementById("donateModalTitle");
  const subEl = document.getElementById("donateModalSubtitle");
  if (titleEl && d.title) titleEl.textContent = d.title;
  if (subEl && d.subtitle) subEl.textContent = d.subtitle;

  const usdtTrcEl = document.getElementById("donateUsdtTrcText");
  if (usdtTrcEl && d.usdtTrc20) usdtTrcEl.textContent = d.usdtTrc20;

  const usdtBepEl = document.getElementById("donateUsdtBepText");
  if (usdtBepEl && d.usdtBep20) usdtBepEl.textContent = d.usdtBep20;

  const coffeeLink = document.getElementById("donateCoffeeLink");
  if (coffeeLink && d.coffeeUrl) coffeeLink.href = d.coffeeUrl;

  const noteEl = document.getElementById("donateCustomNote");
  if (noteEl && d.customNote) noteEl.textContent = d.customNote;

  // Owner-controlled per-item visibility. A missing flag counts as visible (default on).
  const showTrc = d.showUsdtTrc20 !== false;
  const showBep = d.showUsdtBep20 !== false;
  const showCoffee = d.showCoffee !== false;

  const trcCard = document.getElementById("donateCardUsdtTrc");
  if (trcCard) trcCard.style.display = showTrc ? "block" : "none";
  const bepCard = document.getElementById("donateCardUsdtBep");
  if (bepCard) bepCard.style.display = showBep ? "block" : "none";

  // If both crypto items are hidden, show a small placeholder instead of an empty tab.
  const cryptoEmpty = document.getElementById("donateCryptoEmpty");
  if (cryptoEmpty) cryptoEmpty.style.display = (!showTrc && !showBep) ? "block" : "none";

  // Coffee lives on its own tab; hide the tab button and the link when turned off.
  const coffeeTabBtn = document.getElementById("tabBtnCustom");
  if (coffeeTabBtn) coffeeTabBtn.style.display = showCoffee ? "" : "none";
  if (coffeeLink) coffeeLink.style.display = showCoffee ? "" : "none";
  // If the coffee tab is hidden while active, fall back to the crypto tab.
  if (!showCoffee) {
    const customTab = document.getElementById("donateTabCustom");
    if (customTab && customTab.style.display !== "none") switchDonationTab("crypto");
  }
}

function populateDonationEditFields(d) {
  if (!d) return;
  const t = document.getElementById("editDonationTitle");
  if (t && d.title) t.value = d.title;
  const s = document.getElementById("editDonationSubtitle");
  if (s && d.subtitle) s.value = d.subtitle;
  const bep = document.getElementById("editDonationUsdtBep");
  if (bep && d.usdtBep20) bep.value = d.usdtBep20;
  const trc = document.getElementById("editDonationUsdtTrc");
  if (trc && d.usdtTrc20) trc.value = d.usdtTrc20;
  const c = document.getElementById("editDonationCoffee");
  if (c && d.coffeeUrl) c.value = d.coffeeUrl;
  const n = document.getElementById("editDonationNote");
  if (n && d.customNote) n.value = d.customNote;

  const showTrc = document.getElementById("editShowUsdtTrc");
  if (showTrc) showTrc.checked = d.showUsdtTrc20 !== false;
  const showBep = document.getElementById("editShowUsdtBep");
  if (showBep) showBep.checked = d.showUsdtBep20 !== false;
  const showCoffee = document.getElementById("editShowCoffee");
  if (showCoffee) showCoffee.checked = d.showCoffee !== false;
}

// -------------------------------------------------------------
// ixBrowser-Style Easy Proxy Parsing, Assembly, and Testing
// -------------------------------------------------------------
function parseProxyClientSide(rawStr) {
  if (!rawStr) return null;
  let raw = rawStr.trim();
  let protocol = "socks5";

  const protoMatch = raw.match(/^([a-zA-Z0-9]+):\/\//);
  if (protoMatch) {
    const p = protoMatch[1].toLowerCase();
    if (["http", "https", "socks5", "socks4"].includes(p)) {
      protocol = p;
    }
    raw = raw.substring(protoMatch[0].length).trim();
  }

  // Convert tab, comma, semicolon, pipe, or space delimiters to colons
  if (!raw.includes("@")) {
    if (raw.includes("\t")) {
      raw = raw.replace(/\t+/g, ":");
    } else if (raw.includes(",")) {
      raw = raw.replace(/,+/g, ":");
    } else if (raw.includes(";")) {
      raw = raw.replace(/;+/g, ":");
    } else if (raw.includes("|")) {
      raw = raw.replace(/\|+/g, ":");
    } else if (/\s+/.test(raw) && !raw.includes(":")) {
      raw = raw.replace(/\s+/g, ":");
    }
  }
  raw = raw.replace(/\s+/g, "");

  let host = "";
  let port = "";
  let user = "";
  let pass = "";

  if (raw.includes("@")) {
    const [left, right] = raw.split("@");
    if (right && right.includes(":")) {
      const rParts = right.split(":");
      const lParts = left.split(":");
      if (rParts.length >= 2 && !isNaN(rParts[rParts.length - 1])) {
        // user:pass@host:port
        user = lParts[0] || "";
        pass = lParts.slice(1).join(":");
        host = rParts[0] || "";
        port = rParts[1] || "";
      } else if (lParts.length >= 2 && !isNaN(lParts[lParts.length - 1])) {
        // host:port@user:pass
        host = lParts[0] || "";
        port = lParts[1] || "";
        user = rParts[0] || "";
        pass = rParts.slice(1).join(":");
      }
    }
  } else if (raw.includes(":")) {
    const parts = raw.split(":");
    if (parts.length === 2) {
      host = parts[0];
      port = parts[1];
    } else if (parts.length === 4) {
      if (!isNaN(parts[1])) {
        // host:port:user:pass
        host = parts[0];
        port = parts[1];
        user = parts[2];
        pass = parts[3];
      } else if (!isNaN(parts[3])) {
        // user:pass:host:port
        user = parts[0];
        pass = parts[1];
        host = parts[2];
        port = parts[3];
      }
    } else if (parts.length > 4 && !isNaN(parts[1])) {
      host = parts[0];
      port = parts[1];
      user = parts[2];
      pass = parts.slice(3).join(":");
    }
  } else {
    host = raw;
  }

  if (!port || isNaN(port)) port = "8080";

  return { protocol, host, port, user, pass };
}

function applyParsedProxyToInputs(parsed) {
  if (!parsed) return;
  const protoEl = document.getElementById("formProxyProtocol");
  const hostEl = document.getElementById("formProxyHost");
  const portEl = document.getElementById("formProxyPort");
  const userEl = document.getElementById("formProxyUser");
  const passEl = document.getElementById("formProxyPass");

  if (protoEl && parsed.protocol) protoEl.value = parsed.protocol;
  if (hostEl && parsed.host) hostEl.value = parsed.host;
  if (portEl && parsed.port) portEl.value = parsed.port;
  if (userEl) userEl.value = parsed.user || "";
  if (passEl) passEl.value = parsed.pass || "";

  assembleProxyString();
}

function assembleProxyString() {
  const proto = document.getElementById("formProxyProtocol")?.value || "socks5";
  const host = document.getElementById("formProxyHost")?.value?.trim() || "";
  const port = document.getElementById("formProxyPort")?.value?.trim() || "";
  const user = document.getElementById("formProxyUser")?.value?.trim() || "";
  const pass = document.getElementById("formProxyPass")?.value?.trim() || "";

  const customProxyEl = document.getElementById("formCustomProxy");
  if (!host || !port) {
    if (customProxyEl) customProxyEl.value = "";
    return "";
  }

  let formatted = "";
  if (user && pass) {
    formatted = `${proto}://${user}:${pass}@${host}:${port}`;
  } else {
    formatted = `${proto}://${host}:${port}`;
  }

  if (customProxyEl) customProxyEl.value = formatted;
  return formatted;
}

async function handleTestProxyConnection() {
  assembleProxyString();
  const proto = document.getElementById("formProxyProtocol")?.value || "socks5";
  const host = document.getElementById("formProxyHost")?.value?.trim() || "";
  const port = document.getElementById("formProxyPort")?.value?.trim() || "";
  const user = document.getElementById("formProxyUser")?.value?.trim() || "";
  const pass = document.getElementById("formProxyPass")?.value?.trim() || "";

  const btn = document.getElementById("btnTestProxyConn");
  const btnText = document.getElementById("btnTestProxyText");
  const badge = document.getElementById("proxyTestStatusBadge");
  const diagCard = document.getElementById("proxyDiagnosticCard");
  const diagContent = document.getElementById("proxyDiagContent");
  const applyTzBtn = document.getElementById("btnApplyProxyTimezone");

  if (!host || !port) {
    alert("Please enter Proxy Host / IP and Port first.");
    return;
  }

  btn.disabled = true;
  if (btnText) btnText.textContent = "Testing connection...";
  if (badge) {
    badge.style.display = "inline";
    badge.style.color = "var(--accent-cyan)";
    badge.textContent = "⏳ Testing...";
  }
  if (diagCard) diagCard.style.display = "none";
  if (applyTzBtn) applyTzBtn.style.display = "none";

  try {
    const res = await fetch("/api/proxy/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        protocol: proto,
        host: host,
        port: parseInt(port, 10),
        username: user,
        password: pass
      })
    });
    const data = await res.json();
    if (data.success) {
      if (badge) {
        badge.style.color = "#22c55e";
        badge.innerHTML = `✅ Connected (${data.latencyMs}ms)`;
      }
      if (diagCard && diagContent) {
        diagCard.style.display = "block";
        diagContent.innerHTML = `
          <span style="color: #22c55e; font-weight: 700;">🌍 IP: <span style="color:#fff; font-family: 'JetBrains Mono', monospace;">${data.ip || host}</span></span>
          <span style="color: var(--accent-cyan); font-weight: 600;">📍 Location: <span style="color:#fff;">${data.country || 'Unknown'} (${data.city || ''})</span></span>
          <span style="color: #eab308; font-weight: 600;">⚡ Ping: <span style="color:#fff;">${data.latencyMs}ms</span></span>
          ${data.timezone ? `<span style="color: #a855f7; font-weight: 600;">🕒 Timezone: <span style="color:#fff;">${data.timezone}</span></span>` : ''}
          ${data.isp ? `<span style="color: var(--text-muted);">🏢 ISP: ${data.isp}</span>` : ''}
        `;
      }
      if (applyTzBtn && data.timezone) {
        applyTzBtn.style.display = "inline-block";
        applyTzBtn.textContent = `🕒 Auto-Set Profile Timezone to ${data.timezone}`;
        applyTzBtn.onclick = () => {
          const tzSelect = document.getElementById("formTimezone");
          if (tzSelect) {
            let hasOpt = false;
            for (let i = 0; i < tzSelect.options.length; i++) {
              if (tzSelect.options[i].value === data.timezone) {
                tzSelect.selectedIndex = i;
                hasOpt = true;
                break;
              }
            }
            if (!hasOpt) {
              const opt = document.createElement("option");
              opt.value = data.timezone;
              opt.textContent = `📍 ${data.timezone} (Proxy Geo Location)`;
              tzSelect.appendChild(opt);
              tzSelect.value = data.timezone;
            }
            alert(`✅ Profile timezone successfully updated to ${data.timezone}!`);
          }
        };
      }
    } else {
      if (badge) {
        badge.style.color = "#ef4444";
        badge.innerHTML = `❌ Failed`;
      }
      if (diagCard && diagContent) {
        diagCard.style.display = "block";
        diagContent.innerHTML = `
          <span style="color: #ef4444; font-weight: 600;">⚠️ Error: ${data.error || 'Proxy connection failed'}</span>
        `;
      }
    }
  } catch (err) {
    if (badge) {
      badge.style.color = "#ef4444";
      badge.textContent = "❌ Test Error";
    }
    if (diagCard && diagContent) {
      diagCard.style.display = "block";
      diagContent.innerHTML = `<span style="color: #ef4444;">Error: ${err.message}</span>`;
    }
  } finally {
    btn.disabled = false;
    if (btnText) btnText.textContent = "Check / Test Proxy";
  }
}


// ─────────────────────────────────────────────────────────
//  Admin Mode Detection
//  Tries to reach the admin panel on port 5099.
//  If it's reachable (owner's machine), show the Edit Wallets button.
//  On regular user/release builds, port 5099 is never open → button stays hidden.
// ─────────────────────────────────────────────────────────
async function detectAdminMode() {
  try {
    // Use no-cors so CORS errors don't mask a live server
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500); // 1.5s timeout
    try {
      const resp = await fetch("http://127.0.0.1:5099/api/admin/ping", {
        method: "GET",
        mode: "no-cors",
        signal: ctrl.signal
      });
      clearTimeout(timer);
      // In no-cors mode, any response (even opaque) means server is UP
      showAdminEditButton(true);
    } catch (e) {
      clearTimeout(timer);
      if (e.name !== "AbortError") {
        // Network error = server not running = not admin
        showAdminEditButton(false);
      }
    }
  } catch (_) {
    // Silently ignore — default is hidden
  }
}

function showAdminEditButton(isAdmin) {
  const btn = document.getElementById("btnToggleDonateEdit");
  if (btn) {
    btn.style.display = isAdmin ? "flex" : "none";
  }
  const adminPanelBtn = document.getElementById("btnAdminPanel");
  if (adminPanelBtn) {
    adminPanelBtn.style.display = isAdmin ? "flex" : "none";
  }
}
