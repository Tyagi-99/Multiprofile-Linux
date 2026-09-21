"""WindowLayoutManager — sole orchestrator for tile geometry + CDP apply."""
from __future__ import annotations

import logging
from typing import Any

from ..config import get_settings
from .cdp_window import CdpError, CdpWindowController
from .grid import GridCalculator, Rect, SlotPlan, validate_equal_sizes, validate_no_overlap, validate_within_work_area
from .runtime_state import LaunchState, clear_runtime, get_runtime, list_runtimes, set_launch_state, upsert_runtime
from .work_area import WorkArea, WorkAreaError, WorkAreaProvider

log = logging.getLogger("multiprofile.browser.layout_manager")


class WindowLayoutManager:
    """Owns SlotPlan decisions and CDP positioning. Launcher/jobs must not compute geometry."""

    def __init__(
        self,
        work_area_provider: WorkAreaProvider | None = None,
        grid: GridCalculator | None = None,
    ):
        self._work = work_area_provider or WorkAreaProvider()
        self._grid = grid or GridCalculator()

    def detect_work_area(self) -> WorkArea:
        return self._work.detect()

    def build_plan(self, profile_ids: list[str]) -> SlotPlan:
        """Fail-closed plan builder (raises WorkAreaError / ValueError)."""
        area = self.detect_work_area()
        return self._grid.plan(list(profile_ids), area)

    def best_effort_plan(self, profile_ids: list[str]) -> tuple[SlotPlan | None, dict[str, Any]]:
        try:
            plan = self.build_plan(profile_ids)
            return plan, {"ok": True, "error": None, "workArea": plan.work_area.to_dict()}
        except WorkAreaError as e:
            return None, {
                "ok": False,
                "error": "work_area_unavailable",
                "message": str(e),
                "reasons": e.reasons,
            }
        except ValueError as e:
            return None, {"ok": False, "error": "grid_unavailable", "message": str(e)}

    def apply_plan(self, plan: SlotPlan) -> dict[str, Any]:
        """Apply outer bounds via CDP for each slot. Does not kill browsers on failure."""
        settings = get_settings()
        results: list[dict[str, Any]] = []
        for slot in plan.slots:
            pid = slot.profile_id
            rt = get_runtime(pid)
            entry: dict[str, Any] = {
                "profileId": pid,
                "slotIndex": slot.index,
                "desired": slot.rect.to_dict(),
                "ok": False,
                "error": None,
                "message": None,
                "actual": None,
                "windowId": None,
                "targetId": None,
            }
            if rt is None or not rt.cdp_port:
                entry["error"] = "cdp_unreachable"
                entry["message"] = "no CDP port in runtime registry"
                set_launch_state(pid, LaunchState.CDP_FAILED, error=entry["message"], error_code=entry["error"])
                results.append(entry)
                continue
            try:
                with CdpWindowController(rt.cdp_port) as cdp:
                    set_launch_state(pid, LaunchState.CDP_READY, cdp_port=rt.cdp_port)
                    target = cdp.pick_page_target()
                    entry["targetId"] = target.target_id
                    window_id, _ = cdp.get_window_id_for_target(target.target_id)
                    entry["windowId"] = window_id
                    set_launch_state(
                        pid,
                        LaunchState.WINDOW_FOUND,
                        target_id=target.target_id,
                        window_id=window_id,
                        slot_index=slot.index,
                        desired_rect=slot.rect.to_dict(),
                    )
                    set_launch_state(pid, LaunchState.POSITIONING)
                    verify = cdp.apply_and_verify(
                        window_id,
                        slot.rect,
                        tolerance=settings.window_tile_tolerance_px,
                        retries=settings.window_tile_max_retries,
                        settle_ms=settings.window_tile_settle_ms,
                    )
                    entry["actual"] = verify.get("actual")
                    entry["attempts"] = verify.get("attempts")
                    if verify.get("ok"):
                        entry["ok"] = True
                        set_launch_state(
                            pid,
                            LaunchState.VERIFIED,
                            actual_rect=verify.get("actual"),
                            error=None,
                            error_code=None,
                        )
                        set_launch_state(pid, LaunchState.RUNNING)
                    else:
                        entry["error"] = verify.get("error") or "verify_failed"
                        entry["message"] = verify.get("message")
                        st = (
                            LaunchState.POSITION_FAILED
                            if entry["error"] in {"cdp_rpc_error", "cdp_window_bounds_unsupported"}
                            else LaunchState.VERIFY_FAILED
                        )
                        set_launch_state(
                            pid,
                            st,
                            error=entry["message"],
                            error_code=entry["error"],
                            actual_rect=verify.get("actual"),
                        )
            except CdpError as e:
                entry["error"] = e.code
                entry["message"] = str(e)
                st_map = {
                    "cdp_unreachable": LaunchState.CDP_FAILED,
                    "cdp_not_localhost": LaunchState.CDP_FAILED,
                    "no_page_target": LaunchState.WINDOW_NOT_FOUND,
                    "window_not_found": LaunchState.WINDOW_NOT_FOUND,
                    "cdp_window_bounds_unsupported": LaunchState.POSITION_FAILED,
                }
                set_launch_state(
                    pid,
                    st_map.get(e.code, LaunchState.POSITION_FAILED),
                    error=str(e),
                    error_code=e.code,
                )
            except Exception as e:
                entry["error"] = "position_failed"
                entry["message"] = str(e)
                set_launch_state(pid, LaunchState.POSITION_FAILED, error=str(e), error_code="position_failed")
                log.exception("tile apply failed for %s", pid)
            results.append(entry)

        actual_rects: list[Rect] = []
        for r in results:
            if r.get("ok") and r.get("actual"):
                a = r["actual"]
                actual_rects.append(Rect(a["x"], a["y"], a["w"], a["h"]))
        validation = {
            "valid": True,
            "overlaps": [],
            "offscreen": [],
            "unequalSize": False,
        }
        if actual_rects:
            ov = validate_no_overlap(actual_rects)
            wi = validate_within_work_area(actual_rects, plan.work_area)
            eq = validate_equal_sizes(actual_rects)
            validation = {
                "valid": ov["valid"] and wi["valid"] and eq,
                "overlaps": ov["overlaps"],
                "offscreen": wi["offscreen"],
                "unequalSize": not eq,
            }

        ok = all(r.get("ok") for r in results) if results else True
        return {
            "ok": ok and validation["valid"],
            "workArea": plan.work_area.to_dict(),
            "grid": {
                "cols": plan.cols,
                "rows": plan.rows,
                "gap": plan.gap,
                "margin": plan.margin,
                "cellW": plan.slots[0].rect.w if plan.slots else 0,
                "cellH": plan.slots[0].rect.h if plan.slots else 0,
            },
            "results": results,
            "validation": validation,
        }

    def tile_running(self, profile_ids: list[str] | None = None) -> dict[str, Any]:
        """Idempotent tile of running profiles that have CDP ports. Fail-closed on work area."""
        if profile_ids is None:
            ids = [rt.profile_id for rt in list_runtimes() if rt.cdp_port and rt.pid]
        else:
            ids = list(profile_ids)
        if not ids:
            return {
                "ok": False,
                "error": "no_running_profiles",
                "message": "no running profiles with CDP metadata",
                "results": [],
            }
        try:
            plan = self.build_plan(ids)
        except WorkAreaError as e:
            return {
                "ok": False,
                "error": "work_area_unavailable",
                "message": str(e),
                "reasons": e.reasons,
                "results": [],
            }
        except ValueError as e:
            return {"ok": False, "error": "grid_unavailable", "message": str(e), "results": []}
        return self.apply_plan(plan)

    def layout_snapshot(self) -> dict[str, Any]:
        try:
            area = self.detect_work_area()
            area_d = area.to_dict()
            area_err = None
        except WorkAreaError as e:
            area_d = None
            area_err = {"error": "work_area_unavailable", "message": str(e), "reasons": e.reasons}
        profiles = []
        for rt in list_runtimes():
            profiles.append(rt.to_dict())
        return {"workArea": area_d, "workAreaError": area_err, "profiles": profiles}


_default_manager: WindowLayoutManager | None = None


def get_layout_manager() -> WindowLayoutManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = WindowLayoutManager()
    return _default_manager
