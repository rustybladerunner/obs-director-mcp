"""Rehearsal cleanup refuses unknown outputs and proves its configuration target."""
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import rehearse
from obs_director.transport import ObsError, ObsSettings


class FakeClient:
    def __init__(self):
        self.settings = ObsSettings(timeout=20)
        self.profile = "Rehearsal profile"
        self.collection = "Rehearsal collection"
        self.requests = []
        self.active = False
        self.replay_error = None
        self.noop = False
        self.fail_read = False
        self.geometry = {"sourceWidth": 1280, "sourceHeight": 720, "width": 480, "height": 270,
                         "positionX": 760, "positionY": 380, "scaleX": 0.375, "scaleY": 0.375, "alignment": 5}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def request(self, name, data=None):
        self.requests.append((name, data))
        if name == "GetReplayBufferStatus" and self.replay_error:
            raise self.replay_error
        if name.endswith("Status"):
            return {"outputActive": self.active}
        if name == "GetProfileList":
            return {"currentProfileName": self.profile}
        if name == "GetSceneCollectionList":
            return {"currentSceneCollectionName": self.collection}
        if name == "SetCurrentProfile" and not self.noop:
            self.profile = data["profileName"]
        if name == "SetCurrentSceneCollection" and not self.noop:
            self.collection = data["sceneCollectionName"]
        if name == "GetCurrentProgramScene":
            return {"currentProgramSceneName": "Original scene"}
        if name == "GetVideoSettings":
            if self.fail_read:
                raise ObsError("test-only")
            return {"baseWidth": 1280}
        if name == "GetSourceScreenshot":
            return {"imageData": "data:image/png;base64,AA=="}
        if name == "GetSceneItemTransform":
            return {"sceneItemTransform": self.geometry.copy() if isinstance(self.geometry, dict) else self.geometry}
        return {}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class DelayedClient(FakeClient):
    """Selection changes appear only after repeated readback requests."""
    def __init__(self):
        super().__init__()
        self.pending = {}

    def request(self, name, data=None):
        actions = {"CreateProfile": ("profile", "profileName"),
                   "SetCurrentProfile": ("profile", "profileName"),
                   "CreateSceneCollection": ("collection", "sceneCollectionName"),
                   "SetCurrentSceneCollection": ("collection", "sceneCollectionName")}
        if name in actions:
            if self.pending:
                raise AssertionError("A new selection command preceded the earlier readback")
            self.requests.append((name, data))
            attribute, key = actions[name]
            self.pending[attribute] = [data[key], 2]
            return {}
        attribute = {"GetProfileList": "profile", "GetSceneCollectionList": "collection"}.get(name)
        if attribute in self.pending:
            target, reads_left = self.pending[attribute]
            if reads_left:
                self.pending[attribute][1] -= 1
            else:
                setattr(self, attribute, target)
                del self.pending[attribute]
        if name == "SetProfileParameter" and (self.pending or not self.profile.startswith("OBS Director Rehearsal ")):
            raise AssertionError("Configuration preceded owned-profile readback")
        value = super().request(name, data)
        if name == "GetVideoSettings" and self.profile.startswith("OBS Director Rehearsal "):
            return {"baseWidth": 1280, "baseHeight": 720, "outputWidth": 1280,
                    "outputHeight": 720, "fpsNumerator": 30, "fpsDenominator": 1}
        return value


class RehearsalSafetyTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        clock_patch = patch.object(rehearse.time, "monotonic", self.clock.monotonic)
        sleep_patch = patch.object(rehearse.time, "sleep", self.clock.sleep)
        clock_patch.start()
        sleep_patch.start()
        self.addCleanup(clock_patch.stop)
        self.addCleanup(sleep_patch.stop)
        self.client = FakeClient()
        self.original = {"profile": "Original profile", "collection": "Original collection",
                         "scene": "Original scene", "video": {"baseWidth": 1280}}
        self.settings = ObsSettings(password="not-a-real-secret", timeout=20)

    def restore(self):
        settings_seen = []

        def factory(settings):
            settings_seen.append(settings)
            self.client.settings = settings
            return self.client

        result = rehearse.restore_original(self.settings, self.original, "Rehearsal profile", "Rehearsal collection", factory)
        self.assertEqual(len(settings_seen), 1)
        self.assertLessEqual(settings_seen[0].timeout, 3)
        self.assertFalse(any(name.startswith(("Stop", "Start")) for name, _ in self.client.requests))
        return result

    def test_only_documented_unavailable_replay_error_is_permitted(self):
        self.client.replay_error = ObsError("OBS rejected GetReplayBufferStatus (code 604)")
        rehearse.idle(self.client)
        for error in (ObsError("OBS rejected GetReplayBufferStatus (code 702)"),
                      ObsError("OBS rejected GetRecordStatus (code 604)"),
                      TimeoutError("test-only")):
            with self.subTest(error=type(error).__name__):
                self.client.replay_error = error
                with self.assertRaises(type(error)):
                    rehearse.idle(self.client)

    def test_missing_malformed_and_active_output_refuse(self):
        for active in (None, 0, "false", True):
            with self.subTest(active=active):
                self.client.active = active
                with self.assertRaises(RuntimeError):
                    rehearse.idle(self.client)

    def test_selection_requires_both_owned_names(self):
        rehearse.require_owned_selection(self.client, self.client.profile, self.client.collection)
        for profile, collection in (("Other", self.client.collection), (self.client.profile, "Other")):
            with self.assertRaises(RuntimeError):
                rehearse.require_owned_selection(self.client, profile, collection)
        self.assertTrue(all(name.startswith("Get") for name, _ in self.client.requests))

    def test_uncertain_lost_ack_receipt_is_a_failure(self):
        for applied in (None, False, True):
            with self.assertRaises(RuntimeError):
                rehearse.verified_receipt({"applied": applied, "verified": False}, "obs_audio_mute")
        self.assertEqual(rehearse.verified_receipt({"scenes": []}, "obs_scenes"), {"scenes": []})

    def test_fresh_connection_restores_only_own_names_and_checks_readback(self):
        result = self.restore()
        self.assertTrue(all(result.values()))
        setters = [(name, data) for name, data in self.client.requests if name.startswith("Set")]
        self.assertEqual(setters, [("SetCurrentProfile", {"profileName": "Original profile"}),
                                  ("SetCurrentSceneCollection", {"sceneCollectionName": "Original collection"})])

    def test_active_output_blocks_all_restoration_mutation(self):
        self.client.active = True
        result = self.restore()
        self.assertEqual(result["reason"], "restoration_unverified")
        self.assertFalse(any(name.startswith("Set") for name, _ in self.client.requests))

    def test_operator_selection_change_is_preserved(self):
        self.client.profile = "Operator selected another profile"
        result = self.restore()
        self.assertEqual(result["reason"], "operator_selection_changed")
        self.assertFalse(any(name.startswith("Set") for name, _ in self.client.requests))

    def test_noop_and_readback_failure_are_not_reported_restored(self):
        self.client.noop = True
        result = self.restore()
        self.assertFalse(result["profile"])
        self.assertFalse(result["collection"])
        self.client.noop = False
        self.client.fail_read = True
        result = self.restore()
        self.assertEqual(result["reason"], "restoration_unverified")
        self.assertFalse(result["profile"])

    def test_creation_ack_without_selected_owned_profile_never_configures_it(self):
        scratch = ROOT / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.client.profile = self.original["profile"]
        self.client.collection = self.original["collection"]
        fresh = FakeClient()
        fresh.profile = self.original["profile"]
        fresh.collection = self.original["collection"]
        with tempfile.TemporaryDirectory(dir=scratch) as temp, \
                patch.object(rehearse.ObsSettings, "from_environment", return_value=self.settings), \
                patch.object(rehearse, "ObsClient", side_effect=[self.client, fresh]), \
                patch.object(rehearse, "tone"):
            # This failure precedes run's first await, so no event loop/real sockets.
            coroutine = rehearse.run(SimpleNamespace(output=Path(temp) / "evidence", execute=True))
            with self.assertRaisesRegex(RuntimeError, "not fully restored"):
                coroutine.send(None)
        forbidden = {"SetVideoSettings", "SetProfileParameter", "SetInputMute"}
        self.assertFalse(any(name in forbidden for name, _ in self.client.requests))
        self.assertTrue(fresh.requests, "Cleanup must use a newly constructed connection")

    def test_wait_observes_delayed_profile_and_collection_without_mutations(self):
        client = DelayedClient()
        client.pending = {"profile": ["Target profile", 2], "collection": ["Target collection", 3]}
        settings = client.settings
        rehearse.wait_for_selection(client, "Target profile", "Target collection")
        self.assertEqual((client.profile, client.collection), ("Target profile", "Target collection"))
        self.assertGreaterEqual(sum(name == "GetProfileList" for name, _ in client.requests), 4)
        self.assertTrue(all(name.startswith("Get") for name, _ in client.requests))
        self.assertIs(client.settings, settings)

    def test_wait_timeout_bounds_reads_and_restores_request_timeout(self):
        settings = self.client.settings
        observed_timeouts = []
        request = self.client.request

        def bounded_request(name, data=None):
            observed_timeouts.append(self.client.settings.timeout)
            return request(name, data)

        with patch.object(self.client, "request", side_effect=bounded_request):
            with self.assertRaisesRegex(RuntimeError, "not selected before the timeout"):
                rehearse.wait_for_selection(self.client, "Missing profile", "Missing collection", timeout=0.25)
        self.assertAlmostEqual(self.clock.now, 0.25)
        self.assertTrue(all(0 < timeout <= 0.25 for timeout in observed_timeouts))
        self.assertIs(self.client.settings, settings)
        self.assertTrue(all(name.startswith("Get") for name, _ in self.client.requests))

    def geometry(self, **kwargs):
        return rehearse.wait_for_replay_geometry(self.client, "Rehearsal profile", "Rehearsal collection", 3, **kwargs)

    def test_replay_wait_observes_delayed_dimensions_before_layout_verification(self):
        original_request = self.client.request
        reads = []
        settings = self.client.settings

        def delayed(name, data=None):
            result = original_request(name, data)
            if name == "GetSceneItemTransform":
                reads.append(self.clock.now)
                if len(reads) < 3:
                    result["sceneItemTransform"].update(sourceWidth=0, sourceHeight=0)
            return result

        with patch.object(self.client, "request", side_effect=delayed):
            ready = self.geometry()
        self.assertEqual(ready["polls"], 3)
        self.assertEqual(ready["elapsed_ms"], 200)
        self.assertEqual(ready["geometry"], {"sourceWidth": 1280, "sourceHeight": 720})
        self.assertNotIn("positionX", ready["geometry"])
        layout = self.geometry(transformed=True)
        self.assertEqual((layout["geometry"]["width"], layout["geometry"]["height"]), (480, 270))
        self.assertTrue(layout["verified"])
        self.assertIs(self.client.settings, settings)
        self.assertTrue(all(name.startswith("Get") for name, _ in self.client.requests))

    def test_replay_zero_dimensions_and_wrong_layout_timeout_without_mutations(self):
        for transformed, changes in ((False, {"sourceWidth": 0, "sourceHeight": 0}),
                                     (False, {"sourceWidth": 640, "sourceHeight": 360}),
                                     (True, {"width": 0, "height": 0}),
                                     (True, {"width": 1280, "height": 720})):
            with self.subTest(transformed=transformed, changes=changes):
                self.client = FakeClient()
                self.client.geometry.update(changes)
                before = self.clock.now
                settings = self.client.settings
                with self.assertRaisesRegex(RuntimeError, "geometry.*timeout"):
                    self.geometry(transformed=transformed, timeout=0.25)
                self.assertAlmostEqual(self.clock.now - before, 0.25)
                self.assertIs(self.client.settings, settings)
                self.assertTrue(all(name.startswith("Get") for name, _ in self.client.requests))

    def test_replay_malformed_geometry_is_not_readiness(self):
        for malformed in (None, [], {}, {"sourceWidth": 1280},
                {"sourceWidth": True, "sourceHeight": 720}, {"sourceWidth": "1280", "sourceHeight": 720},
                {"sourceWidth": -1, "sourceHeight": 720}, {"sourceWidth": float("nan"), "sourceHeight": 720},
                {"sourceWidth": 10**1000, "sourceHeight": 720}):
            with self.subTest(malformed=malformed):
                self.client.geometry = malformed
                settings = self.client.settings
                with self.assertRaisesRegex(RuntimeError, "malformed replay geometry"):
                    self.geometry()
                self.assertIs(self.client.settings, settings)

    def test_replay_geometry_ownership_is_checked_before_and_after_each_poll(self):
        for changed_at in (1, 2):
            with self.subTest(changed_at=changed_at):
                self.client = FakeClient()
                original_request = self.client.request
                reads = []

                def changing(name, data=None):
                    result = original_request(name, data)
                    if name == "GetSceneItemTransform":
                        reads.append(True)
                        if len(reads) == changed_at:
                            self.client.collection = "Operator collection"
                        if changed_at == 2 and len(reads) == 1:
                            result["sceneItemTransform"].update(sourceWidth=0, sourceHeight=0)
                    return result

                with patch.object(self.client, "request", side_effect=changing):
                    with self.assertRaisesRegex(RuntimeError, "not selected"):
                        self.geometry()
                self.assertEqual(len(reads), changed_at)
                self.assertTrue(all(name.startswith("Get") for name, _ in self.client.requests))
        self.client = FakeClient()
        self.client.profile = "Operator profile"
        with self.assertRaisesRegex(RuntimeError, "not selected"):
            self.geometry()
        self.assertFalse(any(name == "GetSceneItemTransform" for name, _ in self.client.requests))

    def test_replay_deadline_caps_ownership_and_geometry_reads_and_rejects_late_success(self):
        settings = self.client.settings
        original_request = self.client.request
        budgets = []

        def delayed(name, data=None):
            budgets.append(self.client.settings.timeout)
            if name == "GetSceneItemTransform":
                self.clock.sleep(0.21)
            else:
                self.clock.sleep(0.02)
            return original_request(name, data)

        with patch.object(self.client, "request", side_effect=delayed):
            with self.assertRaisesRegex(RuntimeError, "geometry.*timeout"):
                self.geometry(timeout=0.25)
        self.assertEqual(len(budgets), 3)
        for actual, expected in zip(budgets, (0.25, 0.23, 0.21)):
            self.assertAlmostEqual(actual, expected)
        self.assertIs(self.client.settings, settings)

    def test_replay_geometry_read_error_restores_timeout(self):
        settings = self.client.settings
        with patch.object(self.client, "request", side_effect=ObsError("test-only")):
            with self.assertRaises(ObsError):
                self.geometry()
        self.assertIs(self.client.settings, settings)

    def test_delayed_creation_switches_configuration_and_restoration_are_sequenced(self):
        client = DelayedClient()
        client.profile, client.collection = self.original["profile"], self.original["collection"]
        exercised = []

        async def exercise(_client, _output, _trace, profile, collection):
            self.assertEqual((client.profile, client.collection), (profile, collection))
            self.assertFalse(client.pending)
            exercised.append(True)

        with patch.object(rehearse.ObsSettings, "from_environment", return_value=self.settings), \
                patch.object(rehearse, "ObsClient", return_value=client), \
                patch.object(rehearse, "tone"), patch.object(rehearse, "exercise", exercise), \
                patch.object(Path, "mkdir"), patch.object(Path, "write_text"), patch("builtins.print"):
            coroutine = rehearse.run(SimpleNamespace(output=ROOT / "runtime" / "probe", execute=True))
            with self.assertRaises(StopIteration):
                coroutine.send(None)
        self.assertEqual(exercised, [True])
        self.assertEqual((client.profile, client.collection), (self.original["profile"], self.original["collection"]))
        self.assertFalse(client.pending)
        self.assertEqual(sum(name == "SetCurrentProfile" for name, _ in client.requests), 5)
        self.assertEqual(sum(name == "SetCurrentSceneCollection" for name, _ in client.requests), 1)
        self.assertTrue(any(name == "SetProfileParameter" for name, _ in client.requests))
        self.assertFalse(any(name == "SetVideoSettings" for name, _ in client.requests))

    def test_cleanup_waits_for_late_creation_before_claiming_restoration(self):
        client = DelayedClient()
        client.profile, client.collection = self.original["profile"], self.original["collection"]
        client.pending["profile"] = ["Rehearsal profile", 2]
        result = rehearse.restore_original(self.settings, self.original, "Rehearsal profile", "Rehearsal collection",
            lambda settings: client, pending_selection=("Rehearsal profile", self.original["collection"]))
        self.assertTrue(all(result.values()))
        self.assertFalse(client.pending)
        self.assertEqual(client.profile, self.original["profile"])
        self.assertEqual(sum(name == "SetCurrentProfile" for name, _ in client.requests), 1)

    def test_cleanup_does_not_claim_restoration_while_creation_remains_pending(self):
        self.client.profile, self.client.collection = self.original["profile"], self.original["collection"]
        result = rehearse.restore_original(self.settings, self.original, "Rehearsal profile", "Rehearsal collection",
            lambda settings: self.client, pending_selection=("Rehearsal profile", self.original["collection"]))
        self.assertEqual(result["reason"], "restoration_unverified")
        self.assertFalse(any(result[key] for key in ("profile", "collection", "scene", "video")))
        self.assertFalse(any(name.startswith("Set") for name, _ in self.client.requests))

    def exercise_with_selection_change(self, *, wait=None, reconnect=False, before_filter=False, geometry_pending=False):
        """Run real orchestration with fake MCP, no event loop, sockets, or files."""
        client = self.client
        expected_profile, expected_collection = client.profile, client.collection
        calls = []
        connections = 0
        starts = 0
        if geometry_pending:
            client.geometry.update(sourceWidth=0, sourceHeight=0)

        def change_selection():
            client.profile = "Operator profile"
            client.collection = "Operator collection"

        @asynccontextmanager
        async def fake_stdio(_params):
            nonlocal connections
            connections += 1
            if reconnect and connections == 2:
                change_selection()
            yield object(), object()

        class FakeSession:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def initialize(self):
                pass

            async def call_tool(self, name, arguments):
                nonlocal starts
                calls.append((name, client.profile, client.collection))
                value = {"applied": True, "verified": True}
                if name == "obs_inputs":
                    value = {"input_kinds": ["text_gdiplus_v3"]}
                elif name == "obs_scene_sources":
                    value = {"sources": [{"sourceName": source, "sceneItemId": index}
                        for index, source in enumerate(("Demo Chart", "Demo Evidence", "Demo Replay"), 1)]}
                elif name == "obs_start_recording":
                    starts += 1
                    value = {"session_id": "owned-" + str(starts)}
                elif name == "obs_stop_recording":
                    value = {"output_path": str(ROOT / "runtime" / "synthetic-seed.mkv")}
                elif name == "obs_preview_event":
                    value = {"state": "preview"}
                if before_filter and name == "obs_audio_mute" and arguments.get("source_name") == "Demo Replay":
                    change_selection()
                return SimpleNamespace(isError=False, structuredContent=value)

        async def fake_sleep(seconds):
            if seconds == wait:
                change_selection()

        with patch.object(rehearse, "stdio_client", fake_stdio), \
                patch.object(rehearse, "ClientSession", FakeSession), \
                patch.object(rehearse.asyncio, "sleep", fake_sleep), \
                patch.object(Path, "write_text"), patch.object(Path, "write_bytes"):
            coroutine = rehearse.exercise(client, ROOT / "runtime" / "probe", [], expected_profile, expected_collection)
            try:
                # Every mocked await completes immediately; the guard must stop
                # before event dispatch creates a real concurrent task.
                with self.assertRaisesRegex(RuntimeError, "geometry.*timeout" if geometry_pending else "not selected"):
                    coroutine.send(None)
            finally:
                coroutine.close()
        return calls

    def test_selection_change_during_prepare_wait_blocks_recording(self):
        calls = self.exercise_with_selection_change(wait=3)
        self.assertFalse(any(name == "obs_start_recording" for name, _, _ in calls))
        self.assertFalse(any(name == "GetSourceScreenshot" for name, _ in self.client.requests))

    def test_replay_dimensions_timeout_blocks_transform_and_event_recording(self):
        calls = self.exercise_with_selection_change(geometry_pending=True)
        self.assertEqual(sum(name == "obs_source_transform" for name, _, _ in calls), 1,
                         "Only the earlier evidence label transform may run")
        self.assertEqual(sum(name == "obs_start_recording" for name, _, _ in calls), 1,
                         "Only the completed seed recording may run")
        self.assertFalse(any(name == "obs_media_action" for name, _, _ in calls))

    def test_selection_change_during_mcp_reconnect_blocks_event_and_recording(self):
        calls = self.exercise_with_selection_change(reconnect=True)
        self.assertEqual(sum(name == "obs_start_recording" for name, _, _ in calls), 1)
        self.assertFalse(any(name == "obs_preview_event" for name, _, _ in calls))

    def test_selection_change_during_recording_keeps_owned_stop_cleanup(self):
        calls = self.exercise_with_selection_change(wait=2)
        self.assertEqual(sum(name == "obs_start_recording" for name, _, _ in calls), 2)
        self.assertEqual(sum(name == "obs_stop_recording" for name, _, _ in calls), 2)
        self.assertEqual(calls[-1], ("obs_stop_recording", "Operator profile", "Operator collection"))
        self.assertTrue(all(name == "obs_stop_recording" for name, profile, _ in calls if profile == "Operator profile"))

    def test_selection_change_blocks_direct_filter_creation(self):
        self.exercise_with_selection_change(before_filter=True)
        self.assertFalse(any(name == "CreateSourceFilter" for name, _ in self.client.requests))

    def test_screenshot_discards_pixels_if_selection_changes_during_capture(self):
        profile, collection = self.client.profile, self.client.collection
        original_request = self.client.request

        def changed_during_request(name, data=None):
            value = original_request(name, data)
            if name == "GetSourceScreenshot":
                self.client.collection = "Operator collection"
            return value

        with patch.object(self.client, "request", side_effect=changed_during_request), \
                patch.object(Path, "write_bytes") as write:
            with self.assertRaisesRegex(RuntimeError, "not selected"):
                rehearse.screenshot(self.client, ROOT / "runtime" / "unused.png", profile, collection)
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
