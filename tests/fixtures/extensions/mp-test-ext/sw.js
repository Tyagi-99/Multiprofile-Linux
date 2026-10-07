// Records that this extension ran in THIS browser profile. Per-profile by
// construction: chrome.storage.local lives inside the profile's user-data
// directory, so two profiles running the same extension files never share it.
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get({ installs: 0 }, (got) => {
    chrome.storage.local.set({ installs: (got.installs || 0) + 1, version: "1.0.0" });
  });
});

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === "mp-get") {
    chrome.storage.local.get(null, (all) => reply({ ok: true, store: all }));
    return true;
  }
  if (msg && msg.type === "mp-set") {
    chrome.storage.local.set({ [msg.key]: msg.value }, () => reply({ ok: true }));
    return true;
  }
  return false;
});
