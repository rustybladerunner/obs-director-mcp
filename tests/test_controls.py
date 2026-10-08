"""Production controls checked against a stateful synthetic OBS implementation."""
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from obs_director.controls import ProductionService
from obs_director.transport import ObsError


class FakeState:
    def __init__(self):
        self.calls = []
        self.recording = False
        self.streaming = False
        self.virtualcam = False
        self.replay = False
        self.replay_path = ""
        self.scenes = ["Main", "Detail"]
        self.program = "Main"
        self.preview = "Detail"
        self.studio = True
        self.inputs = {"Caption": "text_gdiplus_v2", "Movie": "ffmpeg_source", "Mic": "wasapi_input_capture"}
        self.input_settings = {"Caption": {"text": "original"}, "Movie": {}, "Mic": {}}
        self.items = {"Main": [{"sourceName": "Caption", "inputKind": "text_gdiplus_v2", "sceneItemId": 1, "sceneItemEnabled": True}], "Detail": []}
        self.transform = {"positionX": 0.0, "positionY": 0.0, "scaleX": 1.0, "scaleY": 1.0}
        self.muted = False
        self.volume = -6.0
        self.filter_enabled = True
        self.filter_settings = {"contrast": 0.0}
        self.cursor = 0
        self.media_state = "OBS_MEDIA_STATE_STOPPED"
        self.missing = set()
        self.noop = set()
        self.capabilities = {"GetRecordStatus", "GetStreamStatus", "GetCurrentProgramScene", "GetCurrentPreviewScene",
            "GetVersion", "GetSceneList", "GetInputList", "GetInputKindList", "GetStudioModeEnabled",
            "GetSceneItemList", "GetSourceFilterList", "CreateScene", "CreateInput", "SetCurrentProgramScene",
            "SetCurrentPreviewScene", "GetInputSettings", "SetInputSettings", "GetSceneItemEnabled",
            "SetSceneItemEnabled", "GetSceneItemTransform", "SetSceneItemTransform", "GetInputMute",
            "SetInputMute", "GetInputVolume", "SetInputVolume", "GetSourceFilter", "SetSourceFilterSettings",
            "SetSourceFilterEnabled", "GetMediaInputStatus", "SetMediaInputCursor", "TriggerMediaInputAction",
            "GetVirtualCamStatus", "StartVirtualCam", "StopVirtualCam", "StartStream", "StopStream",
            "GetReplayBufferStatus", "StartReplayBuffer", "StopReplayBuffer", "SaveReplayBuffer", "GetLastReplayBufferReplay"}

    def factory(self):
        return FakeClient(self)

    def mutations(self):
        return [name for name, _ in self.calls if not name.startswith("Get")]


