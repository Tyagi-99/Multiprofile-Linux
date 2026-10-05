# Product, release, and operational parity

## What is already built, and what is still missing for a trustworthy release?

### Takeaway
This is further along than an experimental prototype: the local API, profile storage, backups, proxy checks, and a CI-tested signed update mechanism exist. The critical release gap is that the update channel has never been published with a real signing key or host, and the browser engine is still whichever Chrome/Chromium the host supplies.

### Cited Findings
- The project installs from source with Python 3.11+ and a system Chrome/Chromium; the `.deb` is buildable but the distributed artifact is unsigned and has no live update channel. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/INSTALLATION.md)
- Its release tooling builds a signed apt repository, verifies hashes, tests a delta update and rollback in CI, and can check daily after a channel is configured; it explicitly says no production key or repository is published. It refuses automatic installation while profiles are running. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/UPDATES.md)
- The project already has scoped API tokens, CDP endpoints, bulk operations, a durable job queue, per-profile backup/restore, and account export. The API also says loopback needs no token and non-loopback HTTP has no TLS, requiring a secure tunnel. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/API.md)
- Its feature matrix reports 150 stored / 20 running as the deliberate target, no official Python/Node SDKs, no exposed headless mode, and no cloud sync or team accounts. Competitor cells in that matrix are based on public claims, not hands-on verification. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/03_FEATURE_MATRIX.md)
- Chrome Stable is updated weekly for minor releases and every four weeks for major releases; Chrome for Testing and arbitrary Chromium builds do not auto-update. — [Chrome release channels](https://www.chromium.org/chrome-release-channels/); [Chromium download guidance](https://new.chromium.org/getting-involved/download-chromium/)

### Inferences
- First operational priority: publish the already built apt channel with an offline-protected release key, a separately advertised key fingerprint, update notifications, and a tested rollback. Do not describe the existing CI machinery as a live updater until a production install actually receives an update.
- Define an engine patch and security-response policy independent of the Python app release. If remaining on system Chromium, surface actual binary version, freshness, and vendor in the UI; test against supported distro/browser combinations. If pursuing a fork, fund a standing upstream rebase and security-release operation.
- Preserve the deliberate single-user, 150/20 scope for a personal Linux product. Multi-user cloud infrastructure is a separate commercial product decision.

### Gaps
- No published production package/channel, production signing key custody record, measured update success rate, or busy-profile upgrade exercise was found in project docs. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/UPDATES.md)

## Which parity features should come next, and which can wait?

### Takeaway
Prioritize dependable identity and workflow fundamentals: engine maintenance, route reliability, recovery, and simple automation integrations. Marketplace proxies, no-code automation, cross-device sync, and team billing are later commercial scope.

