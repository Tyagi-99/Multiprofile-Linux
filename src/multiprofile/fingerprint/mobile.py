"""Mobile profile configuration: pure, data-driven CDP plan builder.

``MobileProfileConfig`` describes one mobile identity (UA, client hints,
viewport, touch, hardware, locale, timezone) and compiles it into the exact
CDP commands needed to apply it to a live target:

  1. Emulation.setDeviceMetricsOverride{width,height,deviceScaleFactor,mobile}
  2. Emulation.setTouchEmulationEnabled{enabled}
  3. Network.setUserAgentOverride{userAgent, userAgentMetadata{...}}

NOTE on maxTouchPoints: CDP has no command that sets
``navigator.maxTouchPoints`` directly.  Touch behaviour comes from
``Emulation.setTouchEmulationEnabled`` plus UA / client-hint coherence
(mobile UA + Sec-CH-UA-Mobile=?1 + userAgentMetadata.mobile=true), which
is what this plan produces.  The browser then reports the emulated touch
points.

Pure/data-driven by design: no browser handles, no I/O — launcher wiring
is a one-liner over ``apply_cdp_plan`` with a CDP ``send`` function.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


def _cdp_brand(entry: Any) -> dict[str, str]:
    """Normalize one client-hint brand entry to CDP's {"brand", "version"}."""
    b = entry if isinstance(entry, dict) else {}
    return {
        "brand": str(b.get("brand", "")),
        "version": str(b.get("version") or b.get("fullVersion") or ""),
    }


@dataclass
class MobileProfileConfig:
    user_agent: str
    ch_brands: list
    ch_full_version_list: list
    ch_platform: str
    ch_platform_version: str
    ch_architecture: str
    ch_model: str
    ch_mobile: bool
    viewport_width: int
    viewport_height: int
    device_scale_factor: float
    mobile: bool
    touch_enabled: bool
    max_touch_points: int
    hardware_concurrency: int | None
    device_memory_gb: float | None
    locale: str
    timezone: str | None

    def user_agent_metadata(self) -> dict[str, Any]:
        """CDP Network.setUserAgentOverride.userAgentMetadata shape.

        Device presets store client-hint brand lists with the
        Sec-CH-UA header key ``fullVersion``; CDP's UserAgentMetadata
        schema requires ``version`` in both ``brands[]`` and
        ``fullVersionList[]`` (sending ``fullVersion`` verbatim makes
        Chrome reject the command with "Invalid parameters"). Normalize
        both spellings to the CDP shape here.
        """
        return {
            "brands": [_cdp_brand(b) for b in self.ch_brands],
            "fullVersionList": [_cdp_brand(b) for b in self.ch_full_version_list],
            "platform": self.ch_platform,
            "platformVersion": self.ch_platform_version,
            "architecture": self.ch_architecture,
            "model": self.ch_model,
            "mobile": self.ch_mobile,
        }

    def to_cdp_plan(self) -> list[dict[str, Any]]:
        """Compile this config into the exact CDP command plan.

        Emits EXACTLY three commands, in application order:
          1. Emulation.setDeviceMetricsOverride
          2. Emulation.setTouchEmulationEnabled
          3. Network.setUserAgentOverride
        """
        return [
            {
                "method": "Emulation.setDeviceMetricsOverride",
                "params": {
                    "width": self.viewport_width,
                    "height": self.viewport_height,
                    "deviceScaleFactor": self.device_scale_factor,
                    "mobile": self.mobile,
                },
            },
            {
                "method": "Emulation.setTouchEmulationEnabled",
                "params": {"enabled": self.touch_enabled},
            },
            {
                "method": "Network.setUserAgentOverride",
                "params": {
                    "userAgent": self.user_agent,
                    "userAgentMetadata": self.user_agent_metadata(),
                },
            },
        ]

    @classmethod
    def from_preset_dict(cls, d: dict[str, Any]) -> "MobileProfileConfig":
        """Build from a device-preset dict.

        Consumes keys: user_agent, client_hints{brands, full_version_list,
        platform, platform_version, architecture, model, mobile},
        viewport{width, height}, device_scale_factor, mobile, touch,
        max_touch_points, hardware_concurrency, device_memory_gb,
        language, timezone.
        """
        ch = d.get("client_hints") or {}
        vp = d.get("viewport") or {}
        touch = d.get("touch")
        return cls(
            user_agent=d.get("user_agent") or "",
            ch_brands=list(ch.get("brands") or []),
            ch_full_version_list=list(ch.get("full_version_list") or []),
            ch_platform=ch.get("platform") or "",
            ch_platform_version=ch.get("platform_version") or "",
            ch_architecture=ch.get("architecture") or "",
            ch_model=ch.get("model") or "",
            ch_mobile=bool(ch.get("mobile", False)),
            viewport_width=int(vp.get("width") or 0),
            viewport_height=int(vp.get("height") or 0),
            device_scale_factor=float(d.get("device_scale_factor") or 1.0),
            mobile=bool(d.get("mobile", False)),
            touch_enabled=bool(touch if touch is not None else False),
            max_touch_points=int(d.get("max_touch_points") or 0),
            hardware_concurrency=(
                int(d["hardware_concurrency"])
                if d.get("hardware_concurrency") is not None else None
            ),
            device_memory_gb=(
                float(d["device_memory_gb"])
                if d.get("device_memory_gb") is not None else None
            ),
            locale=d.get("language") or "en-US",
            timezone=d.get("timezone"),
        )


def apply_cdp_plan(
    send: Callable[[str, dict[str, Any]], dict[str, Any]],
    plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply a CDP plan through ``send(method, params) -> dict``.

    Returns per-step results ``[{"method", "ok", "result"|"error"}]``.
    A failing step is recorded (not raised) so partial application is
    visible to the caller.
    """
    results: list[dict[str, Any]] = []
    for step in plan:
        method = step.get("method", "")
        params = step.get("params") or {}
        try:
            result = send(method, params)
            # A None response (send failed without raising, e.g. CDP
            # unreachable) or an error payload is NOT success: callers use
            # "ok" to report "3/3 steps applied" and must not claim that.
            ok = result is not None and not (
                isinstance(result, dict) and result.get("error")
            )
            entry: dict[str, Any] = {"method": method, "ok": ok}
            if ok:
                entry["result"] = result
            else:
                entry["error"] = (
                    result.get("error") if isinstance(result, dict) else "no CDP response"
                )
            results.append(entry)
        except Exception as e:  # noqa: BLE001 - record, don't hide
            results.append({"method": method, "ok": False, "error": str(e)})
    return results
