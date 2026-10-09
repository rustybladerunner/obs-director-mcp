"""Bounded controls for existing OBS transitions; no transition creation or playback."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import time

from .transport import _local_path, utc_now


DEADLINE_SECONDS = 20.0
READBACK_SECONDS = 3.0
MAX_REQUESTS = 160
READS = {"GetSceneTransitionList", "GetCurrentSceneTransition", "GetProfileList", "GetSceneCollectionList"}
GUARDS = {"GetRecordStatus", "GetStreamStatus", "GetCurrentSceneTransitionCursor"}
SETTERS = {"select": "SetCurrentSceneTransition", "duration": "SetCurrentSceneTransitionDuration",
           "stinger": "SetCurrentSceneTransitionSettings"}


class TransitionError(ValueError):
    """A sanitized transition validation or state-continuity failure."""


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise TransitionError("Transition identities must be bounded printable text")
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise TransitionError("Transition timing is outside its supported integer range")
    return value


def _identity(value):
    if not isinstance(value, dict):
        raise TransitionError("OBS returned malformed transition metadata")
    result = {"name": _text(value.get("transitionName")), "kind": _text(value.get("transitionKind")),
              "uuid": value.get("transitionUuid"), "fixed": value.get("transitionFixed"),
              "configurable": value.get("transitionConfigurable")}
    if result["uuid"] is not None:
        _text(result["uuid"])
    if type(result["fixed"]) is not bool or type(result["configurable"]) is not bool:
        raise TransitionError("OBS returned unknown transition capabilities")
    return result


class _Run:
    def __init__(self, client):
        self.client, self.count = client, 0
        self.deadline = time.monotonic() + DEADLINE_SECONDS

    def _call(self, method, *args, deadline=None):
        limit = min(self.deadline, deadline) if deadline is not None else self.deadline
        remaining = limit - time.monotonic()
        self.count += 1
        if remaining <= 0 or self.count > MAX_REQUESTS:
            raise TransitionError("Transition operation exceeded its request budget")
        settings = self.client.settings
        self.client.settings = replace(settings, timeout=min(settings.timeout, remaining))
        try:
            value = method(*args)
            if time.monotonic() >= limit:
                raise TransitionError("Transition response arrived after its deadline")
            return value
        finally:
            self.client.settings = settings

    def require_capabilities(self, names):
        self._call(self.client.require_capabilities, list(names))

    def request(self, name, data=None, *, deadline=None):
        value = self._call(self.client.request, name, data, deadline=deadline)
        if not isinstance(value, dict):
            raise TransitionError("OBS returned malformed transition data")
        return value

    def selection(self, deadline=None):
        return (_text(self.request("GetProfileList", deadline=deadline).get("currentProfileName")),
                _text(self.request("GetSceneCollectionList", deadline=deadline).get("currentSceneCollectionName")))


def _state(run, deadline=None, *, transition_may_change=False):
    owner = run.selection(deadline)
    listing = run.request("GetSceneTransitionList", deadline=deadline)
    values = listing.get("transitions")
    if not isinstance(values, list) or len(values) > 128:
        raise TransitionError("OBS transition inventory is malformed or oversized")
    inventory = [_identity(value) for value in values]
    names = [item["name"] for item in inventory]
    if len(set(names)) != len(names):
        raise TransitionError("Duplicate transition names cannot be selected safely")
    name = listing.get("currentSceneTransitionName")
    current = None
    if name is not None:
        _text(name)
        raw = run.request("GetCurrentSceneTransition", deadline=deadline)
        identity = _identity(raw)
        matches = [item for item in inventory if item["name"] == identity["name"]]
        if (len(matches) != 1 or identity != matches[0]
                or (identity["name"] != name and not transition_may_change)):
            raise TransitionError("Current transition does not match its inventory identity")
        duration = raw.get("transitionDuration")
        if identity["fixed"]:
            if duration is not None:
                raise TransitionError("OBS returned a duration for a fixed transition")
        else:
            _integer(duration, 0, 20000)
        settings = raw.get("transitionSettings")
        if (identity["configurable"] and not isinstance(settings, dict)) or (not identity["configurable"] and settings is not None):
            raise TransitionError("OBS returned malformed transition configuration")
        current = {**identity, "duration_ms": duration, "settings": deepcopy(settings)}
    if run.selection(deadline) != owner:
        raise TransitionError("OBS profile or collection changed during transition inspection")
    return {"owner": owner, "inventory": inventory, "current": current}


def _public(current):
    return None if current is None else {key: value for key, value in current.items() if key not in ("settings", "uuid")}


def _video(video_path):
    try:
        if not isinstance(video_path, str) or len(video_path) > 4096 or not Path(video_path).is_absolute():
            raise ValueError()
        path = _local_path(video_path)
        if path.suffix.lower() not in (".mov", ".webm", ".mp4", ".mkv") or not path.is_file():
            raise ValueError()
        stat = path.stat()
        if not 0 < stat.st_size <= 1024 * 1024 * 1024:
            raise ValueError()
        with path.open("rb") as source:
            if not source.read(1):
                raise ValueError()
        return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                "device": stat.st_dev, "inode": stat.st_ino}
    except Exception:
        raise TransitionError("Stinger requires a readable local MOV, WebM, MP4 or MKV of at most 1 GiB") from None


class TransitionService:
    """Select or configure existing transitions under the production service lock."""

    def __init__(self, production):
        self.production = production

    def transitions(self) -> dict:
        """List existing transition names, kinds, current selection and duration; omit settings."""
        with self.production.lock, self.production.connection() as client:
            run = _Run(client)
            try:
                run.require_capabilities(READS | {"GetTransitionKindList"})
                state = _state(run)
                kinds = run.request("GetTransitionKindList").get("transitionKinds")
                if not isinstance(kinds, list) or len(kinds) > 128:
                    raise TransitionError("OBS returned malformed transition kinds")
                kinds = [_text(kind) for kind in kinds]
                if run.selection() != state["owner"]:
                    raise TransitionError("OBS selection changed during transition inspection")
                return {"checked_at": utc_now(), "transitions": [_public(item) for item in state["inventory"]],
                        "transition_kinds": kinds, "current": _public(state["current"])}
            except TransitionError:
                raise
            except Exception:
                raise TransitionError("Transition inspection could not verify OBS state") from None

    def select_transition(self, transition_name: str, duration_ms: int | None = None,
                          dry_run: bool = True, allow_live: bool = False) -> dict:
        """Select an existing transition and optional 50–20000 ms duration; preview by default."""
        name = _text(transition_name)
        if duration_ms is not None:
            _integer(duration_ms, 50, 20000)
        return self._change(name, duration_ms, None, dry_run, allow_live)

    def configure_stinger(self, transition_name: str, video_path: str, transition_point_ms: int,
                          dry_run: bool = True, allow_live: bool = False) -> dict:
        """Configure the already-selected native stinger from a local clip; preview by default."""
        name = _text(transition_name)
        _integer(transition_point_ms, 0, 20000)
        asset = _video(video_path)
        return self._change(name, transition_point_ms, asset, dry_run, allow_live)

    def _guard(self, run, expected, allow_live):
        if _state(run) != expected:
            raise TransitionError("OBS transition state changed before execution")
        self.production._live_gate(run, allow_live)
        cursor = run.request("GetCurrentSceneTransitionCursor").get("transitionCursor")
        if type(cursor) not in (int, float) or cursor != 1.0:
            raise TransitionError("An active or unknown transition cannot be reconfigured")
        # Duration/settings setters address the current transition, not a name.
        # Recheck identity after the output/cursor reads that can observe a switch.
        if _state(run) != expected:
            raise TransitionError("OBS transition or owning selection changed before execution")

    def _change(self, name, timing, asset, dry_run, allow_live):
        if type(dry_run) is not bool or type(allow_live) is not bool:
            raise TransitionError("Transition execution flags must be boolean")
        with self.production.lock, self.production.connection() as client:
            run = _Run(client)
            try:
                required = READS | GUARDS | ({SETTERS["stinger"]} if asset else {SETTERS["select"]})
                if asset is None and timing is not None:
                    required.add(SETTERS["duration"])
                run.require_capabilities(required)
                state = _state(run)
                target = next((item for item in state["inventory"] if item["name"] == name), None)
                if target is None:
                    raise TransitionError("The named transition does not exist; create it in OBS first")
                current = state["current"]
                if current is None:
                    raise TransitionError("OBS has no current transition to guard")
                changes = []
                desired_settings = None
                if asset:
                    if (current["name"] != name or target["kind"] != "obs_stinger_transition"
                            or not target["configurable"]):
                        raise TransitionError("Configure only an existing, already-selected native stinger")
                    if current["settings"].get("track_matte_enabled", False) is not False:
                        raise TransitionError("Track-matte stingers require separate configuration")
                    desired_settings = {"path": asset["path"], "tp_type": 0, "transition_point": timing}
                    if any(type(current["settings"].get(key)) is not type(value) or current["settings"].get(key) != value
                           for key, value in desired_settings.items()):
                        changes.append(("stinger", {"transitionSettings": desired_settings, "overlay": True}))
                else:
                    if timing is not None and target["fixed"]:
                        raise TransitionError("A fixed-duration transition cannot accept duration_ms")
                    if current["name"] != name:
                        changes.append(("select", {"transitionName": name}))
                    if timing is not None and (current["name"] != name or current["duration_ms"] != timing):
                        changes.append(("duration", {"transitionDuration": timing}))
                self._guard(run, state, allow_live)
            except TransitionError:
                raise
            except Exception:
                raise TransitionError("Transition preflight could not verify the required OBS state") from None
            result = {"action": "configure_stinger" if asset else "select_transition", "transition_name": name,
                      "dry_run": dry_run, "state": "preview", "applied": False, "verified": False, "uncertain": False,
                      "operations": [action for action, _ in changes], "receipts": [], "checked_at": utc_now(),
                      "verification": "OBS state readback only; decoded media and rendered frames are not verified"}
            result["transition_point_ms" if asset else "duration_ms"] = timing
            if dry_run:
                return result
            try:
                for action, data in changes:
                    receipt = {"action": action, "applied": False, "verified": False, "uncertain": False}
                    result["receipts"].append(receipt)
                    self._guard(run, state, allow_live)
                    if asset and _video(asset["path"]) != asset:
                        raise TransitionError("Local stinger file changed after preflight")
                    receipt.update(applied=None, uncertain=True)
                    run.request(SETTERS[action], data)
                    receipt["applied"] = True
                    state = self._readback(run, state, target, action, data)
                    receipt.update(verified=True, uncertain=False)
                self._guard(run, state, allow_live)
                if asset and _video(asset["path"]) != asset:
                    raise TransitionError("Local stinger file changed during configuration")
                result.update(state="completed" if changes else "unchanged", applied=bool(changes), verified=True)
            except Exception:
                receipts = result["receipts"]
                applied = None if any(item["applied"] is None for item in receipts) else any(item["applied"] for item in receipts)
                result.update(state="partial" if any(item["verified"] for item in receipts) else "failed", applied=applied,
                              uncertain=bool(applied) or any(item["uncertain"] for item in receipts),
                              reason="Transition execution stopped because acknowledgement, continuity or readback was not verified")
            return result

    @staticmethod
    def _readback(run, before, target, action, data):
        deadline = min(run.deadline, time.monotonic() + READBACK_SECONDS)
        while True:
            after = _state(run, deadline, transition_may_change=action == "select")
            if after["owner"] != before["owner"] or after["inventory"] != before["inventory"]:
                raise TransitionError("Transition inventory or OBS selection changed during readback")
            current = after["current"]
            if current is None:
                raise TransitionError("Current transition disappeared during readback")
            if action == "select":
                if {key: current[key] for key in target} == target:
                    return after
                if current != before["current"]:
                    raise TransitionError("An unexpected transition became current")
            else:
                expected = deepcopy(before["current"])
                if action == "duration":
                    expected["duration_ms"] = data["transitionDuration"]
                else:
                    expected["settings"].update(data["transitionSettings"])
                if current == expected:
                    return after
                if current != before["current"]:
                    raise TransitionError("Unexpected transition state changed during readback")
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
