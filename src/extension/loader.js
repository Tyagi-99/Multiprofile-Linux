// loader.js - Synchronously injects evasion.js into the page's MAIN execution context
console.log("[Loader] Content script loaded on:", location.href);
try {
  const script = document.createElement('script');
  script.src = chrome.runtime.getURL('evasion.js');
  script.async = false;
  (document.head || document.documentElement).prepend(script);
  console.log("[Loader] evasion.js script tag injected successfully!");
} catch(e) {
  console.error("[Loader] Injection failed:", e);
}
