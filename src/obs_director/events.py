"""Map bounded evidence events to trusted local cues, with process-local dedupe."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from typing import Any

from .cues import CueService, _schema
from .transport import _local_path


MAX_ROUTES = 16
MAX_ROUTE_BYTES = 128 * 1024
EVENT_KEYS = {"schema", "event_id", "event_type", "occurred_at", "payload"}
PAYLOAD_LIMITS = {"title": 80, "summary": 240, "reference": 96}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}\Z")


class EventValidationError(ValueError):
    """An event or a trusted route violates the bounded event contract."""


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise EventValidationError(f"{field} must be a bounded identifier")
    return value


def _event(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != EVENT_KEYS:
        raise EventValidationError("Event requires only schema, event_id, event_type, occurred_at and payload")
    if value["schema"] != "obs.event.v1":
        raise EventValidationError("Unsupported event schema")
    event_id = _identifier(value["event_id"], "event_id")
    event_type = _identifier(value["event_type"], "event_type")
    occurred = value["occurred_at"]
    if not isinstance(occurred, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", occurred):
        raise EventValidationError("occurred_at must be a UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(occurred.replace("Z", "+00:00"))
    except ValueError:
        raise EventValidationError("occurred_at is not a valid UTC timestamp") from None
    payload = value["payload"]
    if not isinstance(payload, dict) or set(payload) != set(PAYLOAD_LIMITS):
        raise EventValidationError("payload requires only title, summary and reference")
    for key, limit in PAYLOAD_LIMITS.items():
        text = payload[key]
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            raise EventValidationError(f"payload {key} must contain 1 to {limit} characters")
        if any(ord(character) < 32 or 127 <= ord(character) < 160 for character in text):
            raise EventValidationError("Event text cannot contain control characters")
    return {"schema": value["schema"], "event_id": event_id, "event_type": event_type,
            "occurred_at": occurred, "payload": dict(payload)}


def _registry(value: dict) -> dict:
    if not isinstance(value, dict) or len(value) > MAX_ROUTES:
        raise EventValidationError("Registry must contain at most 16 routes")
    registered = {}
    for event_type, route in value.items():
        _identifier(event_type, "Registered event type")
        if not isinstance(route, dict) or set(route) - {"cue", "text_step"} or "cue" not in route:
            raise EventValidationError("Registered route accepts only cue and optional text_step")
        try:
            _schema(route["cue"])
        except (ValueError, TypeError, OverflowError):
            raise EventValidationError("Registered cue has an invalid bounded schema") from None
        text_step = route.get("text_step")
        if text_step is not None:
            if type(text_step) is not int or not 0 <= text_step < len(route["cue"]["steps"]):
                raise EventValidationError("text_step must name a registered cue step by zero-based index")
            step = route["cue"]["steps"][text_step]
            if step["action"] != "source_settings" or set(step["parameters"].get("settings", {})) != {"text"}:
                raise EventValidationError("text_step must select a text-only source_settings action")
        registered[event_type] = deepcopy(route)
    return registered


def load_event_registry() -> dict:
    """Load optional trusted local routes. No configuration means no routes."""
    configured = os.environ.get("OBS_MCP_EVENT_ROUTES")
    if not configured:
        return {}
    try:
        path = _local_path(configured)
        with path.open("rb") as source:
            raw = source.read(MAX_ROUTE_BYTES + 1)
        if len(raw) > MAX_ROUTE_BYTES:
            raise ValueError()
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {"schema", "routes"} or document["schema"] != "obs.event-routes.v1":
            raise ValueError()
        return _registry(document["routes"])
    except (OSError, ValueError, TypeError, RecursionError):
        raise EventValidationError("Local event routes could not be loaded; check their schema and size") from None


def build_demo_registry(chart_item_id: int, evidence_item_id: int, replay_item_id: int,
                        scene_name: str = "Director Demo") -> dict:
    """Build one explicit synthetic showcase route after its OBS items exist."""
    if any(type(item) is not int or not 0 <= item <= 2**31 - 1
           for item in (chart_item_id, evidence_item_id, replay_item_id)):
        raise EventValidationError("Demo item IDs must be non-negative integers")
    if len({chart_item_id, evidence_item_id, replay_item_id}) != 3:
        raise EventValidationError("Demo item IDs must identify three separate scene items")

    def action(name: str, **parameters: Any) -> dict:
        return {"action": name, "parameters": parameters}

    def visible(item_id: int, enabled: bool) -> dict:
        return action("source_visibility", scene_name=scene_name, scene_item_id=item_id, enabled=enabled)

    return _registry({"evidence.ready": {"text_step": 0, "cue": {
        "name": "Evidence card, spotlight and replay", "expected_scene": scene_name,
        "steps": [
            action("source_settings", source_name="Demo Evidence", settings={"text": "Synthetic evidence"}),
            visible(evidence_item_id, True),
            {"action": "wait", "seconds": 2},
            action("source_transform", scene_name=scene_name, scene_item_id=chart_item_id,
                   transform={"positionX": -48, "positionY": -65, "scaleX": 1.12, "scaleY": 1.12}),
            {"action": "wait", "seconds": 3},
            visible(replay_item_id, True),
            action("media_action", source_name="Demo Replay", action="restart"),
            {"action": "wait", "seconds": 4},
            visible(replay_item_id, False),
            action("source_transform", scene_name=scene_name, scene_item_id=chart_item_id,
                   transform={"positionX": 0, "positionY": 0, "scaleX": 1, "scaleY": 1}),
        ]}}})


class EventService:
    """Execute only registered cues; event payloads never select OBS actions.

    IDs are consumed after successful preflight, before an execution attempt.
    Failed or uncertain executions remain consumed. Previews do not consume IDs.
    The bounded ID store is process-local and refuses new executions when full.
    """

    def __init__(self, cues: CueService, registry: dict | None = None, max_events: int = 1024):
        if type(max_events) is not int or not 1 <= max_events <= 4096:
            raise EventValidationError("max_events must be between 1 and 4096")
        self.cues = cues
        self._routes = _registry(load_event_registry() if registry is None else registry)
        self._max_events = max_events
        self._seen: dict[str, dict] = {}

    def _prepare(self, event: dict) -> tuple[dict, dict, str]:
        normalized = _event(event)
        route = self._routes.get(normalized["event_type"])
        if route is None:
            raise EventValidationError("Event type has no registered local cue")
        cue = deepcopy(route["cue"])
        if route.get("text_step") is not None:
            payload = normalized["payload"]
            text = f"{payload['title']}\n{payload['summary']}\nSOURCE / {payload['reference']}"
            cue["steps"][route["text_step"]]["parameters"]["settings"] = {"text": text}
        fingerprint = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        return normalized, cue, fingerprint

    def preview_event(self, event: dict, allow_live: bool = False) -> dict:
        """Validate and preview the registered event cue without consuming its ID."""
        return self.dispatch_event(event, dry_run=True, allow_live=allow_live)

    def dispatch_event(self, event: dict, dry_run: bool = True, allow_live: bool = False) -> dict:
        """Return a cue plan by default. Set dry_run=False for execution.

        Each event identifier permits at most one execution attempt per process.
        """
        if type(dry_run) is not bool or type(allow_live) is not bool:
            raise EventValidationError("dry_run and allow_live must be booleans")
        normalized, cue, fingerprint = self._prepare(event)
        with self.cues.production.lock:
            base = {"event_id": normalized["event_id"], "event_type": normalized["event_type"],
                    "dry_run": dry_run, "duplicate": False,
                    "checked_at": datetime.now(timezone.utc).isoformat(), "dedupe_scope": "current_process"}
            previous = self._seen.get(normalized["event_id"])
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise EventValidationError("Event ID was already used for different content")
                return {**base, "state": "duplicate", "duplicate": True, "applied": False,
                        "previous_state": previous["state"]}
            if not dry_run and len(self._seen) >= self._max_events:
                raise EventValidationError("Event ID capacity reached; execution is disabled for this process")
            try:
                preview = self.cues.run_cue(cue, dry_run=True, allow_live=allow_live)
            except Exception:
                raise EventValidationError("Registered event cue failed preflight; no event action was sent") from None
            if dry_run:
                return {**base, "state": "preview", "cue": preview}
            self._seen[normalized["event_id"]] = {"fingerprint": fingerprint, "state": "attempted"}
            try:
                result = self.cues.run_cue(cue, dry_run=False, allow_live=allow_live)
                state = result.get("state")
                if state not in ("completed", "failed"):
                    raise ValueError()
                self._seen[normalized["event_id"]]["state"] = state
                return {**base, "state": state, "cue": result}
            except Exception:
                self._seen[normalized["event_id"]]["state"] = "failed"
                return {**base, "state": "failed", "reason": "execution_failed",
                        "may_have_changed": True, "retry": "Inspect OBS; this event ID remains consumed"}
