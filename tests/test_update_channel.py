"""F12: the signed update channel, exercised without needing dpkg.

The finding was that distribution was not production-ready -- unsigned, no
update path, and ``build-deb.sh`` could not even be run on the machine the
product is developed on, so every packaging change shipped having been built
once in CI and never opened.

Three pieces are tested here, all of which run anywhere:

* ``scripts/build_deb.py`` -- a ``.deb`` written from the standard library,
  so the build is runnable on Arch and the release process can be exercised
  before it matters.
* ``scripts/release.py`` -- a signed apt repository with zstd deltas.
* ``packaging/update/multiprofile-update`` -- the client, against a real
  local HTTP server and a real signature.

What is **not** here is ``dpkg -i``, rollback and the running installed
service: those need a Debian root filesystem, and they are covered by
``scripts/release-lifecycle-inner.sh`` in CI and by
``scripts/release-lifecycle.sh`` locally. Tests that quietly skip the only
part that needs the real system would be worse than tests that say where the
boundary is.

The signing key is generated per test run into a temporary GNUPGHOME and
never leaves it.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
UPDATER = ROOT / "packaging" / "update" / "multiprofile-update"

pytestmark = pytest.mark.skipif(
    shutil.which("gpg") is None, reason="gpg is required to sign the channel"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage(tmp: Path, version: str, payload: bytes = b"#!/bin/sh\nexit 0\n") -> Path:
    stage = tmp / f"stage-{version}"
    (stage / "DEBIAN").mkdir(parents=True)
    (stage / "usr/bin").mkdir(parents=True)
    (stage / "DEBIAN/control").write_text(
        "Package: multiprofile-native\n"
        f"Version: {version}\n"
        "Architecture: amd64\n"
        "Maintainer: Test <test@localhost>\n"
        "Section: utils\nPriority: optional\n"
        "Description: test package\n"
    )
    (stage / "usr/bin/multiprofile").write_bytes(payload)
    (stage / "usr/bin/multiprofile").chmod(0o755)
    return stage


def _build(tmp: Path, version: str, payload: bytes | None = None) -> Path:
    sys.path.insert(0, str(SCRIPTS))
    from build_deb import build_deb

    stage = _stage(tmp, version, payload or b"#!/bin/sh\nexit 0\n")
    return build_deb(stage, tmp / f"multiprofile-native_{version}_amd64.deb")


@pytest.fixture(scope="module")
def gnupghome(tmp_path_factory):
    home = tmp_path_factory.mktemp("gnupg")
    home.chmod(0o700)
    params = home / "params"
    params.write_text(
        "%no-protection\nKey-Type: eddsa\nKey-Curve: ed25519\n"
        "Name-Real: Multiprofile Test Key\nName-Comment: DISPOSABLE\n"
        "Name-Email: test@multiprofile.invalid\nExpire-Date: 0\n%commit\n"
    )
    env = {**os.environ, "GNUPGHOME": str(home)}
    subprocess.run(["gpg", "--batch", "--quiet", "--gen-key", str(params)],
                   check=True, env=env, capture_output=True)
    listing = subprocess.run(
        ["gpg", "--list-secret-keys", "--with-colons"],
        check=True, env=env, capture_output=True, text=True,
    ).stdout
    key_id = next(ln.split(":")[9] for ln in listing.splitlines() if ln.startswith("fpr"))
    return str(home), key_id


@pytest.fixture(scope="module")
def channel(tmp_path_factory, gnupghome):
    """A signed two-version repository, built once for the module."""
    home, key_id = gnupghome
    tmp = tmp_path_factory.mktemp("channel")
    old = _build(tmp, "1.0.0", b"#!/bin/sh\necho one\n" + b"x" * 40_000)
    new = _build(tmp, "1.1.0", b"#!/bin/sh\necho two\n" + b"x" * 40_000)
    repo = tmp / "repo"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "release.py"), "--repo", str(repo),
         "--deb", str(old), "--deb", str(new),
         "--key-id", key_id, "--gnupghome", home],
        check=True, capture_output=True, text=True,
    )
    return {"repo": repo, "old": old, "new": new, "home": home, "key": key_id, "tmp": tmp}


# --------------------------------------------------------------------------
# 1. The builder — F12's first blocker
# --------------------------------------------------------------------------

def test_a_deb_can_be_built_without_dpkg(tmp_path):
    """The reason packaging shipped unverified: the script needed dpkg-deb,
    which the development machine does not have."""
    deb = _build(tmp_path, "1.0.0")
    assert deb.is_file() and deb.stat().st_size > 0
    assert deb.read_bytes()[:8] == b"!<arch>\n"


def test_the_deb_has_exactly_the_three_members_dpkg_expects(tmp_path):
    deb = _build(tmp_path, "1.0.0")
    names = subprocess.run(["ar", "t", str(deb)], check=True,
                           capture_output=True, text=True).stdout.split()
    assert names == ["debian-binary", "control.tar.gz", "data.tar.gz"], names


def test_the_build_is_reproducible(tmp_path):
    """Two builds of one tree must be byte-identical, or "the signature still
    matches" means nothing."""
    sys.path.insert(0, str(SCRIPTS))
    from build_deb import build_deb

    stage = _stage(tmp_path, "1.0.0")
    a = build_deb(stage, tmp_path / "a.deb").read_bytes()
    b = build_deb(stage, tmp_path / "b.deb").read_bytes()
    assert a == b


