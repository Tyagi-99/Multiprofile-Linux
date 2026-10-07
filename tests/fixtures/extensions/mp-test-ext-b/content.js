(() => {
  const el = document.createElement("div");
  el.id = "mp-test-ext-b-marker";
  el.dataset.version = chrome.runtime.getManifest().version;
  el.dataset.extid = chrome.runtime.id;
  el.style.display = "none";
  document.documentElement.appendChild(el);
})();
