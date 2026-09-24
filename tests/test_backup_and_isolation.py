"""Tests for backup, profile isolation, fingerprint stability, and security.

Run: uv run pytest tests/test_backup_and_isolation.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_xdg(tmp_path: Path, monkeypatch):
    """Redirect all XDG paths to a temp directory."""
    import multiprofile.storage.xdg as xdg
    import multiprofile.cookies_store as cs
    monkeypatch.setattr(xdg, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(xdg, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(xdg, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(xdg, "profiles_path", lambda: tmp_path / "data" / "profiles.json")
    monkeypatch.setattr(xdg, "runtime_state_path", lambda: tmp_path / "cache" / "runtime.json")
    monkeypatch.setattr(xdg, "backups_dir", lambda: tmp_path / "data" / "backups")
    monkeypatch.setattr(xdg, "chrome_profile_dir", lambda pid: tmp_path / "data" / "chrome-profiles" / pid)
    # cookies_path lives in cookies_store (not xdg)
    monkeypatch.setattr(cs, "cookies_path",
                        lambda pid: tmp_path / "data" / "cookies" / f"{pid}.json")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)


# ============================================================================
# PROFILE ISOLATION
# ============================================================================

class TestProfileIsolation:
    """Profile A storage must never overlap with Profile B."""

    def test_different_profile_ids(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.xdg as xdg
        import multiprofile.storage.profiles as prof
        p1 = prof.create_profile({"name": "Alpha"})
        p2 = prof.create_profile({"name": "Beta"})
        assert p1["id"] != p2["id"]
        dir1 = xdg.chrome_profile_dir(p1["id"])
        dir2 = xdg.chrome_profile_dir(p2["id"])
        assert dir1 != dir2
        assert p1["id"] not in str(dir2)
        assert p2["id"] not in str(dir1)

    def test_different_cookie_paths(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.cookies_store as cs
        import multiprofile.storage.profiles as prof
        p1 = prof.create_profile({"name": "Alpha"})
        p2 = prof.create_profile({"name": "Beta"})
        # cookies_path is in cookies_store
        cp1 = cs.cookies_path(p1["id"])
        cp2 = cs.cookies_path(p2["id"])
        assert cp1 != cp2

    def test_cookies_do_not_cross_contaminate(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.cookies_store as cs
        p1 = prof.create_profile({"name": "Alpha"})
        p2 = prof.create_profile({"name": "Beta"})
        cs.save_cookies(p1["id"], [{"domain": ".alpha.com", "name": "sess", "value": "aaa"}])
        cs.save_cookies(p2["id"], [{"domain": ".beta.com", "name": "sess", "value": "bbb"}])
        loaded_a = cs.load_cookies(p1["id"])
        loaded_b = cs.load_cookies(p2["id"])
        assert any(c["value"] == "aaa" for c in loaded_a)
        assert not any(c["value"] == "bbb" for c in loaded_a), "Beta cookie leaked into Alpha!"
        assert any(c["value"] == "bbb" for c in loaded_b)
        assert not any(c["value"] == "aaa" for c in loaded_b), "Alpha cookie leaked into Beta!"

    def test_profile_identity_stable_across_restarts(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        from multiprofile.fingerprint import build_fingerprint, normalize_seed
        p = prof.create_profile({"name": "Stable", "seed": 99999})
        seed1 = normalize_seed(p)
        fp1 = build_fingerprint(p)
        p2 = prof.get_profile(p["id"])
        seed2 = normalize_seed(p2)
        fp2 = build_fingerprint(p2)
        assert seed1 == seed2, "Seed changed after reload"
        assert fp1["platform"] == fp2["platform"]
        assert fp1["cores"] == fp2["cores"]
        assert fp1["screen"] == fp2["screen"]

    def test_profile_b_independent_identity(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        from multiprofile.fingerprint import build_fingerprint
        pa = prof.create_profile({"name": "Alpha", "seed": 111111})
        pb = prof.create_profile({"name": "Beta", "seed": 999999})
        fpa = build_fingerprint(pa)
        fpb = build_fingerprint(pb)
        assert fpa["seed"] != fpb["seed"]
        differs = (
            fpa["screen"] != fpb["screen"] or
            fpa["cores"] != fpb["cores"] or
            fpa["memory"] != fpb["memory"]
        )
        assert differs, "Two profiles with different seeds have identical fingerprints"

    def test_clone_always_gets_new_seed(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        src = prof.create_profile({"name": "Source", "seed": 42})
        cloned = prof.clone_profile(src["id"], mode="config_only")
        assert cloned is not None
        assert cloned["id"] != src["id"]
        assert cloned["seed"] != src["seed"], "Clone must get a fresh seed"

    def test_clone_config_and_cookies_copies_cookies(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.cookies_store as cs
        src = prof.create_profile({"name": "Source"})
        cs.save_cookies(src["id"], [{"domain": ".test.com", "name": "x", "value": "42"}])
        cloned = prof.clone_profile(src["id"], mode="config_and_cookies")
        assert cloned is not None
        cloned_cookies = cs.load_cookies(cloned["id"])
        assert len(cloned_cookies) == 1
        assert cloned_cookies[0]["value"] == "42"

    def test_full_session_clone_copies_user_data(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.storage.xdg as xdg
        src = prof.create_profile({"name": "Source"})
        user_dir = xdg.chrome_profile_dir(src["id"]) / "user-data" / "Default"
        user_dir.mkdir(parents=True)
        (user_dir / "Preferences").write_text('{"foo":"bar"}', encoding="utf-8")
        cloned = prof.clone_profile(src["id"], mode="full_session")
        assert cloned is not None
        dst_prefs = xdg.chrome_profile_dir(cloned["id"]) / "user-data" / "Default" / "Preferences"
        assert dst_prefs.exists(), "user-data not copied in full_session clone"
        assert dst_prefs.read_text() == '{"foo":"bar"}'


# ============================================================================
# FINGERPRINT CONSISTENCY
# ============================================================================

class TestFingerprintConsistency:
    def test_client_hints_present(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        from multiprofile.fingerprint import build_fingerprint
        p = prof.create_profile({"name": "FP", "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"})
        fp = build_fingerprint(p)
        assert "ch_platform" in fp
        assert "architecture" in fp
        assert "bitness" in fp
        assert "ch_brands" in fp
        assert isinstance(fp["ch_brands"], list) and len(fp["ch_brands"]) == 3

    def test_windows_ua_gives_windows_platform(self):
        from multiprofile.fingerprint import build_fingerprint
        p = {"id": "t", "seed": 100, "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
        fp = build_fingerprint(p)
        assert fp["platform"] == "Win32"
        assert fp["ch_platform"] == "Windows"
        assert fp["touch"] == 0

    def test_mac_ua_gives_mac_platform(self):
        from multiprofile.fingerprint import build_fingerprint
        p = {"id": "t", "seed": 200, "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/131.0.0.0"}
        fp = build_fingerprint(p)
        assert fp["platform"] in ("MacIntel", "Mac")
        assert fp["ch_platform"] == "macOS"

    def test_android_ua_gives_mobile_profile(self):
        from multiprofile.fingerprint import build_fingerprint
        p = {"id": "t", "seed": 300, "userAgent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) Chrome/131.0.0.0 Mobile Safari/537.36"}
        fp = build_fingerprint(p)
        assert fp["touch"] > 0
        assert fp["isMobile"] is True
        assert fp["ch_platform"] == "Android"

    def test_gpu_not_software_for_windows_profile(self):
        from multiprofile.fingerprint import build_fingerprint
        p = {"id": "t", "seed": 400, "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"}
        fp = build_fingerprint(p)
        renderer = fp["gpu"]["renderer"]
        assert "SwiftShader" not in renderer, f"Windows profile should not have SwiftShader: {renderer}"
        assert "ANGLE" in renderer

    def test_media_device_ids_stable(self):
        from multiprofile.fingerprint import build_fingerprint
        p = {"id": "t", "seed": 77777, "userAgent": "Chrome/131.0.0.0"}
        fp1 = build_fingerprint(p)
        fp2 = build_fingerprint(p)
        assert fp1["mediaDeviceIds"] == fp2["mediaDeviceIds"]
        assert len(fp1["mediaDeviceIds"]["audioinput"]) == 64

    def test_different_seeds_give_different_device_ids(self):
        from multiprofile.fingerprint import build_fingerprint
        fp1 = build_fingerprint({"id": "a", "seed": 1111})
        fp2 = build_fingerprint({"id": "b", "seed": 9999})
        assert fp1["mediaDeviceIds"] != fp2["mediaDeviceIds"]


# ============================================================================
# BACKUP — PLAINTEXT
# ============================================================================

class TestPlaintextBackup:
    def test_create_backup_ok(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.backup as bk
        import multiprofile.audit as audit_mod
        monkeypatch.setattr(audit_mod, "audit_log", lambda *a, **k: None)
        p = prof.create_profile({"name": "BackupTest"})
        result = bk.create_backup(p["id"], include_user_data=False)
        assert result["ok"], result
        assert not result["encrypted"]
        assert Path(result["backupPath"]).exists()

    def test_backup_has_manifest_v2(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.backup as bk
        import multiprofile.audit as audit_mod
        import zipfile
        monkeypatch.setattr(audit_mod, "audit_log", lambda *a, **k: None)
        p = prof.create_profile({"name": "ManifestTest"})
        result = bk.create_backup(p["id"], include_user_data=False)
        with zipfile.ZipFile(result["backupPath"]) as zf:
            assert "manifest.json" in zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["version"] == "2.0"
        assert manifest["profileId"] == p["id"]
        assert "createdAt" in manifest
        assert manifest["encryption"]["algorithm"] == "none"
        assert "zip_sha256" in manifest["checksums"]
        assert "profileIds" in manifest
        assert "clientIds" in manifest

    def test_manifest_has_no_secrets(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.backup as bk
        import multiprofile.audit as audit_mod
        import zipfile
        monkeypatch.setattr(audit_mod, "audit_log", lambda *a, **k: None)
        # Profiles with credential-containing fields
        p = prof.create_profile({
            "name": "SecretsTest",
            "proxyPass": "TOPLEVEL_SECRET",
        })
        result = bk.create_backup(p["id"], include_user_data=False)
        with zipfile.ZipFile(result["backupPath"]) as zf:
            manifest_text = zf.read("manifest.json").decode()
        assert "TOPLEVEL_SECRET" not in manifest_text, "proxyPass leaked in manifest"

    def test_restore_plaintext_backup(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.backup as bk
        import multiprofile.audit as audit_mod
        monkeypatch.setattr(audit_mod, "audit_log", lambda *a, **k: None)
        p = prof.create_profile({"name": "RestoreMe", "notes": "original notes"})
        bk_result = bk.create_backup(p["id"], include_user_data=False)
        prof.update_profile(p["id"], {"notes": "corrupted notes"})
        restore_result = bk.restore_backup(backup_path=bk_result["backupPath"])
        assert restore_result["ok"], restore_result
        restored = prof.get_profile(p["id"])
        assert restored["notes"] == "original notes"

    def test_backup_roundtrip_cookies(self, tmp_path, monkeypatch):
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.backup as bk
        import multiprofile.audit as audit_mod
        import multiprofile.cookies_store as cs
        monkeypatch.setattr(audit_mod, "audit_log", lambda *a, **k: None)
        p = prof.create_profile({"name": "CookieBackup"})
        original_cookies = [
            {"domain": ".site.com", "name": "sess", "value": "secretsession"},
            {"domain": ".site.com", "name": "pref", "value": "dark_mode"},
        ]
        cs.save_cookies(p["id"], original_cookies)
        bk_result = bk.create_backup(p["id"], include_user_data=False)
        cs.save_cookies(p["id"], [])
        bk.restore_backup(backup_path=bk_result["backupPath"])
        restored_cookies = cs.load_cookies(p["id"])
        values = {c["value"] for c in restored_cookies}
        assert "secretsession" in values, "Session cookie not restored"
        assert "dark_mode" in values, "Preference cookie not restored"


# ============================================================================
# BACKUP — ENCRYPTED
# ============================================================================

class TestEncryptedBackup:
    def _has_cryptography(self):
        try:
            import cryptography  # noqa: F401
            return True
        except ImportError:
            return False

    def test_encrypted_backup_ok(self, tmp_path, monkeypatch):
        if not self._has_cryptography():
            pytest.skip("cryptography package not installed")
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.backup as bk
        import multiprofile.audit as audit_mod
        monkeypatch.setattr(audit_mod, "audit_log", lambda *a, **k: None)
        p = prof.create_profile({"name": "EncTest"})
        result = bk.create_backup(p["id"], include_user_data=False, passphrase="correct-horse")
        assert result["ok"], result
        assert result["encrypted"]
        assert result["backupPath"].endswith(".mp_enc")
        assert Path(result["backupPath"]).exists()
        # Plaintext zip must be removed
        assert not Path(result["backupPath"]).with_suffix(".zip").exists()

    def test_encrypted_backup_wrong_passphrase_fails(self, tmp_path, monkeypatch):
        if not self._has_cryptography():
            pytest.skip("cryptography package not installed")
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.backup as bk
        import multiprofile.audit as audit_mod
        monkeypatch.setattr(audit_mod, "audit_log", lambda *a, **k: None)
        p = prof.create_profile({"name": "WrongPass"})
        result = bk.create_backup(p["id"], include_user_data=False, passphrase="correct")
        restore = bk.restore_backup(backup_path=result["backupPath"], passphrase="wrong")
        assert not restore["ok"]
        assert "wrong passphrase" in restore["error"].lower() or "decryption failed" in restore["error"].lower()

    def test_encrypted_backup_no_passphrase_fails_cleanly(self, tmp_path, monkeypatch):
        if not self._has_cryptography():
            pytest.skip("cryptography package not installed")
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.backup as bk
        import multiprofile.audit as audit_mod
        monkeypatch.setattr(audit_mod, "audit_log", lambda *a, **k: None)
        p = prof.create_profile({"name": "NoPPass"})
        result = bk.create_backup(p["id"], include_user_data=False, passphrase="mypass")
        restore = bk.restore_backup(backup_path=result["backupPath"])  # no passphrase
        assert not restore["ok"]
        assert "passphrase" in restore["error"].lower() or "encrypted" in restore["error"].lower()

    def test_encrypted_backup_correct_passphrase_roundtrip(self, tmp_path, monkeypatch):
        if not self._has_cryptography():
            pytest.skip("cryptography package not installed")
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.backup as bk
        import multiprofile.audit as audit_mod
        import multiprofile.cookies_store as cs
        monkeypatch.setattr(audit_mod, "audit_log", lambda *a, **k: None)
        p = prof.create_profile({"name": "EncRoundtrip", "notes": "secret notes"})
        cs.save_cookies(p["id"], [{"domain": ".x.com", "name": "tok", "value": "encval"}])
        bk_result = bk.create_backup(p["id"], include_user_data=False, passphrase="strong-pass-123")
        prof.update_profile(p["id"], {"notes": "corrupted"})
        cs.save_cookies(p["id"], [])
        restore = bk.restore_backup(backup_path=bk_result["backupPath"], passphrase="strong-pass-123")
        assert restore["ok"], restore
        assert prof.get_profile(p["id"])["notes"] == "secret notes"
        assert any(c["value"] == "encval" for c in cs.load_cookies(p["id"]))

    def test_passphrase_not_in_backup_result(self, tmp_path, monkeypatch):
        if not self._has_cryptography():
            pytest.skip("cryptography package not installed")
        _setup_xdg(tmp_path, monkeypatch)
        import multiprofile.storage.profiles as prof
        import multiprofile.backup as bk
        import multiprofile.audit as audit_mod
        monkeypatch.setattr(audit_mod, "audit_log", lambda *a, **k: None)
        p = prof.create_profile({"name": "SecretPass"})
        result = bk.create_backup(p["id"], include_user_data=False, passphrase="t0ps3cr3t")
        assert "t0ps3cr3t" not in json.dumps(result), "Passphrase leaked in backup result!"


# ============================================================================
# SECURITY AUDIT
# ============================================================================

class TestSecurityAudit:
    def test_cdp_binds_localhost_only(self):
        src = Path("src/multiprofile/browser/launcher.py").read_text(encoding="utf-8")
        assert "--remote-debugging-address=127.0.0.1" in src

    def test_no_unconditional_disable_gpu(self):
        """--disable-gpu must not appear as an unconditional cmd.append outside software mode."""
        src = Path("src/multiprofile/browser/launcher.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.strip()
            if '"--disable-gpu"' in stripped and "cmd.append" in stripped:
                assert False, f"Unconditional --disable-gpu found: {line}"

    def test_disable_automation_controlled_flag(self):
        src = Path("src/multiprofile/browser/launcher.py").read_text(encoding="utf-8")
        assert "AutomationControlled" in src

    def test_proxy_password_not_in_chrome_flags(self):
        from multiprofile.network import NetworkConfig, NetworkMode, ProxyConfig, chrome_network_flags
        nc = NetworkConfig(
            mode=NetworkMode.HTTP_PROXY,
            proxy=ProxyConfig(scheme="http", host="proxy.test", port=8080,
                              username="user123", password="hunter2"),
        )
        flag_str = " ".join(chrome_network_flags(nc))
        assert "hunter2" not in flag_str
        assert "user123" not in flag_str

    def test_github_type_is_not_a_network_route(self):
        from multiprofile.network import NetworkConfig, NetworkMode
        nc = NetworkConfig.from_legacy_profile({"proxyType": "github", "customProxy": ""})
        assert nc.mode == NetworkMode.DIRECT

    def test_proxy_masked_str_hides_password(self):
        from multiprofile.network import ProxyConfig
        p = ProxyConfig(scheme="http", host="proxy.com", port=8080,
                        username="u", password="thepassword")
        masked = p.masked_str()
        assert "thepassword" not in masked
        assert "proxy.com" in masked

    def test_gateway_storage_password_masked(self, tmp_path, monkeypatch):
        import multiprofile.storage.gateways as gw_store
        import multiprofile.storage.xdg as xdg
        monkeypatch.setattr(xdg, "data_dir", lambda: tmp_path / "data")
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        gw = gw_store.create_gateway({
            "label": "Test", "host": "1.2.3.4", "port": 1080,
            "username": "u", "password": "SECRETPASS", "providerType": "custom"
        })
        assert "SECRETPASS" not in str(gw)
        loaded = gw_store.load_gateways(safe=True)
        assert all("SECRETPASS" not in str(g) for g in loaded)

    def test_no_console_log_in_evasion_js(self):
        src = Path("src/extension/evasion.js").read_text(encoding="utf-8")
        assert "Anti-Detect Defender Active" not in src


# ============================================================================
# WINDOW TILING (mathematical verification)
# ============================================================================

class TestWindowTiling:
    @pytest.mark.parametrize("n", [4, 9, 20])
    def test_grid_fills_the_work_area_below_the_title_bar_inset(self, n):
        """Rows span edge to edge and reach the bottom.

        The top TOP_CHROME_INSET strip is deliberately not covered: CDP
        reports the client area, so a window placed flush with the top of
        the work area gets pushed down by its own title bar and spills
        into the row below. The 1px frame gaps are not covered either.
        """
        from multiprofile.browser.work_area import WorkArea
        from multiprofile.browser.grid import GridCalculator, TOP_CHROME_INSET

        wa = WorkArea(x=0, y=0, w=1920, h=1080, source="test")
        calc = GridCalculator()
        ids = [f"p{i}" for i in range(n)]
        plan = calc.plan(ids, wa, gap=0, margin=0)
        rects = plan.rects()
        assert len(rects) == n

        rows: dict[int, list] = {}
        for s in plan.slots:
            rows.setdefault(s.index // plan.cols, []).append(s.rect)
        for row in rows.values():
            row.sort(key=lambda r: r.x)
            assert row[0].x == wa.x, f"{n} profiles: row does not start at the left edge"
            assert row[-1].x + row[-1].w == wa.x + wa.w, f"{n} profiles: row does not reach the right edge"

        top_row = rows[0]
        bottom_row = rows[max(rows)]
        assert top_row[0].y == wa.y + TOP_CHROME_INSET, f"{n} profiles: first row ignores the title-bar inset"
        assert bottom_row[0].y + bottom_row[0].h == wa.y + wa.h, f"{n} profiles: last row does not reach the bottom"

    @pytest.mark.parametrize("n", [4, 9, 20])
    def test_grid_no_overlap(self, n):
        from multiprofile.browser.work_area import WorkArea
        from multiprofile.browser.grid import GridCalculator
        wa = WorkArea(x=0, y=0, w=1920, h=1080, source="test")
        calc = GridCalculator()
        rects = calc.plan([f"p{i}" for i in range(n)], wa, gap=0, margin=0).rects()
        for i, a in enumerate(rects):
            for j, b in enumerate(rects):
                if i >= j:
                    continue
                overlap_x = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
                overlap_y = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
                assert overlap_x == 0 or overlap_y == 0, f"Overlap between {i} and {j}"

    @pytest.mark.parametrize("n", [4, 9, 20])
    def test_tile_idempotent(self, n):
        from multiprofile.browser.work_area import WorkArea
        from multiprofile.browser.grid import GridCalculator
        wa = WorkArea(x=0, y=0, w=1920, h=1080, source="test")
        calc = GridCalculator()
        ids = [f"p{i}" for i in range(n)]
        r1 = calc.plan(ids, wa, gap=0, margin=0).rects()
        r2 = calc.plan(ids, wa, gap=0, margin=0).rects()
        assert r1 == r2, f"Tile not idempotent for {n} profiles"
