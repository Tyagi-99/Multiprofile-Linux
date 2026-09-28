"""DeviceRegistry — lookup over all device presets."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .preset import DevicePreset
from .presets_android import ANDROID_PRESETS
from .presets_desktop import DESKTOP_PRESETS
from .presets_ios import IOS_PRESETS
from .presets_tablet import TABLET_PRESETS


class DeviceRegistry:
    """Registry of all DevicePreset entries."""

    def __init__(self, presets: Optional[List[DevicePreset]] = None) -> None:
        all_presets = list(presets) if presets is not None else (
            ANDROID_PRESETS + IOS_PRESETS + TABLET_PRESETS + DESKTOP_PRESETS
        )
        self._by_id: Dict[str, DevicePreset] = {}
        for p in all_presets:
            if p.id in self._by_id:
                raise ValueError(f"duplicate device id: {p.id}")
            self._by_id[p.id] = p
        self._presets = list(self._by_id.values())

    def get(self, device_id: str) -> Optional[DevicePreset]:
        return self._by_id.get(device_id)

    def list(self, category: Optional[str] = None, platform: Optional[str] = None) -> List[DevicePreset]:
        result = self._presets
        if category is not None:
            result = [p for p in result if p.category == category]
        if platform is not None:
            result = [p for p in result if p.platform == platform]
        return list(result)

    def count(self) -> int:
        return len(self._presets)

    def ids(self) -> List[str]:
        return [p.id for p in self._presets]


# Shared default registry instance.
registry = DeviceRegistry()


def preset_view(preset: DevicePreset) -> Dict[str, Any]:
    """The preset as an operator should see it, versions realigned to the engine.

    The catalogue literals say Chrome 131; a launch claims the installed
    engine's major (F5). Serving the raw literals would mean the device page
    and the wire describe two different browsers, which is exactly the
    contradiction this finding is about — so the read path and the launch
    path are rewritten by the same function.

    ``browser_version_resolved`` is the major actually claimed, or None when
    the engine could not be asked. None is not a pass: it says the claim was
    never checked.
    """
    from ..engine import align_brands, align_ua_string, detect_engine_version

    engine = detect_engine_version()
    d = preset.to_dict()
    d["browser_version_resolved"] = engine.major
    d["engine_version"] = engine.to_dict()
    if not engine.known:
        return d

    d["user_agent"] = align_ua_string(d.get("user_agent") or "", engine)
    hints = dict(d.get("client_hints") or {})
    if "brands" in hints:
        hints["brands"] = align_brands(hints.get("brands"), engine)
    if "full_version_list" in hints:
        hints["full_version_list"] = align_brands(hints.get("full_version_list"), engine, full=True)
    d["client_hints"] = hints
    return d
