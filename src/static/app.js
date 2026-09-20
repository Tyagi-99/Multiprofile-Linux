/* Multiprofile 1.2.0 SPA app.js — jobs, bulk launch, clients, API token */
(async function () {
  const res = await fetch("app.payload.b64?v=120");
  if (!res.ok) throw new Error("payload " + res.status);
  const b64 = (await res.text()).trim();
  const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const ds = new DecompressionStream("gzip");
  const ab = await new Response(new Blob([bin]).stream().pipeThrough(ds)).arrayBuffer();
  const code = new TextDecoder().decode(ab);
  (0, eval)(code);
})().catch(e => {
  console.error(e);
  document.body.insertAdjacentHTML("beforeend",
    "<pre style=\"color:#f87171;padding:12px\">app.js load error: " + e + "</pre>");
});
