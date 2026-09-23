"""DeviceRegistry — lookup over all device presets."""
from __future__ import annotations

from typing import Dict, List, Optional

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
