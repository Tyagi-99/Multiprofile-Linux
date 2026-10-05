#!/usr/bin/env bash
# The package lifecycle, run INSIDE a Debian/Ubuntu environment.
#
# F12's acceptance test. The finding was that the packaging path could not be
# exercised on the development machine, so it shipped unverified; this
# exercises every step of it. CI calls this directly because its job already
# runs in a container. On a developer machine, scripts/release-lifecycle.sh
# starts the container and calls this.
#
# The signing key is generated here and dies with the environment. No
# production secret is read, and nothing produced here is published.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK:-/tmp/mp-lifecycle}"
rm -rf "$WORK"; mkdir -p "$WORK"
cd "$ROOT"

step() { printf '\n---- %s %s\n' "$1" "$(printf '%.0s-' $(seq 1 $((60 - ${#1}))))"; }

step "1. build two versions"
bash build-deb.sh >/dev/null
cp dist/multiprofile-native_1.9.0_all.deb "$WORK/"
# A second version, so there is something to update *to* and a delta between
# them. The packaged Python changes too, not just the control fields: the
# whole point of an update is that the code on disk is different afterwards,
# and a lifecycle whose two versions ship byte-identical code cannot tell an
# update that replaced the files from one that only rewrote the dpkg
# database. __version__ is what /api/health reports, so the difference is
# observable from outside the package.
sed -i 's/^VERSION=1.9.0/VERSION=1.9.1/' build-deb.sh
sed -i 's/^Version: 1.9.0/Version: 1.9.1/' packaging/debian/control
sed -i 's/^__version__ = "1.9.0"/__version__ = "1.9.1"/' src/multiprofile/__init__.py
bash build-deb.sh >/dev/null
cp dist/multiprofile-native_1.9.1_all.deb "$WORK/"
sed -i 's/^VERSION=1.9.1/VERSION=1.9.0/' build-deb.sh
sed -i 's/^Version: 1.9.1/Version: 1.9.0/' packaging/debian/control
sed -i 's/^__version__ = "1.9.1"/__version__ = "1.9.0"/' src/multiprofile/__init__.py
ls -l "$WORK"/multiprofile-native_*.deb

step "2. the fallback builder must match dpkg-deb"
# dpkg-deb exists here, so build-deb.sh used it. Build the same stage with the
# pure-Python path and confirm dpkg reads both identically -- that is what
# makes the Arch-side build trustworthy.
python3 scripts/build_deb.py dist/multiprofile-native_1.9.0_all "$WORK/py.deb"
dpkg-deb -I "$WORK/py.deb" >/dev/null && echo "dpkg-deb reads the Python-built package: ok"
dpkg-deb -c "$WORK/py.deb" | awk '{print $6}' | sort > "$WORK/py.list"
dpkg-deb -c "$WORK/multiprofile-native_1.9.0_all.deb" | awk '{print $6}' | sort > "$WORK/dpkg.list"
diff "$WORK/py.list" "$WORK/dpkg.list" && echo "identical file lists: ok"

step "3. throwaway signing key"
export GNUPGHOME="$WORK/gnupg"
mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
cat > "$WORK/keyparams" <<'KEY'
%no-protection
Key-Type: eddsa
Key-Curve: ed25519
Name-Real: Multiprofile CI Test Key
Name-Comment: DISPOSABLE
Name-Email: ci@multiprofile.invalid
Expire-Date: 0
%commit
KEY
gpg --batch --quiet --gen-key "$WORK/keyparams"
KEY_ID=$(gpg --list-secret-keys --with-colons | awk -F: '/^fpr/{print $10; exit}')
echo "key: $KEY_ID (disposable)"

step "4. publish a signed channel with deltas"
python3 scripts/release.py --repo "$WORK/repo" \
  --deb "$WORK/multiprofile-native_1.9.0_all.deb" --deb "$WORK/multiprofile-native_1.9.1_all.deb" \
  --key-id "$KEY_ID" --gnupghome "$GNUPGHOME"

step "5. verify the channel"
python3 scripts/verify_release.py --repo "$WORK/repo" \
  --gnupghome "$GNUPGHOME" --key-id "$KEY_ID"

step "6. install v1"
dpkg -i "$WORK/multiprofile-native_1.9.0_all.deb" >/dev/null 2>&1 || true
apt-get install -y -qq -f >/dev/null 2>&1 || true
dpkg -i "$WORK/multiprofile-native_1.9.0_all.deb" >/dev/null
dpkg-query --showformat='installed: ${Version}\n' --show multiprofile-native
test -x /usr/bin/multiprofile
test -x /usr/bin/multiprofile-update
echo "binaries present: ok"

