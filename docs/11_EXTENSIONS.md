# Extension manager

Import an extension once, assign it to many profiles, and know which profiles
are actually running it.

Everything below is measured against the browser this machine launches rather
than taken from documentation, because the central capability is
binary-specific and changed recently.

---

## 1. The browser, and why it decides the design

| | |
|---|---|
| Runtime on this host | **Chromium 151.0.7922.173** (Arch Linux build) |
| Selected by | `engine.BROWSER_BINARIES` — `google-chrome-stable`, `google-chrome`, `chromium`, `chromium-browser`, **in that order** |
| Unpacked loading | **supported** (`--load-extension`) |
| CRX sideload by command line | **not supported by anything** |
| Manifest versions | MV2 and MV3 |

`--load-extension` was **removed from branded Google Chrome in 137**. It still
works in Chromium and Chrome for Testing. Two consequences the product has to
live with rather than paper over:

- The candidate order above **prefers Google Chrome**. An operator who installs
  Chrome on this machine would silently lose automated per-profile loading.
  `extensions/capability.py` detects the brand and version, reports it, and
  names what would work. **The product never substitutes a different browser.**
- Brave, Edge, Opera and Vivaldi are Chromium-based and generally keep the
  flag, but this product has not measured any of them, so it does not claim
  them. They report "not verified" and refuse automated loading.

### What was measured

Chromium 151, an unpacked MV3 fixture, with the evidence being the marker the
extension's own **content script** writes into a page — a file on disk and a
flag on a command line prove nothing:

| configuration | extension ran |
|---|---|
| control: no extension flags | no |
| `--load-extension=<dir>` | **yes** (ID assigned, v1.0.0 read back) |
| `--load-extension` + `--disable-extensions` | **no** |
| `--load-extension` + `--disable-extensions-except` | **yes** |

**Row three is the one that shaped the launcher.** It appended
`--disable-extensions` to every profile as its anti-detect exclusivity
setting, and that flag wins. A profile with an extension assigned is therefore
launched *without* it.

That is a real change to the profile's posture, not a detail. An extension is
enumerable and can inject into pages, and exclusivity existed to prevent
exactly that. The launch note says so, per profile:

```
Extensions ON for this profile: 2 assigned (--load-extension).
Anti-detect exclusivity is OFF while they load.
```

A profile with no assignment still gets `--disable-extensions`, so assigning
to one profile does not quietly change another's. There is a test for that.

---

## 2. Status vocabulary

The obvious bug in a feature like this is a UI that reports success the moment
a row is written. So package state and per-profile state are **different
columns in different tables**, and only one of them may say installed.

**Package** (`extensions.install_state`) — describes bytes in managed storage:

| state | meaning |
|---|---|
| `imported` | validated and extracted from a ZIP or copied from a folder |
| `downloaded` | fetched from a supported source (see §6) |

**Per profile** (`extension_assignments.state`) — the only state that may say
installed:

| state | meaning |
|---|---|
| `assigned` | chosen for this profile; applies on its next launch |
| `pending_restart` | the profile is running; the flag cannot be retrofitted to a live browser |
| `installed_verified` | the browser reported the extension ID we predicted, and it is recorded |
| `failed` | with a per-profile reason, retryable on its own |

`applied_at` is set when a launch *carried* the extension on its command line.
That is not verification and does not move the state.

---

## 3. Identity, and why the managed path never moves

Chromium derives an **unpacked** extension's ID from the absolute path it was
loaded from: SHA-256 of the path, first 16 bytes, each nibble mapped to
`a`..`p`. A manifest `key` pins it instead.

That is why payloads live at a fixed path:

```
extensions/<ext_uuid>/payload/          <- the path the browser always loads
extensions/<ext_uuid>/versions/<v>/     <- retained for rollback
```

An update replaces the **contents** of `payload/`. A versioned directory
beside it would give the new payload a new ID — and an extension's ID is where
its storage, its permission grants and its site authorisations live, so a new
ID is a new extension with an empty profile, not an update.

Predicting the ID is only useful because it can then be **checked**. The
manager computes the expected value, the launch happens, the browser is asked
what it loaded, and the observed ID is recorded. Verified on this host: the
fixture at `tests/fixtures/extensions/mp-test-ext` predicts
`aelakinaflbnalldpkjgcjikjhljdonc`, and Chromium reported exactly that from
`chrome.runtime.id` inside the extension's own content script.

A mismatch is reported, not smoothed over: it means the browser did not load
what we think it did.

---

## 4. Isolation

Extension **code** is shared: one payload directory, however many profiles
reference it. Extension **state** is not. `chrome.storage.local`, settings,
site permissions and anything the extension writes live inside each browser
profile's own user-data directory.

Measured: two profiles running the same extension files, same extension ID,
each wrote a different value and read its own back after a restart.

