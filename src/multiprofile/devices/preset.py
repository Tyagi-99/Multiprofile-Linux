"""DevicePreset dataclass for Multiprofile-Linux.

A DevicePreset captures everything a profile needs to emulate a real device:
UA string, Client Hints, viewport/screen geometry, input capabilities, and an
honest capability matrix describing what a Linux Chromium host can and cannot
reproduce about the original hardware.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Capability statuses — PARTIAL/UNAVAILABLE with documented reasons beat fake SUPPORTED.
SUPPORTED = "SUPPORTED"
PARTIAL = "PARTIAL"
UNAVAILABLE = "UNAVAILABLE"

# Categories
DESKTOP = "DESKTOP"
MOBILE = "MOBILE"
TABLET = "TABLET"

# Platforms
WINDOWS = "Windows"
LINUX = "Linux"
MACOS = "macOS-class"
ANDROID = "Android"
IOS = "iOS-class"
IPADOS = "iPadOS-class"

RUNTIME_LINUX_CHROMIUM = "linux-chromium"


@dataclass
class DevicePreset:
    """A single device preset."""

    id: str
    name: str
    manufacturer: str
    model: str
    category: str  # DESKTOP | MOBILE | TABLET
    platform: str  # Windows | Linux | macOS-class | Android | iOS-class | iPadOS-class
    browser_family: str  # e.g. "Chrome"
    # "match-engine": the claimed Chrome version is taken from the installed
    # binary at launch, and the literals below are only the template it is
    # rewritten from (F5). "pinned:<major>" would freeze the claim, which is
    # how every preset here came to advertise Chrome 131 on a 151 engine.
    browser_version_policy: str  # "match-engine" | "pinned:<major>" | "latest-stable"
    user_agent: str
    client_hints: Dict[str, Any] = field(
        default_factory=lambda: {
            "brands": [],
            "full_version_list": [],
            "platform": "",
            "platform_version": "",
            "architecture": "",
            "model": "",
            "mobile": False,
        }
    )
    viewport: Dict[str, int] = field(default_factory=lambda: {"width": 0, "height": 0})
    screen: Dict[str, int] = field(default_factory=lambda: {"width": 0, "height": 0})
    device_scale_factor: float = 1.0
    mobile: bool = False
    touch: bool = False
    max_touch_points: int = 0
    hardware_concurrency: Optional[int] = None
    device_memory_gb: Optional[float] = None
    language_defaults: Dict[str, Any] = field(default_factory=dict)
    timezone_policy: str = "auto"
    webgl_policy: str = "real"
    media_capabilities: Dict[str, Any] = field(default_factory=dict)
    supported_runtime: str = RUNTIME_LINUX_CHROMIUM
    validation_status: str = "PENDING"
    capability_matrix: Dict[str, Dict[str, str]] = field(default_factory=dict)
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Full serializable representation (used by GET /api/devices/<id>)."""
        return asdict(self)

    def to_mobile_config_dict(self) -> Dict[str, Any]:
        """Plain-dict config consumed by the profile/launch pipeline.

        Exact keys (contract with the consuming worker — keep it a dict,
        do not import their package):
            user_agent, client_hints{brands, full_version_list, platform,
            platform_version, architecture, model, mobile},
            viewport{width,height}, device_scale_factor, mobile, touch,
            max_touch_points, hardware_concurrency, device_memory_gb,
            language, timezone.
        """
        hints = self.client_hints or {}
        return {
            "user_agent": self.user_agent,
            "client_hints": {
                "brands": hints.get("brands", []),
                "full_version_list": hints.get("full_version_list", []),
                "platform": hints.get("platform", ""),
                "platform_version": hints.get("platform_version", ""),
                "architecture": hints.get("architecture", ""),
                "model": hints.get("model", ""),
                "mobile": bool(hints.get("mobile", False)),
            },
            "viewport": {
                "width": self.viewport.get("width", 0),
                "height": self.viewport.get("height", 0),
            },
            "device_scale_factor": self.device_scale_factor,
            "mobile": self.mobile,
            "touch": self.touch,
            "max_touch_points": self.max_touch_points,
            "hardware_concurrency": self.hardware_concurrency,
            "device_memory_gb": self.device_memory_gb,
            "language": (self.language_defaults or {}).get("language", "en-US"),
            "timezone": self.timezone_policy,
        }
