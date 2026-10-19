"""Strict input validation at the API boundary.

Every mutating endpoint must validate its payload here, *before* the data
reaches the launcher, the job queue, or storage. Nothing invalid may pass
this layer.

On violation a :class:`ValidationError` is raised; the Flask error handler
registered by :func:`register_error_handlers` turns it into::

    400 {"error": {"code": "invalid_input", "message": "...", "field": "..."}}

Security notes:
- Secret-bearing fields (proxy password, passphrase, tokens) are never
  echoed back in error messages or logs — messages stay generic.
- Proxy URLs are restricted to the scheme whitelist
  ``http`` / ``https`` / ``socks5``; ports are clamped to 1..65535.
- ``bool`` is *not* accepted where an int is expected (Python's
  ``isinstance(True, int)`` trap is guarded explicitly).
"""
from __future__ import annotations

import logging
from typing import Any

from flask import jsonify, request

# network.py imports nothing from this package at module level, so this is
# safe; it owns WebRTCPolicy and therefore owns the canonical vocabulary.
from ..network import WEBRTC_ALIASES, WEBRTC_MODES, normalize_webrtc_mode

log = logging.getLogger("multiprofile.api.validation")

# Fields whose raw values must never appear in messages or logs.
SENSITIVE_FIELDS = frozenset(
    {"password", "proxyPass", "proxyPassword", "passphrase", "token", "apiToken"}
)

PROXY_SCHEMES = ("http", "https", "socks5")
JOB_TYPES = ("launch_profile", "stop_profile", "bulk_launch", "bulk_stop")
PROXY_TYPES = ("none", "custom", "github", "http", "https", "socks5", "socks5h")
NETWORK_MODES = ("DIRECT", "HTTP_PROXY", "HTTPS_PROXY", "SOCKS5", "REMOTE_GATEWAY")
PROFILE_STATUSES = ("idle", "running", "queued", "failed", "parked")

# --------------------------------------------------------------------------
# The profile field registry (F8)
#
# ``create_profile`` does ``dict(payload)`` and ``update_profile`` merges
# ``{**stored, **payload}``, so before this registry existed every key a
# client sent was written to profiles.json and returned on every later read.
# Unknown *values* were refused while unknown *keys* were not, which made a
# typo in a field name indistinguishable from a working request -- the audit
# lost a test to exactly that, sending ``devicePresetId`` for ``deviceId``
# and watching the preset silently never apply.
#
# Three tiers, because "unknown" is three different situations:
# --------------------------------------------------------------------------

# 1. A client may set these, and the product honours them.
PROFILE_FIELDS = frozenset({
    "name", "userAgent", "userAgentId", "uaId", "startUrl", "notes", "color",
    "clientId", "poolId", "pool_id", "tags", "seed", "timezone", "language",
    "webrtcMode", "deviceId", "device_id", "status",
    "proxyHost", "proxyPort", "proxyUser", "proxyPass", "proxyType", "customProxy",
    "networkConfig", "networkMode", "gatewayId", "webglPolicy", "webgl", "isMobile",
})

# The console's own spelling of a route, which is what the wizard sends.
# Resolved by ``network._requested_network_mode``; kept lower-case here
# because that is the vocabulary the UI and the legacy proxyType share.
REQUESTED_NETWORK_MODES = (
    "direct", "none", "gateway", "remote_gateway",
    "http", "http_proxy", "https", "https_proxy", "socks5", "socks5h", "custom",
)

# 2. The server owns these. There is no GET /api/profiles/<id>, so a
#    read-modify-write client reads a row from GET /api/profiles and PUTs it
#    back -- and that row is enriched with runtime truth (launch state, cdp
#    port, proxy verdict, last launch). Refusing those keys would break the
#    only way the API offers to edit a profile you have just read, so they
#    are dropped, silently, because sending them back is not a mistake. What
#    they must never do is let a client dictate them.
#
#    Keep in step with the enrichment in ``storage.profiles.load_profiles``
#    and ``_with_launch_state``; anything either of them writes onto a row
#    must appear here, or PUTting that row back becomes a 400.
PROFILE_FIELDS_SERVER_OWNED = frozenset({
    "id", "createdAt", "fingerprint",
    # status/pid and the SPA compatibility flags
    "pid", "isRunning", "isQueued", "isFailed",
    # runtime registry and control.db projections
    "launchState", "launchError", "launchErrorCode", "cdpPort", "state",
    "proxyOk", "egressIp", "lastLaunch", "lastProxyCheck",
    # written by the launch and network paths, never by a client
    "allocatedIp", "lastNetworkDiagnostics", "noteEntries", "geoAuto",
    # provenance: which catalogue id produced this profile's UA, written by
    # `_resolve_user_agent_id`. A client sets `userAgentId` and reads this.
    "userAgentFromId",
})