### Cited Findings
- AdsPower publicly documents multiple browser versions, bulk profile editing, proxy checks, saved proxy assignment, automatic client updates, and local profile snapshots. These are advertised capabilities, not independently verified quality. — [Profile creation](https://help.adspower.com/docs/creating_browser_profiles); [Bulk edit](https://help.adspower.com/docs/editing); [Settings](https://help.adspower.com/docs/personal_settings)
- Multilogin documents profile-specific browser core updates with automatic updates enabled by default, custom and built-in proxies with rotation options, and API/CLI/Selenium/Puppeteer/Playwright automation. — [Core updates](https://multilogin.com/help/en_US/script-runner-predefined-scripts/how-to-control-core-updates-via-api); [Proxy settings](https://multilogin.com/help/profile-settings-proxy-section); [Automation FAQ](https://multilogin.com/help/en_US/automation-faq)
- GoLogin documents cloud profile sync and warns that concurrent use on different devices or closing during poor connectivity can lose or mismatch session state; it also documents proxy checks and bulk proxy assignment. — [Profile sync](https://support.gologin.com/en/articles/14404855-profile-synchronization); [Proxy management](https://support.gologin.com/en/articles/14810002-adding-and-managing-proxies)
- Multiprofile's proxy path already has a prelaunch health gate, default block on a failed proxy, remote DNS controls, and credential redaction. Its matrix marks live proxy rotation/sticky session control as partial and IPv6 behavior untested. — [Network isolation](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/NETWORK_ISOLATION.md); [Feature matrix](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/03_FEATURE_MATRIX.md)
- The current roadmap names SDKs, checker measurements, and publishing the update channel as near-term work, while cloud sync and billing are separate later branches. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/06_ROADMAP.md)

### Inferences
- **Must-have for a serious single-user release:** (1) real signed distribution and a published security/update policy; (2) explicit browser-engine version support; (3) a recovery drill for crashed sessions, corrupt profiles, and restore from encrypted backup; (4) actual proxy egress, DNS, IPv6, WebRTC, and mid-session failure tests on each supported route; (5) official small Python and Node SDKs with start/stop/CDP examples; (6) support bundle with secret redaction and user-facing fault codes.
- **Later commercial extras:** bundled proxy marketplace, cloud sync, team sharing/locking, payment and entitlement, no-code RPA/synchronizer, and very large fleet UI. Add these only when the target customer and operating budget justify them.
- For profiles tied to valuable accounts, stable proxy assignment and prevention of silent route fallback matter more than automatic rotation. Rotation should be explicit per workflow, with logs and a clear session-stability policy.

### Gaps
- No production-scale reliability measurements, restore drill on a separate machine, real-proxy mid-session failure test, or customer support evidence was located in the project documents. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/03_FEATURE_MATRIX.md)

## What evidence is needed before claiming AdsPower/GoLogin/Multilogin-level performance?

### Takeaway
Feature lists and one checker score are insufficient. The release needs repeatable, versioned measurements against clean browser controls, representative sites and proxies, plus reliability results over time. Public claims should match the measured scope.

### Cited Findings
- The project itself says its internal consistency endpoint checks its own declarations and is **not** proof of remote-site fingerprint resistance. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/PROFILE_CONSISTENCY.md)
- Its audit records no implementation for TLS/JA3/JA4 or HTTP/2 alignment, no real-proxy measurement in the documented baseline, and no long-session drift testing. It explicitly rejects an “undetectable” claim. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/04_FINGERPRINT_TEST_RESULTS.md)
- The project roadmap says patched-engine work is a major continuing commitment; it also calls for external checker runs before deciding whether to pursue a patched engine, narrower work, or honest positioning around current limits. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/06_ROADMAP.md)
- SLSA defines provenance as verifiable information connecting a built artifact to the source and process that produced it, and recommends distributing attestations bound to artifacts. — [Provenance](https://slsa.dev/spec/v1.2/provenance); [Distribution](https://slsa.dev/spec/v1.2/distributing-provenance)

### Inferences
- Create a release qualification matrix by engine version, Linux distro, window system, device preset, proxy type, and direct connection. Run CreepJS, Pixelscan, BrowserLeaks, and Iphey beside clean Chromium/Chrome and Firefox controls, but record the actual signal differences rather than treating any proprietary site score as ground truth.
- Capture page, iframe, dedicated/shared/service worker, HTTP headers, DNS/IPv6/WebRTC, TLS, HTTP/2, and 24-hour session behavior with reproducible scripts and timestamped artifacts. Record account-site outcomes only through authorized test accounts and site-compliant activity.
- Before a “commercial-grade” claim, measure: update adoption and rollback, backup restore success, crash recovery, cross-version profile migration, proxy drop behavior, and support response/incident process. Publish pass criteria and known failures with every release.
- Build provenance and a signed package are complementary: a signature establishes an authorized artifact; provenance explains what source and build produced it.

### Gaps
- No independent, representative detection-rate comparison with licensed AdsPower/GoLogin/Multilogin installations was found. Their public docs show feature availability, not equivalent performance under the same test conditions. — [Source](https://github.com/Tyagi-99/Multiprofile-Linux/blob/main/docs/03_FEATURE_MATRIX.md)
