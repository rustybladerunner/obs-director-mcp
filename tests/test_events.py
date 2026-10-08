"""Event routing checks use synthetic production, never a running OBS instance."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from obs_director.cues import CueService
from obs_director.events import EventService, EventValidationError, build_demo_registry, load_event_registry
from test_cues import FakeProduction, action, cue


def event(event_id="synthetic-001"):
    return {"schema": "obs.event.v1", "event_id": event_id, "event_type": "evidence.ready",
            "occurred_at": "2026-01-01T12:00:00Z", "payload": {
                "title": "Synthetic interval reviewed", "summary": "Local fixture only. No real observations.",
                "reference": "fixture-007"}}


def registry():
    return {"evidence.ready": {"text_step": 0, "cue": cue(
        action("source_settings", source_name="Evidence", settings={"text": "placeholder"}),
        action("audio_mute", source_name="Microphone", muted=True))}}


class EventTests(unittest.TestCase):
    def setUp(self):
        self.production = FakeProduction()
        self.cues = CueService(self.production)
        self.service = EventService(self.cues, registry())

    def test_default_has_no_routes_and_does_not_open_obs(self):
        with patch.dict("os.environ", {}, clear=True):
            empty = EventService(self.cues)
        with self.assertRaises(EventValidationError):
            empty.preview_event(event())
        self.assertEqual(0, self.production.opened)

    def test_default_preview_has_no_mutations_writes_waits_or_dedupe_effect(self):
        original = event()
        with patch.object(Path, "open", side_effect=AssertionError("Preview wrote a file")), patch("obs_director.cues.time.sleep") as sleep:
            first = self.service.dispatch_event(original)
            second = self.service.preview_event(original)
        self.assertEqual("preview", first["state"])
        self.assertEqual("preview", second["state"])
        self.assertFalse(second["duplicate"])
        self.assertEqual([], self.production.mutations)
        self.assertEqual(event(), original)
        sleep.assert_not_called()
        result = self.service.dispatch_event(original, dry_run=False)
        self.assertEqual("completed", result["state"])

    def test_text_reaches_only_registered_text_source_and_receipts_omit_it(self):
        value = event()
        value["payload"]["summary"] = "not-a-real-secret"
        result = self.service.dispatch_event(value, dry_run=False)
        sent = self.production.mutations[0]
        self.assertEqual("source_settings", sent[0])
        self.assertEqual("Evidence", sent[1]["source_name"])
        self.assertIn("not-a-real-secret", sent[1]["settings"]["text"])
        self.assertNotIn("not-a-real-secret", json.dumps(result))
        self.assertNotIn("Synthetic interval reviewed", json.dumps(result))
        self.assertNotIn("fixture-007", json.dumps(result))

    def test_duplicate_is_not_replayed_and_changed_id_content_is_rejected(self):
        self.service.dispatch_event(event(), dry_run=False)
        count = len(self.production.mutations)
        repeated = self.service.dispatch_event(event(), dry_run=False)
        self.assertEqual("duplicate", repeated["state"])
        self.assertEqual("completed", repeated["previous_state"])
        self.assertEqual(count, len(self.production.mutations))
        changed = event()
        changed["payload"]["title"] = "Different content"
        with self.assertRaises(EventValidationError):
            self.service.dispatch_event(changed, dry_run=False)
        self.assertEqual(count, len(self.production.mutations))

    def test_partial_failure_consumes_id_and_redacts_errors(self):
        self.production.fail_apply_at = 2
        first = self.service.dispatch_event(event(), dry_run=False)
        self.assertEqual("failed", first["state"])
        self.assertEqual(["completed", "failed"], [step["state"] for step in first["cue"]["steps"]])
        self.assertNotIn("not-a-real-secret", json.dumps(first))
        self.production.fail_apply_at = None
        second = self.service.dispatch_event(event(), dry_run=False)
        self.assertEqual("duplicate", second["state"])
        self.assertEqual("failed", second["previous_state"])
        self.assertEqual(1, len(self.production.mutations))

    def test_unexpected_execution_failure_keeps_id_and_does_not_echo_exception(self):
        original = self.cues.run_cue
        def fail_execution(plan, dry_run=True, allow_live=False):
            if not dry_run:
                raise RuntimeError("not-a-real-secret")
            return original(plan, dry_run=dry_run, allow_live=allow_live)
        with patch.object(self.cues, "run_cue", side_effect=fail_execution):
            first = self.service.dispatch_event(event(), dry_run=False)
        self.assertEqual("failed", first["state"])
        self.assertTrue(first["may_have_changed"])
        self.assertNotIn("not-a-real-secret", json.dumps(first))
        self.assertEqual("duplicate", self.service.dispatch_event(event(), dry_run=False)["state"])

    def test_invalid_events_and_unregistered_types_never_open_obs(self):
        invalid = []
        for field, value in [("schema", "other"), ("event_id", "../../capture"),
                             ("event_type", "unregistered"), ("occurred_at", "2026-02-30T12:00:00Z"),
                             ("occurred_at", "2026-01-01T12:00:00"), ("payload", {"action": "StartStream"})]:
            item = event(); item[field] = value; invalid.append(item)
        item = event(); item["action"] = "StartStream"; invalid.append(item)
        item = event(); item["payload"]["source_name"] = "Operator source"; invalid.append(item)
        for value in ["x" * 241, "line\nfeed", "\u007f", 3, ""]:
            item = event(); item["payload"]["summary"] = value; invalid.append(item)
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(EventValidationError):
                self.service.dispatch_event(item, dry_run=False)
        self.assertEqual(0, self.production.opened)
        self.assertEqual([], self.production.mutations)

    def test_registry_rejects_outputs_invalid_text_binding_and_long_cues(self):
        for step in [{"action": "StartStream", "parameters": {}},
                     {"action": "source_settings", "parameters": {"source_name": "Browser", "settings": {"url": "https://example.invalid"}}},
                     {"action": "wait", "seconds": 16}]:
            bad = registry(); bad["evidence.ready"]["cue"]["steps"].append(step)
            with self.assertRaises(EventValidationError):
                EventService(self.cues, bad)
        for index in [True, -1, 1, 20, "0"]:
            bad = registry(); bad["evidence.ready"]["text_step"] = index
            with self.assertRaises(EventValidationError):
                EventService(self.cues, bad)
        self.assertEqual(0, self.production.opened)

    def test_late_preflight_failure_prevents_earlier_text_write_and_leaves_id_unused(self):
        self.production.missing_source = "Microphone"
        with self.assertRaises(EventValidationError):
            self.service.dispatch_event(event(), dry_run=False)
        self.assertEqual([], self.production.mutations)
        self.production.missing_source = None
        self.assertEqual("completed", self.service.dispatch_event(event(), dry_run=False)["state"])

    def test_live_execution_requires_boolean_opt_in_and_preserves_registry(self):
        registered = registry(); copy = deepcopy(registered)
        service = EventService(self.cues, registered)
        self.production.live = True
        with self.assertRaises(EventValidationError):
            service.dispatch_event(event(), dry_run=False)
        for value in [1, "true", None]:
            with self.assertRaises(EventValidationError):
                service.dispatch_event(event(), dry_run=False, allow_live=value)
        self.assertEqual("completed", service.dispatch_event(event(), dry_run=False, allow_live=True)["state"])
        self.assertEqual(copy, registered)

    def test_capacity_refuses_new_execution_without_evicting_existing_ids(self):
        service = EventService(self.cues, registry(), max_events=1)
        service.dispatch_event(event(), dry_run=False)
        count = len(self.production.mutations)
        self.assertEqual("preview", service.preview_event(event("synthetic-002"))["state"])
        with self.assertRaises(EventValidationError):
            service.dispatch_event(event("synthetic-002"), dry_run=False)
        self.assertEqual("duplicate", service.dispatch_event(event(), dry_run=False)["state"])
        self.assertEqual(count, len(self.production.mutations))

    def test_concurrent_delivery_executes_once(self):
        barrier = threading.Barrier(2)
        def deliver():
            barrier.wait(timeout=2)
            return self.service.dispatch_event(event(), dry_run=False)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: deliver(), range(2)))
        self.assertEqual(["completed", "duplicate"], sorted(result["state"] for result in results))
        self.assertEqual(2, len(self.production.mutations))

    def test_trusted_registry_file_is_bounded_and_errors_do_not_echo_content(self):
        document = {"schema": "obs.event-routes.v1", "routes": registry()}
        with patch.dict("os.environ", {"OBS_MCP_EVENT_ROUTES": str(Path.cwd() / "runtime" / "routes.json")}), patch.object(Path, "open", return_value=io.BytesIO(json.dumps(document).encode())):
            self.assertEqual(registry(), load_event_registry())
        for content in [b"not-a-real-secret", b"x" * (128 * 1024 + 1), b'{"schema":"wrong","routes":{}}']:
            with patch.dict("os.environ", {"OBS_MCP_EVENT_ROUTES": str(Path.cwd() / "runtime" / "routes.json")}), patch.object(Path, "open", return_value=io.BytesIO(content)):
                with self.assertRaises(EventValidationError) as raised:
                    load_event_registry()
                self.assertNotIn("not-a-real-secret", str(raised.exception))

    def test_demo_route_is_explicit_bounded_and_uses_actual_item_ids(self):
        registered = build_demo_registry(7, 12, 19)
        self.production.current_scene = "Director Demo"
        service = EventService(self.cues, registered)
        with patch("obs_director.cues.time.sleep") as sleep:
            result = service.dispatch_event(event(), dry_run=False)
        self.assertEqual("completed", result["state"])
        self.assertEqual(9, result["cue"]["total_wait_seconds"])
        self.assertEqual([2, 3, 4], [call.args[0] for call in sleep.call_args_list])
        ids = {values["scene_item_id"] for _, values in self.production.mutations if "scene_item_id" in values}
        self.assertEqual({7, 12, 19}, ids)
        # OBS FFmpeg restart can report PLAYING without decoding when hidden.
        self.assertEqual(("source_visibility", {"scene_name": "Director Demo",
            "scene_item_id": 19, "enabled": True}), self.production.mutations[3])
        self.assertEqual(("media_action", {"source_name": "Demo Replay",
            "action": "restart"}), self.production.mutations[4])
        with self.assertRaises(EventValidationError):
            build_demo_registry(1, 1, 2)


if __name__ == "__main__":
    unittest.main()