**What this does not cover.** If an extension signs into its own account or
syncs through its own cloud service, that data lives on someone else's server
and is keyed by that account, not by the browser profile. Using one login
across several profiles links them, and nothing this product does changes
that. It is not claimed.

---

## 5. Developer Mode

Developer Mode is a **browser** setting and this product does not reimplement
it or add a toggle that changes nothing. There are two real things, and both
are wired to something:

1. **The browser's own Developer Mode and *Load unpacked*.** The Extensions
   page opens `chrome://extensions` in a chosen running profile, where they
   live. That is the browser's UI doing the browser's job.
2. **Application-managed loading.** Whether a profile is launched with
   `--load-extension` is decided by its assignments, and the per-assignment
   *enabled* switch genuinely changes it: disabling removes the payload from
   the next launch's command line without deleting the assignment. There is a
   test asserting the extension stops running and the assignment survives.

---

## 6. CRX, and the Web Store

**CRX is refused**, with the reason and an alternative.

A `.crx` is a signed container: a header with a public key and a signature,
then a ZIP. Extracting it and loading the result as unpacked discards the
signature check entirely and **changes the extension's ID** — from the one the
signature attests to one derived from the install path. Presenting that as
"installed from CRX" would be a false claim about provenance.

Installing a CRX the way Chrome does requires either the Web Store flow or
enterprise policy (`ExtensionInstallForcelist` with a hosted update manifest).
Neither is a file copied into a directory.

**Third-party "CRX downloader" sites are not used.** They are unverified
mirrors of other people's code, and routing installs through one is a
supply-chain substitution this product should not make. The Web Store route
accepts `chromewebstore.google.com` URLs only, and refuses anything else by
construction rather than by policy text.

**Download from a supported source** is not implemented, and the honest reason
is that there is no general-purpose one: the Web Store's terms do not permit
programmatic download, and anything else is an unverified mirror. The
`download` package state exists for a source that could be verified — a
vendor's own signed distribution with a published key — and nothing currently
claims to be one.

---

## 7. What goes wrong, and what you see

| situation | behaviour |
|---|---|
| Archive has two extension roots | refused — picking one would install something you did not choose |
| Path traversal, absolute paths, symlinks | refused before anything is written |
| Decompression bomb | refused on declared size **and** on bytes actually written, because a declared size is attacker-controlled |
| `manifest.json` names a file the package lacks | refused — the browser would load it and fail later, less clearly |
| MV1 | refused as unsupported |
| Payload missing from disk at launch | that extension is skipped and marked failed, rather than handing a bad path to `--load-extension` and taking the others down with it |
| Profile is running | `pending_restart`, with a restart offered — **no session is closed for you**, and the restart route refuses to pick the profiles itself |
| A restart stops but fails to relaunch | reported as failed for that profile, saying it is now stopped |
| Browser cannot load unpacked | the assignment fails with the reason and the remediation |
| Some profiles fail in a bulk operation | HTTP **207**, per-profile ledger, retry limited to the failures |
| Requested profile no longer exists | reported as missing, and the response is **not** `ok` |
| Delete while profiles still reference it | refused unless forced; shared payload files are never removed while another extension row points at them |

---

## 8. Limitations and untested areas

- **The Chrome Web Store install flow is not end-to-end tested.** The route is
  tested for input validation and for requiring a running profile. Completing
  a real install needs a Google account, a visible browser and a human, so it
  has not been exercised here. **It is not claimed to work end to end.**
- **Only Chromium is verified.** Google Chrome below 137, Brave, Edge, Opera
  and Vivaldi are untested. Chrome 137+ is tested only in the sense that the
  capability gate refuses it.
- **The pending-restart → restart → verified cycle is not tested end to end
  against a live browser.** The state, its messaging and the restart route's
  refusals are tested; `POST /api/extensions/<id>/restart-profiles` acts only
  on profile IDs it is handed, is confirmed in the console before it runs, and
  reports each profile separately — but a full live cycle has not been
  exercised.

- **An update is fleet-wide for that extension, not per profile.** Profiles
  share one payload directory, which is what keeps the extension ID and
  therefore the per-profile storage stable. Updating replaces that payload, so
  every assigned profile gets the new version on its next launch. Running two
  versions of the same extension in different profiles would need two
  extension entries with two payload paths, and they would be two different
  extension IDs with separate storage — which is a different thing from an
  update, and is not offered.
- **Extension-to-extension interaction is not considered.** Several
  extensions assigned to one profile are passed together on one
  `--load-extension`; they are not tested for conflicts with each other.
- **Enterprise-policy installation is not implemented.** It is named above as
  the supported CRX route, not offered.
- **Assigning an extension turns exclusivity off for that profile**, and the
  fingerprint consequences of running an extension have not been measured. The
  launch note states the trade; no claim is made about detectability.