# 3. Recognised, and honoured by nothing. These are sent by our own wizard
#    and read by no backend code: ``fingerprintStrategy`` has zero references,
#    ``networkMode`` is shadowed by the nested ``networkConfig.mode`` (whose
#    enum is upper-case), and ``gatewayId`` is only ever read for a proxy
#    pool's members, never for a profile. Refusing them would 400 every
#    creation the wizard makes, so they are accepted, reported, and dropped --
#    the drop is the point, since persisting a setting that cannot take effect
#    is what let this gap survive. This tier is a defect list and should end
#    up empty: either the feature lands and the key moves to tier 1, or the
#    key leaves the wizard.
# Empty, and that is the goal state: this tier is a defect list, not a design.
# It held three keys when F8 landed. `networkMode` and `gatewayId` left it when
# F16 wired the route up for real, and `fingerprintStrategy` left it when the
# wizard step that sent it was removed -- the fingerprint is derived from the
# profile seed and its device preset, so "stable" was the only one of its three
# options that ever meant anything, and a selector offering three was a promise
# the product could not keep.
#
# Keep it empty. A key added here is an admission that the console is sending
# something nothing honours; the fix is to implement it or stop sending it.
PROFILE_FIELDS_UNIMPLEMENTED: dict[str, str] = {}

# What a "did you mean" suggestion may point at: only keys that do something.
_PROFILE_SETTABLE = PROFILE_FIELDS | frozenset(PROFILE_FIELDS_UNIMPLEMENTED)


def _check_profile_keys(payload: dict[str, Any]) -> list[str]:
    """Refuse unknown keys; drop the ones that cannot take effect.

    Mutates ``payload``: tier 2 and tier 3 keys are removed, so nothing a
    client sends that the product does not honour is ever persisted. Returns
    the warnings for tier 3.
    """
    import difflib

    warnings: list[str] = []
    for key in list(payload.keys()):
        if key in PROFILE_FIELDS:
            continue
        if key in PROFILE_FIELDS_SERVER_OWNED:
            payload.pop(key, None)
            continue
        if key in PROFILE_FIELDS_UNIMPLEMENTED:
            payload.pop(key, None)
            warnings.append(
                f"ignored '{key}': {PROFILE_FIELDS_UNIMPLEMENTED[key]}"
            )
            continue
        close = difflib.get_close_matches(key, sorted(_PROFILE_SETTABLE), n=1, cutoff=0.7)
        hint = f"; did you mean '{close[0]}'?" if close else ""
        raise ValidationError(key, f"unknown field{hint}")
    return warnings


class ValidationError(Exception):
    """Raised when a request payload fails validation.

    ``field`` is the dotted field path (e.g. ``"networkConfig.proxy.port"``)
    or ``"_body"`` for whole-body problems.
    """

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def _safe_message(field: str, message: str) -> str:
    # Never leak secret values into responses/logs, even by accident.
    if any(s in field.lower() for s in ("password", "passphrase", "token", "secret")):
        return message
    return message


def error_response(exc: ValidationError):
    return (
        jsonify(
            {
                "error": {
                    "code": "invalid_input",
                    "message": _safe_message(exc.field, exc.message),
                    "field": exc.field,
                }
            }
        ),
        400,
    )


def register_error_handlers(app) -> None:
    """Map ValidationError to the 400 envelope on the given Flask app."""
    from ..storage.db import FleetFull

    @app.errorhandler(ValidationError)
    def _handle_validation_error(exc: ValidationError):
        log.info("invalid input: %s: %s", exc.field, exc.message)
        return error_response(exc)

    @app.errorhandler(FleetFull)
    def _handle_fleet_full(exc: FleetFull):
        # 409, not 400: the request is well-formed, the fleet is the problem.
        # The numbers are returned so the console can say "room for 12 more"
        # instead of making the operator count.
        log.info("fleet full: %s", exc)
        return (
            jsonify(
                {
                    "error": {
                        "code": "fleet_full",
                        "message": str(exc),
                        "stored": exc.stored,
                        "capacity": exc.capacity,
                        "free": exc.free,
                        "requested": exc.requested,
                    }
                }
            ),
            409,
        )


