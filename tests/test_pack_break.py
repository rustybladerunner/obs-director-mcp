"""Pack-break show moves, panic cue, event routes and docs, run against synthetic typed controls."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from obs_director.controls import ProductionService
from obs_director.cues import CueService, load_panic_cue, _schema
from obs_director.events import EventService
from test_controls import FakeState

PACK = ROOT / "examples" / "pack-break"
CUES = ("starting-soon", "open-pack", "reveal-hit", "be-right-back", "wrap-up")
SCENES = ["Pack Break Table", "Pack Break Closeup", "Pack Break Holding"]
TEXT_INPUTS = ("Title Text", "Now Opening Text", "Hit Card Text")


def load(name):
    return json.loads((PACK / name).read_text(encoding="utf-8"))


def show_state():
    state = FakeState()
    state.scenes = ["Main", *SCENES]
    state.program = "Pack Break Table"
    state.inputs = {name: "text_gdiplus_v2" for name in TEXT_INPUTS}
    state.inputs["Microphone"] = "wasapi_input_capture"
    state.input_settings = {name: {} for name in state.inputs}
    return state


def instant(*_args):
    return False


class ShowMoveTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("obs_director.cues._wait", side_effect=instant))
        self.state = show_state()
        self.service = CueService(ProductionService(self.state.factory))

    def run_cue(self, plan):
        self.assertTrue(self.service.validate_cue(plan)["valid"])
        self.assertEqual("preview", self.service.run_cue(plan)["state"])
        self.assertEqual([], self.state.mutations())
        result = self.service.run_cue(plan, dry_run=False)
        self.assertEqual("completed", result["state"], result)
        self.assertTrue(all(step["verified"] for step in result["steps"] if step["action"] != "wait"))
        return result

    def test_every_show_move_is_data_only_bounded_and_executes(self):
        for name in CUES:
            with self.subTest(cue=name):
                plan = load(name + ".json")
                normalized = _schema(plan)
                self.assertLessEqual(normalized["total_wait_seconds"], 15)
                self.assertNotIn("expected_scene", plan, "a show move must work from whatever scene is live")
                self.state = show_state()
                self.service = CueService(ProductionService(self.state.factory))
                result = self.run_cue(plan)
                for step in result["steps"]:
                    self.assertNotIn("settings", step["parameters"])

    def test_open_pack_shows_the_table_sets_the_card_and_opens_the_microphone(self):
        self.state.program, self.state.muted = "Pack Break Holding", True
        self.run_cue(load("open-pack.json"))
        self.assertEqual("Pack Break Table", self.state.program)
        self.assertEqual("Booster Pack 12", self.state.input_settings["Now Opening Text"]["text"])
        self.assertFalse(self.state.muted)

    def test_reveal_hit_names_the_card_cuts_to_the_closeup_holds_then_returns_to_the_table(self):
        plan = load("reveal-hit.json")
        steps = plan["steps"]
        self.assertEqual("source_settings", steps[0]["action"])
        self.assertEqual(["Pack Break Closeup"], [s["parameters"]["scene_name"] for s in steps if s["action"] == "scene_select"][:1])
        self.assertEqual("Pack Break Table", steps[-1]["parameters"]["scene_name"])
        hold = sum(s["seconds"] for s in steps if s["action"] == "wait")
        self.assertTrue(3 <= hold <= 12, hold)
        self.run_cue(plan)
        self.assertEqual("Pack Break Table", self.state.program)
        self.assertIn("Holo Rare", self.state.input_settings["Hit Card Text"]["text"])
        scene_changes = [data["sceneName"] for name, data in self.state.calls if name == "SetCurrentProgramScene"]
        self.assertEqual(["Pack Break Closeup", "Pack Break Table"], scene_changes)

    def test_holding_moves_cut_to_the_holding_scene_and_label_it(self):
        for name, label, muted in (("starting-soon", "Starting Soon", False), ("be-right-back", "Be Right Back", True)):
            with self.subTest(cue=name):
                self.state = show_state()
                self.service = CueService(ProductionService(self.state.factory))
                self.run_cue(load(name + ".json"))
                self.assertEqual("Pack Break Holding", self.state.program)
                self.assertEqual(label, self.state.input_settings["Title Text"]["text"])
                self.assertEqual(muted, self.state.muted)

    def test_wrap_up_returns_to_the_table_with_a_thank_you(self):
        self.state.program = "Pack Break Holding"
        self.run_cue(load("wrap-up.json"))
        self.assertEqual("Pack Break Table", self.state.program)
        self.assertEqual("Thanks for Watching", self.state.input_settings["Title Text"]["text"])

    def test_no_show_move_can_start_an_output(self):
        for name in (*CUES, "panic.json"):
            text = (PACK / (name if name.endswith(".json") else name + ".json")).read_text(encoding="utf-8")
            self.assertNotIn("stream", text.replace("Pack Break", "").lower())


class PanicCueTests(unittest.TestCase):
    def test_panic_file_loads_through_the_environment_and_is_a_bounded_cue(self):
        with patch.dict("os.environ", {"OBS_MCP_PANIC_CUE": str(PACK / "panic.json")}):
            cue = load_panic_cue()
        normalized = _schema(cue)
        self.assertLessEqual(len(normalized["steps"]), 4)
        self.assertEqual(0, normalized["total_wait_seconds"])
        self.assertIsNone(normalized["expected_scene"])
        self.assertEqual({"scene_select", "audio_mute"}, {step["action"] for step in normalized["steps"]})

    def test_panic_cuts_to_the_safe_scene_and_mutes_the_microphone_from_any_scene(self):
        for start in SCENES:
            with self.subTest(start=start):
                state = show_state()
                state.program, state.muted = start, False
                with patch.dict("os.environ", {"OBS_MCP_PANIC_CUE": str(PACK / "panic.json")}):
                    service = CueService(ProductionService(state.factory))
                preview = service.panic()
                self.assertEqual("preview", preview["state"])
                self.assertEqual([], state.mutations())
                result = service.panic(dry_run=False)
                self.assertEqual("completed", result["state"], result)
                self.assertEqual("Pack Break Holding", state.program)
                self.assertTrue(state.muted)


class EventRouteTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("obs_director.cues._wait", side_effect=instant))
        self.state = show_state()
        self.document = load("event-routes.json")
        self.events = EventService(CueService(ProductionService(self.state.factory)), registry=self.document["routes"])

    def event(self, kind, event_id, title, summary):
        return {"schema": "obs.event.v1", "event_id": event_id, "event_type": kind,
                "occurred_at": "2026-01-01T12:00:00Z",
                "payload": {"title": title, "summary": summary, "reference": "Pack 12 box"}}

    def test_route_file_declares_the_schema_and_maps_each_event_to_a_show_move(self):
        self.assertEqual("obs.event-routes.v1", self.document["schema"])
        self.assertEqual({"pack.opened", "hit.revealed"}, set(self.document["routes"]))
        for kind, cue in (("pack.opened", "open-pack"), ("hit.revealed", "reveal-hit")):
            route = self.document["routes"][kind]
            self.assertEqual(load(cue + ".json"), route["cue"])
            self.assertEqual(0, route["text_step"])

    def test_route_file_loads_through_the_environment(self):
        with patch.dict("os.environ", {"OBS_MCP_EVENT_ROUTES": str(PACK / "event-routes.json")}):
            service = EventService(CueService(ProductionService(self.state.factory)))
        self.assertEqual({"pack.opened", "hit.revealed"}, set(service._routes))

    def test_pack_opened_fills_the_now_opening_card_from_the_event(self):
        self.state.program = "Pack Break Holding"
        result = self.events.dispatch_event(self.event("pack.opened", "open-1", "Booster Pack 12", "Opening now"), dry_run=False)
        self.assertEqual("completed", result["state"], result)
        text = self.state.input_settings["Now Opening Text"]["text"]
        self.assertTrue(text.startswith("Booster Pack 12\nOpening now"))
        self.assertEqual("Pack Break Table", self.state.program)

    def test_hit_revealed_fills_the_hit_card_cuts_to_the_closeup_and_comes_back(self):
        result = self.events.dispatch_event(self.event("hit.revealed", "hit-1", "Card of the Night", "Holo Rare"), dry_run=False)
        self.assertEqual("completed", result["state"], result)
        self.assertTrue(self.state.input_settings["Hit Card Text"]["text"].startswith("Card of the Night\nHolo Rare"))
        scene_changes = [data["sceneName"] for name, data in self.state.calls if name == "SetCurrentProgramScene"]
        self.assertEqual(["Pack Break Closeup", "Pack Break Table"], scene_changes)


class DocsTests(unittest.TestCase):
    def test_docs_page_names_every_source_scene_cue_and_phrase_and_is_linked(self):
        page = (ROOT / "docs" / "PACK-BREAK.md").read_text(encoding="utf-8")
        for token in (*SCENES, *TEXT_INPUTS, "Table Camera", "Closeup Camera", "Face Camera", "Microphone",
                      "OBS_MCP_PANIC_CUE", "OBS_MCP_EVENT_ROUTES", "pack.opened", "hit.revealed",
                      "pack-break-table", "pack-break-closeup", "pack-break-vertical",
                      *(name + ".json" for name in CUES), "panic.json", "event-routes.json"):
            self.assertIn(token, page)
        self.assertIn("docs/PACK-BREAK.md", (ROOT / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
