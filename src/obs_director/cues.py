"""Bounded director cues: validate everything, then execute with honest receipts.

The model chooses semantic actions. This local runtime owns timing and ordering.
OBS operations are delegated to ProductionService's typed, verified controls.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
import threading
import time
from typing import Any, TYPE_CHECKING

from .transport import _local_path

if TYPE_CHECKING:
    from .controls import ProductionService


MAX_STEPS = 20
MAX_WAIT_SECONDS = 15.0
MAX_PANIC_BYTES = 128 * 1024
CUE_ACTIONS = frozenset({
    "scene_select", "source_visibility", "source_transform", "audio_mute",
    "audio_volume", "source_settings", "media_action", "media_seek",
})


class CueValidationError(ValueError):
    """A cue violates its bounded schema or initial-state precondition."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wait(cancel: threading.Event, seconds: float) -> bool:
    """Sleep up to `seconds`; return True as soon as the run is cancelled."""
    return cancel.wait(seconds)


def load_panic_cue() -> dict | None:
    """Load the optional trusted local panic cue. No configuration means none."""
    configured = os.environ.get("OBS_MCP_PANIC_CUE")
    if not configured:
        return None
    try:
        path = _local_path(configured)
        with path.open("rb") as source:
            raw = source.read(MAX_PANIC_BYTES + 1)
        if len(raw) > MAX_PANIC_BYTES:
            raise ValueError()
        document = json.loads(raw)
        if (not isinstance(document, dict) or set(document) != {"schema", "cue"}
                or document["schema"] != "obs.panic-cue.v1"):
            raise ValueError()
        return document["cue"]
    except (OSError, ValueError, TypeError, RecursionError):
        raise CueValidationError("Local panic cue could not be loaded; check its schema and size") from None


