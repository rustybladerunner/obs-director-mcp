"""Existing-transition protocol fixtures; no OBS connection or media playback."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from obs_director.controls import ProductionService
from obs_director.transport import ObsError, ObsSettings
from obs_director.transitions import TransitionError, TransitionService


class FakeTransitions:
    def __init__(self):
        self.settings = ObsSettings()
        self.profile, self.collection = "Profile", "Collection"
        self.recording = self.streaming = False
        self.cursor = 1.0
        self.current, self.duration = "Fade", 300
        self.items = {name: {"transitionName": name, "transitionKind": kind, "transitionUuid": name + "-uuid",
                     "transitionFixed": fixed, "transitionConfigurable": configurable}
                     for name, kind, fixed, configurable in (
                         ("Fade", "fade_transition", False, False), ("Cut", "cut_transition", True, False),
                         ("Wipe", "wipe_transition", False, True), ("Stinger", "obs_stinger_transition", True, True))}
        self.config = {"Wipe": {"direction": "left"}, "Stinger": {"path": "test-only.webm", "tp_type": 0,
                       "transition_point": 100, "hw_decode": True, "track_matte_enabled": False}}
        self.calls, self.missing, self.noop, self.lost_ack = [], set(), set(), set()
        self.before = self.after = None
        self.delay = 0
        self.pending = None

    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def require_capabilities(self, names):
        if set(names) & self.missing: raise ObsError("not-a-real-secret")
    def writes(self): return [(name, data) for name, data in self.calls if name.startswith("Set")]

    def request(self, name, data=None):
        self.require_capabilities([name])
        self.calls.append((name, deepcopy(data)))
        if self.before: self.before(name, data)
        result = self._request(name, data or {})
        if self.after: self.after(name, data, result)
        if name in self.lost_ack: raise ObsError("not-a-real-secret")
        return deepcopy(result)

    def _request(self, name, data):
        if name == "GetProfileList": return {"currentProfileName": self.profile}
        if name == "GetSceneCollectionList": return {"currentSceneCollectionName": self.collection}
        if name == "GetRecordStatus": return {"outputActive": self.recording}
        if name == "GetStreamStatus": return {"outputActive": self.streaming}
        if name == "GetCurrentSceneTransitionCursor": return {"transitionCursor": self.cursor}
        if name == "GetTransitionKindList": return {"transitionKinds": [item["transitionKind"] for item in self.items.values()]}
        if name == "GetSceneTransitionList":
            return {"currentSceneTransitionName": self.current, "transitions": list(self.items.values())}
        if name == "GetCurrentSceneTransition":
            if self.pending:
                action, values, left = self.pending
                self.pending = (action, values, left - 1) if left else None
                if not left: self._apply(action, values)
            current = deepcopy(self.items[self.current])
            return {**current, "transitionDuration": None if current["transitionFixed"] else self.duration,
                    "transitionSettings": deepcopy(self.config.get(self.current))}
        if name.startswith("Set"):
            if name not in self.noop:
                if self.delay: self.pending = (name, data, self.delay)
                else: self._apply(name, data)
            return {}
        raise AssertionError(name)

    def _apply(self, name, data):
        if name == "SetCurrentSceneTransition": self.current = data["transitionName"]
        elif name == "SetCurrentSceneTransitionDuration": self.duration = data["transitionDuration"]
        elif name == "SetCurrentSceneTransitionSettings": self.config[self.current].update(data["transitionSettings"])
        else: raise AssertionError(name)


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.obs = FakeTransitions()
        self.production = ProductionService(lambda: self.obs)
        self.service = TransitionService(self.production)
        self.clock = 0.0
        self.enterContext(patch("obs_director.transitions.time.monotonic", side_effect=lambda: self.clock))
        self.enterContext(patch("obs_director.transitions.time.sleep", side_effect=self.advance))

    def advance(self, seconds): self.clock += seconds

    def clip(self, root):
        path = Path(root) / "private-test-only.webm"
        path.write_bytes(b"synthetic fixture, not decoded")
        return str(path.resolve())

    def test_inventory_has_current_duration_and_kinds_but_no_settings(self):
        self.obs.current = "Stinger"
        value = self.service.transitions()
        self.assertEqual(value["current"]["name"], "Stinger")
        self.assertIsNone(value["current"]["duration_ms"])
        self.assertIn("obs_stinger_transition", value["transition_kinds"])
        self.assertNotIn("settings", json.dumps(value))
        self.assertNotIn("test-only.webm", json.dumps(value))
        self.assertEqual(self.obs.writes(), [])

    def test_preview_defaults_to_no_writes_and_lists_both_requested_changes(self):
        result = self.service.select_transition("Wipe", 450)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["operations"], ["select", "duration"])
        self.assertEqual(self.obs.writes(), [])

    def test_select_duration_and_repeat_have_verified_idempotent_results(self):
        value = self.service.select_transition("Wipe", 450, False)
        self.assertTrue(value["verified"], value)
        self.assertEqual((self.obs.current, self.obs.duration), ("Wipe", 450))
        self.obs.calls.clear()
        result = self.service.select_transition("Wipe", 450, False)
        self.assertEqual(result["state"], "unchanged")
        self.assertTrue(result["verified"])
        self.assertEqual(self.obs.writes(), [])

    def test_asynchronous_selection_and_duration_wait_without_repeated_writes(self):
        self.obs.delay = 2
        result = self.service.select_transition("Wipe", 550, False)
        self.assertTrue(result["verified"], result)
        self.assertEqual([name for name, _ in self.obs.writes()], ["SetCurrentSceneTransition", "SetCurrentSceneTransitionDuration"])

    def test_missing_target_fixed_duration_and_duplicate_names_fail_before_writes(self):
        for name, duration in (("Unknown", None), ("Cut", 300)):
            with self.assertRaises(TransitionError): self.service.select_transition(name, duration, False)
        self.obs.items["Duplicate"] = deepcopy(self.obs.items["Fade"])
        with self.assertRaises(TransitionError): self.service.select_transition("Fade", None, False)
        self.assertEqual(self.obs.writes(), [])

    def test_all_needed_capabilities_checked_before_first_setter(self):
        self.obs.missing.add("SetCurrentSceneTransitionDuration")
        with self.assertRaises(TransitionError): self.service.select_transition("Wipe", 400, False)
        self.assertEqual(self.obs.writes(), [])

    def test_active_outputs_need_explicit_allow_live_and_unknown_is_never_allowed(self):
        self.obs.recording = True
        with self.assertRaises(TransitionError): self.service.select_transition("Wipe", None, False)
        self.assertEqual(self.obs.writes(), [])
        self.assertTrue(self.service.select_transition("Wipe", None, False, True)["verified"])
        self.obs.streaming = None
        self.obs.calls.clear()
        with self.assertRaises(TransitionError): self.service.select_transition("Fade", None, False, True)
        self.assertEqual(self.obs.writes(), [])

    def test_active_or_malformed_cursor_refuses_preflight(self):
        for cursor in (0.5, None, True, float("nan")):
            self.obs.cursor = cursor
            with self.assertRaises(TransitionError): self.service.select_transition("Wipe", None, False, True)
        self.assertEqual(self.obs.writes(), [])

    def test_owned_selection_change_before_setter_refuses_without_mutation(self):
        reads = [0]
        def change(name, data):
            if name == "GetCurrentSceneTransitionCursor":
                reads[0] += 1
                if reads[0] == 2: self.obs.profile = "Operator profile"
        self.obs.before = change
        result = self.service.select_transition("Wipe", None, False)
        self.assertFalse(result["verified"])
        self.assertFalse(result["applied"])
        self.assertEqual(self.obs.writes(), [])

    def test_transition_changed_during_final_guard_cannot_receive_duration_setter(self):
        reads = [0]
        def change(name, data):
            if name == "GetCurrentSceneTransitionCursor":
                reads[0] += 1
                if reads[0] == 2: self.obs.current = "Wipe"
        self.obs.before = change
        result = self.service.select_transition("Fade", 450, False)
        self.assertFalse(result["verified"])
        self.assertFalse(result["applied"])
        self.assertEqual(self.obs.duration, 300)
        self.assertEqual(self.obs.writes(), [])

    def test_live_gate_rechecked_before_second_mutation(self):
        def live(name, data, _result):
            if name == "SetCurrentSceneTransition": self.obs.streaming = True
        self.obs.after = live
        result = self.service.select_transition("Wipe", 400, False)
        self.assertEqual(result["state"], "partial")
        self.assertTrue(result["applied"])
        self.assertEqual([name for name, _ in self.obs.writes()], ["SetCurrentSceneTransition"])

    def test_noop_and_lost_ack_do_not_retry_or_claim_success(self):
        self.obs.noop.add("SetCurrentSceneTransition")
        result = self.service.select_transition("Wipe", None, False)
        self.assertFalse(result["verified"])
        self.assertTrue(result["applied"])
        self.assertTrue(result["uncertain"])
        self.assertEqual(len(self.obs.writes()), 1)
        self.obs.calls.clear()
        self.obs.noop.clear()
        self.obs.lost_ack.add("SetCurrentSceneTransition")
        result = self.service.select_transition("Wipe", 400, False)
        self.assertIsNone(result["applied"])
        self.assertTrue(result["uncertain"])
        self.assertEqual(len(self.obs.writes()), 1)
        self.assertNotIn("not-a-real-secret", json.dumps(result))

    def test_post_ack_read_failure_retains_applied_true(self):
        def fail(name, data):
            if name == "GetCurrentSceneTransition" and self.obs.current == "Wipe":
                raise ObsError("not-a-real-secret")
        self.obs.before = fail
        result = self.service.select_transition("Wipe", None, False)
        self.assertTrue(result["applied"])
        self.assertFalse(result["verified"])
        self.assertTrue(result["uncertain"])
        self.assertNotIn("not-a-real-secret", json.dumps(result))

    def test_uuid_replacement_after_ack_invalidates_identity(self):
        def replace(name, data, result):
            if name == "SetCurrentSceneTransition": self.obs.items["Wipe"]["transitionUuid"] = "replacement"
        self.obs.after = replace
        result = self.service.select_transition("Wipe", 400, False)
        self.assertFalse(result["verified"])
        self.assertEqual(len(self.obs.writes()), 1)

    def test_late_matching_reply_does_not_pass_and_original_timeout_is_restored(self):
        settings = self.obs.settings
        budgets = []
        def late(name, data):
            if name == "GetCurrentSceneTransition" and self.obs.current == "Wipe":
                budgets.append(self.obs.settings.timeout)
                self.advance(3)
        self.obs.before = late
        result = self.service.select_transition("Wipe", None, False)
        self.assertFalse(result["verified"])
        self.assertEqual(budgets, [3])
        self.assertIs(self.obs.settings, settings)

    def test_stinger_is_current_only_and_kind_must_be_native(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.clip(root)
            with self.assertRaises(TransitionError): self.service.configure_stinger("Stinger", path, 250, False)
            self.obs.current = "Wipe"
            with self.assertRaises(TransitionError): self.service.configure_stinger("Wipe", path, 250, False)
        self.assertEqual(self.obs.writes(), [])

    def test_stinger_updates_only_whitelist_preserves_other_settings_and_redacts_path(self):
        self.obs.current = "Stinger"
        with tempfile.TemporaryDirectory() as root:
            path = self.clip(root)
            preview = self.service.configure_stinger("Stinger", path, 750)
            self.assertEqual(self.obs.writes(), [])
            self.assertNotIn(path, json.dumps(preview))
            result = self.service.configure_stinger("Stinger", path, 750, False)
            self.assertTrue(result["verified"], result)
            setter, payload = self.obs.writes()[0]
            self.assertEqual(setter, "SetCurrentSceneTransitionSettings")
            self.assertTrue(payload["overlay"])
            self.assertEqual(set(payload["transitionSettings"]), {"path", "tp_type", "transition_point"})
            self.assertTrue(self.obs.config["Stinger"]["hw_decode"])
            self.assertNotIn(path, json.dumps(result))
            self.assertNotIn("private-test-only", json.dumps(result))
            self.obs.calls.clear()
            self.assertEqual(self.service.configure_stinger("Stinger", path, 750, False)["state"], "unchanged")
            self.assertEqual(self.obs.writes(), [])

    def test_track_matte_and_changed_local_file_refuse_before_configuration(self):
        self.obs.current = "Stinger"
        with tempfile.TemporaryDirectory() as root:
            path = self.clip(root)
            self.obs.config["Stinger"]["track_matte_enabled"] = True
            with self.assertRaises(TransitionError): self.service.configure_stinger("Stinger", path, 250, False)
            self.obs.config["Stinger"]["track_matte_enabled"] = False
            def change(name, data):
                if name == "GetCurrentSceneTransitionCursor": Path(path).write_bytes(b"changed" + bytes(len(self.obs.calls)))
            self.obs.before = change
            result = self.service.configure_stinger("Stinger", path, 250, False)
            self.assertFalse(result["verified"])
            self.assertFalse(result["applied"])
        self.assertEqual(self.obs.writes(), [])

    def test_input_bounds_and_nonlocal_files_refuse_before_connection(self):
        for duration in (True, 49, 20001, 1.5):
            with self.assertRaises(TransitionError): self.service.select_transition("Fade", duration)
        for path in ("relative.webm", "https://example.invalid/clip.webm", "\\\\server\\share\\clip.webm"):
            with self.assertRaises(TransitionError): self.service.configure_stinger("Stinger", path, 100)
        self.assertEqual(self.obs.calls, [])


if __name__ == "__main__":
    unittest.main()
