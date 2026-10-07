# Technical gap to credible browser parity

## What is actually failing today?

### Takeaway
The current build is a useful multi-profile Chromium manager, but its live identity still disagrees across browser surfaces. The first target is a repeatable cross-surface test, not a single checker score.

### Cited Findings
- A fresh no-proxy Multiprofile profile on this host claimed Windows/Win32 in the page, while the original CreepJS `ServiceWorkerGlobalScope` result contained `userAgentData: Chromium 151, Linux`. Its JavaScript screen was `1366 × 768`, but CreepJS's CSS media-query screen result was `2560 × 1600`. The stock Chromium control reported Linux and `2560 × 1600` in both relevant screen fields. — [Multiprofile capture](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/creepjs_original.json); [control capture](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/stock_chromium_creepjs_original.json)
- The project's internal audit reports page/worker agreement across five worker types and screen coherence, based on its own CDP suite. The public-site result above conflicts with that broad conclusion, likely because the site's service-worker path and CSS media query exercise surfaces the internal probe missed. The exact cause requires reproduction and tracing. — [internal audit](/home/davish/Projects/Multiprofile-Linux/docs/04_FINGERPRINT_TEST_RESULTS.md#L237); [live capture](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/creepjs_original.json)
- The original CreepJS capture also says `0% headless` and `20% stealth`; the separate creepjs.org checker scored Multiprofile `97/100` versus `90/100` for stock Chromium, yet flagged only three detected fonts in Multiprofile. These scores are site-specific and do not negate the cross-surface inconsistencies. — [original CreepJS](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/creepjs_original.json); [Multiprofile checker](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/creepjs_checker.json); [stock checker](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/stock_chromium_creepjs_checker.json)
- Pixelscan labeled **both** Multiprofile and clean stock Chromium “inconsistent” and “masking detected,” with “no automated behavior detected.” It is unsuitable as a binary pass/fail gate on this machine without a stronger control cohort. — [Multiprofile Pixelscan](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/pixelscan_checker.json); [stock Pixelscan](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/stock_chromium_pixelscan_checker.json)
- The repository already documents residual Windows-font mismatches: missing expected faces and enumerable Linux metric backers; the live checker corroborates a sparse list. The fontconfig implementation explicitly warns that aliasing names does not alter a font file's internal name table. — [audit](/home/davish/Projects/Multiprofile-Linux/docs/04_FINGERPRINT_TEST_RESULTS.md#L174); [fontconfig implementation](/home/davish/Projects/Multiprofile-Linux/src/multiprofile/browser/fontconfig.py#L1); [live checker](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/creepjs_checker.json)

### Inferences
- Prioritize the real service-worker path and CSS/media-query screen geometry ahead of adding more spoofed fields. Reconcile page, dedicated/shared/service workers, iframe, CSS, and the actual window on both initial load and new tabs.
- Prefer a coherent native Linux identity as an interim selectable mode if Windows emulation cannot pass the cross-surface gate; this is a product option, not evidence that Linux profiles are untrackable.

### Gaps
- These captures are scraped checker output from one host and one profile; they do not establish the exact implementation defect, prevalence across presets, or outcomes on real account sites.
- No Windows/macOS reference-device cohort has been measured to define realistic distributions for fonts, GPU, screen, voices, and protocol behavior.

## Which architecture can close the gap?

### Takeaway
Actual parity in identity control requires a maintained browser engine or an equally deep, fully verified alternative. A configured upstream proxy changes egress IP but does not repair JavaScript, CSS, font, or browser-generated transport signatures.

### Cited Findings
- The code launches stock Chromium, attaches DevTools, uses `Network.setUserAgentOverride`, and injects identity scripts into renderer targets and workers. `Runtime.enable` is used in the worker and page paths; the internal audit calls CDP attachment an unresolved detection surface. — [feature matrix](/home/davish/Projects/Multiprofile-Linux/docs/03_FEATURE_MATRIX.md#L25); [CDP injection](/home/davish/Projects/Multiprofile-Linux/src/multiprofile/browser/cdp_inject.py#L710); [audit](/home/davish/Projects/Multiprofile-Linux/docs/04_FINGERPRINT_TEST_RESULTS.md#L250)
- The repo says TLS/JA3/JA4 and HTTP/2 alignment are absent. BrowserLeaks did record a JA3 and JA4 for the current claimed Windows profile, but the supplied capture has no genuine Windows Chrome control, so it does **not** prove a specific mismatch by itself. — [feature matrix](/home/davish/Projects/Multiprofile-Linux/docs/03_FEATURE_MATRIX.md#L76); [BrowserLeaks capture](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/browserleaks_tls.json)
- The roadmap's proposal that traffic “already passes through a local proxy the product controls” conflicts with current routing code: configured profiles pass an upstream `--proxy-server=...` directly to Chromium, and direct profiles have no proxy flag. The network model explicitly defines `DIRECT` as the host network. Consequently, a local transport-rewriting component is a new subsystem, not a small rewrite of an existing one. — [roadmap](/home/davish/Projects/Multiprofile-Linux/docs/06_ROADMAP.md#L145); [network flags](/home/davish/Projects/Multiprofile-Linux/src/multiprofile/network.py#L529); [network specification](/home/davish/Projects/Multiprofile-Linux/docs/NETWORK_ISOLATION.md#L10)
- The network specification says a `--host-resolver-rules` flag enforces proxied DNS, whereas current code explicitly removed that flag after measuring it broke proxy browsing. The specification also calls proxy behavior fail-closed, while code permits explicit `WARN` and `ALLOW` fallback to host IP. These documents should be reconciled before making safety claims. — [specification](/home/davish/Projects/Multiprofile-Linux/docs/NETWORK_ISOLATION.md#L50); [current flags](/home/davish/Projects/Multiprofile-Linux/src/multiprofile/network.py#L539); [fallback code](/home/davish/Projects/Multiprofile-Linux/src/multiprofile/network.py#L508)
- The feature matrix expressly says competitor columns are public-documentation/general-knowledge assertions, **not installed or measured competitor builds**. Its competitor checkmarks and the roadmap's “6–12 months” estimate are planning assumptions, not parity evidence. — [feature matrix](/home/davish/Projects/Multiprofile-Linux/docs/03_FEATURE_MATRIX.md#L1); [roadmap estimate](/home/davish/Projects/Multiprofile-Linux/docs/06_ROADMAP.md#L38)

### Inferences
- A normal HTTP CONNECT or SOCKS tunnel still passes Chromium's end-to-end TLS handshake and HTTP/2 frames to the destination. Changing those signatures requires an explicit client-side transport implementation or interception/re-originating design; the latter adds certificate, security, compatibility, and maintenance costs. A proxy subscription alone cannot do it.
- For a real product target, choose and fund either (1) a pinned, maintained Chromium-based build with identity controls below page JavaScript, including network and font handling; or (2) a narrower stock-Chromium manager positioned around profile/proxy workflows. Avoid treating score improvements as proof of full competitor parity.

### Gaps
- No measured official competitor builds or controlled server-side TLS/HTTP2 reference traces are available here.
- No test demonstrates the proposed local transport-rewriting design for HTTPS, HTTP/2, HTTP/3, WebSockets, service workers, authentication, or certificate handling.

## What measurable gates should precede important account use?

### Takeaway
Define pass/fail by consistency, isolation, stability, and regression against real reference browsers. A clean checker score alone is insufficient.

### Cited Findings
- The internal audit already has a CDP vector harness and restart-stability check, but it says real-proxy behavior, long sessions, and mobile real-world conditions were not measured. Its older statement that third-party sites had never been visited is now stale relative to the supplied October 5 live captures. — [internal audit](/home/davish/Projects/Multiprofile-Linux/docs/04_FINGERPRINT_TEST_RESULTS.md#L1); [unmeasured list](/home/davish/Projects/Multiprofile-Linux/docs/04_FINGERPRINT_TEST_RESULTS.md#L296); [live capture](/home/davish/.codex/visualizations/2026/10/05/01a10bf5-aaba-79c3-a361-6162165ade3e/creepjs_original.json)
- The current proxy health check is pre-launch, and the code's default fallback is `BLOCK`; explicit `WARN`/`ALLOW` paths can launch with the host IP. Thus proxy preflight cannot be taken as a complete runtime leak test. — [network code](/home/davish/Projects/Multiprofile-Linux/src/multiprofile/network.py#L124); [health/fallback code](/home/davish/Projects/Multiprofile-Linux/src/multiprofile/network.py#L508)

### Inferences
- **Gate 1, surface agreement:** on each supported preset, collect a structured manifest from HTTP headers, page, fresh and persistent workers, service workers, iframes, CSS media queries, DOM, canvas, WebGL/WebGPU, fonts, timezone, locale, screen and window geometry. Fail on contradictory OS, browser version, screen, language, GPU, or worker identity. Include both a known reference device and stock Chromium control.
- **Gate 2, network isolation:** test direct, HTTP(S), SOCKS5, authenticated and dead proxies with a server-side echo and host packet capture. Verify exit IP, DNS, IPv6 and WebRTC, then sever the proxy after launch and assert no host-IP fallback under the supported safety mode. Repeat on new tabs, workers, WebSockets, downloads, and after restart.
- **Gate 3, transport:** compare server-observed TLS ClientHello/JA3/JA4, ALPN, HTTP/2 settings/order, and HTTP/3 when enabled to matched reference browsers at the *same engine version*; repeat through each proxy type. Fix or narrow the marketed OS claims if the traces diverge.
- **Gate 4, stability and usability:** repeat tests across several seeds, restarts, long sessions and engine updates; verify cookies and profile data remain isolated, common sites work, and the update pipeline rolls out a security release without changing a saved profile's identity unexpectedly.

### Gaps
- Pass rates, reference distributions, acceptable drift thresholds, and target-site outcomes need actual controlled measurements; none can be inferred from the supplied checker scores.