def test_maintainer_scripts_stay_executable(tmp_path):
    """dpkg silently skips a postinst that is not executable."""
    sys.path.insert(0, str(SCRIPTS))
    from build_deb import build_deb

    stage = _stage(tmp_path, "1.0.0")
    (stage / "DEBIAN/postinst").write_text("#!/bin/sh\nexit 0\n")
    deb = build_deb(stage, tmp_path / "p.deb")
    subprocess.run(["ar", "x", str(deb)], cwd=tmp_path, check=True)
    listing = subprocess.run(
        ["tar", "tvzf", str(tmp_path / "control.tar.gz")],
        check=True, capture_output=True, text=True,
    ).stdout
    line = next(ln for ln in listing.splitlines() if ln.endswith("postinst"))
    assert line.startswith("-rwxr-xr-x"), line


def test_md5sums_is_generated(tmp_path):
    """dpkg --verify reads it; a stale one reports intact files as modified."""
    deb = _build(tmp_path, "1.0.0")
    subprocess.run(["ar", "x", str(deb)], cwd=tmp_path, check=True)
    names = subprocess.run(["tar", "tzf", str(tmp_path / "control.tar.gz")],
                           check=True, capture_output=True, text=True).stdout
    assert "./md5sums" in names.split()


# --------------------------------------------------------------------------
# 2. The signed channel
# --------------------------------------------------------------------------

def test_the_channel_is_signed_and_verifies(channel):
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "verify_release.py"),
         "--repo", str(channel["repo"]), "--gnupghome", channel["home"],
         "--key-id", channel["key"]],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "OK:" in out.stdout


def test_inrelease_and_release_gpg_both_exist(channel):
    """apt prefers InRelease; older clients use the detached Release.gpg."""
    dist = channel["repo"] / "dists/stable"
    assert (dist / "InRelease").is_file()
    assert (dist / "Release.gpg").is_file()


def test_the_public_key_is_published(channel):
    key = channel["repo"] / "multiprofile-archive-keyring.asc"
    assert key.is_file()
    assert "BEGIN PGP PUBLIC KEY BLOCK" in key.read_text()


def test_packages_carries_the_hash_of_each_deb(channel):
    packages = (channel["repo"] / "dists/stable/main/binary-amd64/Packages").read_text()
    for deb in (channel["old"], channel["new"]):
        assert _sha256(deb) in packages, deb.name


def test_release_covers_packages_gz_too(channel):
    """apt fetches the compressed index; leaving it unhashed would leave the
    file apt actually downloads outside the signature."""
    release = (channel["repo"] / "dists/stable/Release").read_text()
    assert "main/binary-amd64/Packages.gz" in release
    gz = channel["repo"] / "dists/stable/main/binary-amd64/Packages.gz"
    assert _sha256(gz) in release
    # ...and it must decompress to the same index.
    plain = (channel["repo"] / "dists/stable/main/binary-amd64/Packages").read_bytes()
    assert gzip.decompress(gz.read_bytes()) == plain


