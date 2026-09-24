"""One window per profile.

Chrome can come up with more windows than we asked for: session restore
reopens the previous run's windows beside the ``--new-window`` we spawn.
The grid gives each profile exactly one slot and ``layout_manager`` moves
exactly one page target, so those extras are never positioned. They sit
wherever the compositor put them, they overlap the tiled windows, and on
screen they read as "more profiles than I launched".

This module decides which window survives and which page targets are
closed to collapse the rest. Two rules govern it:

  * Planning is pure. ``plan_reap`` takes window groups and returns a
    plan. It does not talk to Chrome, so the policy is unit-testable.
  * Closing is fail-closed and scoped to one profile's own browser.
    ``chrome_owns_profile_dir`` must confirm the pid's ``--user-data-dir``
    resolves under ``chrome-profiles/<profile_id>/`` before any target is
    closed. We never signal a process, and we never touch a Chrome that
    is not ours -- the operator's personal browser included.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..storage.xdg import chrome_profile_dir, data_dir

log = logging.getLogger("multiprofile.browser.window_reaper")


@dataclass(frozen=True)
class WindowGroup:
    """The page targets Chrome reports for one browser window."""

    window_id: int
    target_ids: tuple[str, ...]
    urls: tuple[str, ...] = ()

    @property
    def tab_count(self) -> int:
        return len(self.target_ids)


@dataclass(frozen=True)
class ReapPlan:
    """Which window stays, which page targets get closed."""

    keep_window_id: int | None
    keep_target_id: str | None
    close_target_ids: tuple[str, ...]
    close_window_ids: tuple[int, ...]
    reason: str

    @property
    def is_noop(self) -> bool:
        return not self.close_target_ids

    def to_dict(self) -> dict:
        return {
            "keepWindowId": self.keep_window_id,
            "keepTargetId": self.keep_target_id,
            "closeTargetIds": list(self.close_target_ids),
            "closeWindowIds": list(self.close_window_ids),
            "reason": self.reason,
        }


def group_windows(pairs: list[tuple[str, int, str]]) -> list[WindowGroup]:
    """Group ``(target_id, window_id, url)`` triples into windows.

    Order is stable: windows by first appearance, targets within a window
    in the order Chrome listed them.
    """
    order: list[int] = []
    by_window: dict[int, list[tuple[str, str]]] = {}
    for target_id, window_id, url in pairs:
        if not target_id:
            continue
        wid = int(window_id)
        if wid not in by_window:
            by_window[wid] = []
            order.append(wid)
        by_window[wid].append((target_id, url or ""))
    return [
        WindowGroup(
            window_id=wid,
            target_ids=tuple(t for t, _ in by_window[wid]),
            urls=tuple(u for _, u in by_window[wid]),
        )
        for wid in order
    ]


def plan_reap(groups: list[WindowGroup], preferred_target_id: str | None = None) -> ReapPlan:
    """Collapse ``groups`` to a single window.

    The survivor is the window holding the most tabs, because that is the
    operator's real work -- session restore brings back a loaded window
    while our own ``--new-window`` usually holds a single start page.
    Closing the fuller window to keep a blank one would throw away more
    than it tidies. Ties break toward the window holding
    ``preferred_target_id`` (the target the tiler would have moved), then
    toward the lowest window id so repeated runs agree.

    One window, or none, is already correct: the returned plan closes
    nothing.
    """
    live = [g for g in groups if g.target_ids]
    if len(live) <= 1:
        keep = live[0] if live else None
        return ReapPlan(
            keep_window_id=keep.window_id if keep else None,
            keep_target_id=_keep_target(keep, preferred_target_id) if keep else None,
            close_target_ids=(),
            close_window_ids=(),
            reason="single_window" if keep else "no_windows",
        )

    def rank(g: WindowGroup) -> tuple[int, int, int]:
        holds_preferred = 1 if preferred_target_id in g.target_ids else 0
        # Sorted ascending, so negate what we want to maximise.
        return (-g.tab_count, -holds_preferred, g.window_id)

    keep = sorted(live, key=rank)[0]
    extras = [g for g in live if g.window_id != keep.window_id]
    close_targets = tuple(t for g in extras for t in g.target_ids)
    return ReapPlan(
        keep_window_id=keep.window_id,
        keep_target_id=_keep_target(keep, preferred_target_id),
        close_target_ids=close_targets,
        close_window_ids=tuple(g.window_id for g in extras),
        reason=f"kept window {keep.window_id} with {keep.tab_count} tab(s), closing {len(extras)} extra window(s)",
    )


def _keep_target(group: WindowGroup, preferred_target_id: str | None) -> str | None:
    if preferred_target_id and preferred_target_id in group.target_ids:
        return preferred_target_id
    return group.target_ids[0] if group.target_ids else None


def chrome_owns_profile_dir(pid: int | None, profile_id: str) -> tuple[bool, str]:
    """True when ``pid`` is a Chrome we launched for ``profile_id``.

    Fail-closed: anything we cannot read or cannot prove is a refusal.
    The pid is only ever used as evidence -- no signal is sent here --
    but nothing gets closed until this says yes, so a stray CDP port
    cannot be used to shut windows in the operator's personal Chrome.
    """
    if not pid or not profile_id:
        return False, "no pid or profile id"
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, ValueError) as e:
        return False, f"cannot read /proc/{pid}/cmdline: {e}"
    if not raw:
        return False, f"/proc/{pid}/cmdline is empty"

    args = [a for a in raw.decode("utf-8", "replace").split("\0") if a]
    flag = next((a for a in args if a.startswith("--user-data-dir=")), None)
    if flag is None:
        return False, "chrome has no --user-data-dir"

    try:
        used = Path(flag.split("=", 1)[1]).expanduser().resolve()
        owned = chrome_profile_dir(profile_id).resolve()
        root = (data_dir() / "chrome-profiles").resolve()
    except (OSError, ValueError) as e:
        return False, f"cannot resolve user-data-dir: {e}"

    if not used.is_relative_to(root):
        return False, f"user-data-dir {used} is outside {root}"
    if not used.is_relative_to(owned):
        return False, f"user-data-dir {used} does not belong to profile {profile_id}"
    return True, f"{used} is owned by {profile_id}"
