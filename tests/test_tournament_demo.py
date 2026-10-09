"""Synthetic overlay timing and local asset checks; no OBS, subprocess or sockets."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import tournament_demo as tournament
from obs_director.transport import ObsSettings


def finish(coroutine):
    """All fixture awaits complete inline; do not initialize an OS event loop."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("Fixture coroutine unexpectedly suspended")


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.cursors = [0, 450, 950, 1100]
        self.calls, self.reads, self.trace = [], [], []
        self.scene = "Before"
        self.lost_lease = False
        self.observe_count = 0
        self.settings = ObsSettings(timeout=10)
        self.read_hook = self.call_hook = None
        self.enterContext(patch.object(tournament.time, "monotonic", side_effect=lambda: self.clock))
        self.enterContext(patch.object(tournament.asyncio, "sleep", side_effect=self.sleep))

    async def sleep(self, seconds):
        self.clock += seconds

    def request(self, name, data=None):
        self.reads.append((name, data, self.settings.timeout))
        if self.read_hook:
            self.read_hook(name)
        if name == "GetCurrentProgramScene":
            return {"currentProgramSceneName": self.scene}
        if name != "GetMediaInputStatus":
            raise AssertionError(name)
        value = self.cursors.pop(0) if len(self.cursors) > 1 else self.cursors[0]
        if isinstance(value, dict):
            return deepcopy(value)
        return {"mediaState": "OBS_MEDIA_STATE_PLAYING", "mediaCursor": value}

    def observe(self):
        self.observe_count += 1
        if self.lost_lease:
            raise RuntimeError("Synthetic lease continuity lost")
        return {"active": True}

    async def call(self, name, **arguments):
        self.calls.append((name, arguments))
        if self.call_hook:
            self.call_hook(name, arguments)
        if name == "obs_select_scene":
            self.scene = arguments["scene_name"]
        return {"applied": True, "verified": True}

    def run_cut(self):
        return finish(tournament.stinger_cut(self, self, self.call, "Before", "After",
                                            {"Before": 11, "After": 22}, self.trace))

    def scene_writes(self):
        return [arguments for name, arguments in self.calls if name == "obs_select_scene"]

    def test_covered_progression_switches_once_and_hides_both_items(self):
        self.run_cut()
        self.assertEqual(len(self.scene_writes()), 1)
        self.assertEqual(self.scene, "After")
        self.assertEqual(self.trace[0]["cursor_before_ms"], 950)
        self.assertEqual(self.trace[0]["cursor_after_ms"], 1100)
        self.assertIs(self.trace[0]["covered"], True)
        self.assertEqual([args["enabled"] for name, args in self.calls if name == "obs_source_visibility"],
                         [True, True, False, False])

    def test_missed_window_refuses_before_scene_write(self):
        self.cursors = [1201]
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(self.scene_writes(), [])
        self.assertEqual(self.trace, [])

    def test_missing_malformed_or_not_playing_cursor_refuses_before_scene_write(self):
        for state in ({}, {"mediaState": "OBS_MEDIA_STATE_STOPPED", "mediaCursor": 950},
                      {"mediaState": "OBS_MEDIA_STATE_PLAYING", "mediaCursor": True},
                      {"mediaState": "OBS_MEDIA_STATE_PLAYING", "mediaCursor": float("nan")},
                      {"mediaState": "OBS_MEDIA_STATE_PLAYING", "mediaCursor": None}):
            with self.subTest(state=state):
                self.cursors = [state]
                with self.assertRaises(RuntimeError): self.run_cut()
                self.assertEqual(self.scene_writes(), [])

    def test_cut_that_finishes_after_opaque_hold_is_not_reported_covered(self):
        self.cursors = [950, 1734]
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(len(self.scene_writes()), 1)
        self.assertEqual(self.trace, [])

    def test_rewinding_after_cut_is_not_reported_covered(self):
        self.cursors = [950, 500]
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(len(self.scene_writes()), 1)
        self.assertEqual(self.trace, [])

    def test_stalled_cursor_times_out_without_scene_write(self):
        self.cursors = [200]
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(self.scene_writes(), [])
        self.assertLessEqual(self.clock, 4.1)

    def test_matching_cursor_arriving_after_deadline_cannot_authorize_cut(self):
        self.cursors = [950, 1100]
        def delay(name):
            if name == "GetMediaInputStatus": self.clock += 4.1
        self.read_hook = delay
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(self.scene_writes(), [])

    def test_media_rpc_budget_is_capped_and_restored(self):
        original = self.settings
        self.run_cut()
        budgets = [timeout for name, _, timeout in self.reads if name == "GetMediaInputStatus"]
        self.assertTrue(budgets)
        self.assertTrue(all(0 < timeout <= 4 for timeout in budgets), budgets)
        self.assertIs(self.settings, original)

    def test_final_guard_cannot_carry_an_old_cursor_past_deadline_into_scene_write(self):
        self.cursors = [950, 1100]
        def delay(name):
            if name == "GetCurrentProgramScene" and any(row[0] == "GetMediaInputStatus" for row in self.reads):
                self.clock += 4.1
        self.read_hook = delay
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(self.scene_writes(), [])

    def test_final_guard_that_outlasts_opaque_window_refuses_before_scene_write(self):
        self.cursors = [950, 1100]
        def delay(name):
            if name == "GetCurrentProgramScene" and any(row[0] == "GetMediaInputStatus" for row in self.reads):
                self.clock += 1
                self.cursors = [1950]
        self.read_hook = delay
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(self.scene_writes(), [])

    def test_cursor_reply_latency_counts_toward_the_opaque_window(self):
        self.cursors = [950, 1950]
        def delay(name):
            if name == "GetMediaInputStatus" and len([row for row in self.reads if row[0] == name]) == 1:
                self.clock += 1
        self.read_hook = delay
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(self.scene_writes(), [])

    def test_lost_scene_ack_is_not_retried_or_recorded_as_covered(self):
        self.cursors = [950, 1100]
        def fail(name, arguments):
            if name == "obs_select_scene": raise RuntimeError("Synthetic acknowledgement lost")
        self.call_hook = fail
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(len(self.scene_writes()), 1)
        self.assertEqual(self.trace, [])

    def test_wrong_current_scene_refuses_before_overlay_or_scene_mutation(self):
        self.scene = "Operator scene"
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(self.calls, [])

    def test_program_change_during_cursor_read_refuses_scene_write(self):
        self.cursors = [950, 1100]
        def change(name):
            if name == "GetMediaInputStatus": self.scene = "Operator scene"
        self.read_hook = change
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(self.scene_writes(), [])

    def test_lease_loss_after_first_show_refuses_following_mutations(self):
        def lose(name, arguments):
            if name == "obs_source_visibility": self.lost_lease = True
        self.call_hook = lose
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.scene_writes(), [])


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        names = {"table": "Table", "standings": "Standings", "replay": "Replay",
                 "starting-soon": "Starting Soon", "break": "Break", "ending": "Ending"}
        scenes = [{"id": key, "name": "Tournament " + name, "file": key + ".html"}
                  for key, name in names.items()]
        files = [row["file"] for row in scenes] + ["broadcast.js", "broadcast.css", "snapshot.json"]
        for name in files: (self.directory / name).write_text("synthetic:" + name, encoding="utf-8")
        self.manifest = {"schema": "obs.tournament-scenes.v1", "canvas": {"width": 1920, "height": 1080},
                         "scenes": scenes, "hashes": {name: self.digest(self.directory / name) for name in files}}
        self.stinger = self.directory / "test-only.webm"
        self.stinger.write_bytes(b"synthetic clip; no decode claimed")
        self.metadata = {"schema": "obs.overlay-stinger.v1", "file": self.stinger.name, "frames": 66,
                         "fps": 30, "width": 1920, "height": 1080, "duration_ms": 2200,
                         "opaque_start_ms": 500, "opaque_end_ms": 1733, "transition_point_ms": 900,
                         "audio": False, "sha256": self.digest(self.stinger)}
        self.save()

    @staticmethod
    def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

    def save(self):
        (self.directory / "scenes.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.stinger.with_suffix(".json").write_text(json.dumps(self.metadata), encoding="utf-8")

    def load(self): return tournament.assets(self.directory, self.stinger)

    def test_contract_accepts_all_six_pages_with_declared_hashes(self):
        self.assertEqual(set(self.load()["scenes"]), set(tournament.SCENES))

    def test_changed_page_and_changed_stinger_are_rejected(self):
        (self.directory / "table.html").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError): self.load()
        self.manifest["hashes"]["table.html"] = self.digest(self.directory / "table.html")
        self.save()
        self.stinger.write_bytes(b"changed")
        with self.assertRaises(ValueError): self.load()

    def test_traversal_in_manifest_hash_key_is_rejected(self):
        self.manifest["hashes"]["../outside.html"] = "0" * 64
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_unknown_scene_identity_and_wrong_stinger_timing_are_rejected(self):
        self.manifest["scenes"][0]["name"] = "Unexpected"
        self.save()
        with self.assertRaises(ValueError): self.load()
        self.manifest["scenes"][0]["name"] = "Tournament Table"
        self.metadata["opaque_end_ms"] = 2000
        self.save()
        with self.assertRaises(ValueError): self.load()

    def test_missing_snapshot_hash_is_rejected_before_scene_setup(self):
        del self.manifest["hashes"]["snapshot.json"]
        self.save()
        with self.assertRaises(ValueError): self.load()


if __name__ == "__main__":
    unittest.main()