@pytest.mark.parametrize("target", [
    "pool/main/multiprofile-native_1.1.0_amd64.deb",
    "dists/stable/main/binary-amd64/Packages",
    "deltas/deltas.json",
])
def test_tampering_is_detected(channel, tmp_path, target):
    """Each file in the chain, altered on its own, must fail verification."""
    evil = tmp_path / f"evil-{Path(target).name}"
    shutil.copytree(channel["repo"], evil)
    path = evil / target
    path.write_bytes(path.read_bytes() + b"tampered")
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "verify_release.py"),
         "--repo", str(evil), "--gnupghome", channel["home"], "--key-id", channel["key"]],
        capture_output=True, text=True,
    )
    assert out.returncode != 0, f"tampering with {target} was not detected\n{out.stdout}"


def test_a_signature_from_another_key_is_refused(channel, tmp_path):
    """The check must be "signed by *this* key", not merely "signed"."""
    other = tmp_path / "other-gnupg"
    other.mkdir()
    other.chmod(0o700)
    params = other / "params"
    params.write_text(
        "%no-protection\nKey-Type: eddsa\nKey-Curve: ed25519\n"
        "Name-Real: Impostor\nName-Email: nope@multiprofile.invalid\n"
        "Expire-Date: 0\n%commit\n"
    )
    env = {**os.environ, "GNUPGHOME": str(other)}
    subprocess.run(["gpg", "--batch", "--quiet", "--gen-key", str(params)],
                   check=True, env=env, capture_output=True)
    listing = subprocess.run(["gpg", "--list-secret-keys", "--with-colons"],
                             check=True, env=env, capture_output=True, text=True).stdout
    impostor = next(ln.split(":")[9] for ln in listing.splitlines() if ln.startswith("fpr"))

    evil = tmp_path / "evil-key"
    shutil.copytree(channel["repo"], evil)
    subprocess.run(
        [sys.executable, str(SCRIPTS / "release.py"), "--repo", str(evil),
         "--key-id", impostor, "--gnupghome", str(other)],
        check=True, capture_output=True, text=True,
    )
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "verify_release.py"), "--repo", str(evil),
         "--gnupghome", str(other), "--key-id", channel["key"]],
        capture_output=True, text=True,
    )
    assert out.returncode != 0, out.stdout


# --------------------------------------------------------------------------
# 3. Deltas
# --------------------------------------------------------------------------

zstd_required = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")


@zstd_required
def test_a_delta_is_published_between_consecutive_versions(channel):
    manifest = json.loads((channel["repo"] / "deltas/deltas.json").read_text())
    entries = manifest["deltas"]
    assert len(entries) == 1
    assert entries[0]["from"] == "1.0.0" and entries[0]["to"] == "1.1.0"


@zstd_required
def test_the_delta_is_smaller_than_the_package(channel):
    """The roadmap's requirement, as a number rather than a claim."""
    entry = json.loads((channel["repo"] / "deltas/deltas.json").read_text())["deltas"][0]
    assert entry["size"] < entry["fullSize"], (entry["size"], entry["fullSize"])


@zstd_required
def test_the_delta_reconstructs_the_published_package_exactly(channel, tmp_path):
    entry = json.loads((channel["repo"] / "deltas/deltas.json").read_text())["deltas"][0]
    out = tmp_path / "rebuilt.deb"
    subprocess.run(
        ["zstd", "-q", "-d", "--patch-from", str(channel["old"]),
         str(channel["repo"] / entry["path"]), "-o", str(out)],
        check=True, capture_output=True,
    )
    assert _sha256(out) == _sha256(channel["new"])
    assert _sha256(out) == entry["resultSha256"]


@zstd_required
def test_the_delta_manifest_is_covered_by_the_signature(channel):
    """Otherwise a delta could be swapped without breaking anything signed."""
    release = (channel["repo"] / "dists/stable/Release").read_text()
    assert _sha256(channel["repo"] / "deltas/deltas.json") in release


# --------------------------------------------------------------------------
# 4. The client
# --------------------------------------------------------------------------

