"""Device preset system: curated device definitions with honest capability matrices."""
from .preset import (
    ANDROID,
    DESKTOP,
    IOS,
    IPADOS,
    LINUX,
    MACOS,
    MOBILE,
    PARTIAL,
    SUPPORTED,
    TABLET,
    UNAVAILABLE,
    WINDOWS,
    DevicePreset,
)
from .registry import DeviceRegistry, registry
from .validator import DevicePresetValidator, find_chromium_binary
from .routes import register_devices_routes

__all__ = [
    "ANDROID",
    "DESKTOP",
    "IOS",
    "IPADOS",
    "LINUX",
    "MACOS",
    "MOBILE",
    "PARTIAL",
    "SUPPORTED",
    "TABLET",
    "UNAVAILABLE",
    "WINDOWS",
    "DevicePreset",
    "DeviceRegistry",
    "DevicePresetValidator",
    "find_chromium_binary",
    "register_devices_routes",
    "registry",
]