class FakeClient:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def version(self):
        return {"obsVersion": "32.1", "obsWebSocketVersion": "5.7", "rpcVersion": 1,
                "availableRequests": sorted(self.state.capabilities - self.state.missing)}

    def require_capabilities(self, names):
        if set(names) - (self.state.capabilities - self.state.missing):
            raise ObsError("Required capability unavailable")

    def request(self, name, data=None):
        self.require_capabilities([name])
        data = data or {}
        state = self.state
        state.calls.append((name, dict(data)))
        if name in state.noop:
            return {}
        if name == "GetVersion":
            return self.version()
        if name in ("GetRecordStatus", "GetStreamStatus", "GetVirtualCamStatus", "GetReplayBufferStatus"):
            attr = {"GetRecordStatus": "recording", "GetStreamStatus": "streaming", "GetVirtualCamStatus": "virtualcam", "GetReplayBufferStatus": "replay"}[name]
            return {"outputActive": getattr(state, attr)}
        if name == "GetSceneList":
            return {"currentProgramSceneName": state.program, "currentPreviewSceneName": state.preview,
                    "scenes": [{"sceneName": value, "sceneIndex": i} for i, value in enumerate(state.scenes)]}
        if name == "CreateScene":
            state.scenes.append(data["sceneName"])
            state.items[data["sceneName"]] = []
        elif name == "GetCurrentProgramScene":
            return {"currentProgramSceneName": state.program}
        elif name == "GetCurrentPreviewScene":
            return {"currentPreviewSceneName": state.preview}
        elif name == "SetCurrentProgramScene":
            state.program = data["sceneName"]
        elif name == "SetCurrentPreviewScene":
            state.preview = data["sceneName"]
        elif name == "GetStudioModeEnabled":
            return {"studioModeEnabled": state.studio}
        elif name == "GetInputList":
            return {"inputs": [{"inputName": key, "inputKind": value} for key, value in state.inputs.items()]}
        elif name == "GetInputKindList":
            return {"inputKinds": ["text_gdiplus_v2", "ffmpeg_source", "wasapi_input_capture", "color_source_v3"]}
        elif name == "CreateInput":
            state.inputs[data["inputName"]] = data["inputKind"]
            state.input_settings[data["inputName"]] = dict(data["inputSettings"])
            state.items[data["sceneName"]].append({"sourceName": data["inputName"], "inputKind": data["inputKind"], "sceneItemId": 2, "sceneItemEnabled": True})
            return {"sceneItemId": 2}
        elif name == "GetSceneItemList":
            return {"sceneItems": state.items[data["sceneName"]]}
        elif name == "GetInputSettings":
            return {"inputSettings": state.input_settings[data["inputName"]]}
        elif name == "SetInputSettings":
            state.input_settings[data["inputName"]].update(data["inputSettings"])
        elif name == "GetSceneItemEnabled":
            return {"sceneItemEnabled": state.items[data["sceneName"]][0]["sceneItemEnabled"]}
        elif name == "SetSceneItemEnabled":
            state.items[data["sceneName"]][0]["sceneItemEnabled"] = data["sceneItemEnabled"]
        elif name == "GetSceneItemTransform":
            return {"sceneItemTransform": dict(state.transform)}
        elif name == "SetSceneItemTransform":
            state.transform.update(data["sceneItemTransform"])
        elif name == "GetInputMute":
            return {"inputMuted": state.muted}
        elif name == "SetInputMute":
            state.muted = data["inputMuted"]
        elif name == "GetInputVolume":
            return {"inputVolumeDb": state.volume}
        elif name == "SetInputVolume":
            state.volume = data["inputVolumeDb"]
        elif name == "GetSourceFilterList":
            return {"filters": [{"filterName": "Color", "filterKind": "color_filter_v2", "filterEnabled": state.filter_enabled,
                                 "filterSettings": {"private_fixture": "do not echo this"}}]}
        elif name == "GetSourceFilter":
            return {"filterEnabled": state.filter_enabled, "filterSettings": dict(state.filter_settings)}
        elif name == "SetSourceFilterSettings":
            state.filter_settings.update(data["filterSettings"])
        elif name == "SetSourceFilterEnabled":
            state.filter_enabled = data["filterEnabled"]
        elif name == "GetMediaInputStatus":
            return {"mediaState": state.media_state, "mediaCursor": state.cursor, "mediaDuration": 100000}
        elif name == "SetMediaInputCursor":
            state.cursor = data["mediaCursor"]
        elif name == "TriggerMediaInputAction":
            action = data["mediaAction"].rsplit("_", 1)[1]
            state.media_state = "OBS_MEDIA_STATE_" + {"PLAY": "PLAYING", "RESTART": "PLAYING", "PAUSE": "PAUSED", "STOP": "STOPPED", "NEXT": "PLAYING", "PREVIOUS": "PLAYING"}[action]
            if action == "RESTART":
                state.cursor = 0
        elif name in ("StartVirtualCam", "StopVirtualCam", "StartStream", "StopStream", "StartReplayBuffer", "StopReplayBuffer"):
            attr = "virtualcam" if name.endswith("VirtualCam") else "streaming" if name.endswith("Stream") else "replay"
            setattr(state, attr, name.startswith("Start"))
        elif name == "GetLastReplayBufferReplay":
            return {"savedReplayPath": state.replay_path}
        elif name == "SaveReplayBuffer":
            state.replay_path = str(Path.cwd() / "synthetic-replay.mkv")
        else:
            raise AssertionError("Unexpected request " + name)
        return {}


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.state = FakeState()
        self.service = ProductionService(self.state.factory)

    def test_inventory_omits_settings_and_previews_do_not_mutate(self):
        values = [self.service.status(), self.service.capabilities(), self.service.scenes(),
                  self.service.inputs(), self.service.scene_sources("Main"), self.service.filters("Caption"),
                  self.service.select_scene("Detail"), self.service.audio_mute("Mic", True),
                  self.service.source_settings("Caption", {"text": "private caption"})]
        self.assertEqual([], self.state.mutations())
        serialized = json.dumps(values)
        self.assertNotIn("private caption", serialized)
        self.assertNotIn("private_fixture", serialized)

    def test_typed_controls_apply_and_verify_every_supported_domain(self):
        results = [self.service.create_scene("New", False), self.service.select_scene("Detail", dry_run=False),
            self.service.select_scene("Main", target="preview", dry_run=False),
            self.service.add_source("New", "Background", "color_source_v3", {"color": 0}, False),
            self.service.source_settings("Caption", {"text": "updated"}, False),
            self.service.source_visibility("Main", 1, False, False),
            self.service.source_transform("Main", 1, {"positionX": 120, "scaleX": 0.5}, False),
            self.service.audio_mute("Mic", True, False), self.service.audio_volume("Mic", -12, False),
            self.service.filter_enabled("Caption", "Color", False, False),
            self.service.filter_settings("Caption", "Color", {"contrast": 0.2}, False),
            self.service.media_action("Movie", "play", False), self.service.media_seek("Movie", 10000, False)]
        self.assertTrue(all(item["applied"] and item["verified"] for item in results), results)
        self.assertEqual("updated", self.state.input_settings["Caption"]["text"])
        self.assertEqual(120, self.state.transform["positionX"])
        self.assertEqual(-12, self.state.volume)
        self.assertEqual(10000, self.state.cursor)
        self.assertNotIn("updated", json.dumps(results))

    def test_active_output_requires_live_opt_in_including_dry_run(self):
        for field in ("recording", "streaming"):
            with self.subTest(field=field):
                setattr(self.state, field, True)
                with self.assertRaises(ObsError):
                    self.service.select_scene("Detail", dry_run=False)
                self.assertEqual([], self.state.mutations())
                result = self.service.select_scene("Detail", allow_live=True)
                self.assertFalse(result["applied"])
                setattr(self.state, field, False)
        self.state.recording = True
        self.assertTrue(self.service.select_scene("Detail", dry_run=False, allow_live=True)["verified"])

    def test_unknown_status_and_missing_capability_fail_before_mutation(self):
        for value in (None, 0, "false"):
            self.state.recording = value
            with self.assertRaises(ObsError):
                self.service.audio_mute("Mic", True, False, True)
        self.state.recording = False
        self.state.missing.add("SetInputMute")
        with self.assertRaises(ObsError):
            self.service.audio_mute("Mic", True, False)
        self.assertEqual([], self.state.mutations())

    def test_wrong_references_and_invalid_values_fail_before_mutation(self):
        cases = [lambda: self.service.select_scene("Missing", dry_run=False),
            lambda: self.service.source_visibility("Main", 999, True, False),
            lambda: self.service.filter_enabled("Caption", "Missing", True, False),
            lambda: self.service.audio_mute("Absent", True, False),
            lambda: self.service.source_transform("Main", 1, {"scaleX": float("nan")}, False),
            lambda: self.service.source_transform("Main", 1, {"positionX": 10**1000}, False),
            lambda: self.service.audio_volume("Mic", 100, False),
            lambda: self.service.media_seek("Movie", 100001, False),
            lambda: self.service.source_settings("Movie", {"text": "wrong kind"}, False)]
        for case in cases:
            with self.subTest(case=case), self.assertRaises((ObsError, ValueError)):
                case()
        self.assertEqual([], self.state.mutations())

    def test_preview_needs_studio_mode(self):
        self.state.studio = False
        with self.assertRaises(ObsError):
            self.service.select_scene("Main", "preview", False)
        self.assertEqual([], self.state.mutations())

    def test_acknowledgement_is_not_state_verification(self):
        self.state.noop.add("SetInputMute")
        result = self.service.audio_mute("Mic", True, False)
        self.assertTrue(result["applied"])
        self.assertFalse(result["verified"])
        result = self.service.media_action("Movie", "next", False)
        self.assertFalse(result["verified"])

    def test_restart_must_observe_cursor_reset_not_merely_playing(self):
        self.state.cursor = 50000
        self.state.media_state = "OBS_MEDIA_STATE_PLAYING"
        self.state.noop.add("TriggerMediaInputAction")
        rejected = self.service.media_action("Movie", "restart", False)
        self.assertTrue(rejected["applied"])
        self.assertFalse(rejected["verified"])
        self.assertEqual(50000, self.state.cursor)
        self.state.noop.clear()
        accepted = self.service.media_action("Movie", "restart", False)
        self.assertTrue(accepted["verified"])
        self.assertEqual(0, self.state.cursor)
        self.state.noop.add("TriggerMediaInputAction")
        ambiguous = self.service.media_action("Movie", "restart", False)
        self.assertFalse(ambiguous["verified"])

    def test_paused_seek_noop_fails_even_inside_frame_tolerance(self):
        self.state.cursor = 10000
        self.state.media_state = "OBS_MEDIA_STATE_PAUSED"
        self.state.noop.add("SetMediaInputCursor")
        for target in (10500, 10010):
            with self.subTest(target=target):
                result = self.service.media_seek("Movie", target, False)
                self.assertTrue(result["applied"])
                self.assertFalse(result["verified"])
                self.assertEqual(10000, self.state.cursor)
        self.state.noop.clear()
        self.assertTrue(self.service.media_seek("Movie", 10500, False)["verified"])

    def test_playing_seek_requires_change_distinct_from_normal_playback(self):
        self.state.cursor = 10000
        self.state.media_state = "OBS_MEDIA_STATE_PLAYING"
        self.state.noop.add("SetMediaInputCursor")
        self.assertFalse(self.service.media_seek("Movie", 10050, False)["verified"])
        self.state.noop.clear()
        self.assertTrue(self.service.media_seek("Movie", 20000, False)["verified"])

    def test_seek_noop_interval_includes_delayed_prewrite_snapshot(self):
        clock = [0.0]

        class DelayedSnapshot(FakeClient):
            reads = 0

            def request(self, name, data=None):
                result = super().request(name, data)
                if name == "GetMediaInputStatus":
                    self.reads += 1
                    if self.reads == 2:
                        # OBS sampled at 50s, then normal playback advanced while the reply travelled.
                        self.state.cursor += 2000
                        clock[0] += 2.0
                return result

        self.state.cursor = 50000
        self.state.media_state = "OBS_MEDIA_STATE_PLAYING"
        self.state.noop.add("SetMediaInputCursor")
        service = ProductionService(lambda: DelayedSnapshot(self.state))
        with patch("obs_director.controls.time.monotonic", side_effect=lambda: clock[0]):
            result = service.media_seek("Movie", 52000, False)
        self.assertEqual(52000, self.state.cursor)
        self.assertFalse(result["verified"])

    def test_accepted_control_preserves_applied_receipt_when_readback_fails(self):
        class ReadFailure(FakeClient):
            def request(self, name, data=None):
                if name == "GetCurrentProgramScene" and self.state.program == "Detail":
                    raise ObsError("not-a-real-secret")
                return super().request(name, data)

        service = ProductionService(lambda: ReadFailure(self.state))
        result = service.select_scene("Detail", dry_run=False)
        self.assertEqual("Detail", self.state.program)
        self.assertTrue(result["applied"])
        self.assertFalse(result["verified"])
        self.assertTrue(result["uncertain"])
        self.assertNotIn("not-a-real-secret", json.dumps(result))

    def test_accepted_output_preserves_applied_receipt_when_readback_fails(self):
        class ReadFailure(FakeClient):
            def request(self, name, data=None):
                if name == "GetVirtualCamStatus" and self.state.virtualcam:
                    raise ObsError("not-a-real-secret")
                return super().request(name, data)

        service = ProductionService(lambda: ReadFailure(self.state))
        with patch.dict("os.environ", {"OBS_MCP_ALLOW_OUTPUT_CONTROL": "1"}):
            result = service.output_control("virtualcam", "start", False)
        self.assertTrue(self.state.virtualcam)
        self.assertTrue(result["applied"])
        self.assertFalse(result["verified"])
        self.assertTrue(result["uncertain"])
        self.assertNotIn("not-a-real-secret", json.dumps(result))

    def test_lost_write_acknowledgement_marks_application_unknown(self):
        class LostAck(FakeClient):
            def request(self, name, data=None):
                result = super().request(name, data)
                if name == "SetInputMute":
                    raise ObsError("not-a-real-secret")
                return result

        result = ProductionService(lambda: LostAck(self.state)).audio_mute("Mic", True, False)
        self.assertTrue(self.state.muted)
        self.assertIsNone(result["applied"])
        self.assertFalse(result["verified"])
        self.assertTrue(result["uncertain"])
        self.assertNotIn("not-a-real-secret", json.dumps(result))

    def test_output_controls_require_environment_opt_in_and_explicit_action(self):
        with patch.dict("os.environ", {}, clear=True):
            preview = self.service.output_control("stream", "start")
            self.assertTrue(preview["dry_run"])
            with self.assertRaises(ObsError):
                self.service.output_control("stream", "start", False)
        self.assertEqual([], self.state.mutations())
        with patch.dict("os.environ", {"OBS_MCP_ALLOW_OUTPUT_CONTROL": "1"}):
            for output in ("stream", "virtualcam", "replay"):
                self.assertTrue(self.service.output_control(output, "start", False)["verified"])
                if output == "replay":
                    self.assertTrue(self.service.output_control(output, "save", False)["verified"])
                self.assertTrue(self.service.output_control(output, "stop", False)["verified"])
        with self.assertRaises(ValueError):
            self.service.execute_action("output_control", {"output": "stream", "action": "start"}, False)


if __name__ == "__main__":
    unittest.main()
