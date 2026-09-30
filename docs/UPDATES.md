# Signing and the update channel

**Status: everything below is built and tested. Nothing is published.** The
machinery works against a throwaway key and a local repository, in CI and on a
developer machine; it has never been pointed at a real signing key or a real
host, because neither exists yet. §5 is the exact list of what that leaves
unverified, stated rather than implied.

This closes the buildable half of **F12**. See [`02`](02_AUDIT_REPORT.md).

---

## 1. What exists

| | |
|---|---|
| [`scripts/build_deb.py`](../scripts/build_deb.py) | Builds a `.deb` from the standard library. **`build-deb.sh` now runs on Arch**, which is the machine this product is developed on and where it previously could not run at all. |
| [`scripts/release.py`](../scripts/release.py) | Publishes a signed apt repository: `pool/`, `Packages{,.gz}`, `Release`, `Release.gpg`, `InRelease`, plus zstd deltas and a signed `deltas.json`. |
| [`scripts/verify_release.py`](../scripts/verify_release.py) | Verifies the whole chain offline — signature, index hashes, package hashes, and that each delta reconstructs its target byte for byte. |
| [`packaging/update/multiprofile-update`](../packaging/update/multiprofile-update) | The client: `--check`, `--download`, `--install`, `--rollback`. Shipped inside the package it updates. |
| [`scripts/release-lifecycle.sh`](../scripts/release-lifecycle.sh) | The whole lifecycle in a clean Debian container: build → sign → publish → install → delta update → verify → refuse a tampered channel → roll back → confirm the result still runs. |

## 2. Where the trust sits

apt verifies `InRelease`. That file carries the hashes of `Packages`.
`Packages` carries the SHA-256 of every `.deb`. **One signature covers the
whole chain**, and a tampered package fails before dpkg sees it.

Per-package signatures (`debsigs`) are deliberately *not* used. Almost nothing
verifies them by default, and shipping a signature nobody checks is worse than
shipping none, because it reads as protection.

A delta is **an optimisation and never a trust boundary**. The client applies
it to the installed `.deb` and then checks the result against the hash in the
signed `Packages` — not against the delta manifest. A corrupt or hostile delta
therefore costs a download, never an install. That is asserted in
`tests/test_update_channel.py::test_a_corrupt_delta_costs_a_download_not_an_install`.

## 3. Running it now

Everything here works today, with no secrets:

```bash
# The full lifecycle in a throwaway Debian container.
bash scripts/release-lifecycle.sh
```

```bash
# Or the parts that need no container, as ordinary tests.
.venv/bin/python -m pytest tests/test_update_channel.py -q
```

## 4. Going live — the exact steps

Everything in this section needs something I do not have: a signing identity
and somewhere to host files.

### 4.1 Create the signing key

Do this on a machine that is not a build machine, and keep the private key off
it afterwards.

```bash
export GNUPGHOME=~/.multiprofile-signing
mkdir -p "$GNUPGHOME" && chmod 700 "$GNUPGHOME"
gpg --batch --gen-key <<'EOF'
Key-Type: eddsa
Key-Curve: ed25519
Name-Real: Multiprofile Archive Signing Key
Name-Email: releases@YOURDOMAIN
Expire-Date: 2y
EOF
gpg --list-secret-keys --with-colons | awk -F: '/^fpr/{print $10; exit}'
```

Use a passphrase. Back the key up offline, and **publish the fingerprint
somewhere that is not the update channel** — a key fetched over the channel it
is meant to authenticate proves nothing.

`Expire-Date: 2y` is deliberate: an expiring key forces a decision every two
years instead of leaving one key valid forever with nobody remembering where
it lives.

### 4.2 Build and publish

```bash
bash build-deb.sh
python3 scripts/release.py \
  --repo /srv/multiprofile-repo \
  --deb dist/multiprofile-native_1.9.0_amd64.deb \
  --key-id <FINGERPRINT> \
  --gnupghome ~/.multiprofile-signing

python3 scripts/verify_release.py \
  --repo /srv/multiprofile-repo --key-id <FINGERPRINT>
```

Keep **every previously published `.deb` in the pool**. `release.py` generates
deltas between consecutive versions from what it finds there, so removing old
packages silently turns the delta channel off.

### 4.3 Host it

Any static HTTPS host: S3 and CloudFront, a plain nginx, GitHub Pages. There
is no server component — the channel is files.

Serve over HTTPS. The signature is what provides integrity, so HTTPS is not
load-bearing for that, but it stops a passive observer learning which version
each install is on.

### 4.4 What an operator then does

```bash
sudo install -d /usr/share/keyrings /etc/multiprofile
curl -fsSL https://YOURHOST/multiprofile-archive-keyring.asc \
  | gpg --dearmor | sudo tee /usr/share/keyrings/multiprofile-archive-keyring.gpg >/dev/null

# Verify the fingerprint against the one you published out of band.
gpg --show-keys /usr/share/keyrings/multiprofile-archive-keyring.gpg

sudo tee /etc/multiprofile/update.conf >/dev/null <<'EOF'
[update]
url = https://YOURHOST
suite = stable
component = main
arch = amd64
keyring = /usr/share/keyrings/multiprofile-archive-keyring.gpg
EOF

multiprofile-update --check
sudo multiprofile-update --install
```

Or through apt, which verifies the same signature:

```
deb [signed-by=/usr/share/keyrings/multiprofile-archive-keyring.gpg] https://YOURHOST stable main
```

### 4.5 CI signing, when there is a real key

The lifecycle job uses a key generated per run and discarded with the
container, and **no production key should ever be added to this repository or
to GitHub Actions secrets without deciding that deliberately.** A release key
in CI means anyone who can push a workflow change can sign a release.

The safer arrangement, if it is ever needed: build in CI, publish the
unsigned artefacts, and sign on a machine a human controls.

## 5. What remains unverified

Stated plainly, because "signed builds are done" would not be true.

| | Why |
|---|---|
| **A real signing key** | None exists. Every signature so far is from a throwaway key generated inside a test. |
| **A real host** | Nothing is published. The channel has only ever been served from a local directory or a container. |
| **`apt update` / `apt install` against a live repository** | The lifecycle installs with `dpkg -i` from a local HTTP repo. The apt sources line in §4.4 is written from the format's requirements, not from a run. |
| **Upgrade across a genuine code change** | The two versions in the lifecycle differ only in their version markers, because that is enough to exercise the mechanism. A real upgrade also has to survive `postinst` against changed files and a running instance. |
| **Anything but amd64, and anything but Debian/Ubuntu** | `Architecture: amd64` is hard-coded; there is no arm64 build and no `.rpm` or AppImage. |
| **Key rotation and revocation** | No procedure is written. This matters before the two-year expiry, not after. |
| **Unattended updates** | Deliberately absent. `multiprofile-update` never installs without being asked, and a timer that upgrades a desktop app mid-session is a decision to make on purpose. |

---

*Companions:* [`02_AUDIT_REPORT`](02_AUDIT_REPORT.md) F12 ·
[`INSTALLATION`](INSTALLATION.md) · [`06_ROADMAP`](06_ROADMAP.md)
