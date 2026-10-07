// The evidence a test reads: a marker in the page's DOM that only an actually
// RUNNING extension can produce. A saved file or a database row cannot.
(() => {
  const el = document.createElement("div");
  el.id = "mp-test-ext-marker";
  el.dataset.version = chrome.runtime.getManifest().version;
  el.dataset.extid = chrome.runtime.id;
  el.style.display = "none";
  document.documentElement.appendChild(el);
  // Also expose it where a page-world probe can see it without extension APIs.
  const s = document.createElement("script");
  s.textContent =
    'window.__MP_TEST_EXT__ = ' +
    JSON.stringify({ version: chrome.runtime.getManifest().version, id: chrome.runtime.id }) +
    ';';
  document.documentElement.appendChild(s);
  s.remove();
})();
