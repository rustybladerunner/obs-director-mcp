# Derived from in-house prototype SHA256 04933b2733106d6f093aba821c48626e3bd21b90f8e1dae24eb869811aaeb861.
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import replace
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Iterator
import uuid
from PIL import Image, ImageStat
from .transport import ObsClient, ObsError, utc_now, _text, _local_path, data_directory

RECORD_TRANSITION_TIMEOUT = 10.0
RECORD_POLL_INTERVAL = 0.1

class CaptureService:
    """Small recording workflow. Metadata persists; stop authority does not.

    Only a recording started by this object on its still-connected client can
    be stopped. After MCP restart, use OBS itself to finish a pending recording.
    """

    def __init__(self, client_factory: Callable[[], ObsClient] = ObsClient,
                 evidence_root: Path | None = None):
        self.client_factory = client_factory
        self.evidence_root = _local_path(evidence_root or os.environ.get("OBS_MCP_EVIDENCE_ROOT") or data_directory() / "captures")
        self._lock = threading.RLock()
        self._owned: dict[str, tuple[ObsClient, int]] = {}

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        with (self.evidence_root / ".bridge.lock").open("a+b") as handle:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ObsError("Another OBS capture operation holds the local session lock") from None
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _manifest_path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("session_id must be the 32-character capture identifier")
        return self.evidence_root / session_id / "manifest.json"

    def _read_manifest(self, session_id: str) -> dict[str, Any]:
        try:
            result = json.loads(self._manifest_path(session_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ObsError("Capture session does not exist or its manifest cannot be read") from None
        if not isinstance(result, dict) or result.get("session_id") != session_id or result.get("schema") != "obs.capture.v1":
            raise ObsError("Capture manifest has an invalid identity")
        return result

    def _write_manifest(self, manifest: dict[str, Any], *, initial: bool = False) -> None:
        path = self._manifest_path(manifest["session_id"])
        manifest["updated_at"] = utc_now()
        if initial:
            path.parent.mkdir(parents=True, exist_ok=False)
            with path.open("x", encoding="utf-8") as file:
                json.dump(manifest, file, indent=2)
                file.flush()
                os.fsync(file.fileno())
        else:
            temporary = path.with_suffix(".pending.json")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(manifest, file, indent=2)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)

    @staticmethod
    def _versions(client: ObsClient) -> dict[str, Any]:
        version = client.request("GetVersion")
        return {key: version.get(key) for key in ("obsVersion", "obsWebSocketVersion", "rpcVersion")}

    @staticmethod
    def _idle(client: ObsClient) -> None:
        streaming = client.request("GetStreamStatus").get("outputActive")
        recording = client.request("GetRecordStatus").get("outputActive")
        if type(streaming) is not bool or type(recording) is not bool:
            raise ObsError("OBS returned an unknown recording/streaming state; capture changes are refused")
        if streaming:
            raise ObsError("OBS is streaming; capture changes are refused")
        if recording:
            raise ObsError("OBS already has an active recording; it will not be adopted")

    @staticmethod
    def _scene(client: ObsClient, scene_name: str) -> dict[str, Any]:
        scenes = client.request("GetSceneList")
        if scene_name not in [item.get("sceneName") for item in scenes.get("scenes", [])]:
            raise ObsError("Named OBS scene does not exist")
        return scenes

    @staticmethod
    def _restore_directory(client: ObsClient, manifest: dict[str, Any],
                           stop_event_offset: int | None = None) -> None:
        """Restore our temporary setting only when no recording uses it now."""
        # StartRecord acknowledgement can precede output activation. An inactive
        # status alone never proves that a requested start is cancelled/finished.
        if manifest.get("start_requested_at") and not manifest.get("stop_confirmed_at"):
            manifest["record_directory_restored"] = False
            manifest["restore_note"] = "Recording start/stop is unresolved; keep the session directory and inspect OBS"
            return
        try:
            if client.request("GetRecordStatus").get("outputActive") is not False:
                manifest["record_directory_restored"] = False
                return
            if stop_event_offset is not None:
                CaptureService._check_stopped_continuity(client, stop_event_offset)
            manifest["may_be_recording"] = False
            current = _local_path(client.request("GetRecordDirectory")["recordDirectory"])
            if stop_event_offset is not None:
                CaptureService._check_stopped_continuity(client, stop_event_offset)
            if current == _local_path(manifest["record_directory"]):
                client.request("SetRecordDirectory", {"recordDirectory": manifest["previous_record_directory"]})
                confirmed = _local_path(client.request("GetRecordDirectory")["recordDirectory"])
                if stop_event_offset is not None:
                    CaptureService._check_stopped_continuity(client, stop_event_offset)
                manifest["record_directory_restored"] = confirmed == _local_path(manifest["previous_record_directory"])
            else:
                manifest["record_directory_restored"] = False
        except Exception:
            manifest["record_directory_restored"] = False
            manifest["restore_note"] = "Could not confirm safe recording-directory restoration; inspect OBS"

    def _require_resolved_captures(self) -> None:
        """A persisted uncertain start cannot be cleared by a transient idle read."""
        for path in self.evidence_root.glob("*/manifest.json"):
            manifest = self._read_manifest(path.parent.name)
            if manifest.get("may_be_recording") is not False and manifest.get("state") != "completed":
                raise ObsError("An unresolved capture blocks new recording in this evidence root; inspect OBS and resolve its saved manifest explicitly")

    @staticmethod
    def _record_states(client: ObsClient, event_offset: int) -> list[str]:
        if client.closed:
            raise ObsError("OBS connection was lost; recording ownership is unproven")
        allowed = {"OBS_WEBSOCKET_OUTPUT_" + name for name in (
            "STARTING", "STARTED", "STOPPING", "STOPPED", "PAUSED", "RESUMED")}
        states = []
        for event in client.record_events[event_offset:]:
            if (not isinstance(event, dict) or not isinstance(event.get("outputState"), str)
                    or event["outputState"] not in allowed
                    or type(event.get("outputActive")) is not bool):
                raise ObsError("OBS returned an unknown recording event; ownership is unproven")
            expected_active = event["outputState"] in {
                "OBS_WEBSOCKET_OUTPUT_STARTED", "OBS_WEBSOCKET_OUTPUT_RESUMED"}
            if event["outputActive"] is not expected_active:
                raise ObsError("OBS returned an inconsistent recording event; ownership is unproven")
            states.append(event["outputState"])
        return states

    @staticmethod
    def _check_stopped_continuity(client: ObsClient, event_offset: int) -> None:
        if any(state.endswith(("_STARTING", "_STARTED"))
               for state in CaptureService._record_states(client, event_offset)):
            raise ObsError("An intervening start invalidated recording finalization")

    @classmethod
    def _wait_record_state(cls, client: ObsClient, event_offset: int, active: bool) -> None:
        """Poll one bounded connection for both transition event and matching status."""
        deadline = time.monotonic() + RECORD_TRANSITION_TIMEOUT
        original_settings = client.settings
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ObsError("OBS recording transition remained unconfirmed within the wait limit")
                client.settings = replace(original_settings, timeout=min(original_settings.timeout, remaining))
                status = client.request("GetRecordStatus").get("outputActive")
                if time.monotonic() >= deadline:
                    raise ObsError("OBS recording transition remained unconfirmed within the wait limit")
                if type(status) is not bool:
                    raise ObsError("OBS returned an unknown recording state; transition is unconfirmed")
                if active:
                    cls._check_continuity(client, event_offset)
                else:
                    cls._check_stopped_continuity(client, event_offset)
                target = "OBS_WEBSOCKET_OUTPUT_STARTED" if active else "OBS_WEBSOCKET_OUTPUT_STOPPED"
                if status is active and target in cls._record_states(client, event_offset):
                    return
                time.sleep(min(RECORD_POLL_INTERVAL, max(0, deadline - time.monotonic())))
        finally:
            client.settings = original_settings

    def status(self) -> dict[str, Any]:
        """Read OBS version, current scene, recording state and local recording directory."""
        with self._lock, self.client_factory() as client:
            recording = client.request("GetRecordStatus")
            streaming = client.request("GetStreamStatus")
            scene = client.request("GetCurrentProgramScene")
            return {"checked_at": utc_now(), "connected": True, **self._versions(client),
                    "scene_name": scene.get("currentProgramSceneName"),
                    "recording": {key: recording.get(key) for key in (
                        "outputActive", "outputPaused", "outputDuration", "outputBytes")},
                    "streaming": streaming.get("outputActive"),
                    "record_directory": client.request("GetRecordDirectory").get("recordDirectory")}

    def scenes(self) -> dict[str, Any]:
        """Read scene names and the current program scene without source settings."""
        with self._lock, self.client_factory() as client:
            result = client.request("GetSceneList")
            return {"current_scene": result.get("currentProgramSceneName"), "scenes": [
                {"scene_name": item.get("sceneName"), "scene_index": item.get("sceneIndex")}
                for item in result.get("scenes", [])]}

    def scene_sources(self, scene_name: str) -> dict[str, Any]:
        """Read source identities and visibility for one named scene."""
        scene_name = _text(scene_name, "scene_name")
        with self._lock, self.client_factory() as client:
            result = client.request("GetSceneItemList", {"sceneName": scene_name})
            return {"scene_name": scene_name, "sources": [{key: item.get(key) for key in (
                "sourceName", "sourceType", "inputKind", "sceneItemEnabled", "sceneItemId")}
                for item in result.get("sceneItems", [])]}

    def select_scene(self, scene_name: str, dry_run: bool = True) -> dict[str, Any]:
        """Preview or select a capture scene only while recording and streaming are inactive."""
        scene_name = _text(scene_name, "scene_name")
        if type(dry_run) is not bool:
            raise ValueError("dry_run must be a boolean")
        with self._lock, self.client_factory() as client:
            self._idle(client)
            scenes = self._scene(client, scene_name)
            result = {"dry_run": dry_run, "previous_scene": scenes.get("currentProgramSceneName"),
                      "scene_name": scene_name}
            if not dry_run:
                with self._file_lock():
                    self._idle(client)
                    client.request("SetCurrentProgramScene", {"sceneName": scene_name})
                    if client.request("GetCurrentProgramScene").get("currentProgramSceneName") != scene_name:
                        raise ObsError("OBS did not confirm the requested scene")
            return result

    def capture_health(self, source_name: str, samples: int = 3,
                       interval_seconds: float = 1) -> dict[str, Any]:
        """Save bounded local screenshots and return black/unchanged metrics, never pixels or a content-verification claim."""
        source_name = _text(source_name, "source_name")
        if type(samples) is not int or not 2 <= samples <= 5:
            raise ValueError("samples must be an integer from 2 to 5")
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float)) or not math.isfinite(interval_seconds) or not 0.1 <= interval_seconds <= 3:
            raise ValueError("interval_seconds must be between 0.1 and 3")
        with self._lock, self._file_lock(), self.client_factory() as client:
            capture_dir = self.evidence_root / "health" / uuid.uuid4().hex
            capture_dir.mkdir(parents=True, exist_ok=False)
            metrics = []
            for index in range(samples):
                data = client.request("GetSourceScreenshot", {"sourceName": source_name,
                    "imageFormat": "png", "imageWidth": 640, "imageCompressionQuality": -1})
                encoded = data.get("imageData", "")
                if not isinstance(encoded, str) or len(encoded) > 10 * 1024 * 1024 or not encoded.startswith("data:image/png;base64,"):
                    raise ObsError("OBS did not return a PNG screenshot")
                try:
                    raw = base64.b64decode(encoded.split(",", 1)[1], validate=True)
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.format != "PNG" or image.width * image.height > 2_000_000:
                            raise ValueError()
                        gray = image.convert("L")
                        luminance = ImageStat.Stat(gray).mean[0]
                        histogram = gray.histogram()
                        dark_fraction = sum(histogram[:9]) / (gray.width * gray.height)
                        digest = hashlib.sha256(gray.tobytes()).hexdigest()
                        width, height = image.size
                    path = capture_dir / f"{index + 1}.png"
                    with path.open("xb") as file:
                        file.write(raw)
                except (ValueError, OSError):
                    raise ObsError("OBS screenshot could not be decoded or saved locally") from None
                metrics.append({"captured_at": utc_now(), "local_path": str(path),
                    "width": width, "height": height, "mean_luminance": round(luminance, 3),
                    "dark_pixel_fraction": round(dark_fraction, 6), "pixel_sha256": digest,
                    "near_black": dark_fraction >= 0.995})
                if index + 1 < samples:
                    time.sleep(interval_seconds)
            result = {"source_name": source_name, "samples": metrics,
                "all_near_black": all(item["near_black"] for item in metrics),
                "unchanged": len({item["pixel_sha256"] for item in metrics}) == 1,
                "capture_verified": False,
                "interpretation": "Black frames need inspection. Unchanged frames can be stationary content; these measurements do not verify correct content, motion, or audio."}
            (capture_dir / "health.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result

    def start_recording(self, title: str, category: str, scene_name: str,
                        dry_run: bool = True) -> dict[str, Any]:
        """Preview or start an owned recording in a unique local session folder; actual output control requires environment opt-in."""
        title = _text(title, "title")
        category = _text(category, "category", 80)
        scene_name = _text(scene_name, "scene_name")
        if type(dry_run) is not bool:
            raise ValueError("dry_run must be a boolean")
        with self._lock:
            client = self.client_factory().__enter__()
            retained = False
            configuration_event_offset = len(client.record_events)
            try:
                client.require_capabilities(["GetVersion", "GetRecordStatus", "GetStreamStatus",
                    "GetSceneList", "GetRecordDirectory", "SetRecordDirectory",
                    "SetCurrentProgramScene", "GetCurrentProgramScene", "StartRecord", "StopRecord"])
                self._require_resolved_captures()
                self._idle(client)
                scenes = self._scene(client, scene_name)
                directory = _local_path(client.request("GetRecordDirectory")["recordDirectory"])
                result = {"dry_run": dry_run, "title": title, "category": category,
                    "scene_name": scene_name, "previous_scene": scenes.get("currentProgramSceneName"),
                    "previous_record_directory": str(directory), "session_directory_parent": str(self.evidence_root),
                    "plan": "Create a unique local session directory and manifest, temporarily change OBS Recording Path to that directory, select the named scene, then record. Restore the previous path after stopping if it is still ours. No intake or transcription runs."}
                if dry_run:
                    return result
                if os.environ.get("OBS_MCP_ALLOW_OUTPUT_CONTROL") != "1":
                    raise ObsError("Recording control requires OBS_MCP_ALLOW_OUTPUT_CONTROL=1")
                with self._file_lock():
                    self._require_resolved_captures()
                    self._idle(client)
                    session_id = uuid.uuid4().hex
                    session_dir = self._manifest_path(session_id).parent
                    manifest = {"schema": "obs.capture.v1", "session_id": session_id,
                        "state": "starting", "created_at": utc_now(), "title": title,
                        "category": category, "scene_name": scene_name,
                        "previous_scene": result["previous_scene"], "obs": self._versions(client),
                        "previous_record_directory": str(directory), "record_directory": str(session_dir),
                        "may_be_recording": False, "capture_verified": False}
                    self._write_manifest(manifest, initial=True)
                    try:
                        self._check_stopped_continuity(client, configuration_event_offset)
                        client.request("SetRecordDirectory", {"recordDirectory": str(session_dir)})
                        if _local_path(client.request("GetRecordDirectory")["recordDirectory"]) != session_dir:
                            raise ObsError("OBS did not confirm the unique recording directory")
                        client.request("SetCurrentProgramScene", {"sceneName": scene_name})
                        observed = client.request("GetCurrentProgramScene").get("currentProgramSceneName")
                        manifest["observed_scene"] = observed
                        if observed != scene_name:
                            raise ObsError("OBS did not confirm the requested capture scene")
                        self._idle(client)
                        self._check_stopped_continuity(client, configuration_event_offset)
                        event_offset = len(client.record_events)
                        manifest.update(may_be_recording=True, start_requested_at=utc_now())
                        self._write_manifest(manifest)
                        client.request("StartRecord")
                        self._wait_record_state(client, event_offset, True)
                        if _local_path(client.request("GetRecordDirectory")["recordDirectory"]) != session_dir:
                            raise ObsError("Recording directory changed during startup; ownership is unproven")
                        self._check_continuity(client, event_offset)
                        manifest.update(state="recording", started_at=utc_now())
                        self._write_manifest(manifest)
                        self._owned[session_id] = (client, event_offset)
                        retained = True
                        return {**manifest, "manifest_path": str(self._manifest_path(session_id)),
                            "stop_ownership": "Held by this MCP process and live OBS connection; a restart requires stopping in OBS."}
                    except Exception as exc:
                        manifest.update(state="failed", error=str(exc) if isinstance(exc, ObsError) else "Local capture operation failed",
                            ownership="unproven; inspect OBS before further actions")
                        self._restore_directory(client, manifest, configuration_event_offset)
                        self._write_manifest(manifest)
                        raise ObsError(f"Capture start failed; inspect session {session_id} and OBS before retrying") from None
            finally:
                if not retained:
                    client.close()

    @staticmethod
    def _check_continuity(client: ObsClient, event_offset: int) -> None:
        starts = 0
        starting = 0
        for state in CaptureService._record_states(client, event_offset):
            if state.endswith(("_STOPPING", "_STOPPED")):
                raise ObsError("An intervening stop invalidated recording ownership")
            if state.endswith("_STARTED"):
                starts += 1
            if state.endswith("_STARTING"):
                starting += 1
            if starts > 1 or starting > 1 or (starts and state.endswith("_STARTING")):
                raise ObsError("An intervening restart invalidated recording ownership")

    def stop_recording(self, session_id: str, dry_run: bool = True) -> dict[str, Any]:
        """Preview or stop this process's owned recording, then verify and hash its finalized local file."""
        if type(dry_run) is not bool:
            raise ValueError("dry_run must be a boolean")
        with self._lock:
            manifest = self._read_manifest(session_id)
            if manifest["state"] != "recording" or session_id not in self._owned:
                raise ObsError("Recording ownership is unproven in this MCP process; inspect and stop it in OBS")
            client, event_offset = self._owned[session_id]
            status = client.request("GetRecordStatus")
            self._check_continuity(client, event_offset)
            if status.get("outputActive") is not True:
                raise ObsError("Owned recording is no longer active; no StopRecord was sent")
            if dry_run:
                return {"dry_run": True, "session_id": session_id, "state": "recording",
                    "plan": "Stop this owned recording, verify the local finalized file, and hash it. Content and audio remain unverified."}
            if os.environ.get("OBS_MCP_ALLOW_OUTPUT_CONTROL") != "1":
                raise ObsError("Recording control requires OBS_MCP_ALLOW_OUTPUT_CONTROL=1")
            with self._file_lock():
                manifest.update(state="stopping", stop_requested_at=utc_now())
                self._write_manifest(manifest)
                stop_event_offset = None
                try:
                    # Recheck after the filesystem lock; never adopt a replacement recording.
                    if client.request("GetRecordStatus").get("outputActive") is not True:
                        raise ObsError("Recording stopped before StopRecord; no stop was sent")
                    self._check_continuity(client, event_offset)
                    stop_event_offset = len(client.record_events)
                    stopped = client.request("StopRecord")
                    output_path = _local_path(stopped.get("outputPath", ""))
                    manifest["output_path"] = str(output_path)
                    self._wait_record_state(client, stop_event_offset, False)
                    manifest.update(may_be_recording=False, stop_confirmed_at=utc_now())
                    if output_path.parent != _local_path(manifest["record_directory"]):
                        raise ObsError("OBS output path differs from this session directory; file was not read")
                    before = output_path.stat()
                    if not output_path.is_file() or before.st_size <= 0:
                        raise ObsError("OBS output file is missing or empty")
                    digest = hashlib.sha256()
                    with output_path.open("rb") as file:
                        for chunk in iter(lambda: file.read(1024 * 1024), b""):
                            digest.update(chunk)
                    after = output_path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ObsError("Recording file changed while hashing; finalization is unverified")
                    manifest.update(state="completed", stopped_at=utc_now(),
                        output_bytes=after.st_size, output_sha256=digest.hexdigest(),
                        file_finalized=True, content_verification="Not performed; file existence and hash do not prove correct video or audio")
                    self._restore_directory(client, manifest, stop_event_offset)
                    self._write_manifest(manifest)
                    return {**manifest, "manifest_path": str(self._manifest_path(session_id))}
                except Exception as exc:
                    manifest.update(state="failed", error=str(exc) if isinstance(exc, ObsError) else "Local file verification failed",
                        ownership="Inspect OBS; do not infer that a failed request stopped recording")
                    self._restore_directory(client, manifest, stop_event_offset)
                    self._write_manifest(manifest)
                    raise ObsError(f"Capture stop or verification failed; inspect session {session_id} and OBS") from None
                finally:
                    self._owned.pop(session_id, None)
                    client.close()

    def capture_sessions(self, limit: int = 20) -> dict[str, Any]:
        """Read recent saved capture manifests and this process's current permission to stop a recording."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        with self._lock:
            paths = list(self.evidence_root.glob("*/manifest.json")) if self.evidence_root.exists() else []
            paths.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
            sessions = []
            for path in paths[:limit]:
                manifest = self._read_manifest(path.parent.name)
                sessions.append({key: manifest.get(key) for key in ("session_id", "state", "created_at",
                    "updated_at", "title", "category", "scene_name", "output_path", "output_bytes",
                    "output_sha256", "capture_verified", "may_be_recording", "error")})
                lease = self._owned.get(manifest["session_id"])
                sessions[-1]["stop_ownership_held"] = lease is not None and not lease[0].closed
            return {"sessions": sessions, "total": len(paths)}