step "7. configure the update channel"
mkdir -p /etc/multiprofile /usr/share/keyrings /var/lib/multiprofile/updates
gpg --dearmor < "$WORK/repo/multiprofile-archive-keyring.asc" \
  > /usr/share/keyrings/multiprofile-archive-keyring.gpg
( cd "$WORK/repo" && nohup python3 -m http.server 8099 >/dev/null 2>&1 & )
sleep 2
cat > /etc/multiprofile/update.conf <<'CONF'
[update]
url = http://127.0.0.1:8099
suite = stable
component = main
arch = amd64
keyring = /usr/share/keyrings/multiprofile-archive-keyring.gpg
CONF
# The updater needs the currently-installed .deb to apply a delta to.
cp "$WORK/multiprofile-native_1.9.0_all.deb" /var/lib/multiprofile/updates/multiprofile-native_1.9.0_all.deb

step "8. check reports an available update"
set +e; multiprofile-update --check; rc=$?; set -e
test "$rc" = "10" || { echo "expected exit 10 (update available), got $rc"; exit 1; }
echo "reports an available update: ok"

step "9. update across the delta"
multiprofile-update --install --yes 2>&1 | tee "$WORK/update.log"
grep -q "applied a delta" "$WORK/update.log" || { echo "the delta path was not used"; exit 1; }
test "$(dpkg-query --showformat='${Version}' --show multiprofile-native)" = "1.9.1" \
  || { echo "update did not take"; exit 1; }
echo "updated 1.9.0 -> 1.9.1 via delta: ok"

# dpkg says 1.9.1; the question is whether the *files* changed. Ask the
# installed application, not the package database.
( nohup multiprofile --server-only --port 8778 >"$WORK/app-new.log" 2>&1 & )
sleep 10
python3 - <<'NEWVER'
import json, urllib.request
body = json.loads(urllib.request.urlopen("http://127.0.0.1:8778/api/health", timeout=15).read())
assert body["version"] == "1.9.1", f"the updated install still runs {body['version']}"
print("the updated install serves 1.9.1:", body["ok"])
NEWVER
pkill -f "multiprofile --server-only --port 8778" || true
sleep 2
echo "the update replaced the code, not just the dpkg record: ok"

step "10. a tampered channel must be refused"
# Two separate gates, because they fail for different reasons and a channel
# that catches one and not the other is not protected.
#
# The first version of this step rewrote `Packages` and watched for a refusal
# that never came: the client fetches `Packages.gz`, so the tampered file was
# never read. It then reported "up to date" and the step passed its own
# grep for the wrong reason. Tamper with what is actually fetched.

tamper_dir() {
  rm -rf "$2"; cp -r "$1" "$2"
}

serve() {
  ( cd "$1" && nohup python3 -m http.server "$2" >/dev/null 2>&1 & )
  sleep 2
}

expect_refusal() {
  local label="$1" port="$2" pattern="$3"
  sed -i "s|:[0-9]\{4\}|:$port|" /etc/multiprofile/update.conf
  set +e
  multiprofile-update --check > "$WORK/$label.log" 2>&1
  local rc=$?
  set -e
  cat "$WORK/$label.log"
  if [ "$rc" = "0" ] || [ "$rc" = "10" ]; then
    echo "FAIL: $label was accepted (exit $rc)"; exit 1
  fi
  grep -qiE "$pattern" "$WORK/$label.log" || {
    echo "FAIL: $label was refused, but not for the expected reason"; exit 1; }
  echo "$label refused: ok"
}

# 10a. The package index rewritten by a mirror that cannot sign.
tamper_dir "$WORK/repo" "$WORK/evil-index"
printf 'evil' >> "$WORK/evil-index/pool/main/multiprofile-native_1.9.1_all.deb"
python3 - "$WORK/evil-index" <<'PY'
import gzip, hashlib, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
deb = root / "pool/main/multiprofile-native_1.9.1_all.deb"
idx = root / "dists/stable/main/binary-amd64/Packages"
blocks = idx.read_text().split("\n\n")
digest = hashlib.sha256(deb.read_bytes()).hexdigest()
for i, b in enumerate(blocks):
    if "1.9.1" in b:
        b = re.sub(r"(?m)^SHA256: .*$", f"SHA256: {digest}", b)
        blocks[i] = re.sub(r"(?m)^Size: .*$", f"Size: {deb.stat().st_size}", b)
text = "\n\n".join(blocks)
idx.write_text(text)
# ...and the compressed copy, which is the one the client actually fetches.
with gzip.GzipFile(str(root / "dists/stable/main/binary-amd64/Packages.gz"), "wb", mtime=0) as fh:
    fh.write(text.encode())
PY
serve "$WORK/evil-index" 8098
expect_refusal "tampered-index" 8098 "does not match the signed Release"

