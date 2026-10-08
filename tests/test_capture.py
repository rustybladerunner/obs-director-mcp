"""Capture lifecycle regression checks against synthetic OBS, never live OBS."""

import base64
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from obs_director import capture as obs_bridge
from obs_director.transport import ObsSettings


RECORDING_BYTES = b"synthetic-finalized-video-for-digest-test\x00\x01"
SECRET = "not-a-real-secret"


def png_data(color):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class FakeObsState:
    """One OBS process; connections share it but own their event subscriptions."""

    def __init__(self, directory):
        self.directory = str(directory)
        self.active = False
        self.streaming = False
        self.paused = False
        self.duration = 0
        self.current_scene = "Course"
        self.scenes = ["Course", "Other"]
        self.calls = []
        self.clients = []
        self.screenshots = [png_data((80, 100, 120))]
        self.write_output = True
        self.output_override = None
        self.fail_requests = {}
        self.record_status_override = None
        self.stream_status_override = None
        self.ignore_directory_changes = False
        self.missing_capabilities = set()
        self.start_delay = self.stop_delay = 0
        self.pending_start = self.pending_stop = False
        self.start_ack_error = self.stop_ack_error = None
        self.start_status_override = self.stop_status_override = None
        self.emit_start_event = self.emit_stop_event = True
        self.start_hook = self.stop_hook = None
        self.get_directory_hook = None

    def factory(self, *args, **kwargs):
        client = FakeObsClient(self)
        self.clients.append(client)
        return client

    def emit(self, output_state):
        for client in self.clients:
            if not client.closed:
                client.record_events.append({"outputActive": self.active, "outputState": output_state})

    def replace_recording(self):
        self.active = False
        self.emit("OBS_WEBSOCKET_OUTPUT_STOPPED")
        self.active = True
        self.duration = 1
        self.emit("OBS_WEBSOCKET_OUTPUT_STARTED")

    def finish_start(self):
        self.pending_start = False
        self.active = True
        self.duration = 1
        if self.emit_start_event:
            self.emit("OBS_WEBSOCKET_OUTPUT_STARTED")
        if self.start_hook:
            self.start_hook()

    def finish_stop(self):
        self.pending_stop = False
        self.active = False
        if self.emit_stop_event:
            self.emit("OBS_WEBSOCKET_OUTPUT_STOPPED")
        output = Path(self.output_override or Path(self.directory) / "recording.mkv")
        if self.write_output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(RECORDING_BYTES)
        if self.stop_hook:
            self.stop_hook()


