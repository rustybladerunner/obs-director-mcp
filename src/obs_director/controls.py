"""Bounded production controls over the public OBS websocket API.

Settings are accepted as explicit user input but are never returned in receipts.
No method exposes arbitrary OBS requests or streaming credentials.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
import math
import os
import threading
import time
from typing import Any, Callable, Iterator

from .transport import ObsClient, ObsError, _text, utc_now


CUE_ACTIONS = frozenset({"scene_select", "source_visibility", "source_transform",
    "audio_mute", "audio_volume", "filter_settings", "filter_enabled", "media_action",
    "media_seek", "source_settings"})
MEDIA_ACTIONS = {name: "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_" + name.upper()
                 for name in ("play", "pause", "stop", "restart", "next", "previous")}
TRANSFORM_BOUNDS = {
    "positionX": (-32768, 32768), "positionY": (-32768, 32768), "rotation": (-360, 360),
    "scaleX": (0.01, 100), "scaleY": (0.01, 100), "boundsWidth": (0, 32768),
    "boundsHeight": (0, 32768), "cropLeft": (0, 16384), "cropRight": (0, 16384),
    "cropTop": (0, 16384), "cropBottom": (0, 16384), "alignment": (0, 15),
    "boundsAlignment": (0, 15),
}
BOUNDS_TYPES = {"OBS_BOUNDS_" + value for value in (
    "NONE", "STRETCH", "SCALE_INNER", "SCALE_OUTER", "SCALE_TO_WIDTH", "SCALE_TO_HEIGHT", "MAX_ONLY")}
MEDIA_READBACK_TIMEOUT = 3.0
MEDIA_POLL_INTERVAL = 0.05


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def _number(value: Any, label: str, lower: float, upper: float, integer: bool = False) -> float | int:
    if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value) or (integer and type(value) is not int):
        raise ValueError(f"{label} is outside its supported numeric range")
    return value


def _settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value or len(value) > 64:
        raise ValueError("settings must contain 1 to 64 named fields")
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError("settings must be finite JSON values") from None
    if len(encoded) > 65536 or not all(isinstance(key, str) and 0 < len(key) <= 128 for key in value):
        raise ValueError("settings exceed the supported size")
    return dict(value)


class ProductionService:
    """Typed production actions with whole-action validation and readback.

    Cue executors can hold ``lock`` and one ``connection()`` while validating
    every action before calling ``apply_action``. Output control is separate.
    """

    def __init__(self, client_factory: Callable[[], ObsClient] = ObsClient):
        self.client_factory = client_factory
        self.lock = threading.RLock()

    @contextmanager
    def connection(self) -> Iterator[ObsClient]:
        with self.client_factory() as client:
            yield client

    def capabilities(self) -> dict[str, Any]:
        """Read OBS version and supported requests, without settings or credentials."""
        with self.lock, self.connection() as client:
            return client.version()

    def status(self) -> dict[str, Any]:
        """Read OBS connection, version, program scene and output activity."""
        with self.lock, self.connection() as client:
            client.require_capabilities(["GetRecordStatus", "GetStreamStatus", "GetCurrentProgramScene"])
            recording = client.request("GetRecordStatus")
            streaming = client.request("GetStreamStatus")
            scene = client.request("GetCurrentProgramScene")
            return {"checked_at": utc_now(), "connected": True,
                    "version": {key: value for key, value in client.version().items() if key != "availableRequests"},
                    "scene_name": scene.get("currentProgramSceneName"),
                    "recording": {key: recording.get(key) for key in ("outputActive", "outputPaused", "outputDuration", "outputBytes")},
                    "streaming": streaming.get("outputActive")}

    def scenes(self) -> dict[str, Any]:
        """List available scenes and current program/preview scene names."""
        with self.lock, self.connection() as client:
            result = client.request("GetSceneList")
            return {"program_scene": result.get("currentProgramSceneName"),
                    "preview_scene": result.get("currentPreviewSceneName"),
                    "scenes": [{"scene_name": item.get("sceneName"), "scene_index": item.get("sceneIndex")}
                               for item in result.get("scenes", [])]}

    def inputs(self) -> dict[str, Any]:
        """List input names/kinds and creatable kinds; never input settings."""
        with self.lock, self.connection() as client:
            inputs = client.request("GetInputList").get("inputs", [])
            kinds = client.request("GetInputKindList").get("inputKinds", [])
            return {"inputs": [{"source_name": item.get("inputName"), "input_kind": item.get("inputKind")}
                               for item in inputs], "input_kinds": kinds}

    def scene_sources(self, scene_name: str) -> dict[str, Any]:
        """List a scene's source identities, item IDs and visibility."""
        scene_name = _text(scene_name, "scene_name")
        with self.lock, self.connection() as client:
            result = client.request("GetSceneItemList", {"sceneName": scene_name})
            return {"scene_name": scene_name, "sources": [{key: item.get(key) for key in (
                "sourceName", "inputKind", "sceneItemId", "sceneItemEnabled", "isGroup")}
                for item in result.get("sceneItems", [])]}

    def filters(self, source_name: str) -> dict[str, Any]:
        """List filter names, kinds and enabled states without their settings."""
        source_name = _text(source_name, "source_name")
        with self.lock, self.connection() as client:
            values = client.request("GetSourceFilterList", {"sourceName": source_name})
            return {"source_name": source_name, "filters": [{key: item.get(key) for key in (
                "filterName", "filterKind", "filterEnabled", "filterIndex")}
                for item in values.get("filters", [])]}

    @staticmethod
    def _live_gate(client: ObsClient, allow_live: bool) -> None:
        _boolean(allow_live, "allow_live")
        client.require_capabilities(["GetRecordStatus", "GetStreamStatus"])
        recording = client.request("GetRecordStatus").get("outputActive")
        streaming = client.request("GetStreamStatus").get("outputActive")
        if type(recording) is not bool or type(streaming) is not bool:
            raise ObsError("OBS output state is unknown; production changes are refused")
        if (recording or streaming) and not allow_live:
            raise ObsError("OBS is on air or recording; this change requires explicit allow_live=True")

    @staticmethod
    def describe_action(action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        safe = {key: value for key, value in parameters.items() if key != "settings"}
        if "settings" in parameters:
            # Even setting names can carry private user-defined values. Return a count only.
            safe["settings_field_count"] = len(parameters["settings"])
        return {"action": action, "parameters": safe}

    @staticmethod
    def _normalize(action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        schemas = {
            "scene_create": ({"scene_name"}, set()),
            "scene_select": ({"scene_name"}, {"target"}),
            "source_add": ({"scene_name", "source_name", "input_kind", "settings"}, set()),
            "source_settings": ({"source_name", "settings"}, set()),
            "source_visibility": ({"scene_name", "scene_item_id", "enabled"}, set()),
            "source_transform": ({"scene_name", "scene_item_id", "transform"}, set()),
            "audio_mute": ({"source_name", "muted"}, set()),
            "audio_volume": ({"source_name", "volume_db"}, set()),
            "filter_settings": ({"source_name", "filter_name", "settings"}, set()),
            "filter_enabled": ({"source_name", "filter_name", "enabled"}, set()),
            "media_action": ({"source_name", "action"}, set()),
            "media_seek": ({"source_name", "position_ms"}, set()),
        }
        if action not in schemas or not isinstance(parameters, dict):
            raise ValueError("Unsupported production action or parameter shape")
        required, optional = schemas[action]
        if not required <= parameters.keys() or parameters.keys() - required - optional:
            raise ValueError("Missing or unexpected production action parameters")
        values = dict(parameters)
        for key in ("scene_name", "source_name", "input_kind", "filter_name"):
            if key in values:
                values[key] = _text(values[key], key)
        for key in ("enabled", "muted"):
            if key in values:
                _boolean(values[key], key)
        if "settings" in values:
            values["settings"] = {} if action == "source_add" and values["settings"] == {} else _settings(values["settings"])
        if "scene_item_id" in values:
            _number(values["scene_item_id"], "scene_item_id", 0, 2**31 - 1, True)
        if action == "scene_select":
            values.setdefault("target", "program")
            if values["target"] not in ("program", "preview"):
                raise ValueError("target must be program or preview")
        if "volume_db" in values:
            _number(values["volume_db"], "volume_db", -100, 26)
        if "position_ms" in values:
            _number(values["position_ms"], "position_ms", 0, 604800000, True)
        if action == "media_action" and values["action"] not in MEDIA_ACTIONS:
            raise ValueError("Unsupported media action")
        if "transform" in values:
            transform = values["transform"]
            if not isinstance(transform, dict) or not transform or set(transform) - set(TRANSFORM_BOUNDS) - {"boundsType"}:
                raise ValueError("Unsupported or empty scene-item transform")
            for key, value in transform.items():
                if key == "boundsType":
                    if value not in BOUNDS_TYPES:
                        raise ValueError("Unsupported boundsType")
                else:
                    _number(value, key, *TRANSFORM_BOUNDS[key], key.startswith("crop") or key.endswith("lignment"))
            values["transform"] = dict(transform)
        return values

    @staticmethod
    def _requests(action: str, values: dict[str, Any]) -> tuple[str, str]:
        if action == "scene_select":
            suffix = "ProgramScene" if values["target"] == "program" else "PreviewScene"
            return "SetCurrent" + suffix, "GetCurrent" + suffix
        return {
            "scene_create": ("CreateScene", "GetSceneList"),
            "source_add": ("CreateInput", "GetSceneItemList"),
            "source_settings": ("SetInputSettings", "GetInputSettings"),
            "source_visibility": ("SetSceneItemEnabled", "GetSceneItemEnabled"),
            "source_transform": ("SetSceneItemTransform", "GetSceneItemTransform"),
            "audio_mute": ("SetInputMute", "GetInputMute"),
            "audio_volume": ("SetInputVolume", "GetInputVolume"),
            "filter_settings": ("SetSourceFilterSettings", "GetSourceFilter"),
            "filter_enabled": ("SetSourceFilterEnabled", "GetSourceFilter"),
            "media_action": ("TriggerMediaInputAction", "GetMediaInputStatus"),
            "media_seek": ("SetMediaInputCursor", "GetMediaInputStatus"),
        }[action]

    def validate_action(self, action: str, parameters: dict[str, Any], client: ObsClient,
                        allow_live: bool = False) -> dict[str, Any]:
        values = self._normalize(action, parameters)
        input_kind = None
        self._live_gate(client, allow_live)
        required = list(self._requests(action, values))
        if "scene_name" in values:
            required.append("GetSceneList")
        if "source_name" in values:
            required.append("GetInputList")
        if action == "source_add":
            required.extend(["GetInputKindList", "GetInputSettings"])
        if action == "scene_select" and values["target"] == "preview":
            required.append("GetStudioModeEnabled")
        if "scene_item_id" in values:
            required.append("GetSceneItemList")
        if "filter_name" in values:
            required.append("GetSourceFilterList")
        client.require_capabilities(required)
        if "scene_name" in values:
            scenes = client.request("GetSceneList").get("scenes", [])
            exists = values["scene_name"] in [item.get("sceneName") for item in scenes]
            if exists == (action == "scene_create"):
                raise ObsError("Scene already exists" if exists else "Named scene does not exist")
        if "source_name" in values:
            inputs = client.request("GetInputList").get("inputs", [])
            found = next((item for item in inputs if item.get("inputName") == values["source_name"]), None)
            input_kind = found.get("inputKind") if found else None
            if action == "source_add":
                if found:
                    raise ObsError("Input already exists; refusing to overwrite it")
                if values["input_kind"] not in client.request("GetInputKindList").get("inputKinds", []):
                    raise ObsError("Requested input kind is not available in OBS")
            elif found is None:
                raise ObsError("Named input does not exist")
            elif action == "source_settings" and set(values["settings"]) == {"text"}:
                if not str(found.get("inputKind", "")).startswith("text_") or not isinstance(values["settings"]["text"], str):
                    raise ObsError("Text settings require an OBS text input and a text value")
            if action == "media_action" and values["action"] == "restart" and input_kind == "ffmpeg_source":
                required.append("GetSourceActive")
                client.require_capabilities(["GetSourceActive"])
        if action == "scene_select" and values["target"] == "preview":
            if client.request("GetStudioModeEnabled").get("studioModeEnabled") is not True:
                raise ObsError("Preview scene selection requires OBS Studio Mode")
        if "scene_item_id" in values:
            items = client.request("GetSceneItemList", {"sceneName": values["scene_name"]}).get("sceneItems", [])
            if values["scene_item_id"] not in [item.get("sceneItemId") for item in items]:
                raise ObsError("Scene item does not exist in the named scene")
        if "filter_name" in values:
            filters = client.request("GetSourceFilterList", {"sourceName": values["source_name"]}).get("filters", [])
            if values["filter_name"] not in [item.get("filterName") for item in filters]:
                raise ObsError("Named source filter does not exist")
        # These reads reject inputs without the requested audio/media capability.
        if action in ("audio_mute", "audio_volume", "media_action", "media_seek"):
            state = client.request(self._requests(action, values)[1], {"inputName": values["source_name"]})
            if action == "media_seek" and isinstance(state.get("mediaDuration"), (int, float)) and state["mediaDuration"] > 0 and values["position_ms"] > state["mediaDuration"]:
                raise ObsError("Seek position exceeds the media duration")
        return {"action": action, "parameters": values, "required_requests": sorted(set(required)),
                "input_kind": input_kind,
                "media_before": state if action in ("media_action", "media_seek") else None,
                "readback_expected": action != "media_action" or values["action"] not in ("next", "previous")}

    @staticmethod
    def _payload(action: str, values: dict[str, Any]) -> dict[str, Any]:
        if action in ("scene_create", "scene_select"):
            return {"sceneName": values["scene_name"]}
        if action == "source_add":
            return {"sceneName": values["scene_name"], "inputName": values["source_name"],
                    "inputKind": values["input_kind"], "inputSettings": values["settings"], "sceneItemEnabled": True}
        if action.startswith("filter_"):
            payload = {"sourceName": values["source_name"], "filterName": values["filter_name"]}
            payload.update({"filterSettings": values["settings"], "overlay": True} if action == "filter_settings" else {"filterEnabled": values["enabled"]})
            return payload
        if "scene_item_id" in values:
            payload = {"sceneName": values["scene_name"], "sceneItemId": values["scene_item_id"]}
            payload["sceneItemEnabled" if action == "source_visibility" else "sceneItemTransform"] = values.get("enabled", values.get("transform"))
            return payload
        payload = {"inputName": values["source_name"]}
        if action == "source_settings":
            payload.update(inputSettings=values["settings"], overlay=True)
        elif action == "audio_mute":
            payload["inputMuted"] = values["muted"]
        elif action == "audio_volume":
            payload["inputVolumeDb"] = values["volume_db"]
        elif action == "media_action":
            payload["mediaAction"] = MEDIA_ACTIONS[values["action"]]
        elif action == "media_seek":
            payload["mediaCursor"] = values["position_ms"]
        return payload

    def apply_action(self, action: str, parameters: dict[str, Any], client: ObsClient,
                     allow_live: bool = False) -> dict[str, Any]:
        # Include the pre-write snapshot's response latency in the no-op playback interval.
        started = time.monotonic()
        plan = self.validate_action(action, parameters, client, allow_live)
        values = plan["parameters"]
        setter, getter = self._requests(action, values)
        receipt = {**self.describe_action(action, values), "applied": None, "verified": False,
                   "checked_at": utc_now(), "verification": "OBS accepted the action; readback did not establish the requested state"}
        if action == "media_action" and values["action"] == "restart" and plan["input_kind"] == "ffmpeg_source":
            try:
                prepared = self._prepare_media_restart(client, values["source_name"], allow_live)
            except Exception:
                prepared = None
            if prepared is None:
                return {**receipt, "applied": False, "uncertain": False,
                        "verification": "FFmpeg source did not pass visibility and output-state checks; restart was not sent"}
            plan["media_before"], started = prepared
        try:
            client.request(setter, self._payload(action, values))
            receipt["applied"] = True
            receipt["verified"] = self._readback(action, values, client, getter, plan["media_before"], started)
        except Exception:
            receipt["uncertain"] = True
            receipt["verification"] = ("OBS acknowledged the action, but readback failed; current state is unknown"
                if receipt["applied"] else "OBS did not acknowledge the action; whether it took effect is unknown")
            return receipt
        receipt["uncertain"] = not receipt["verified"]
        if receipt["verified"]:
            receipt["verification"] = "OBS readback matches the requested state; rendered content has not been visually verified"
        return receipt

    def _readback(self, action: str, values: dict[str, Any], client: ObsClient,
                  getter: str, media_before: dict[str, Any] | None, started: float) -> bool:
        if action == "media_action":
            return self._wait_media_action(values, client, media_before, started)
        if action in ("scene_create", "scene_select"):
            state = client.request(getter)
            verified = (values["scene_name"] in [item.get("sceneName") for item in state.get("scenes", [])]
                        if action == "scene_create" else state.get("currentProgramSceneName" if values["target"] == "program" else "currentPreviewSceneName") == values["scene_name"])
        elif action == "source_add":
            state = client.request(getter, {"sceneName": values["scene_name"]})
            verified = any(item.get("sourceName") == values["source_name"] and item.get("inputKind") == values["input_kind"] for item in state.get("sceneItems", []))
            settings = client.request("GetInputSettings", {"inputName": values["source_name"]}).get("inputSettings", {})
            verified = verified and all(key in settings and settings[key] == value for key, value in values["settings"].items())
        elif action.startswith("filter_"):
            state = client.request(getter, {"sourceName": values["source_name"], "filterName": values["filter_name"]})
            verified = (all(key in state.get("filterSettings", {}) and state["filterSettings"][key] == value for key, value in values["settings"].items())
                        if action == "filter_settings" else state.get("filterEnabled") is values["enabled"])
        elif "scene_item_id" in values:
            state = client.request(getter, {"sceneName": values["scene_name"], "sceneItemId": values["scene_item_id"]})
            verified = (state.get("sceneItemEnabled") is values["enabled"] if action == "source_visibility" else
                        all(self._matches(state.get("sceneItemTransform", {}).get(key), value) for key, value in values["transform"].items()))
        else:
            state = client.request(getter, {"inputName": values["source_name"]})
            if action == "source_settings":
                verified = all(key in state.get("inputSettings", {}) and state["inputSettings"][key] == value for key, value in values["settings"].items())
            elif action == "audio_mute":
                verified = state.get("inputMuted") is values["muted"]
            elif action == "audio_volume":
                verified = self._matches(state.get("inputVolumeDb"), values["volume_db"], 0.05)
            elif action == "media_seek":
                verified = self._media_cursor_verified(media_before, state, values["position_ms"],
                    (time.monotonic() - started) * 1000, restart=False)
        return verified

    @staticmethod
    def _prepare_media_restart(client: ObsClient, source_name: str, allow_live: bool) -> tuple[dict[str, Any], float] | None:
        """Wait for a showing FFmpeg source and refresh its pre-write media snapshot."""
        deadline = time.monotonic() + MEDIA_READBACK_TIMEOUT
        original_settings = client.settings

        def read(request: str, data: dict[str, Any]) -> dict[str, Any]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ObsError("FFmpeg source visibility wait expired")
            client.settings = replace(original_settings, timeout=min(original_settings.timeout, remaining))
            result = client.request(request, data)
            if time.monotonic() >= deadline:
                raise ObsError("FFmpeg source visibility wait expired")
            return result

        def showing() -> bool:
            value = read("GetSourceActive", {"sourceName": source_name}).get("videoShowing")
            if type(value) is not bool:
                raise ObsError("FFmpeg source visibility is unknown")
            return value

        try:
            while time.monotonic() < deadline:
                if showing():
                    # Becoming visible may itself start playback. Do not credit
                    # that earlier transition to a restart which has not been sent.
                    started = time.monotonic()
                    before = read("GetMediaInputStatus", {"inputName": source_name})
                    recording = read("GetRecordStatus", {}).get("outputActive")
                    streaming = read("GetStreamStatus", {}).get("outputActive")
                    if (type(recording) is not bool or type(streaming) is not bool
                            or ((recording or streaming) and not allow_live)):
                        raise ObsError("OBS output activity changed while waiting for the FFmpeg source")
                    if showing():
                        return before, started
                time.sleep(min(MEDIA_POLL_INTERVAL, max(0, deadline - time.monotonic())))
            return None
        finally:
            client.settings = original_settings

    def _wait_media_action(self, values: dict[str, Any], client: ObsClient,
                           before: dict[str, Any] | None, started: float) -> bool:
        """Wait for OBS's queued action without resending it or extending the deadline."""
        action = values["action"]
        if action in ("next", "previous"):
            return False
        expected = {"play": "PLAYING", "pause": "PAUSED", "stop": "STOPPED", "restart": "PLAYING"}[action]
        deadline = time.monotonic() + MEDIA_READBACK_TIMEOUT
        original_settings = client.settings
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                client.settings = replace(original_settings, timeout=min(original_settings.timeout, remaining))
                state = client.request("GetMediaInputStatus", {"inputName": values["source_name"]})
                observed_at = time.monotonic()
                if observed_at >= deadline:
                    return False
                if state.get("mediaState") == "OBS_MEDIA_STATE_" + expected:
                    if action != "restart" or self._media_cursor_verified(
                            before, state, 0, (observed_at - started) * 1000, restart=True):
                        return True
                time.sleep(min(MEDIA_POLL_INTERVAL, max(0, deadline - time.monotonic())))
        finally:
            client.settings = original_settings

    @staticmethod
    def _media_cursor_verified(before: dict[str, Any] | None, after: dict[str, Any],
                               target: int, elapsed_ms: float, restart: bool) -> bool:
        """Require a target cursor plus evidence distinguishable from a no-op.

        Fifty milliseconds permits frame quantization. For playing media the
        measured round-trip adds an advancement bound, never a fixed 1.5s grace.
        A seek too close to normal playback, or restart already at the beginning,
        remains unverified because status alone cannot distinguish a no-op.
        """
        before = before or {}
        old = before.get("mediaCursor")
        actual = after.get("mediaCursor")
        if type(actual) not in (int, float) or not math.isfinite(actual):
            return False
        playing = after.get("mediaState") == "OBS_MEDIA_STATE_PLAYING"
        if restart and not playing:
            return False
        if not restart and after.get("mediaState") != before.get("mediaState"):
            return False
        allowance = elapsed_ms if playing else 0
        if not target - 50 <= actual <= target + allowance + 50:
            return False
        # A stopped FFmpeg input can clear its cursor. A new PLAYING state near
        # zero proves this restart, whereas an already-playing input needs reset evidence.
        if restart and before.get("mediaState") == "OBS_MEDIA_STATE_STOPPED" and "mediaCursor" in before and old is None:
            return True
        if type(old) not in (int, float) or not math.isfinite(old):
            return False
        was_playing = before.get("mediaState") == "OBS_MEDIA_STATE_PLAYING"
        if was_playing:
            duration = before.get("mediaDuration")
            if type(duration) in (int, float) and math.isfinite(duration) and duration > 0 and old + elapsed_ms + 50 >= duration:
                # A looping source can wrap to zero without the command taking effect.
                return False
            # A point in the original playback's possible interval is not reset/seek evidence.
            return not old - 50 <= actual <= old + elapsed_ms + 50
        if restart:
            return old > actual + 50 or before.get("mediaState") != "OBS_MEDIA_STATE_PLAYING"
        return target == old or actual != old

    @staticmethod
    def _matches(actual: Any, expected: Any, tolerance: float = 0.001) -> bool:
        if type(expected) in (int, float):
            return type(actual) in (int, float) and math.isfinite(actual) and abs(actual - expected) <= tolerance
        return actual == expected

    def execute_action(self, action: str, parameters: dict[str, Any], dry_run: bool = True,
                       allow_live: bool = False) -> dict[str, Any]:
        _boolean(dry_run, "dry_run")
        # Reject shape errors before opening OBS.
        values = self._normalize(action, parameters)
        with self.lock, self.connection() as client:
            plan = self.validate_action(action, values, client, allow_live)
            if dry_run:
                return {**self.describe_action(action, plan["parameters"]), "dry_run": True,
                        "applied": False, "required_requests": plan["required_requests"]}
            return {"dry_run": False, **self.apply_action(action, plan["parameters"], client, allow_live)}

    def create_scene(self, scene_name: str, dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Return a scene-creation plan by default. To create the scene, set dry_run=False.

        Active recording or streaming needs allow_live=True.
        """
        return self.execute_action("scene_create", {"scene_name": scene_name}, dry_run, allow_live)

    def select_scene(self, scene_name: str, target: str = "program", dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Preview or select a program/preview scene, with capability and state checks."""
        return self.execute_action("scene_select", {"scene_name": scene_name, "target": target}, dry_run, allow_live)

    def add_source(self, scene_name: str, source_name: str, input_kind: str, settings: dict[str, Any], dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Preview or create an input in a scene; existing input names are preserved."""
        return self.execute_action("source_add", {"scene_name": scene_name, "source_name": source_name, "input_kind": input_kind, "settings": settings}, dry_run, allow_live)

    def source_settings(self, source_name: str, settings: dict[str, Any], dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Return a plan, or apply the supplied input settings.

        Preserve settings that were not supplied. Results do not contain settings.
        """
        return self.execute_action("source_settings", {"source_name": source_name, "settings": settings}, dry_run, allow_live)

    def source_visibility(self, scene_name: str, scene_item_id: int, enabled: bool, dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Preview or change a scene item's visibility and verify OBS readback."""
        return self.execute_action("source_visibility", {"scene_name": scene_name, "scene_item_id": scene_item_id, "enabled": enabled}, dry_run, allow_live)

    def source_transform(self, scene_name: str, scene_item_id: int, transform: dict[str, Any], dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Preview or apply bounded position, scale, crop, rotation and alignment values."""
        return self.execute_action("source_transform", {"scene_name": scene_name, "scene_item_id": scene_item_id, "transform": transform}, dry_run, allow_live)

    def audio_mute(self, source_name: str, muted: bool, dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Preview or set an audio input's mute state, then verify readback."""
        return self.execute_action("audio_mute", {"source_name": source_name, "muted": muted}, dry_run, allow_live)

    def audio_volume(self, source_name: str, volume_db: float, dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Preview or set input volume in decibels, between -100 and 26 dB."""
        return self.execute_action("audio_volume", {"source_name": source_name, "volume_db": volume_db}, dry_run, allow_live)

    def filter_settings(self, source_name: str, filter_name: str, settings: dict[str, Any], dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Preview or overlay an existing filter's settings; return no setting values."""
        return self.execute_action("filter_settings", {"source_name": source_name, "filter_name": filter_name, "settings": settings}, dry_run, allow_live)

    def filter_enabled(self, source_name: str, filter_name: str, enabled: bool, dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Preview or enable/disable an existing source filter and verify readback."""
        return self.execute_action("filter_enabled", {"source_name": source_name, "filter_name": filter_name, "enabled": enabled}, dry_run, allow_live)

    def media_action(self, source_name: str, action: str, dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Return a plan, or control media playback. Next/previous results remain unverified."""
        return self.execute_action("media_action", {"source_name": source_name, "action": action}, dry_run, allow_live)

    def media_seek(self, source_name: str, position_ms: int, dry_run: bool = True, allow_live: bool = False) -> dict[str, Any]:
        """Preview or seek media in milliseconds and check its resulting cursor."""
        return self.execute_action("media_seek", {"source_name": source_name, "position_ms": position_ms}, dry_run, allow_live)

    def output_control(self, output: str, action: str, dry_run: bool = True) -> dict[str, Any]:
        """Return an output-command plan by default.

        Execution needs OBS_MCP_ALLOW_OUTPUT_CONTROL=1. Cues cannot use this tool.
        """
        _boolean(dry_run, "dry_run")
        choices = {"stream": ("Stream", "GetStreamStatus"),
                   "virtualcam": ("VirtualCam", "GetVirtualCamStatus"),
                   "replay": ("ReplayBuffer", "GetReplayBufferStatus")}
        if output not in choices or action not in ("start", "stop", "save") or (action == "save" and output != "replay"):
            raise ValueError("Unsupported output/action combination")
        suffix, getter = choices[output]
        setter = action.title() + suffix
        with self.lock, self.connection() as client:
            required = [getter, setter] + (["GetLastReplayBufferReplay"] if action == "save" else [])
            client.require_capabilities(required)
            current = client.request(getter).get("outputActive")
            if type(current) is not bool:
                raise ObsError("OBS output state is unknown")
            if (action == "start" and current) or (action in ("stop", "save") and not current):
                raise ObsError("Output is not in the required state for this action")
            if dry_run:
                return {"output": output, "action": action, "dry_run": True, "applied": False,
                        "output_control_enabled": os.environ.get("OBS_MCP_ALLOW_OUTPUT_CONTROL") == "1"}
            if os.environ.get("OBS_MCP_ALLOW_OUTPUT_CONTROL") != "1":
                raise ObsError("Output control requires OBS_MCP_ALLOW_OUTPUT_CONTROL=1")
            prior_path = client.request("GetLastReplayBufferReplay").get("savedReplayPath") if action == "save" else None
            receipt = {"output": output, "action": action, "dry_run": False, "applied": None,
                       "verified": False, "uncertain": True}
            try:
                client.request(setter)
                receipt["applied"] = True
                if action == "save":
                    state = client.request("GetLastReplayBufferReplay")
                    path = state.get("savedReplayPath")
                    receipt.update(verified=bool(path) and path != prior_path, local_output_path=path,
                        verification="Replay path changed" if path and path != prior_path else "OBS accepted save; a new replay path is not yet confirmed")
                else:
                    receipt["verified"] = client.request(getter).get("outputActive") is (action == "start")
                    receipt["verification"] = "OBS status readback; no audience/media acceptance claim"
                receipt["uncertain"] = not receipt["verified"]
            except Exception:
                receipt["verification"] = ("OBS acknowledged the output action, but readback failed; current state is unknown"
                    if receipt["applied"] else "OBS did not acknowledge the output action; whether it took effect is unknown")
            return receipt