# 10b. The signature removed entirely.
tamper_dir "$WORK/repo" "$WORK/evil-unsigned"
cp "$WORK/evil-unsigned/dists/stable/Release" "$WORK/evil-unsigned/dists/stable/InRelease"
serve "$WORK/evil-unsigned" 8097
expect_refusal "unsigned" 8097 "not signed by the expected key"

# Back to the real channel.
sed -i "s|:[0-9]\{4\}|:8099|" /etc/multiprofile/update.conf

step "11. roll back"
multiprofile-update --rollback --yes
test "$(dpkg-query --showformat='${Version}' --show multiprofile-native)" = "1.9.0" \
  || { echo "rollback did not take"; exit 1; }
echo "rolled back to 1.9.0: ok"

step "12. the rolled-back install still runs"
( nohup multiprofile --server-only --port 8777 >"$WORK/app.log" 2>&1 & )
sleep 10
python3 - <<'PY'
import json, urllib.request
body = json.loads(urllib.request.urlopen("http://127.0.0.1:8777/api/health", timeout=15).read())
print("health ok:", body["ok"], "version:", body["version"])
assert body["version"] == "1.9.0", body["version"]
PY
echo "the installed package serves its API: ok"

step "13. apt, against the same signed channel"
# docs/UPDATES.md 4.4 gives operators an apt sources line. Until now that
# line was written from the format's requirements and had never been run,
# so a wrong option name or a missing index would have been found by the
# first operator to paste it. apt is a second, independent client of the
# same signature: it rejects what gpgv rejects, for its own reasons.
pkill -f "multiprofile --server-only --port 8777" || true
sleep 2
cat > /etc/apt/sources.list.d/multiprofile.list <<APTSRC
deb [signed-by=/usr/share/keyrings/multiprofile-archive-keyring.gpg] http://127.0.0.1:8099 stable main
APTSRC
apt-get update -o Dir::Etc::sourcelist=sources.list.d/multiprofile.list \
  -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0 2>&1 | tail -5
echo "apt accepted the signed channel: ok"

# The candidate must come from the channel, not from the installed copy.
apt-cache policy multiprofile-native | tee "$WORK/policy.log"
grep -q "Candidate: 1.9.1" "$WORK/policy.log" || {
  echo "FAIL: apt does not offer 1.9.1 from the channel"; exit 1; }
echo "apt offers 1.9.1 as the candidate: ok"

# And the upgrade itself, driven by apt rather than by our updater.
# --no-install-recommends matters: the package Recommends chromium, and
# apt installs Recommends by default.
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  --no-install-recommends multiprofile-native >/dev/null
test "$(dpkg-query --showformat='${Version}' --show multiprofile-native)" = "1.9.1" \
  || { echo "FAIL: apt did not upgrade to 1.9.1"; exit 1; }
echo "apt upgraded 1.9.0 -> 1.9.1: ok"

# apt must refuse an unsigned channel too. Not the one from step 10b:
# that copy only overwrote InRelease, and apt falls back to Release plus
# the detached Release.gpg, which was still the valid signature over an
# unmodified Release -- so apt would have accepted it and the step would
# have passed for the wrong reason, the way step 10 did once before.
# Remove both signatures, which is what an unsigned repository actually
# looks like.
rm -rf "$WORK/apt-nosig"; cp -r "$WORK/repo" "$WORK/apt-nosig"
rm -f "$WORK/apt-nosig/dists/stable/InRelease" "$WORK/apt-nosig/dists/stable/Release.gpg"
( cd "$WORK/apt-nosig" && nohup python3 -m http.server 8096 >/dev/null 2>&1 & )
sleep 2
sed -i "s|:8099|:8096|" /etc/apt/sources.list.d/multiprofile.list
set +e
apt-get update -o Dir::Etc::sourcelist=sources.list.d/multiprofile.list \
  -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0 > "$WORK/apt-unsigned.log" 2>&1
rc=$?
set -e
tail -5 "$WORK/apt-unsigned.log"
if [ "$rc" = "0" ] && ! grep -qiE "not signed|no_pubkey|no longer signed|badsig|does not have a Release file" "$WORK/apt-unsigned.log"; then
  echo "FAIL: apt accepted a channel with no signature"; exit 1
fi
echo "apt refused the unsigned channel: ok"
rm -f /etc/apt/sources.list.d/multiprofile.list
printf '\n%s\n' "===================================================================="
echo "LIFECYCLE PASSED: build -> sign -> publish -> install -> delta update"
echo "                  -> verify -> refuse tampering -> rollback -> runs"
echo "                  -> apt update, apt upgrade, apt refuses unsigned"
printf '%s\n' "===================================================================="
