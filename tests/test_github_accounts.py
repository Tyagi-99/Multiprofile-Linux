"""GitHub Cloud Account PAT connect/store/verify (no live GitHub in unit tests)."""
from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch

import pytest

from multiprofile.storage import github_accounts as gh


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200, scopes: str = "repo, workflow"):
        self._payload = payload
        self.status = status
        self.headers = {"X-OAuth-Scopes": scopes}

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_mask_token_keeps_prefix_and_last4():
    assert gh.mask_token("ghp_abcdefghijklmnop").endswith("mnop")
    assert "abcdefgh" not in gh.mask_token("ghp_abcdefghijklmnop")


def test_add_account_stores_public_shape_without_raw_token(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fake = _FakeResp({"login": "Tyagi-99", "name": "Tyagi", "avatar_url": "https://avatars.example/u"})
    with patch("urllib.request.urlopen", return_value=fake):
        result = gh.add_account("ghp_secretTOKEN1234", label="Channel 2", is_default=True)
    assert result["ok"] is True
    acc = result["account"]
    assert acc["username"] == "Tyagi-99"
    assert acc["label"] == "Channel 2"
    assert acc["is_default"] is True
    assert acc["token_masked"].endswith("1234")
    assert "token" not in acc or acc.get("token") in (None, "")
    listed = gh.list_accounts()
    assert len(listed) == 1
    assert listed[0]["username"] == "Tyagi-99"
    assert "secretTOKEN" not in json.dumps(listed)
    stored = json.loads(gh.github_accounts_path().read_text())
    assert stored[0]["token"] == "ghp_secretTOKEN1234"


def test_invalid_token_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    import urllib.error

    def boom(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.github.com/user", 401, "Unauthorized", hdrs=None, fp=BytesIO())

    with patch("urllib.request.urlopen", side_effect=boom):
        result = gh.add_account("ghp_bad")
    assert result["ok"] is False
    assert "token" in result["error"].lower() or "401" in result["error"]
    assert gh.list_accounts() == []


def test_missing_workflow_scope_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fake = _FakeResp({"login": "x"}, scopes="repo")
    with patch("urllib.request.urlopen", return_value=fake):
        result = gh.add_account("ghp_noscope")
    assert result["ok"] is False
    assert "workflow" in result["error"].lower()


def test_set_default_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fake_a = _FakeResp({"login": "aaa", "name": "A", "avatar_url": ""})
    fake_b = _FakeResp({"login": "bbb", "name": "B", "avatar_url": ""})
    with patch("urllib.request.urlopen", return_value=fake_a):
        a = gh.add_account("ghp_aaaaAAAAaaaa", is_default=True)["account"]
    with patch("urllib.request.urlopen", return_value=fake_b):
        b = gh.add_account("ghp_bbbbBBBBbbbb", is_default=False)["account"]
    assert gh.set_default(b["id"]) is True
    listed = {x["username"]: x for x in gh.list_accounts()}
    assert listed["bbb"]["is_default"] is True
    assert listed["aaa"]["is_default"] is False
    assert gh.delete_account(a["id"]) is True
    left = gh.list_accounts()
    assert len(left) == 1 and left[0]["username"] == "bbb"


def test_verify_existing_account(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fake = _FakeResp({"login": "Tyagi-99", "name": "T", "avatar_url": ""}, scopes="repo, workflow")
    with patch("urllib.request.urlopen", return_value=fake):
        acc = gh.add_account("ghp_okTOKEN0001")["account"]
        checked = gh.verify_account(acc["id"])
    assert checked["success"] is True
    assert checked["username"] == "Tyagi-99"
    assert "repo" in (checked.get("scopes") or "")


def test_flask_connect_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    from multiprofile.api.server import create_app

    fake = _FakeResp({"login": "Tyagi-99", "name": "Tyagi", "avatar_url": "https://avatars.example/u"})
    app = create_app()
    client = app.test_client()
    with patch("urllib.request.urlopen", return_value=fake):
        res = client.post("/api/github-accounts", json={"token": "ghp_liveTOKEN99", "label": "Main", "is_default": True})
    assert res.status_code == 201
    body = res.get_json()
    assert body["ok"] is True
    assert body["account"]["username"] == "Tyagi-99"
    listed = client.get("/api/github-accounts").get_json()
    assert isinstance(listed, list) and listed[0]["username"] == "Tyagi-99"
    vid = listed[0]["id"]
    with patch("urllib.request.urlopen", return_value=fake):
        v = client.post(f"/api/github-accounts/{vid}/verify").get_json()
    assert v["success"] is True
