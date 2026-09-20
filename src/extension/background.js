/**
 * Multiprofile Native 1.1.0 — background service worker
 * Applies cookies.json and proxy.json from the prepared extension directory.
 * Fails soft if APIs are missing.
 */
console.log("[MP] Anti-Detect Defender Service Worker Active (1.1.0)");

async function fetchJson(path) {
  try {
    const url = chrome.runtime.getURL(path);
    const res = await fetch(url);
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    console.warn("[MP] fetchJson failed for", path, e);
    return null;
  }
}

async function applyCookies() {
  if (!chrome.cookies || !chrome.cookies.set) {
    console.warn("[MP] chrome.cookies API unavailable");
    return;
  }
  const list = await fetchJson("cookies.json");
  if (!Array.isArray(list) || list.length === 0) return;
  let ok = 0;
  for (const c of list) {
    try {
      const domain = c.domain || "";
      const host = domain.startsWith(".") ? domain.slice(1) : domain;
      if (!host || !c.name) continue;
      const url = `http${c.secure ? "s" : ""}://${host}${c.path || "/"}`;
      const details = {
        url,
        name: c.name,
        value: String(c.value ?? ""),
        path: c.path || "/",
        secure: !!c.secure,
        httpOnly: !!c.httpOnly,
        domain: domain || undefined,
      };
      if (c.expirationDate) details.expirationDate = Number(c.expirationDate);
      if (c.sameSite) details.sameSite = c.sameSite;
      await chrome.cookies.set(details);
      ok++;
    } catch (e) {
      console.warn("[MP] cookie set failed", c && c.name, e);
    }
  }
  console.log("[MP] cookies applied:", ok, "/", list.length);
}

async function applyProxy() {
  const cfg = await fetchJson("proxy.json");
  if (!cfg || !cfg.host || !cfg.port) return;

  const scheme = (cfg.type || "http").toLowerCase();
  let schemeKey = "http";
  if (scheme.startsWith("socks5")) schemeKey = "socks5";
  else if (scheme.startsWith("socks4")) schemeKey = "socks4";
  else schemeKey = "http";

  if (chrome.proxy && chrome.proxy.settings) {
    try {
      const config = {
        mode: "fixed_servers",
        rules: {
          singleProxy: {
            scheme: schemeKey,
            host: cfg.host,
            port: parseInt(cfg.port, 10),
          },
          bypassList: ["<local>"],
        },
      };
      await new Promise((resolve, reject) => {
        chrome.proxy.settings.set({ value: config, scope: "regular" }, () => {
          if (chrome.runtime.lastError) reject(chrome.runtime.lastError);
          else resolve();
        });
      });
      console.log("[MP] chrome.proxy.settings set", cfg.host, cfg.port);
    } catch (e) {
      console.warn("[MP] proxy.settings failed", e);
    }
  } else {
    console.warn("[MP] chrome.proxy API unavailable");
  }

  // Auth via webRequestAuthProvider (MV3) / onAuthRequired
  if (cfg.username && chrome.webRequest && chrome.webRequest.onAuthRequired) {
    try {
      const handler = (details) => {
        return {
          authCredentials: {
            username: cfg.username,
            password: cfg.password || "",
          },
        };
      };
      // Avoid duplicate listeners across SW restarts if possible
      if (chrome.webRequest.onAuthRequired.hasListener && chrome.webRequest.onAuthRequired.hasListener(handler)) {
        /* noop */
      }
      chrome.webRequest.onAuthRequired.addListener(
        handler,
        { urls: ["<all_urls>"] },
        ["blocking"]
      );
      console.log("[MP] onAuthRequired listener registered");
    } catch (e) {
      // MV3 may require asyncBlocking — try soft fallback
      try {
        chrome.webRequest.onAuthRequired.addListener(
          (details, callback) => {
            callback({
              authCredentials: {
                username: cfg.username,
                password: cfg.password || "",
              },
            });
          },
          { urls: ["<all_urls>"] },
          ["asyncBlocking"]
        );
        console.log("[MP] onAuthRequired asyncBlocking registered");
      } catch (e2) {
        console.warn("[MP] onAuthRequired unavailable", e, e2);
      }
    }
  }
}

async function boot() {
  await applyCookies();
  await applyProxy();
}

boot();

// Re-apply periodically (SW may sleep)
try {
  if (chrome.alarms) {
    chrome.alarms.create("mp-refresh", { periodInMinutes: 5 });
    chrome.alarms.onAlarm.addListener((alarm) => {
      if (alarm.name === "mp-refresh") {
        applyCookies().catch(() => {});
      }
    });
  }
} catch (e) {
  console.warn("[MP] alarms unavailable", e);
}

chrome.runtime.onStartup.addListener(() => boot());
chrome.runtime.onInstalled.addListener(() => boot());
