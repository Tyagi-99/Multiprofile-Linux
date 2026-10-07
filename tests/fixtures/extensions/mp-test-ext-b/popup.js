chrome.storage.local.get(null, (all) => {
  document.getElementById("v").textContent =
    "B v" + chrome.runtime.getManifest().version + " · " + JSON.stringify(all);
});