class _Server:
    def __init__(self, root: Path):
        handler = partial(SimpleHTTPRequestHandler, directory=str(root))
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _client_env(tmp_path: Path, repo: Path, url: str, installed: str | None):
    """Config, keyring and a fake dpkg-query, so the updater can be driven
    without a Debian root filesystem."""
    keyring = tmp_path / "keyring.gpg"
    armoured = (repo / "multiprofile-archive-keyring.asc").read_bytes()
    keyring.write_bytes(subprocess.run(
        ["gpg", "--dearmor"], input=armoured, check=True, capture_output=True
    ).stdout)

    conf = tmp_path / "update.conf"
    conf.write_text(
        f"[update]\nurl = {url}\nsuite = stable\ncomponent = main\n"
        f"arch = amd64\nkeyring = {keyring}\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "dpkg-query"
    if installed is None:
        fake.write_text("#!/bin/sh\nexit 1\n")
    else:
        fake.write_text(f'#!/bin/sh\nprintf "{installed}"\n')
    fake.chmod(0o755)

    env = {**os.environ}
    env["MULTIPROFILE_UPDATE_CONF"] = str(conf)
    env["MULTIPROFILE_UPDATE_STATE"] = str(tmp_path / "state")
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return env


def _run_updater(env, *args):
    return subprocess.run([sys.executable, str(UPDATER), *args],
                          capture_output=True, text=True, env=env)


def test_check_reports_an_available_update(channel, tmp_path):
    srv = _Server(channel["repo"])
    try:
        env = _client_env(tmp_path, channel["repo"], srv.url, installed="1.0.0")
        out = _run_updater(env, "--check")
        assert out.returncode == 10, out.stdout + out.stderr
        assert "1.0.0 -> 1.1.0" in out.stdout
    finally:
        srv.close()


def test_check_says_up_to_date_when_it_is(channel, tmp_path):
    srv = _Server(channel["repo"])
    try:
        env = _client_env(tmp_path, channel["repo"], srv.url, installed="1.1.0")
        out = _run_updater(env, "--check")
        assert out.returncode == 0, out.stdout + out.stderr
        assert "up to date" in out.stdout
    finally:
        srv.close()


@zstd_required
def test_download_uses_the_delta_and_verifies_the_result(channel, tmp_path):
    """The delta path, end to end: the updater must apply it and check the
    result against the hash in the *signed* index, not the delta manifest."""
    srv = _Server(channel["repo"])
    try:
        env = _client_env(tmp_path, channel["repo"], srv.url, installed="1.0.0")
        state = tmp_path / "state"
        state.mkdir(parents=True, exist_ok=True)
        shutil.copy2(channel["old"], state / "multiprofile-native_1.0.0_amd64.deb")
        out = _run_updater(env, "--download")
        assert out.returncode == 0, out.stdout + out.stderr
        assert "applied a delta" in out.stdout, out.stdout
        got = state / "multiprofile-native_1.1.0_amd64.deb"
        assert got.is_file()
        assert _sha256(got) == _sha256(channel["new"])
    finally:
        srv.close()


def test_download_falls_back_to_the_full_package(channel, tmp_path):
    """With no local copy of the installed version there is nothing to patch,
    and the updater must simply download instead of failing."""
    srv = _Server(channel["repo"])
    try:
        env = _client_env(tmp_path, channel["repo"], srv.url, installed="1.0.0")
        out = _run_updater(env, "--download")
        assert out.returncode == 0, out.stdout + out.stderr
        assert "downloaded the full package" in out.stdout
    finally:
        srv.close()


@zstd_required
def test_a_corrupt_delta_costs_a_download_not_an_install(channel, tmp_path):
    """The property that makes deltas safe to use at all."""
    evil = tmp_path / "evil-delta"
    shutil.copytree(channel["repo"], evil)
    entry = json.loads((evil / "deltas/deltas.json").read_text())["deltas"][0]
    target = evil / entry["path"]
    target.write_bytes(b"not a delta at all")

    srv = _Server(evil)
    try:
        env = _client_env(tmp_path, evil, srv.url, installed="1.0.0")
        state = tmp_path / "state"
        state.mkdir(parents=True, exist_ok=True)
        shutil.copy2(channel["old"], state / "multiprofile-native_1.0.0_amd64.deb")
        out = _run_updater(env, "--download")
        assert out.returncode == 0, out.stdout + out.stderr
        assert "falling back to the full package" in out.stderr, out.stderr
        got = state / "multiprofile-native_1.1.0_amd64.deb"
        assert _sha256(got) == _sha256(channel["new"]), "the result must still be correct"
    finally:
        srv.close()


def test_a_tampered_package_is_refused(channel, tmp_path):
    """Packages still carries the real hash, so the download must not match."""
    evil = tmp_path / "evil-pkg"
    shutil.copytree(channel["repo"], evil)
    deb = evil / "pool/main/multiprofile-native_1.1.0_amd64.deb"
    deb.write_bytes(deb.read_bytes() + b"tampered")

    srv = _Server(evil)
    try:
        env = _client_env(tmp_path, evil, srv.url, installed="1.0.0")
        out = _run_updater(env, "--download")
        assert out.returncode != 0
        assert "does not match the signed index" in out.stderr, out.stderr
    finally:
        srv.close()


def test_an_unsigned_index_is_refused(channel, tmp_path):
    """The whole point of the channel."""
    evil = tmp_path / "unsigned"
    shutil.copytree(channel["repo"], evil)
    (evil / "dists/stable/InRelease").write_text("Origin: Multiprofile\nSuite: stable\n")

    srv = _Server(evil)
    try:
        env = _client_env(tmp_path, evil, srv.url, installed="1.0.0")
        out = _run_updater(env, "--check")
        assert out.returncode != 0
        assert "not signed by the expected key" in out.stderr, out.stderr
    finally:
        srv.close()


def test_a_rewritten_packages_index_is_refused(channel, tmp_path):
    """A mirror that can rewrite files but not sign them."""
    evil = tmp_path / "rewritten"
    shutil.copytree(channel["repo"], evil)
    idx = evil / "dists/stable/main/binary-amd64/Packages"
    idx.write_text(idx.read_text().replace("Version: 1.1.0", "Version: 9.9.9"))
    gz = evil / "dists/stable/main/binary-amd64/Packages.gz"
    with gzip.GzipFile(str(gz), "wb", mtime=0) as fh:
        fh.write(idx.read_bytes())

    srv = _Server(evil)
    try:
        env = _client_env(tmp_path, evil, srv.url, installed="1.0.0")
        out = _run_updater(env, "--check")
        assert out.returncode != 0
        assert "does not match the signed Release" in out.stderr, out.stderr
    finally:
        srv.close()


def test_no_configured_channel_is_a_clear_message(tmp_path):
    """A build installed from a file has no channel, and that is not an error
    to be shouted -- but it must not look like "no update available" either."""
    env = {**os.environ, "MULTIPROFILE_UPDATE_CONF": str(tmp_path / "absent.conf")}
    out = _run_updater(env, "--check")
    assert out.returncode == 2
    assert "no update channel configured" in out.stderr


def test_installing_without_root_refuses_early(channel, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("running as root")
    srv = _Server(channel["repo"])
    try:
        env = _client_env(tmp_path, channel["repo"], srv.url, installed="1.0.0")
        out = _run_updater(env, "--install", "--yes")
        assert out.returncode == 2
        assert "requires root" in out.stderr
    finally:
        srv.close()


# --------------------------------------------------------------------------
# 5. Automatic checking, and the decision not to auto-install
# --------------------------------------------------------------------------
#
# The matrix row is "signed delta *auto*-update" and everything above is
# started by hand, so this is the part that makes it automatic -- and the
# part where a second principle applies. F14 established that this product
# makes no network requests of its own, which an updater polling on a timer
# plainly is. The resolution is that the request only happens once the
# operator has written /etc/multiprofile/update.conf, and installing stays a
# separate decision that is off by default and refused under a running fleet.

def test_no_channel_means_no_requests(monkeypatch, tmp_path):
    """The default state of a freshly installed package, and the one F14's
    principle requires: it asks nobody anything."""
    from multiprofile import updates

    monkeypatch.setenv("MULTIPROFILE_UPDATE_CONF", str(tmp_path / "absent.conf"))
    assert updates.channel_configured() is False
    result = updates.check_now()
    assert result["available"] is None
    assert result["error"] == "no update channel configured"


def test_the_checker_does_not_start_without_a_channel(monkeypatch, tmp_path):
    from multiprofile import updates

    monkeypatch.setenv("MULTIPROFILE_UPDATE_CONF", str(tmp_path / "absent.conf"))
    assert updates.start_update_checker() is None


def test_configuring_a_channel_is_the_opt_in(channel, tmp_path, monkeypatch):
    """Writing the config is the deliberate act. Once it exists, checking on
    a schedule is what was asked for."""
    from multiprofile import updates

    srv = _Server(channel["repo"])
    try:
        env = _client_env(tmp_path, channel["repo"], srv.url, installed="1.0.0")
        monkeypatch.setenv("MULTIPROFILE_UPDATE_CONF", env["MULTIPROFILE_UPDATE_CONF"])
        monkeypatch.setenv("MULTIPROFILE_UPDATE_STATE", env["MULTIPROFILE_UPDATE_STATE"])
        monkeypatch.setenv("PATH", env["PATH"])
        assert updates.channel_configured() is True
        result = updates.check_now()
        assert result["available"] is True, result
        assert result["installed"] == "1.0.0"
        assert result["latest"] == "1.1.0"
        assert result["error"] is None
    finally:
        srv.close()


def test_up_to_date_is_reported_as_false_not_none(channel, tmp_path, monkeypatch):
    """"No update" and "could not tell" have to be different answers, or a
    stale install reads as a healthy one."""
    from multiprofile import updates

    srv = _Server(channel["repo"])
    try:
        env = _client_env(tmp_path, channel["repo"], srv.url, installed="1.1.0")
        for key in ("MULTIPROFILE_UPDATE_CONF", "MULTIPROFILE_UPDATE_STATE", "PATH"):
            monkeypatch.setenv(key, env[key])
        result = updates.check_now()
        assert result["available"] is False
        assert result["error"] is None
    finally:
        srv.close()


def test_a_broken_channel_reports_unknown_not_up_to_date(tmp_path, monkeypatch):
    """The important distinction: a signature that will not verify must never
    read as "you are on the latest version"."""
    from multiprofile import updates

    conf = tmp_path / "update.conf"
    conf.write_text(
        "[update]\nurl = http://127.0.0.1:1\nsuite = stable\n"
        f"keyring = {tmp_path / 'absent.gpg'}\n"
    )
    monkeypatch.setenv("MULTIPROFILE_UPDATE_CONF", str(conf))
    result = updates.check_now(timeout=30)
    assert result["available"] is None, "a failure must not read as up to date"
    assert result["error"]


def test_auto_install_is_off_by_default():
    """The product decision. Replacing the binary under a running fleet risks
    exactly what the operator uses this product to avoid."""
    from multiprofile.config import get_settings

    assert get_settings().update_auto_install is False


def test_auto_install_is_refused_while_profiles_are_running(monkeypatch, tmp_path):
    """Even when it has been turned on. An update that waits is an update;
    one that interrupts a session is an incident."""
    from multiprofile import updates

    conf = tmp_path / "update.conf"
    conf.write_text("[update]\nurl = http://127.0.0.1:1\nkeyring = /dev/null\n")
    monkeypatch.setenv("MULTIPROFILE_UPDATE_CONF", str(conf))
    monkeypatch.setenv("MULTIPROFILE_UPDATE_AUTO_INSTALL", "1")
    monkeypatch.setattr(
        "multiprofile.storage.db.list_profile_runtimes",
        lambda *a, **k: [{"profile_id": "p1", "state": "running"}],
    )
    result = updates.install_now(require_opt_in=True)
    assert result["ok"] is False
    assert result["reason"] == "profiles_running"


def test_auto_install_refuses_when_it_cannot_tell_what_is_running(monkeypatch, tmp_path):
    """If the runtime table cannot be read, the safe answer is not to
    install -- not to assume the fleet is idle."""
    from multiprofile import updates

    conf = tmp_path / "update.conf"
    conf.write_text("[update]\nurl = http://127.0.0.1:1\nkeyring = /dev/null\n")
    monkeypatch.setenv("MULTIPROFILE_UPDATE_CONF", str(conf))
    monkeypatch.setenv("MULTIPROFILE_UPDATE_AUTO_INSTALL", "1")

    def boom(*a, **k):
        raise RuntimeError("control db unavailable")

    monkeypatch.setattr("multiprofile.storage.db.list_profile_runtimes", boom)
    result = updates.install_now(require_opt_in=True)
    assert result["ok"] is False
    assert result["reason"] == "runtime_unknown"


def test_the_scheduled_path_requires_the_opt_in(monkeypatch, tmp_path):
    from multiprofile import updates

    conf = tmp_path / "update.conf"
    conf.write_text("[update]\nurl = http://127.0.0.1:1\nkeyring = /dev/null\n")
    monkeypatch.setenv("MULTIPROFILE_UPDATE_CONF", str(conf))
    monkeypatch.delenv("MULTIPROFILE_UPDATE_AUTO_INSTALL", raising=False)
    assert updates.install_now(require_opt_in=True)["reason"] == "auto_install_disabled"


# --------------------------------------------------------------------------
# 6. The endpoints that used to be stubs
# --------------------------------------------------------------------------

@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_UPDATE_CONF", str(tmp_path / "absent.conf"))
    from multiprofile.api.server import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_the_update_endpoints_are_no_longer_stubs(api):
    """They returned "Updater disabled in native build", {"status":
    "disabled"} and {"ok": false} -- the F16 shape, a control the console can
    call that reaches nothing."""
    body = api.get("/api/update/status").get_json()
    assert body["status"] != "disabled"
    assert body["status"] == "no_channel"
    assert "channelConfigured" in body


def test_update_status_answers_without_a_channel(api):
    body = api.get("/api/update").get_json()
    assert body["ok"] is True
    assert body["channelConfigured"] is False
    assert body["autoInstall"] is False


def test_checking_without_a_channel_is_not_a_server_error(api):
    """200 with a body that says why. A 5xx would make the default state of
    every fresh install look like a broken server."""
    response = api.post("/api/update/check")
    assert response.status_code == 200
    assert response.get_json()["error"] == "no update channel configured"


def test_apply_without_a_channel_is_refused_clearly(api):
    response = api.post("/api/update/apply")
    assert response.status_code == 409
    assert response.get_json()["reason"] == "no_channel"


def test_health_always_carries_the_update_block(api):
    """Like the permissions and identity blocks: "no channel configured" is a
    statement about this install, not an absence to be inferred."""
    block = api.get("/api/health").get_json()["update"]
    assert block["channelConfigured"] is False
    assert block["autoInstall"] is False
    assert "available" in block


def test_tampering_with_only_the_compressed_index_is_refused(channel, tmp_path):
    """The client fetches Packages.gz, not Packages.

    The release lifecycle's tamper step originally rewrote the plain
    `Packages`, watched for a refusal that never came, and passed anyway --
    the file it corrupted was never read. A gate aimed at a file nobody
    fetches protects nothing, so this pins which one is actually verified.
    """
    evil = tmp_path / "gz-only"
    shutil.copytree(channel["repo"], evil)
    idx = evil / "dists/stable/main/binary-amd64/Packages"
    rewritten = idx.read_text().replace("Version: 1.1.0", "Version: 9.9.9")
    gz = evil / "dists/stable/main/binary-amd64/Packages.gz"
    with gzip.GzipFile(str(gz), "wb", mtime=0) as fh:
        fh.write(rewritten.encode())
    # The plain index is left untouched on purpose: only the compressed one
    # is corrupt, and the client must still refuse.

    srv = _Server(evil)
    try:
        env = _client_env(tmp_path, evil, srv.url, installed="1.0.0")
        out = _run_updater(env, "--check")
        assert out.returncode != 0, out.stdout
        assert "does not match the signed Release" in out.stderr, out.stderr
    finally:
        srv.close()


def test_an_untouched_plain_index_does_not_rescue_a_bad_gz(channel, tmp_path):
    """Belt and braces for the above: the client must not silently fall back
    to the uncompressed index when the compressed one fails its hash."""
    evil = tmp_path / "gz-bad-plain-good"
    shutil.copytree(channel["repo"], evil)
    gz = evil / "dists/stable/main/binary-amd64/Packages.gz"
    gz.write_bytes(b"not gzip at all")

    srv = _Server(evil)
    try:
        env = _client_env(tmp_path, evil, srv.url, installed="1.0.0")
        out = _run_updater(env, "--check")
        assert out.returncode != 0, out.stdout
    finally:
        srv.close()
