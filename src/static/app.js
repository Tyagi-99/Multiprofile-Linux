
/* Multiprofile 1.2.0 SPA — jobs/bulk/clients/token (functional sync; full spa.json body pending size-limited MCP embed) */
function getApiToken(){try{return localStorage.getItem("mp_api_token")||"";}catch(e){return"";}}
function setApiToken(tok){try{if(tok)localStorage.setItem("mp_api_token",tok);else localStorage.removeItem("mp_api_token");}catch(e){}}
async function apiFetch(url,opts={}){const headers=Object.assign({},opts.headers||{});const tok=getApiToken();if(tok)headers["Authorization"]="Bearer "+tok;if(opts.body&&!headers["Content-Type"]&&!(opts.body instanceof FormData))headers["Content-Type"]="application/json";return fetch(url,Object.assign({},opts,{headers}));}
function showJobToast(msg){let el=document.getElementById("jobToast");if(!el){el=document.createElement("div");el.id="jobToast";el.style.cssText="position:fixed;bottom:20px;right:20px;z-index:9999;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:10px;padding:10px 14px;font-size:13px;max-width:360px;";document.body.appendChild(el);}el.textContent=msg;el.style.display="block";clearTimeout(el._t);el._t=setTimeout(()=>{el.style.display="none";},4000);}
let allClients=[],allProfiles=[],selectedProfileIds=new Set(),activeFilter="all";
async function refreshHealthBadge(){try{const res=await apiFetch("/api/health");const data=await res.json();const el=document.getElementById("healthVersionText");if(el&&data.version)el.textContent=`v${data.version}`+(data.chrome_ok?" · Chrome OK":" · No Chrome");}catch(e){}}
async function loadClients(){try{const res=await apiFetch("/api/clients");allClients=await res.json();if(!Array.isArray(allClients))allClients=[];const sel=document.getElementById("clientFilter");if(sel){const cur=sel.value;sel.innerHTML='<option value="">All clients</option>'+allClients.map(c=>`<option value="${c.id}">${c.name}</option>`).join("");sel.value=cur||"";}}catch(e){console.error(e);}}
async function pollJob(jobId){for(let i=0;i<60;i++){await new Promise(r=>setTimeout(r,500));try{const res=await apiFetch(`/api/jobs/${jobId}`);const data=await res.json();const job=data.job||data;showJobToast(`Job ${job.type}: ${job.status} (${job.progress||0}/${job.total||"?"})`);if(["completed","failed","cancelled"].includes(job.status)){await loadProfiles();return job;}}catch(e){break;}}}
async function bulkJob(type){const ids=Array.from(selectedProfileIds);if(!ids.length){alert("Select one or more profiles first.");return;}const res=await apiFetch("/api/jobs",{method:"POST",body:JSON.stringify({type,profileIds:ids})});const data=await res.json();if(!res.ok){alert(data.error||"Job failed");return;}const job=data.job||data;showJobToast(`Queued ${type} for ${ids.length} profile(s)`);pollJob(job.id);}
async function backupProfile(id){showJobToast("Creating backup…");const res=await apiFetch(`/api/profiles/${id}/backup`,{method:"POST",body:"{}"});const data=await res.json();if(!res.ok||data.error){alert(data.error||"Backup failed");return;}showJobToast("Backup saved: "+(data.backupId||data.backupPath||"ok"));}
async function loadProfiles(){try{const clientId=document.getElementById("clientFilter")?.value||"";const url=clientId?`/api/profiles?clientId=${encodeURIComponent(clientId)}&limit=500`:"/api/profiles";const res=await apiFetch(url);const data=await res.json();allProfiles=Array.isArray(data)?data:(data.items||[]);renderProfiles();updateCounts();}catch(e){console.error(e);}}
function updateCounts(){const set=(id,n)=>{const el=document.getElementById(id);if(el)el.textContent=n;};set("countAll",allProfiles.length);set("countRunning",allProfiles.filter(p=>p.isRunning).length);set("countMobile",allProfiles.filter(p=>p.isMobile).length);set("countGithub",allProfiles.filter(p=>p.proxyType==="github").length);}
function renderProfiles(){const grid=document.getElementById("profilesGrid");const empty=document.getElementById("emptyState");if(!grid)return;let list=allProfiles;if(activeFilter==="running")list=list.filter(p=>p.isRunning);if(activeFilter==="mobile")list=list.filter(p=>p.isMobile);if(activeFilter==="github")list=list.filter(p=>p.proxyType==="github");const q=(document.getElementById("searchInput")?.value||"").toLowerCase().trim();if(q)list=list.filter(p=>(p.name||"").toLowerCase().includes(q)||(p.notes||"").toLowerCase().includes(q));if(!list.length){grid.innerHTML="";if(empty)empty.style.display="block";return;}if(empty)empty.style.display="none";grid.innerHTML=list.map(p=>{const checked=selectedProfileIds.has(p.id)?"checked":"";return `<div class="profile-card" data-id="${p.id}" style="padding:10px;border:1px solid var(--border-color);border-radius:8px;margin:6px 0;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
<input type="checkbox" ${checked} onchange="toggleSelectProfile('${p.id}', this.checked)">
<strong>${p.name||"Untitled"}</strong>
<span style="opacity:.7;font-size:12px;">${p.isRunning?"Running":"Idle"}</span>
<button onclick="launchProfile('${p.id}')">Launch</button>
<button onclick="stopProfile('${p.id}')">Stop</button>
<button onclick="backupProfile('${p.id}')">Backup</button>
</div>`;}).join("");}
function toggleSelectProfile(id,checked){if(checked)selectedProfileIds.add(id);else selectedProfileIds.delete(id);}
async function launchProfile(id){await apiFetch(`/api/profiles/${id}/launch`,{method:"POST",body:"{}"});await loadProfiles();}
async function stopProfile(id){await apiFetch(`/api/profiles/${id}/stop`,{method:"POST"});await loadProfiles();}
window.toggleSelectProfile=toggleSelectProfile;window.launchProfile=launchProfile;window.stopProfile=stopProfile;window.backupProfile=backupProfile;
document.addEventListener("DOMContentLoaded",async()=>{
const tokInput=document.getElementById("apiTokenInput");
if(tokInput){tokInput.value=getApiToken();tokInput.addEventListener("change",()=>setApiToken(tokInput.value.trim()));}
document.getElementById("btnBulkLaunch")?.addEventListener("click",()=>bulkJob("bulk_launch"));
document.getElementById("btnBulkStop")?.addEventListener("click",()=>bulkJob("bulk_stop"));
document.getElementById("clientFilter")?.addEventListener("change",loadProfiles);
document.getElementById("searchInput")?.addEventListener("input",renderProfiles);
document.querySelectorAll(".pill").forEach(btn=>btn.addEventListener("click",()=>{document.querySelectorAll(".pill").forEach(b=>b.classList.remove("active"));btn.classList.add("active");activeFilter=btn.dataset.filter||"all";renderProfiles();}));
await loadClients();await refreshHealthBadge();await loadProfiles();
setInterval(loadProfiles,3000);
});