def _label(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise CueValidationError(f"{field} must contain 1 to 200 characters")
    if any(ord(character) < 32 for character in value):
        raise CueValidationError(f"{field} cannot contain control characters")
    return value.strip()


def _schema(cue: dict) -> dict[str, Any]:
    """Reject the entire syntactic plan before opening an OBS connection."""
    if not isinstance(cue, dict) or set(cue) - {"name", "expected_scene", "steps"}:
        raise CueValidationError("Cue must contain only name, expected_scene and steps")
    if "name" not in cue or "steps" not in cue:
        raise CueValidationError("Cue requires name and steps")
    name = _label(cue["name"], "name")
    expected = _label(cue["expected_scene"], "expected_scene") if "expected_scene" in cue else None
    steps = cue["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise CueValidationError(f"Cue must contain 1 to {MAX_STEPS} steps")
    normalized = []
    waits = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not isinstance(step.get("action"), str):
            raise CueValidationError(f"Step {index} requires an action")
        action = step["action"]
        if action == "wait":
            if set(step) != {"action", "seconds"}:
                raise CueValidationError(f"Wait step {index} accepts only action and seconds")
            seconds = step["seconds"]
            if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                    or not 0 <= seconds <= MAX_WAIT_SECONDS or not math.isfinite(seconds)):
                raise CueValidationError(f"Wait step {index} must be between 0 and 15 seconds")
            waits.append(seconds)
            normalized.append({"action": "wait", "seconds": seconds})
            continue
        if action not in CUE_ACTIONS:
            raise CueValidationError(f"Step {index} uses an unsupported cue action")
        if set(step) != {"action", "parameters"} or not isinstance(step["parameters"], dict):
            raise CueValidationError(f"Step {index} requires only action and parameters")
        parameters = deepcopy(step["parameters"])
        if action == "source_settings":
            # Cues can change text, never arbitrary source URLs/files/settings.
            settings = parameters.get("settings")
            if (not isinstance(settings, dict) or set(settings) != {"text"}
                    or not isinstance(settings["text"], str) or len(settings["text"]) > 4096):
                raise CueValidationError(f"Step {index} accepts text-only source settings (up to 4096 characters)")
        normalized.append({"action": action, "parameters": parameters})
    total_wait = math.fsum(waits)
    if total_wait > MAX_WAIT_SECONDS:
        raise CueValidationError("Combined cue waits cannot exceed 15 seconds")
    return {"name": name, "expected_scene": expected, "steps": normalized,
            "total_wait_seconds": total_wait}


class CueService:
    """Serialize cues on the same lock used by individual production controls.

    Validation checks all actions against the current OBS state before the first
    mutation. OBS itself remains independently controllable: execution rechecks
    live state, but validation is not a transaction or an external-control lock.
    """

    def __init__(self, production: ProductionService, panic_cue: dict | None = None):
        self.production = production
        configured = load_panic_cue() if panic_cue is None else panic_cue
        self._panic_cue = None if configured is None else deepcopy(configured)
        if self._panic_cue is not None:
            _schema(self._panic_cue)
        # Guards only the pointer to the running cue's cancel event; never held
        # across OBS work, so cancel_cue stays instant while production.lock is busy.
        self._run_guard = threading.Lock()
        self._active_cancel: threading.Event | None = None

    def cancel_cue(self) -> dict:
        """Ask the running cue to stop. Never takes production.lock.

        Only the cue running right now is affected; a cancel with nothing running
        is a no-op and cannot leak into a later cue.
        """
        with self._run_guard:
            active = self._active_cancel
            if active is not None:
                active.set()
        return {"cancel_requested": active is not None, "cue_running": active is not None}

    def panic(self, dry_run: bool = True, allow_live: bool = False) -> dict:
        """Cancel the running cue, then run the trusted local panic cue, if any.

        Dry run (default) changes and cancels nothing. It previews the panic cue
        against OBS when the production lock is free, and statically otherwise.
        """
        if type(dry_run) is not bool or type(allow_live) is not bool:
            raise CueValidationError("dry_run and allow_live must be booleans")
        result = {"dry_run": dry_run, "allow_live": allow_live,
                  "panic_cue_registered": self._panic_cue is not None, "checked_at": _now()}
        if not dry_run:
            result["cancel"] = self.cancel_cue()
            if self._panic_cue is None:
                return {**result, "state": "cancelled_only", "panic_cue": None}
            return {**result, "state": "completed",
                    "panic_cue": self.run_cue(self._panic_cue, dry_run=False, allow_live=allow_live)}
        with self._run_guard:
            running = self._active_cancel is not None
        result["cancel"] = {"cancel_requested": False, "cue_running": running}
        if self._panic_cue is None:
            return {**result, "state": "preview", "panic_cue": None}
        if self.production.lock.acquire(blocking=False):
            try:
                preview = self.run_cue(self._panic_cue, dry_run=True, allow_live=allow_live)
            finally:
                self.production.lock.release()
        else:
            normalized = _schema(self._panic_cue)
            preview = {"name": normalized["name"], "state": "preview", "validated": False,
                       "step_count": len(normalized["steps"]),
                       "reason": "production busy; panic cue not checked against OBS"}
        return {**result, "state": "preview", "panic_cue": preview}

    @staticmethod
    def _expected_scene(client: Any, expected: str | None) -> None:
        if expected is None:
            return
        client.require_capabilities(["GetCurrentProgramScene"])
        scene = client.request("GetCurrentProgramScene").get("currentProgramSceneName")
        if scene != expected:
            raise CueValidationError("Current program scene does not match expected_scene")

    def _prepare(self, cue: dict, client: Any, allow_live: bool) -> tuple[list[dict], list[dict]]:
        plans = []
        descriptions = []
        for step in cue["steps"]:
            action = step["action"]
            if action == "wait":
                plans.append(step)
                descriptions.append({"action": "wait", "parameters": {"seconds": step["seconds"]}})
                continue
            plan = self.production.validate_action(action, step["parameters"], client, allow_live=allow_live)
            if plan.get("readback_expected") is False:
                raise CueValidationError("Cue action cannot verify its result; use the individual control instead")
            plans.append({"action": action, "parameters": plan["parameters"]})
            descriptions.append(self.production.describe_action(action, plan["parameters"]))
        self._expected_scene(client, cue["expected_scene"])
        return plans, descriptions

    def validate_cue(self, cue: dict) -> dict:
        """Validate a complete cue against OBS without writes or mutations.

        Active output changes are not opted into by validation. Use run_cue with
        dry_run=True and allow_live=True to preview an explicitly live cue.
        """
        normalized = _schema(cue)
        with self.production.lock, self.production.connection() as client:
            _, descriptions = self._prepare(normalized, client, allow_live=False)
        return {"valid": True, "name": normalized["name"], "expected_scene": normalized["expected_scene"],
                "step_count": len(descriptions), "total_wait_seconds": normalized["total_wait_seconds"],
                "steps": descriptions, "checked_at": _now()}

    def run_cue(self, cue: dict, dry_run: bool = True, allow_live: bool = False) -> dict:
        """Preview by default; stop on the first failed action or readback.

        A failure receipt preserves completed steps and marks later steps skipped.
        No rollback is attempted. cancel_cue() ends the run in a wait or between steps:
        the step in progress is "cancelled" and later steps "skipped". Only explicit waits consume the 15-second wait
        budget; network latency and OBS processing can extend total elapsed time.
        """
        if type(dry_run) is not bool or type(allow_live) is not bool:
            raise CueValidationError("dry_run and allow_live must be booleans")
        normalized = _schema(cue)
        with self.production.lock, self.production.connection() as client:
            plans, descriptions = self._prepare(normalized, client, allow_live)
            started_at, started = _now(), time.monotonic()
            receipts = [{"index": index, **description, "state": "skipped", "reason": "dry_run" if dry_run else "not_run",
                         "started_at": None, "finished_at": started_at, "duration_ms": 0.0}
                        for index, description in enumerate(descriptions, 1)]
            result = {"name": normalized["name"], "dry_run": dry_run, "allow_live": allow_live,
                      "expected_scene": normalized["expected_scene"], "validated": True,
                      "total_wait_seconds": normalized["total_wait_seconds"],
                      "state": "preview" if dry_run else "completed", "started_at": started_at,
                      "steps": receipts, "rollback_attempted": False,
                      "verification_scope": "OBS control readback; rendered content has not been visually verified"}
            if not dry_run:
                cancel = threading.Event()
                with self._run_guard:
                    self._active_cancel = cancel
                try:
                    self._execute(plans, receipts, result, client, normalized["expected_scene"], allow_live, cancel)
                finally:
                    with self._run_guard:
                        if self._active_cancel is cancel:
                            self._active_cancel = None
            result.update(finished_at=_now(), duration_ms=round((time.monotonic() - started) * 1000, 3))
            return result


    def _execute(self, plans: list[dict], receipts: list[dict], result: dict, client: Any,
                 expected_scene: str | None, allow_live: bool, cancel: threading.Event) -> None:
        """Run validated plans in order, stopping on failure or cancellation."""
        checked_expected = False
        for index, plan in enumerate(plans):
            receipt = receipts[index]
            if cancel.is_set():
                # Cancelled between steps: nothing in progress, this and later steps never ran.
                result["state"] = "cancelled"
                for later in receipts[index:]:
                    later.update(reason="cancelled", finished_at=_now())
                return
            receipt["started_at"] = _now()
            step_started = time.monotonic()
            phase = "wait" if plan["action"] == "wait" else "precondition"
            try:
                if plan["action"] == "wait":
                    if _wait(cancel, plan["seconds"]):
                        receipt.update(state="cancelled", reason="cancelled")
                        result["state"] = "cancelled"
                else:
                    if not checked_expected:
                        self._expected_scene(client, expected_scene)
                        checked_expected = True
                    phase = "apply"
                    applied = self.production.apply_action(plan["action"], plan["parameters"],
                                                           client, allow_live=allow_live)
                    if applied.get("applied") is not True or applied.get("verified") is not True:
                        receipt.update(state="failed", reason="readback_not_verified",
                                       failure_phase="readback", may_have_changed=True)
                        result["state"] = "failed"
                    else:
                        receipt.update(applied=True, verified=True)
                if receipt["state"] not in ("failed", "cancelled"):
                    receipt.update(state="completed", reason=None)
            except Exception:
                # Exceptions may contain source URLs or settings. Preserve
                # the failure phase, never an arbitrary upstream message.
                receipt.update(state="failed", reason="action_failed", failure_phase=phase,
                               may_have_changed=phase == "apply")
                result["state"] = "failed"
            finally:
                receipt["finished_at"] = _now()
                receipt["duration_ms"] = round((time.monotonic() - step_started) * 1000, 3)
            if receipt["state"] in ("failed", "cancelled"):
                key, reason = (("failed_step", "prior_step_failed") if receipt["state"] == "failed"
                               else ("cancelled_step", "cancelled"))
                for later in receipts[index + 1:]:
                    later.update(reason=reason, finished_at=_now())
                result[key] = index + 1
                return
