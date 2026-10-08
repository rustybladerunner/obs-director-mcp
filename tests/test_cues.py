"""Independent orchestration tests; synthetic production only, never live OBS."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from obs_director.controls import ProductionService
from obs_director.cues import CueService, CueValidationError
from obs_director.transport import ObsError
from test_controls import FakeClient, FakeState


SECRET = "not-a-real-secret"


class FakeProduction:
    """A controllable production boundary, not an OBS implementation."""

    def __init__(self):
        self.lock = threading.RLock()
        self.validations = []
        self.mutations = []
        self.opened = 0
        self.current_scene = "Program"
        self.live = False
        self.missing_source = None
        self.unsupported_action = None
        self.fail_apply_at = None
        self.fail_readback_at = None
        self.no_readback_action = None
        self.on_apply = None

    @contextmanager
    def connection(self):
        self.opened += 1
        yield self

    def require_capabilities(self, names):
        if names != ["GetCurrentProgramScene"]:
            raise AssertionError("Unexpected cue-side OBS capability")

    def request(self, request, parameters=None):
        if request != "GetCurrentProgramScene":
            raise AssertionError("Cue must delegate typed operations to production")
        return {"currentProgramSceneName": self.current_scene}

    def validate_action(self, action, parameters, client, allow_live=False):
        self.validations.append(action)
        if self.live and not allow_live:
            raise ValueError("Active outputs require explicit opt-in")
        if action == self.unsupported_action:
            raise ValueError("Required OBS request unavailable")
        if parameters.get("source_name") == self.missing_source and self.missing_source is not None:
            raise ValueError("Referenced source does not exist")
        if action == "audio_mute":
            if set(parameters) != {"source_name", "muted"} or type(parameters["muted"]) is not bool:
                raise ValueError("Invalid typed control payload")
        return {"action": action, "parameters": deepcopy(parameters), "required_requests": [],
                "readback_expected": action != self.no_readback_action}

    def describe_action(self, action, parameters):
        safe = deepcopy(parameters)
        if "settings" in safe:
            safe["settings_field_count"] = len(safe.pop("settings"))
        return {"action": action, "parameters": safe}

    def apply_action(self, action, parameters, client, allow_live=False):
        self.validate_action(action, parameters, client, allow_live)
        position = len(self.mutations) + 1
        if self.on_apply:
            self.on_apply(action, parameters)
        if self.fail_apply_at == position:
            raise RuntimeError(SECRET)
        self.mutations.append((action, deepcopy(parameters)))
        if action == "scene_select":
            self.current_scene = parameters["scene_name"]
        return {"action": action, "applied": True, "verified": self.fail_readback_at != position,
                "verification": "synthetic readback", "parameters": self.describe_action(action, parameters)}


def action(name, **parameters):
    return {"action": name, "parameters": parameters}


def cue(*steps, expected_scene="Program"):
    return {"name": "Example cue", "expected_scene": expected_scene, "steps": list(steps)}


class CueTests(unittest.TestCase):
    def setUp(self):
        self.production = FakeProduction()
        self.service = CueService(self.production)
        self.select = action("scene_select", scene_name="Replay", target="program")
        self.mute = action("audio_mute", source_name="Microphone", muted=True)

    def test_default_dry_run_validates_every_step_without_mutation_wait_or_files(self):
        plan = cue(self.select, {"action": "wait", "seconds": 3}, self.mute)
        original = deepcopy(plan)
        with patch("obs_director.cues.time.sleep") as sleep, patch.object(Path, "open", side_effect=AssertionError("Cue wrote a file")):
            result = self.service.run_cue(plan)
        self.assertEqual(["scene_select", "audio_mute"], self.production.validations)
        self.assertEqual([], self.production.mutations)
        self.assertEqual(original, plan)
        sleep.assert_not_called()
        self.assertEqual("preview", result["state"])
        self.assertTrue(result["validated"])
        self.assertEqual(["skipped"] * 3, [step["state"] for step in result["steps"]])

    def test_malformed_late_step_rejects_whole_cue_before_connection(self):
        bad_steps = [
            {"action": "StartStream", "parameters": {}},
            {"action": "output_control", "parameters": {"output": "stream", "action": "start"}},
            {"action": "wait", "seconds": 1, "request": "StartRecord"},
            {"action": "audio_mute", "parameters": {}, "extra": True},
            action("source_settings", source_name="Browser", settings={"url": "https://example.invalid"}),
        ]
        for bad in bad_steps:
            with self.subTest(bad=bad), self.assertRaises(CueValidationError):
                self.service.run_cue(cue(self.select, bad), dry_run=False)
        self.assertEqual(0, self.production.opened)
        self.assertEqual([], self.production.mutations)

    def test_late_reference_capability_and_typed_payload_errors_prevent_earlier_changes(self):
        for failure in ("reference", "capability", "payload"):
            with self.subTest(failure=failure):
                self.production.missing_source = "Missing" if failure == "reference" else None
                self.production.unsupported_action = "audio_mute" if failure == "capability" else None
                late = action("audio_mute", source_name="Missing", muted="true" if failure == "payload" else True)
                with self.assertRaises(ValueError):
                    self.service.run_cue(cue(self.select, late), dry_run=False)
                self.assertEqual([], self.production.mutations)

    def test_wait_and_step_bounds_reject_before_connection(self):
        invalid = [cue(*([self.select] * 21)), cue(),
                   cue({"action": "wait", "seconds": 8}, {"action": "wait", "seconds": 8})]
        for seconds in (True, -1, float("nan"), float("inf"), 15.01, "1", 10 ** 1000):
            invalid.append(cue({"action": "wait", "seconds": seconds}))
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(CueValidationError):
                self.service.run_cue(plan, dry_run=False)
        self.assertEqual(0, self.production.opened)
        self.assertEqual([], self.production.mutations)
        validated = self.service.validate_cue(cue({"action": "wait", "seconds": 7}, {"action": "wait", "seconds": 8}))
        self.assertEqual(15, validated["total_wait_seconds"])

    def test_live_guard_requires_real_boolean_opt_in(self):
        self.production.live = True
        with self.assertRaises(ValueError):
            self.service.run_cue(cue(self.select), dry_run=False)
        for flag in (1, "true", None):
            with self.subTest(flag=flag), self.assertRaises(CueValidationError):
                self.service.run_cue(cue(self.select), dry_run=False, allow_live=flag)
        self.assertEqual([], self.production.mutations)
        result = self.service.run_cue(cue(self.select), dry_run=False, allow_live=True)
        self.assertEqual("completed", result["state"])

    def test_expected_scene_mismatch_refuses_whole_plan(self):
        with self.assertRaises(CueValidationError):
            self.service.run_cue(cue(self.select, self.mute, expected_scene="Unexpected"), dry_run=False)
        self.assertEqual([], self.production.mutations)

    def test_expected_scene_rechecked_after_initial_wait(self):
        def change_scene(_):
            self.production.current_scene = "Operator change"
        with patch("obs_director.cues.time.sleep", side_effect=change_scene):
            result = self.service.run_cue(cue({"action": "wait", "seconds": 1}, self.select), dry_run=False)
        self.assertEqual([], self.production.mutations)
        self.assertEqual(["completed", "failed"], [step["state"] for step in result["steps"]])
        self.assertEqual("failed", result["state"])
        self.assertFalse(result["steps"][1]["may_have_changed"])
        self.assertEqual("precondition", result["steps"][1]["failure_phase"])

    def test_readback_failure_stops_remaining_steps_without_rollback_claim(self):
        self.production.fail_readback_at = 1
        result = self.service.run_cue(cue(self.select, self.mute), dry_run=False)
        self.assertEqual(1, len(self.production.mutations))
        self.assertEqual("Replay", self.production.current_scene)
        self.assertEqual(["failed", "skipped"], [step["state"] for step in result["steps"]])
        self.assertEqual("readback_not_verified", result["steps"][0]["reason"])
        self.assertTrue(result["steps"][0]["may_have_changed"])
        self.assertFalse(result["rollback_attempted"])

    def test_known_unverifiable_late_action_is_rejected_before_earlier_mutations(self):
        self.production.no_readback_action = "media_action"
        with self.assertRaises(CueValidationError):
            self.service.run_cue(cue(self.select, action("media_action", source_name="Playlist", action="next")), dry_run=False)
        self.assertEqual([], self.production.mutations)

    def test_midcue_failure_preserves_completed_steps_and_redacts_exception(self):
        self.production.fail_apply_at = 2
        result = self.service.run_cue(cue(self.select, self.mute, self.select), dry_run=False)
        self.assertEqual(["completed", "failed", "skipped"], [step["state"] for step in result["steps"]])
        self.assertEqual(2, result["failed_step"])
        self.assertEqual("prior_step_failed", result["steps"][2]["reason"])
        self.assertEqual(1, len(self.production.mutations))
        self.assertNotIn(SECRET, json.dumps(result))
        for step in result["steps"]:
            self.assertIsNotNone(step["finished_at"])
            self.assertGreaterEqual(step["duration_ms"], 0)

    def test_text_is_not_echoed_and_completed_waits_are_explicit(self):
        text = action("source_settings", source_name="Caption", settings={"text": SECRET})
        with patch("obs_director.cues.time.sleep") as sleep:
            result = self.service.run_cue(cue(text, {"action": "wait", "seconds": 0.25}), dry_run=False)
        sleep.assert_called_once_with(0.25)
        self.assertEqual("completed", result["state"])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(1, result["steps"][0]["parameters"]["settings_field_count"])

    def test_two_cues_share_the_production_execution_lock(self):
        entered = threading.Event()
        release = threading.Event()
        second_attempted = threading.Event()

        def pause_first(action_name, parameters):
            if not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(2), "Test did not release first cue")

        self.production.on_apply = pause_first
        first_plan = cue(self.mute, self.mute)
        other_service = CueService(self.production)

        def second():
            second_attempted.set()
            return other_service.run_cue(first_plan, dry_run=False)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(self.service.run_cue, first_plan, False)
            self.assertTrue(entered.wait(2))
            second_future = pool.submit(second)
            self.assertTrue(second_attempted.wait(2))
            try:
                self.assertEqual(1, self.production.opened)
            finally:
                release.set()
            self.assertEqual("completed", first_future.result(timeout=2)["state"])
            self.assertEqual("completed", second_future.result(timeout=2)["state"])
        self.assertEqual(2, self.production.opened)
        self.assertEqual(4, len(self.production.mutations))


class CueProductionIntegrationTests(unittest.TestCase):
    def test_all_data_only_examples_execute_against_real_typed_controls(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        paths = sorted(examples.glob("*.json"))
        self.assertEqual({"intermission.json", "presenter-to-replay.json", "spotlight-layout.json"},
                         {path.name for path in paths})
        for path in paths:
            with self.subTest(example=path.name):
                plan = json.loads(path.read_text(encoding="utf-8"))
                state = FakeState()
                state.scenes = ["Program", "Presenter", "Replay", "Intermission"]
                state.program = plan["expected_scene"]
                state.inputs = {"Caption": "text_gdiplus_v2", "Intermission caption": "text_gdiplus_v2",
                                "Microphone": "wasapi_input_capture", "Replay clip": "ffmpeg_source",
                                "Intermission music": "ffmpeg_source"}
                state.input_settings = {name: {} for name in state.inputs}
                state.items = {name: [{"sourceName": "Caption", "inputKind": "text_gdiplus_v2",
                                      "sceneItemId": 1, "sceneItemEnabled": False}]
                               for name in state.scenes}
                service = CueService(ProductionService(state.factory))
                self.assertTrue(service.validate_cue(plan)["valid"])
                self.assertEqual("preview", service.run_cue(plan)["state"])
                self.assertEqual([], state.mutations())
                result = service.run_cue(plan, dry_run=False)
                self.assertEqual("completed", result["state"], result)
                self.assertEqual(len(plan["steps"]), len(state.mutations()))
                self.assertTrue(all(step["verified"] for step in result["steps"]))
                for step in result["steps"]:
                    self.assertNotIn("settings", step["parameters"])

    def test_real_late_step_preflight_failures_leave_earlier_scene_untouched(self):
        late_steps = [
            action("audio_mute", source_name="Absent", muted=True),
            action("audio_mute", source_name="Mic", muted=1),
            action("source_visibility", scene_name="Main", scene_item_id=999, enabled=True),
            action("source_settings", source_name="Mic", settings={"text": "Caption"}),
            action("media_action", source_name="Movie", action="next"),
        ]
        for late in late_steps:
            with self.subTest(late=late):
                state = FakeState()
                service = CueService(ProductionService(state.factory))
                with self.assertRaises((ValueError, ObsError)):
                    service.run_cue(cue(action("scene_select", scene_name="Detail"), late,
                                        expected_scene="Main"), dry_run=False)
                self.assertEqual("Main", state.program)
                self.assertEqual([], state.mutations())
        state = FakeState()
        state.missing.add("SetInputMute")
        with self.assertRaises(ObsError):
            CueService(ProductionService(state.factory)).run_cue(cue(
                action("scene_select", scene_name="Detail"),
                action("audio_mute", source_name="Mic", muted=True), expected_scene="Main"), dry_run=False)
        self.assertEqual([], state.mutations())

    def test_real_readback_and_rpc_failures_produce_partial_receipts(self):
        class FailingClient(FakeClient):
            def request(self, name, data=None):
                if name == "SetInputMute":
                    self.state.calls.append((name, dict(data or {})))
                    raise ObsError(SECRET)
                return super().request(name, data)

        for mode in ("unapplied", "rpc_error"):
            with self.subTest(mode=mode):
                state = FakeState()
                if mode == "unapplied":
                    state.noop.add("SetInputMute")
                factory = state.factory if mode == "unapplied" else lambda: FailingClient(state)
                service = CueService(ProductionService(factory))
                result = service.run_cue(cue(action("scene_select", scene_name="Detail"),
                    action("audio_mute", source_name="Mic", muted=True),
                    action("media_action", source_name="Movie", action="play"), expected_scene="Main"), dry_run=False)
                self.assertEqual(["completed", "failed", "skipped"], [step["state"] for step in result["steps"]])
                self.assertEqual(["SetCurrentProgramScene", "SetInputMute"], state.mutations())
                self.assertEqual("Detail", state.program)
                self.assertFalse(result["rollback_attempted"])
                self.assertNotIn(SECRET, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
