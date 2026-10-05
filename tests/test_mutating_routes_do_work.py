"""F49: a mutating endpoint must not report success for work it never did.

`POST /api/profiles/switch-all-direct` was, in full:

    @app.post("/api/profiles/switch-all-direct")
    def switch_all_direct():
        return jsonify({"ok": True})

Measured against two profiles carrying a SOCKS5 proxy, it returned
``200 {"ok": true}`` and left both proxies in place. In an anti-detect
product a control named "switch everything to direct egress" is a routing
control, and one that claims to have moved every profile and moved none is
the F16 shape at its worst: the operator's belief about where their traffic
leaves from is now wrong, and nothing anywhere says so.

It is removed rather than implemented. Nothing called it -- not the console,
not the built static bundle, not the tests, not `docs/API.md` -- and a
one-click bulk switch of every profile onto the host's own IP is not a
control this product should grow by accident. If it is ever wanted it should
be built deliberately, with a confirmation and an audit-log entry.

The first test below is the regression. The second is the general policy, so
that the next endpoint of this shape fails a test instead of waiting to be
measured: a route that mutates must call something.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

SERVER = pathlib.Path(__file__).resolve().parents[1] / "src/multiprofile/api/server.py"

MUTATING = ("post", "put", "patch", "delete")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


def _client():
    from multiprofile.api.server import create_app

    return create_app().test_client()


def _make_proxied(client, name: str, host: str) -> str:
    r = client.post("/api/profiles", json={
        "name": name, "proxyType": "socks5", "proxyHost": host, "proxyPort": 1080,
    })
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json()["profile"]["id"]


def _proxy_of(client, profile_id: str) -> str | None:
    body = client.get(f"/api/profiles/{profile_id}").get_json()
    return (body.get("profile", body) or {}).get("proxyHost")


# --------------------------------------------------------------------------
# 1. The finding itself
# --------------------------------------------------------------------------

def test_no_endpoint_claims_to_have_switched_every_profile_to_direct():
    """Either the route does the work, or it does not answer at all.

    A 404 is a better answer than ``{"ok": true}``: a client that gets a 404
    knows nothing happened, and a client that gets ``ok: true`` believes
    every profile is now on the host's own IP.
    """
    client = _client()
    a = _make_proxied(client, "proxied-a", "10.0.0.1")
    b = _make_proxied(client, "proxied-b", "10.0.0.2")

    r = client.post("/api/profiles/switch-all-direct")
    assert r.status_code != 200, (
        "the endpoint answers 200; if it has been reimplemented it must "
        f"actually clear the proxies -- got {r.get_json()}"
    )

    # And whatever answered, the proxies must be untouched by it.
    assert _proxy_of(client, a) == "10.0.0.1"
    assert _proxy_of(client, b) == "10.0.0.2"


# --------------------------------------------------------------------------
# 2. The policy, so the next one fails here instead of in the field
# --------------------------------------------------------------------------

def _route_handlers():
    tree = ast.parse(SERVER.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            verb = dec.func.attr
            if verb not in MUTATING:
                continue
            paths = [a.value for a in dec.args if isinstance(a, ast.Constant)]
            yield node, verb.upper(), paths[0] if paths else "?"


def _calls_something(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) for n in ast.walk(node))


def test_every_mutating_route_actually_does_something():
    """A POST/PUT/PATCH/DELETE handler whose whole body is a literal success
    is a control that reaches nothing.

    The check is deliberately crude -- "the body contains a call other than
    the one that builds the response" -- because the defect it catches is
    crude. `jsonify({...})` on its own is not work; anything that reads,
    writes, validates or delegates is.
    """
    offenders = []
    for node, verb, path in _route_handlers():
        body = [s for s in node.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            continue  # more than one statement: it does something
        value = body[0].value
        if value is None:
            continue
        # Strip the response wrapper: jsonify(...) / make_response(...).
        inner = value
        if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                and inner.func.id in ("jsonify", "make_response")):
            inner = ast.Tuple(elts=list(inner.args), ctx=ast.Load())
        elif isinstance(inner, ast.Tuple) and inner.elts:
            first = inner.elts[0]
            if (isinstance(first, ast.Call) and isinstance(first.func, ast.Name)
                    and first.func.id in ("jsonify", "make_response")):
                inner = ast.Tuple(elts=list(first.args), ctx=ast.Load())
        if not _calls_something(inner):
            offenders.append(f"{verb} {path} (line {node.lineno}) in {node.name}")

    assert not offenders, (
        "these mutating routes return a literal response without doing any "
        "work, which is F49's shape -- implement them or remove them:\n  "
        + "\n  ".join(offenders)
    )
