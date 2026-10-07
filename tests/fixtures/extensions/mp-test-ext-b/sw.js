// Distinct marker and storage key from extension A, so "both ran" cannot be
// satisfied by one of them running twice.
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ who: "B", version: "1.0.0" });
});