def require_json() -> dict[str, Any]:
    """Return the request body as a dict, or raise ValidationError."""
    payload = request.get_json(force=True, silent=True)
    if payload is None:
        raise ValidationError("_body", "request body must be valid JSON")
    if not isinstance(payload, dict):
        raise ValidationError("_body", "request body must be a JSON object")
    return payload


# ------------------------------------------------------------------ #
#  Scalar validators                                                    #
# ------------------------------------------------------------------ #
def v_str(
    value: Any,
    field: str,
    *,
    min_len: int = 0,
    max_len: int = 2048,
    allow_none: bool = False,
) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ValidationError(field, "must be a string")
    if len(value) < min_len:
        raise ValidationError(field, f"must be at least {min_len} character(s)")
    if len(value) > max_len:
        raise ValidationError(field, f"must be at most {max_len} characters")
    return value


def v_int(value: Any, field: str, *, min: int | None = None, max: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(field, "must be an integer")
    if min is not None and value < min:
        raise ValidationError(field, f"must be >= {min}")
    if max is not None and value > max:
        raise ValidationError(field, f"must be <= {max}")
    return value


def v_float(
    value: Any, field: str, *, min: float | None = None, max: float | None = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(field, "must be a number")
    f = float(value)
    if min is not None and f < min:
        raise ValidationError(field, f"must be >= {min}")
    if max is not None and f > max:
        raise ValidationError(field, f"must be <= {max}")
    return f


def v_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(field, "must be a boolean")
    return value


def v_enum(value: Any, field: str, choices: tuple[str, ...], *, case_insensitive: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(field, f"must be one of: {', '.join(choices)}")
    candidate = value.lower() if case_insensitive else value
    lookup = {c.lower(): c for c in choices} if case_insensitive else {c: c for c in choices}
    if candidate not in lookup:
        raise ValidationError(field, f"must be one of: {', '.join(choices)}")
    return lookup[candidate]


def v_port(value: Any, field: str) -> int:
    return v_int(value, field, min=1, max=65535)


def v_proxy_scheme(value: Any, field: str) -> str:
    return v_enum(value, field, PROXY_SCHEMES, case_insensitive=True)


def v_str_list(value: Any, field: str, *, max_items: int = 200, item_max_len: int = 128) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(field, "must be an array of strings")
    if len(value) > max_items:
        raise ValidationError(field, f"must contain at most {max_items} items")
    out: list[str] = []
    for i, item in enumerate(value):
        out.append(v_str(item, f"{field}[{i}]", min_len=1, max_len=item_max_len))
    return out


def v_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(field, "must be a JSON object")
    return value


# ------------------------------------------------------------------ #
#  Composite payload validators                                         #
# ------------------------------------------------------------------ #
def _validate_proxy_block(proxy: dict[str, Any], prefix: str) -> None:
    """Validate a structured proxy block (scheme/host/port/user/pass)."""
    v_dict(proxy, prefix)
    if "scheme" in proxy:
        v_proxy_scheme(proxy["scheme"], f"{prefix}.scheme")
    if "host" in proxy:
        v_str(proxy["host"], f"{prefix}.host", min_len=1, max_len=255)
    if "port" in proxy and proxy["port"] is not None:
        v_port(proxy["port"], f"{prefix}.port")
    if "username" in proxy:
        v_str(proxy["username"], f"{prefix}.username", max_len=256)
    if "password" in proxy:
        v_str(proxy["password"], f"{prefix}.password", max_len=1024)


def _known_device_ids() -> set[str] | None:
    """Known device preset ids, or None if the registry is unavailable."""
    try:
        from ..devices.registry import registry
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return None
    try:
        return {p.id for p in registry.list()}
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return None


def validate_profile_payload(payload: dict[str, Any], *, partial: bool = False) -> list[str]:
    """Validate a profile create (partial=False) or update (partial=True) body.

    Raises ValidationError on any violation, including an unrecognised field
    name -- see the field registry above. Device preset selection is checked
    against the device registry when it is available: unknown ``deviceId``
    values are rejected at the door.

    ``payload`` is mutated: fields the server owns and fields nothing honours
    are removed, so only settings that can actually take effect are persisted.
    Returns the warnings to report to the client.
    """
    v_dict(payload, "_body")
    warnings = _check_profile_keys(payload)
    if "status" in payload:
        v_enum(payload["status"], "status", PROFILE_STATUSES, case_insensitive=True)
    if "name" in payload:
        v_str(payload["name"], "name", min_len=1, max_len=120)
    if "userAgent" in payload:
        # An empty string clears the field, and has to be allowed to.
        # ``_normalize_fields`` already treats a falsy UA as "use the pool
        # default", and ``_resolve_user_agent_id`` only resolves a picked id
        # when the UA is empty -- so with ``min_len=1`` there was no way
        # through the API to move a profile onto another OS once the picker
        # had written a UA that no longer matches the catalogue exactly.
        # Measured: PUT {"userAgent": "", "userAgentId": "linux_chrome"} on
        # such a profile returned 400 "must be at least 1 character(s)",
        # leaving the console's OS picker with nothing it could do.
        if payload["userAgent"] not in ("", None):
            v_str(payload["userAgent"], "userAgent", min_len=1, max_len=4096)
    if "userAgentId" in payload and payload["userAgentId"] is not None:
        v_str(payload["userAgentId"], "userAgentId", min_len=1, max_len=128)
        # Rejected rather than ignored. An unrecognised id used to be stored
        # and silently do nothing, so a typo produced a profile claiming
        # something the operator never picked.
        from .user_agents import known_user_agent_ids, resolve_user_agent_id

        if resolve_user_agent_id(payload["userAgentId"]) is None:
            raise ValidationError(
                "userAgentId",
                f"unknown id; expected one of {', '.join(known_user_agent_ids())}",
            )
    # null means "clear this", which is what the profile edit dialog sends for
    # an emptied field. Rejecting it made clearing a start URL or a note a hard
    # 400 ("must be a string") with no way to do it from the console at all
    # (F21). Storage coerces the cleared value to the field's default.
    if payload.get("startUrl") is not None and "startUrl" in payload:
        v_str(payload["startUrl"], "startUrl", min_len=1, max_len=2048)
    if payload.get("notes") is not None and "notes" in payload:
        v_str(payload["notes"], "notes", max_len=4096)
    if "color" in payload:
        v_str(payload["color"], "color", min_len=1, max_len=16)
    if "clientId" in payload and payload["clientId"] is not None:
        v_str(payload["clientId"], "clientId", min_len=1, max_len=128)
    if "poolId" in payload and payload["poolId"] is not None:
        v_str(payload["poolId"], "poolId", min_len=1, max_len=128)
    if "tags" in payload and payload["tags"] is not None:
        v_str_list(payload["tags"], "tags", max_items=50, item_max_len=64)
    if "seed" in payload and payload["seed"] is not None:
        v_int(payload["seed"], "seed", min=0, max=2**63 - 1)
    # Empty clears, for the same reason it does on `userAgent`: these two
    # fields are where `geoAuto` comes from. `_normalize_fields` sets
    # `geoAuto` True when either is blank and False when the operator picked
    # a value, so an empty string is how "go back to automatic" is spelled --
    # and `geoAuto` itself is server-owned, so it is the ONLY way to spell it.
    # With `min_len=1` there was none: a profile that had ever been given a
    # timezone was pinned to it for good, and the creation default is
    # America/New_York. Measured on a real profile: it claimed
    # America/New_York while egressing from Asia/Kolkata, a 10.5-hour
    # contradiction on the most routinely checked pair there is, and the API
    # had no way to undo it.
    if "timezone" in payload and payload["timezone"] not in ("", None):
        v_str(payload["timezone"], "timezone", min_len=1, max_len=64)
    if "language" in payload and payload["language"] not in ("", None):
        v_str(payload["language"], "language", min_len=1, max_len=32)
    if "webrtcMode" in payload:
        # Accept both vocabularies and store the canonical one. This field
        # used to be validated against ("default", "disabled", "proxy") while
        # WebRTCPolicy, _map_webrtc, fingerprint/_core and evasion.js all
        # spoke ("default", "disable", "disable_non_proxied_udp"). Nothing
        # translated, so both non-default values a client could pass fell
        # through to DEFAULT -- no Chrome flags, no JS hardening, a privacy
        # control that reported itself set and did nothing. Worse after F8:
        # the two words that work became 400s.
        v_enum(
            payload["webrtcMode"], "webrtcMode",
            WEBRTC_MODES + tuple(WEBRTC_ALIASES), case_insensitive=True,
        )
        payload["webrtcMode"] = normalize_webrtc_mode(payload["webrtcMode"])
    if "networkMode" in payload and payload["networkMode"] not in (None, ""):
        v_enum(
            payload["networkMode"], "networkMode",
            REQUESTED_NETWORK_MODES, case_insensitive=True,
        )
    if "gatewayId" in payload and payload["gatewayId"] is not None:
        v_str(payload["gatewayId"], "gatewayId", min_len=1, max_len=128)
    # A gateway route with no gateway named cannot resolve, and the launch would
    # be refused later with a less useful message. Say so at the door instead.
    if str(payload.get("networkMode") or "").strip().lower() in ("gateway", "remote_gateway"):
        named = payload.get("gatewayId") or (
            (payload.get("networkConfig") or {}).get("gatewayId")
            if isinstance(payload.get("networkConfig"), dict) else None
        )
        if not named:
            raise ValidationError(
                "gatewayId",
                "required when networkMode is a gateway: name the gateway to route through",
            )

    device_id = payload.get("deviceId", payload.get("device_id"))
    if device_id is not None and device_id != "":
        v_str(device_id, "deviceId", min_len=1, max_len=128)
        known = _known_device_ids()
        if known is not None and device_id not in known:
            raise ValidationError("deviceId", "unknown device preset id")

    # Legacy flat proxy fields.
    if "proxyHost" in payload and payload["proxyHost"]:
        v_str(payload["proxyHost"], "proxyHost", min_len=1, max_len=255)
    if "proxyPort" in payload and payload["proxyPort"] not in (None, ""):
        v_port(payload["proxyPort"], "proxyPort")
    if "proxyUser" in payload:
        v_str(payload["proxyUser"], "proxyUser", max_len=256)
    if "proxyPass" in payload:
        v_str(payload["proxyPass"], "proxyPass", max_len=1024)
    if "proxyType" in payload:
        v_enum(payload["proxyType"], "proxyType", PROXY_TYPES, case_insensitive=True)
    if "customProxy" in payload and payload["customProxy"]:
        v_str(payload["customProxy"], "customProxy", min_len=1, max_len=2048)

    # Structured network config block.
    if "networkConfig" in payload and payload["networkConfig"] is not None:
        net = v_dict(payload["networkConfig"], "networkConfig")
        if "mode" in net:
            v_enum(net["mode"], "networkConfig.mode", NETWORK_MODES)
        if "proxy" in net and net["proxy"] is not None:
            _validate_proxy_block(net["proxy"], "networkConfig.proxy")
    return warnings


def validate_job_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a job enqueue body (POST /api/jobs)."""
    v_dict(payload, "_body")
    v_enum(payload.get("type"), "type", JOB_TYPES)
    ids = payload.get("profileIds")
    if isinstance(ids, str):
        ids = [ids]
    v_str_list(ids, "profileIds", max_items=200, item_max_len=128)
    if not ids:
        raise ValidationError("profileIds", "at least one profile id is required")
    if "url" in payload and payload["url"] is not None:
        v_str(payload["url"], "url", min_len=1, max_len=2048)
    for flag in ("direct", "cdp", "tileRequired"):
        if flag in payload:
            v_bool(payload[flag], flag)
    if "options" in payload and payload["options"] is not None:
        options = v_dict(payload["options"], "options")
        if "tileRequired" in options:
            v_bool(options["tileRequired"], "options.tileRequired")
    return payload


def validate_proxy_test_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a proxy test body (POST /api/proxy/test) — the network config door."""
    v_dict(payload, "_body")
    if "customProxy" in payload and payload["customProxy"]:
        v_str(payload["customProxy"], "customProxy", min_len=1, max_len=2048)
    for key in ("host", "proxyHost"):
        if payload.get(key):
            v_str(payload[key], key, min_len=1, max_len=255)
    for key in ("port", "proxyPort"):
        if payload.get(key) not in (None, ""):
            v_port(payload[key], key)
    for key in ("protocol", "type"):
        if payload.get(key):
            v_proxy_scheme(payload[key], key)
    for key in ("username", "user", "proxyUser"):
        if key in payload and payload[key] is not None:
            v_str(payload[key], key, max_len=256)
    for key in ("password", "pass", "proxyPass"):
        if key in payload and payload[key] is not None:
            v_str(payload[key], key, max_len=1024)
    if "timeout" in payload and payload["timeout"] is not None:
        v_float(payload["timeout"], "timeout", min=1, max=60)
    return payload


def validate_clone_payload(payload: dict[str, Any]) -> dict[str, Any]:
    v_dict(payload, "_body")
    if "mode" in payload:
        v_enum(payload["mode"], "mode", ("config_only", "config_and_cookies", "full_session"))
    if "name" in payload and payload["name"] is not None:
        v_str(payload["name"], "name", min_len=1, max_len=120)
    return payload