class FakeObsClient:
    def __init__(self, state):
        self.state = state
        self.settings = ObsSettings()
        self.record_events = []
        self.closed = False

    def __enter__(self):
        return self

    def require_capabilities(self, names):
        if set(names) & self.state.missing_capabilities:
            raise obs_bridge.ObsError("Synthetic required capability missing")

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.closed = True

    def request(self, name, data=None):
        if self.closed:
            raise obs_bridge.ObsError("synthetic connection dropped")
        data = data or {}
        state = self.state
        state.calls.append((name, dict(data)))
        if name in state.fail_requests:
            raise state.fail_requests[name]
        if name == "GetVersion":
            return {"obsVersion": "32.0.0", "obsWebSocketVersion": "5.6.3", "rpcVersion": 1,
                    "availableRequests": ["GetRecordDirectory", "SetRecordDirectory"],
                    "supportedImageFormats": ["png"], "platform": "windows"}
        if name == "GetRecordStatus":
            if state.record_status_override is not None:
                return state.record_status_override
            if state.pending_start:
                if state.start_delay > 0:
                    state.start_delay -= 1
                else:
                    state.finish_start()
            if state.pending_stop:
                if state.stop_delay > 0:
                    state.stop_delay -= 1
                else:
                    state.finish_stop()
            return {"outputActive": state.active, "outputPaused": state.paused,
                    "outputDuration": state.duration, "outputBytes": 1024 if state.active else 0,
                    "outputTimecode": "00:00:01.000"}
        if name == "GetStreamStatus":
            if state.stream_status_override is not None:
                return state.stream_status_override
            return {"outputActive": state.streaming, "outputReconnecting": False,
                    "outputDuration": 0, "outputBytes": 0}
        if name == "GetSceneList":
            return {"currentProgramSceneName": state.current_scene, "currentPreviewSceneName": None,
                    "scenes": [{"sceneName": scene, "sceneIndex": i} for i, scene in enumerate(state.scenes)]}
        if name == "GetCurrentProgramScene":
            return {"currentProgramSceneName": state.current_scene, "sceneName": state.current_scene}
        if name == "GetSceneItemList":
            return {"sceneItems": [{"sourceName": "Lesson window", "sceneItemEnabled": True,
                                    "sceneItemId": 1, "sourceType": "OBS_SOURCE_TYPE_INPUT"}]}
        if name == "GetInputList":
            return {"inputs": [{"inputName": "Course Audio", "inputKind": "wasapi_output_capture"}]}
        if name == "GetInputMute":
            return {"inputMuted": False}
        if name == "GetInputVolume":
            return {"inputVolumeMul": 1.0, "inputVolumeDb": 0.0}
        if name == "GetStats":
            return {"activeFps": 30.0, "renderSkippedFrames": 0, "renderTotalFrames": 60,
                    "outputSkippedFrames": 0, "outputTotalFrames": 60,
                    "availableDiskSpace": 100000.0, "cpuUsage": 3.0, "memoryUsage": 200.0,
                    "averageFrameRenderTime": 1.0}
        if name == "GetRecordDirectory":
            if state.get_directory_hook:
                state.get_directory_hook()
            return {"recordDirectory": state.directory}
        if name == "SetRecordDirectory":
            if not state.ignore_directory_changes:
                state.directory = data["recordDirectory"]
            return {}
        if name == "SetCurrentProgramScene":
            state.current_scene = data["sceneName"]
            return {}
        if name == "StartRecord":
            state.pending_start = True
            if not state.start_delay:
                state.finish_start()
            if state.start_status_override is not None:
                state.record_status_override = state.start_status_override
            if state.start_ack_error:
                raise state.start_ack_error
            return {}
        if name == "StopRecord":
            output = Path(state.output_override or Path(state.directory) / "recording.mkv")
            state.pending_stop = True
            if not state.stop_delay:
                state.finish_stop()
            if state.stop_status_override is not None:
                state.record_status_override = state.stop_status_override
            if state.stop_ack_error:
                raise state.stop_ack_error
            return {"outputPath": str(output)}
        if name == "GetSourceScreenshot":
            image = state.screenshots.pop(0) if len(state.screenshots) > 1 else state.screenshots[0]
            return {"imageData": image}
        raise AssertionError(f"Unexpected OBS request in independent fake: {name}")


class CaptureLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / ".tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="obs-unit-", dir=temp_root)
        env = patch.dict("os.environ", {"OBS_MCP_ALLOW_OUTPUT_CONTROL": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.evidence = self.root / "evidence"
        self.original_directory = self.root / "original-recordings"
        self.obs = FakeObsState(self.original_directory)
        self.service = obs_bridge.CaptureService(client_factory=self.obs.factory,
                                                evidence_root=self.evidence)

    def start(self):
        return self.service.start_recording("Synthetic lesson", "test-course", "Course", dry_run=False)

    def mutations(self):
        return [(name, data) for name, data in self.obs.calls if not name.startswith("Get")]

    def manifest(self, session_id):
        return json.loads((self.evidence / session_id / "manifest.json").read_text(encoding="utf-8"))

    @contextmanager
    def fast_transitions(self):
        clock = [0.0]
        with patch.object(obs_bridge, "RECORD_TRANSITION_TIMEOUT", 0.3), \
                patch.object(obs_bridge.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(obs_bridge.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            yield

    def unresolved_manifest(self):
        session = self.service.capture_sessions()["sessions"][0]
        manifest = self.manifest(session["session_id"])
        self.assertEqual("failed", manifest["state"])
        self.assertTrue(manifest["may_be_recording"])
        self.assertTrue(session["may_be_recording"])
        self.assertFalse(manifest["record_directory_restored"])
        self.assertFalse(session["stop_ownership_held"])
        self.assertEqual(manifest["record_directory"], self.obs.directory)
        return manifest

    def test_acknowledged_start_waits_through_false_until_same_connection_event_and_active(self):
        self.obs.start_delay = 2
        with self.fast_transitions():
            started = self.start()
        self.assertEqual("recording", started["state"])
        self.assertTrue(started["may_be_recording"])
        self.assertTrue(self.obs.active)
        self.assertEqual(1, len(self.obs.clients))
        self.assertFalse(self.obs.clients[0].closed)
        self.assertEqual(started["record_directory"], self.obs.directory)
        self.assertEqual(1, sum(name == "SetRecordDirectory" for name, _ in self.obs.calls))
        self.assertEqual(1, sum(name == "StartRecord" for name, _ in self.obs.calls))
        self.service.stop_recording(started["session_id"], dry_run=False)

    def test_unconfirmed_start_keeps_directory_and_blocks_restart_even_while_inactive(self):
        self.obs.start_delay = 100
        with self.fast_transitions(), self.assertRaises(obs_bridge.ObsError):
            self.start()
        self.assertFalse(self.obs.active)
        manifest = self.unresolved_manifest()
        before = self.mutations()
        restarted = obs_bridge.CaptureService(self.obs.factory, self.evidence)
        with self.assertRaisesRegex(obs_bridge.ObsError, "unresolved capture blocks"):
            restarted.start_recording("Retry", "test", "Course", dry_run=False)
        self.assertEqual(before, self.mutations())
        self.obs.finish_start()  # OBS eventually starts after our bounded wait ended.
        self.assertTrue(self.obs.active)
        self.assertEqual(manifest["record_directory"], self.obs.directory)
        self.assertTrue(self.manifest(manifest["session_id"])["may_be_recording"])
        with self.assertRaises(obs_bridge.ObsError):
            restarted.stop_recording(manifest["session_id"], dry_run=False)
        self.assertFalse(any(name == "StopRecord" for name, _ in self.obs.calls))

    def test_start_lost_ack_keeps_persistent_uncertainty_without_adopting_active_output(self):
        self.obs.start_ack_error = obs_bridge.ObsError("Synthetic acknowledgement lost")
        with self.assertRaises(obs_bridge.ObsError):
            self.start()
        manifest = self.unresolved_manifest()
        self.assertTrue(self.obs.active)
        self.assertIn("start_requested_at", manifest)
        self.assertEqual(1, len(self.obs.clients))
        self.assertTrue(self.obs.clients[0].closed)
        self.assertFalse(any(name == "StopRecord" for name, _ in self.obs.calls))

    def test_malformed_start_status_keeps_uncertainty_and_directory(self):
        for index, status in enumerate(({}, {"outputActive": None}, {"outputActive": 1}, {"outputActive": "false"})):
            with self.subTest(status=status):
                # Separate evidence roots model independent failed launches.
                self.evidence = self.root / ("malformed-" + str(index))
                self.obs = FakeObsState(self.original_directory)
                self.service = obs_bridge.CaptureService(self.obs.factory, self.evidence)
                self.obs.start_status_override = status
                with self.assertRaises(obs_bridge.ObsError):
                    self.start()
                self.unresolved_manifest()

    def test_active_without_start_event_does_not_grant_lease(self):
        self.obs.emit_start_event = False
        with self.fast_transitions(), self.assertRaises(obs_bridge.ObsError):
            self.start()
        self.unresolved_manifest()

    def test_start_wait_rejects_intervening_stop_restart(self):
        self.obs.start_delay = 1
        self.obs.start_hook = self.obs.replace_recording
        with self.fast_transitions(), self.assertRaises(obs_bridge.ObsError):
            self.start()
        self.unresolved_manifest()
        self.assertFalse(any(name == "StopRecord" for name, _ in self.obs.calls))

    def test_stop_ack_waits_for_inactive_and_stopped_event_before_file_verification(self):
        started = self.start()
        self.obs.stop_delay = 2
        with self.fast_transitions():
            stopped = self.service.stop_recording(started["session_id"], dry_run=False)
        self.assertEqual("completed", stopped["state"])
        self.assertFalse(stopped["may_be_recording"])
        self.assertIn("stop_confirmed_at", stopped)
        self.assertEqual(hashlib.sha256(RECORDING_BYTES).hexdigest(), stopped["output_sha256"])
        self.assertTrue(stopped["record_directory_restored"])
        self.assertEqual(1, sum(name == "StopRecord" for name, _ in self.obs.calls))
        self.assertEqual(1, len(self.obs.clients))

    def test_stop_timeout_cannot_restore_or_clear_uncertainty(self):
        started = self.start()
        self.obs.stop_delay = 100
        with self.fast_transitions(), self.assertRaises(obs_bridge.ObsError):
            self.service.stop_recording(started["session_id"], dry_run=False)
        self.unresolved_manifest()
        self.assertTrue(self.obs.active)

    def test_stop_lost_ack_after_inactive_still_preserves_uncertainty(self):
        started = self.start()
        self.obs.stop_ack_error = obs_bridge.ObsError("Synthetic stop acknowledgement lost")
        with self.assertRaises(obs_bridge.ObsError):
            self.service.stop_recording(started["session_id"], dry_run=False)
        self.unresolved_manifest()
        self.assertFalse(self.obs.active)

    def test_stop_followed_by_new_pending_start_never_restores_directory(self):
        started = self.start()
        self.obs.stop_hook = lambda: self.obs.emit("OBS_WEBSOCKET_OUTPUT_STARTING")
        with self.assertRaises(obs_bridge.ObsError):
            self.service.stop_recording(started["session_id"], dry_run=False)
        self.unresolved_manifest()
        self.assertFalse(self.obs.active)

    def test_pending_start_ingested_during_directory_read_prevents_restore_setter(self):
        started = self.start()
        self.obs.get_directory_hook = lambda: self.obs.emit("OBS_WEBSOCKET_OUTPUT_STARTING")
        stopped = self.service.stop_recording(started["session_id"], dry_run=False)
        self.assertEqual("completed", stopped["state"])
        self.assertFalse(stopped["record_directory_restored"])
        self.assertEqual(started["record_directory"], self.obs.directory)
        self.assertEqual(1, sum(name == "SetRecordDirectory" for name, _ in self.obs.calls))

    def test_external_pending_start_during_prestart_failure_prevents_restore(self):
        self.obs.fail_requests["SetCurrentProgramScene"] = obs_bridge.ObsError("Synthetic scene failure")
        def inject_only_during_cleanup():
            if any(name == "SetCurrentProgramScene" for name, _ in self.obs.calls):
                self.obs.emit("OBS_WEBSOCKET_OUTPUT_STARTING")
        self.obs.get_directory_hook = inject_only_during_cleanup
        with self.assertRaises(obs_bridge.ObsError):
            self.start()
        session = self.service.capture_sessions()["sessions"][0]
        manifest = self.manifest(session["session_id"])
        self.assertFalse(manifest["record_directory_restored"])
        self.assertEqual(manifest["record_directory"], self.obs.directory)
        self.assertEqual(1, sum(name == "SetRecordDirectory" for name, _ in self.obs.calls))
        self.assertFalse(any(name == "StartRecord" for name, _ in self.obs.calls))

    def test_contradictory_start_event_cannot_grant_lease(self):
        def contradict_event():
            self.obs.clients[0].record_events[-1]["outputActive"] = False
        self.obs.start_hook = contradict_event
        with self.assertRaises(obs_bridge.ObsError):
            self.start()
        self.unresolved_manifest()

    def test_contradictory_stop_event_cannot_claim_finalization(self):
        started = self.start()
        def contradict_event():
            self.obs.clients[0].record_events[-1]["outputActive"] = True
        self.obs.stop_hook = contradict_event
        with self.assertRaises(obs_bridge.ObsError):
            self.service.stop_recording(started["session_id"], dry_run=False)
        self.unresolved_manifest()

    def test_wait_caps_rpc_timeout_and_refuses_success_after_deadline(self):
        client = self.obs.factory()
        original = client.settings
        clock = [0.0]
        observed_timeouts = []
        def delayed_status(_name):
            observed_timeouts.append(client.settings.timeout)
            clock[0] += 0.4
            self.obs.active = True
            self.obs.emit("OBS_WEBSOCKET_OUTPUT_STARTED")
            return {"outputActive": True}
        with patch.object(obs_bridge, "RECORD_TRANSITION_TIMEOUT", 0.3), \
                patch.object(obs_bridge.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(client, "request", side_effect=delayed_status), \
                self.assertRaisesRegex(obs_bridge.ObsError, "wait limit"):
            self.service._wait_record_state(client, 0, True)
        self.assertEqual([0.3], observed_timeouts)
        self.assertIs(original, client.settings)

    def assert_private_payload(self, result):
        serialized = json.dumps(result)
        self.assertNotIn(SECRET, serialized)
        self.assertNotIn("imageData", serialized)
        self.assertNotIn("data:image", serialized)
        for image in self.obs.screenshots:
            self.assertNotIn(image.split(",", 1)[1], serialized)

    def test_inspection_and_default_previews_do_not_write_or_mutate(self):
        results = [self.service.status(), self.service.scenes(),
                   self.service.scene_sources("Course"), self.service.capture_sessions(),
                   self.service.select_scene("Other"),
                   self.service.start_recording("Lesson", "test", "Course")]
        self.assertTrue(results[-1]["dry_run"])
        self.assertTrue(results[-2]["dry_run"])
        self.assertFalse(self.evidence.exists())
        self.assertEqual([], self.mutations())
        self.assertEqual("Course", self.obs.current_scene)
        self.assertFalse(self.obs.active)
        for result in results:
            self.assert_private_payload(result)

    def test_preexisting_recording_and_stream_refused_without_mutation(self):
        for attribute in ("active", "streaming"):
            with self.subTest(attribute=attribute):
                setattr(self.obs, attribute, True)
                with self.assertRaises(obs_bridge.ObsError):
                    self.start()
                with self.assertRaises(obs_bridge.ObsError):
                    self.service.select_scene("Other", dry_run=False)
                setattr(self.obs, attribute, False)
        self.assertFalse(self.evidence.exists())
        self.assertEqual([], self.mutations())

    def test_missing_start_capability_prevents_artifacts_and_partial_configuration(self):
        self.obs.missing_capabilities.add("StartRecord")
        with self.assertRaises(obs_bridge.ObsError):
            self.start()
        self.assertFalse(self.evidence.exists())
        self.assertEqual([], self.mutations())

    def test_recording_control_requires_explicit_environment_opt_in(self):
        with patch.dict("os.environ", {"OBS_MCP_ALLOW_OUTPUT_CONTROL": "0"}):
            preview = self.service.start_recording("Title", "Category", "Course")
            self.assertTrue(preview["dry_run"])
            with self.assertRaises(obs_bridge.ObsError):
                self.start()
        self.assertFalse(self.evidence.exists())
        self.assertEqual([], self.mutations())

    def test_missing_scene_is_refused_before_any_change(self):
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run), self.assertRaises(obs_bridge.ObsError):
                self.service.start_recording("Lesson", "test", "missing", dry_run=dry_run)
        self.assertFalse(self.evidence.exists())
        self.assertEqual([], self.mutations())

    def test_unknown_recording_or_streaming_status_refuses_mutations(self):
        for attribute in ("record_status_override", "stream_status_override"):
            for status in ({}, {"outputActive": None}, {"outputActive": 0}, {"outputActive": "false"}):
                with self.subTest(attribute=attribute, status=status):
                    setattr(self.obs, attribute, status)
                    with self.assertRaises(obs_bridge.ObsError):
                        self.start()
                    with self.assertRaises(obs_bridge.ObsError):
                        self.service.select_scene("Other", dry_run=False)
                    setattr(self.obs, attribute, None)
        self.assertFalse(self.evidence.exists())
        self.assertEqual([], self.mutations())

    def test_non_object_manifest_is_rejected_as_unproven_ownership(self):
        session_id = "a" * 32
        path = self.evidence / session_id / "manifest.json"
        path.parent.mkdir(parents=True)
        for malformed in ([], None, "invalid"):
            with self.subTest(manifest=malformed):
                path.write_text(json.dumps(malformed), encoding="utf-8")
                with self.assertRaises(obs_bridge.ObsError):
                    self.service.stop_recording(session_id, dry_run=False)
        self.assertEqual([], self.obs.calls)

    def test_unapplied_recording_directory_change_prevents_start(self):
        self.obs.ignore_directory_changes = True
        with self.assertRaises(obs_bridge.ObsError):
            self.start()
        self.assertFalse(self.obs.active)
        self.assertEqual(str(self.original_directory), self.obs.directory)
        self.assertNotIn("StartRecord", [name for name, _ in self.obs.calls])
        self.assertEqual("failed", self.service.capture_sessions()["sessions"][0]["state"])

    def test_invalid_session_and_evidence_paths_do_not_escape(self):
        for identifier in ("../outside", "..\\outside", "A" * 32, "a" * 31,
                           "a" * 33, "", None, "C:" + chr(92) + "outside"):
            with self.subTest(session_id=identifier), self.assertRaises((ValueError, obs_bridge.ObsError)):
                self.service.stop_recording(identifier, dry_run=False)
        for path in ("relative-evidence", "\\\\server\\share", "//server/share", "https://example.invalid/evidence"):
            with self.subTest(evidence_root=path), self.assertRaises((ValueError, obs_bridge.ObsError)):
                obs_bridge.CaptureService(client_factory=self.obs.factory, evidence_root=path)
        self.assertFalse(self.evidence.exists())
        self.assertEqual([], self.obs.calls)

    def test_completed_file_hash_directory_restoration_and_new_session_identity(self):
        first = self.start()
        self.assertTrue(self.obs.active)
        self.assertEqual(self.evidence / first["session_id"], Path(self.obs.directory))
        self.assertNotEqual(self.original_directory, Path(self.obs.directory))
        completed = self.service.stop_recording(first["session_id"], dry_run=False)
        self.assertEqual("completed", completed["state"])
        self.assertTrue(completed["file_finalized"])
        self.assertFalse(completed["capture_verified"])
        self.assertEqual(hashlib.sha256(RECORDING_BYTES).hexdigest(), completed["output_sha256"])
        self.assertEqual(len(RECORDING_BYTES), completed["output_bytes"])
        self.assertEqual(RECORDING_BYTES, Path(completed["output_path"]).read_bytes())
        self.assertEqual(str(self.original_directory), self.obs.directory)
        self.assertTrue(completed["record_directory_restored"])
        second = self.start()
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertNotEqual(first["record_directory"], second["record_directory"])
        self.service.stop_recording(second["session_id"], dry_run=False)
        self.assertEqual(2, self.service.capture_sessions()["total"])
        self.assert_private_payload(completed)

    def test_stop_preview_preserves_recording_and_manifest(self):
        started = self.start()
        path = self.evidence / started["session_id"] / "manifest.json"
        before = path.read_bytes()
        before_mutations = list(self.mutations())
        result = self.service.stop_recording(started["session_id"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(self.obs.active)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(before_mutations, self.mutations())

    def test_restart_cannot_adopt_persisted_recording(self):
        started = self.start()
        restarted = obs_bridge.CaptureService(client_factory=self.obs.factory, evidence_root=self.evidence)
        with self.assertRaises(obs_bridge.ObsError):
            restarted.stop_recording(started["session_id"], dry_run=False)
        self.assertTrue(self.obs.active)
        self.assertNotIn("StopRecord", [name for name, _ in self.obs.calls])
        self.assertFalse(restarted.capture_sessions()["sessions"][0]["stop_ownership_held"])

    def test_external_stop_and_restart_invalidates_original_lease(self):
        started = self.start()
        self.obs.replace_recording()
        with self.assertRaises(obs_bridge.ObsError):
            self.service.stop_recording(started["session_id"], dry_run=False)
        self.assertTrue(self.obs.active, "Replacement recording must remain untouched")
        self.assertNotIn("StopRecord", [name for name, _ in self.obs.calls])

    def test_lost_connection_does_not_reconnect_to_stop(self):
        started = self.start()
        self.obs.clients[-1].close()
        connections_before = len(self.obs.clients)
        with self.assertRaises(obs_bridge.ObsError):
            self.service.stop_recording(started["session_id"], dry_run=False)
        self.assertEqual(connections_before, len(self.obs.clients))
        self.assertTrue(self.obs.active)
        self.assertNotIn("StopRecord", [name for name, _ in self.obs.calls])

    def test_missing_finalized_file_cannot_be_reported_complete(self):
        started = self.start()
        self.obs.write_output = False
        with self.assertRaises(obs_bridge.ObsError):
            self.service.stop_recording(started["session_id"], dry_run=False)
        manifest = self.manifest(started["session_id"])
        self.assertEqual("failed", manifest["state"])
        self.assertNotIn("output_sha256", manifest)
        self.assertFalse(manifest.get("file_finalized", False))
        with self.assertRaises(obs_bridge.ObsError):
            self.service.stop_recording(started["session_id"], dry_run=False)
        self.assertEqual(1, sum(name == "StopRecord" for name, _ in self.obs.calls))

    def test_unexpected_output_path_is_rejected_without_reading_external_file(self):
        started = self.start()
        self.obs.output_override = self.root / "outside-the-session.mkv"
        original_open = Path.open
        external_reads = []

        def observed_open(path, mode="r", *args, **kwargs):
            if path == self.obs.output_override and "r" in mode:
                external_reads.append(path)
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", observed_open), self.assertRaises(obs_bridge.ObsError):
            self.service.stop_recording(started["session_id"], dry_run=False)
        self.assertEqual([], external_reads)
        manifest = self.manifest(started["session_id"])
        self.assertEqual("failed", manifest["state"])
        self.assertNotIn("output_sha256", manifest)

    def test_operator_directory_change_is_not_overwritten_on_stop(self):
        started = self.start()
        actual_recording = Path(started["record_directory"]) / "recording.mkv"
        self.obs.output_override = actual_recording
        operator_directory = self.root / "operator-selected-output"
        self.obs.directory = str(operator_directory)
        completed = self.service.stop_recording(started["session_id"], dry_run=False)
        self.assertEqual("completed", completed["state"])
        self.assertFalse(completed["record_directory_restored"])
        self.assertEqual(str(operator_directory), self.obs.directory)

    def test_pre_start_failure_restores_directory_without_recording(self):
        self.obs.fail_requests["SetCurrentProgramScene"] = obs_bridge.ObsError("Synthetic scene switch failure")
        with self.assertRaises(obs_bridge.ObsError):
            self.start()
        self.assertFalse(self.obs.active)
        self.assertNotIn("StartRecord", [name for name, _ in self.obs.calls])
        self.assertEqual(str(self.original_directory), self.obs.directory)
        sessions = self.service.capture_sessions()["sessions"]
        self.assertEqual(1, len(sessions))
        self.assertEqual("failed", sessions[0]["state"])

    def test_black_capture_and_stationary_chart_have_distinct_health_evidence(self):
        for color, near_black in (((0, 0, 0), True), ((80, 100, 120), False)):
            with self.subTest(color=color), patch.object(obs_bridge.time, "sleep"):
                self.obs.screenshots = [png_data(color)]
                result = self.service.capture_health("Course", samples=2, interval_seconds=0.1)
                self.assertEqual(near_black, result["all_near_black"])
                self.assertTrue(result["unchanged"])
                self.assertFalse(result["capture_verified"])
                self.assertNotIn("failed", result.get("state", ""))
                self.assert_private_payload(result)
                for sample in result["samples"]:
                    path = Path(sample["local_path"])
                    self.assertTrue(path.is_relative_to(self.evidence))
                    with Image.open(path) as saved:
                        self.assertEqual(color, saved.convert("RGB").getpixel((0, 0)))

    def test_health_rejects_malformed_data_without_returning_pixels(self):
        self.obs.screenshots = ["data:image/png;base64,not-valid-base64!" + SECRET]
        with self.assertRaises(obs_bridge.ObsError) as caught:
            self.service.capture_health("Course", samples=2, interval_seconds=0.1)
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertFalse(list(self.evidence.rglob("*.png")))

    def test_settings_password_is_not_repr_and_bad_configuration_is_sanitized(self):
        settings = ObsSettings(password=SECRET)
        self.assertNotIn(SECRET, repr(settings))
        with patch.dict("os.environ", {"APPDATA": str(self.root), "OBS_MCP_PORT": SECRET,
                                       "OBS_MCP_PASSWORD": SECRET}, clear=False):
            with self.assertRaises(obs_bridge.ObsError) as caught:
                ObsSettings.from_environment()
        self.assertNotIn(SECRET, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
