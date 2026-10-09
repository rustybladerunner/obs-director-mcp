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
        self.states()
        return {"active": True}

    def states(self):
        if self.lost_lease:
            raise RuntimeError("Synthetic lease continuity lost")
        return ["STARTING", "STARTED"]

    async def call(self, name, **arguments):
        self.calls.append((name, arguments))
        if self.call_hook:
            self.call_hook(name, arguments)
        if name == "obs_select_scene":
            self.scene = arguments["scene_name"]
        return {"applied": True, "verified": True}

    def run_cut(self, **kwargs):
        return finish(tournament.stinger_cut(self, self, self.call, "Before", "After",
                                            {"Before": 11, "After": 22}, self.trace, **kwargs))

    def test_deluxe_uses_its_own_transition_and_opaque_window(self):
        self.cursors = [950, 1150, 1400]
        self.run_cut(timing=tournament.DELUXE)
        self.assertEqual(self.trace[0]["cursor_before_ms"], 1150)
        self.assertEqual(self.trace[0]["cursor_after_ms"], 1400)
        self.assertEqual(len(self.scene_writes()), 1)
        self.assertGreaterEqual(self.clock, 1.7)

    def test_deluxe_late_reply_is_not_covered(self):
        self.cursors = [1150, 2067]
        with self.assertRaises(RuntimeError): self.run_cut(timing=tournament.DELUXE)
        self.assertEqual(self.trace, [])

    def test_unrecognized_timing_cannot_mutate_obs(self):
        with self.assertRaises(ValueError): self.run_cut(timing={**tournament.DELUXE, "opaque_end_ms": 3000})
        self.assertEqual(self.calls, [])

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

    def test_stop_event_received_during_final_scene_read_blocks_first_overlay_write(self):
        def stop(name):
            if name == "GetCurrentProgramScene": self.lost_lease = True
        self.read_hook = stop
        with self.assertRaises(RuntimeError): self.run_cut()
        self.assertEqual(self.calls, [])

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

    def test_neutral_starter_requires_no_mascot_stinger_or_audio(self):
        result = tournament.assets(self.directory)
        self.assertIsNone(result["stinger"])
        self.assertIsNone(result["audio"])
        with self.assertRaises(ValueError): tournament.assets(self.directory, audio_directory=self.directory)

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

    def test_deluxe_hash_and_certified_timing_are_accepted(self):
        self.metadata.update(tournament.DELUXE, style="deluxe")
        self.save()
        self.assertEqual(self.load()["timing"], tournament.DELUXE)
        self.metadata["opaque_start_ms"] = 0
        self.save()
        with self.assertRaises(ValueError): self.load()


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.manifest = {"schema": "obs.original-audio.v1", "stinger_impact_seconds": 1.1, "artifacts": {}}
        for name, frames in (("intermission.ogg", 960000), ("stinger.wav", 144000), ("alert.wav", 33600)):
            data = b"synthetic asset, no audio decode claim"
            (self.directory / name).write_bytes(data)
            self.manifest["artifacts"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "sample_rate": 48000, "channels": 2, "decoded": {"frames": frames}}
        self.save()

    def save(self):
        (self.directory / "audio-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_pack_checks_exact_names_format_duration_and_hashes(self):
        self.assertEqual(set(tournament.audio_assets(self.directory)), set(self.manifest["artifacts"]))
        (self.directory / "stinger.wav").write_bytes(b"changed")
        with self.assertRaises(ValueError): tournament.audio_assets(self.directory)

    def test_path_traversal_and_wrong_duration_are_rejected(self):
        self.manifest["artifacts"]["../stinger.wav"] = self.manifest["artifacts"].pop("stinger.wav")
        self.save()
        with self.assertRaises(ValueError): tournament.audio_assets(self.directory)
        self.manifest["artifacts"]["stinger.wav"] = self.manifest["artifacts"].pop("../stinger.wav")
        self.manifest["artifacts"]["stinger.wav"]["decoded"]["frames"] = 140000
        self.save()
        with self.assertRaises(ValueError): tournament.audio_assets(self.directory)

    def test_mux_maps_existing_picture_and_effect_to_one_clock_without_reencoding_alpha(self):
        files = {"stinger": self.directory / "clip.webm", "audio": tournament.audio_assets(self.directory)}
        with patch.object(tournament.subprocess, "run") as run:
            target = tournament.mux_soundtrack(files, self.directory, "ffmpeg")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertIn("-n", command)
        self.assertEqual(command[command.index("-metadata:s:v:0") + 1], "alpha_mode=1")
        self.assertEqual(command[-1], str(target))
        self.assertIn(str(files["audio"]["stinger.wav"]), command)
        self.assertIs(run.call_args.kwargs["check"], True)

    def test_only_intermission_states_get_background_music(self):
        self.assertEqual(tournament.INTERMISSIONS, {"starting-soon", "break", "ending"})
        self.assertTrue({"table", "standings", "replay"}.isdisjoint(tournament.INTERMISSIONS))


class ReactionAssetTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.manifest = {"schema": "obs.puppet-reactions.v1", "clips": {}}
        for name in ("welcome", "rethink", "break"):
            data = ("synthetic " + name + "; no decode claim").encode()
            path = self.directory / (name + ".webm")
            path.write_bytes(data)
            self.manifest["clips"][path.name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                "duration_seconds": 3.0, "frames": 90, "fps": 30, "width": 960, "height": 540,
                "verification": {"alpha_mode": "1", "audio": {"frames": 144000}}}
        self.save()

    def save(self):
        (self.directory / "reactions-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_exact_three_clip_manifest_loads_and_changed_bytes_refuse(self):
        self.assertEqual(set(tournament.reaction_assets(self.directory)), {"welcome", "rethink", "break"})
        (self.directory / "welcome.webm").write_bytes(b"changed")
        with self.assertRaises(ValueError): tournament.reaction_assets(self.directory)

    def test_relative_cli_reaction_directory_is_resolved_before_validation(self):
        with patch.object(tournament.os, "getcwd", return_value=str(self.directory.parent)):
            result = tournament.reaction_assets(Path(self.directory.name))
        self.assertEqual(result["welcome"], (self.directory / "welcome.webm").resolve())

    def test_missing_extra_and_traversal_names_refuse(self):
        original = deepcopy(self.manifest)
        for replacement in (None, "extra.webm", "../welcome.webm"):
            self.manifest = deepcopy(original)
            row = self.manifest["clips"].pop("welcome.webm")
            if replacement: self.manifest["clips"][replacement] = row
            self.save()
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                tournament.reaction_assets(self.directory)

    def test_malformed_geometry_timing_alpha_audio_and_json_refuse(self):
        original = deepcopy(self.manifest)
        changes = [{"frames": True}, {"width": 1280}, {"duration_seconds": 4}, {"bytes": True},
                   {"verification": {"alpha_mode": "0", "audio": {"frames": 144000}}},
                   {"verification": {"alpha_mode": "1", "audio": {"frames": 140000}}}]
        for change in changes:
            self.manifest = deepcopy(original)
            self.manifest["clips"]["welcome.webm"].update(change)
            self.save()
            with self.subTest(change=change), self.assertRaises(ValueError): tournament.reaction_assets(self.directory)
        self.manifest = []
        self.save()
        with self.assertRaises(ValueError): tournament.reaction_assets(self.directory)

    def test_invalid_volume_refuses_before_asset_reads(self):
        for volume in (True, float("nan"), float("inf"), -61, 1):
            with self.subTest(volume=volume), self.assertRaises(ValueError):
                tournament.assets(self.directory, reaction_volume_db=volume)

    def test_default_duration_unchanged_optional_layers_are_hidden_and_keep_clear(self):
        self.assertEqual(tournament.phase_seconds({}), 6)
        files = {"reactions": {"welcome": self.directory / "welcome.webm"}}
        self.assertEqual(tournament.phase_seconds(files), 8)
        self.assertEqual(tournament.REACTION_PHASES, {1: "welcome", 3: "rethink", 4: "break"})
        self.assertIsNone(tournament.reaction_layer("table", {}))
        self.assertIsNone(tournament.reaction_layer("standings", files))
        layer = tournament.reaction_layer("table", files)
        self.assertIs(layer["visible"], False)
        self.assertEqual(layer["rect"], {"x": 1344, "y": 320, "width": 512, "height": 288})
        layer["rect"]["y"] = 0
        self.assertEqual(tournament.REACTION_RECTS["table"]["y"], 320)

    def test_each_reaction_has_its_reserved_scene_geometry(self):
        files = {"reactions": {"welcome": self.directory / "welcome.webm"}}
        expected = {"table": (1344, 320, 512, 288), "replay": (1420, 820, 400, 225), "break": (720, 735, 400, 225)}
        self.assertEqual(set(tournament.REACTION_RECTS), set(expected))
        for key, values in expected.items():
            with self.subTest(scene=key):
                layer = tournament.reaction_layer(key, files)
                rect = layer["rect"]
                self.assertEqual(tuple(rect[name] for name in ("x", "y", "width", "height")), values)
                self.assertGreaterEqual(rect["x"], 0)
                self.assertGreaterEqual(rect["y"], 0)
                self.assertLessEqual(rect["x"] + rect["width"], 1920)
                self.assertLessEqual(rect["y"] + rect["height"], 1080)
                self.assertEqual(rect["width"] * 9, rect["height"] * 16)
                self.assertIs(layer["visible"], False)
        table = tournament.reaction_layer("table", files)["rect"]
        self.assertLessEqual(table["y"] + table["height"], 700)
        for key in ("starting-soon", "standings", "ending"):
            self.assertIsNone(tournament.reaction_layer(key, files))

    def test_chart_space_is_reserved_only_for_reaction_table(self):
        files = {"reactions": {"welcome": self.directory / "welcome.webm"}}
        self.assertEqual(tournament.browser_css("table", files),
                         ".chart-panel:not(.replay-panel) .chart{width:calc(100% - 440px)}")
        for key in set(tournament.SCENES):
            self.assertEqual(tournament.browser_css(key, {}), "")
            self.assertEqual(tournament.browser_css(key, {"reactions": None}), "")
            if key != "table": self.assertEqual(tournament.browser_css(key, files), "")


class ReactionGeometryTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.settings = ObsSettings(timeout=10)
        self.shapes = [(0, 0), (960, 540)]
        self.hook = None
        self.owner = "owned"
        self.active = False
        self.reads = []
        self.enterContext(patch.object(tournament.time, "monotonic", side_effect=lambda: self.clock))
        self.enterContext(patch.object(tournament.time, "sleep", side_effect=self.sleep))

    def sleep(self, seconds): self.clock += seconds

    def request(self, name, data=None):
        self.reads.append((name, self.settings.timeout))
        if self.hook: self.hook(name)
        if name == "GetProfileList": return {"currentProfileName": self.owner}
        if name == "GetSceneCollectionList": return {"currentSceneCollectionName": self.owner}
        if name in ("GetRecordStatus", "GetStreamStatus"): return {"outputActive": self.active}
        if name == "GetSceneItemTransform":
            shape = self.shapes.pop(0) if len(self.shapes) > 1 else self.shapes[0]
            return {"sceneItemTransform": {"sourceWidth": shape[0], "sourceHeight": shape[1]}}
        raise AssertionError(name)

    def wait(self): return tournament.wait_reaction_geometry(self, "owned", "owned", 7)

    def test_delayed_decoded_shape_has_bounded_requests_and_restores_settings(self):
        original = self.settings
        self.assertIs(self.wait()["verified"], True)
        self.assertGreater(self.clock, 0)
        self.assertTrue(all(0 < budget <= 5 for _, budget in self.reads))
        self.assertIs(self.settings, original)

    def test_bad_shape_unknown_output_and_wrong_dimensions_refuse(self):
        for shape in ((True, 540), (-1, 540), (float("nan"), 540), (1280, 720)):
            self.shapes = [shape]
            with self.subTest(shape=shape), self.assertRaises(RuntimeError): self.wait()
        self.shapes = [(960, 540)]
        self.active = None
        with self.assertRaises(RuntimeError): self.wait()

    def test_zero_shape_times_out_and_late_matching_read_is_rejected(self):
        self.shapes = [(0, 0)]
        with self.assertRaises(RuntimeError): self.wait()
        self.clock = 0
        self.shapes = [(960, 540)]
        self.hook = lambda name: self.sleep(5) if name == "GetSceneItemTransform" else None
        with self.assertRaises(RuntimeError): self.wait()

    def test_selection_or_recording_change_during_geometry_read_refuses(self):
        for field, value in (("owner", "operator"), ("active", True)):
            self.owner, self.active = "owned", False
            self.shapes = [(960, 540)]
            self.hook = lambda name: setattr(self, field, value) if name == "GetSceneItemTransform" else None
            with self.subTest(field=field), self.assertRaises(RuntimeError): self.wait()


class ReactionPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.fake = OverlayTests()
        self.fake.setUp()
        self.addCleanup(self.fake.doCleanups)
        self.fake.cursors = [0, 700, 1450, 1700, 2300, {"mediaState": "OBS_MEDIA_STATE_ENDED", "mediaCursor": None}]
        self.captures = []

    def run_reaction(self):
        f = self.fake
        return finish(tournament.play_reaction(f, f, f.call, "Before", "welcome", 7, f.trace,
                                              lambda: self.captures.append(f.clock)))

    def test_show_stop_restart_capture_end_hide_in_order(self):
        self.run_reaction()
        f = self.fake
        self.assertEqual([name for name, _ in f.calls], ["obs_source_visibility", "obs_media_action", "obs_media_action", "obs_source_visibility"])
        self.assertEqual([a["action"] for n, a in f.calls if n == "obs_media_action"], ["stop", "restart"])
        self.assertEqual([a["enabled"] for n, a in f.calls if n == "obs_source_visibility"], [True, False])
        self.assertEqual(len(self.captures), 1)
        self.assertEqual(f.trace[-1]["step"], "reaction_completed")
        self.assertEqual(f.scene_writes(), [])

    def test_late_missing_stopped_and_stalled_media_cannot_claim_completion(self):
        for values in ([2200], [{}], [{"mediaState": "OBS_MEDIA_STATE_STOPPED"}], [1500]):
            self.fake.cursors = values
            self.fake.trace.clear()
            with self.subTest(values=values), self.assertRaises(RuntimeError): self.run_reaction()
            self.assertFalse(any(row["step"] == "reaction_completed" for row in self.fake.trace))

    def test_queued_lease_loss_or_wrong_program_prevents_first_write(self):
        self.fake.read_hook = lambda name: setattr(self.fake, "lost_lease", True) if name == "GetCurrentProgramScene" else None
        with self.assertRaises(RuntimeError): self.run_reaction()
        self.assertEqual(self.fake.calls, [])
        self.fake.read_hook = None
        self.fake.lost_lease = False
        self.fake.scene = "Operator"
        with self.assertRaises(RuntimeError): self.run_reaction()
        self.assertEqual(self.fake.calls, [])

    def test_lost_restart_ack_is_not_retried_or_claimed_complete(self):
        def lose(name, args):
            if args.get("action") == "restart": raise RuntimeError("Synthetic ACK loss")
        self.fake.call_hook = lose
        with self.assertRaises(RuntimeError): self.run_reaction()
        self.assertEqual(len([a for n, a in self.fake.calls if a.get("action") == "restart"]), 1)
        self.assertEqual(self.captures, [])
        self.assertEqual(self.fake.trace, [])

    def test_late_status_reply_restores_timeout_and_does_not_capture(self):
        original = self.fake.settings
        self.fake.cursors = [1500]
        self.fake.read_hook = lambda name: setattr(self.fake, "clock", self.fake.clock + 5) if name == "GetMediaInputStatus" else None
        with self.assertRaises(RuntimeError): self.run_reaction()
        self.assertEqual(self.captures, [])
        self.assertIs(self.fake.settings, original)

    def test_slow_screenshot_or_ended_post_read_cannot_claim_visible_evidence(self):
        f = self.fake
        f.cursors = [1450, 1700, {"mediaState": "OBS_MEDIA_STATE_ENDED"}]
        def capture(): f.clock += 3.2
        with self.assertRaises(RuntimeError):
            finish(tournament.play_reaction(f, f, f.call, "Before", "welcome", 7, f.trace, capture))
        self.assertEqual(f.trace, [])
        f.cursors = [1450, {"mediaState": "OBS_MEDIA_STATE_ENDED"}]
        with self.assertRaises(RuntimeError): self.run_reaction()
        self.assertEqual(f.trace, [])

    def test_guard_that_consumes_completion_deadline_refuses_hide_receipt(self):
        self.fake.cursors = [1450, 1700, {"mediaState": "OBS_MEDIA_STATE_ENDED"}]
        def delay(name):
            if name == "GetCurrentProgramScene" and any(row.get("step") == "reaction_visible" for row in self.fake.trace):
                self.fake.clock += 5
        self.fake.read_hook = delay
        with self.assertRaises(RuntimeError): self.run_reaction()
        self.assertFalse(any(row["step"] == "reaction_completed" for row in self.fake.trace))


class ReactionPrimeTests(unittest.TestCase):
    def test_sources_prime_muted_then_hide_before_independent_gain_and_unmute(self):
        calls, trace = [], []
        class Client:
            def request(self, name, data=None):
                if name == "GetProfileList": return {"currentProfileName": "owned"}
                if name == "GetSceneCollectionList": return {"currentSceneCollectionName": "owned"}
                if name == "GetMediaInputStatus": return {"mediaState": "OBS_MEDIA_STATE_ENDED"}
                raise AssertionError(name)
        async def call(name, **args):
            calls.append((name, args))
            if name == "obs_scene_sources":
                return {"sources": [{"sourceName": tournament.reaction_source("welcome"), "sceneItemId": 4}]}
            return {"verified": True}
        async def sleep(seconds): pass
        files = {"reactions": {"welcome": Path("test-only.webm")}, "reaction_volume_db": -12}
        with patch.object(tournament, "wait_reaction_geometry", return_value={"verified": True}) as geometry, patch.object(tournament.asyncio, "sleep", sleep):
            finish(tournament.prime_reactions(Client(), call, call, files, trace, "owned", "owned"))
        self.assertEqual(geometry.call_count, 2)
        self.assertEqual([a["muted"] for n, a in calls if n == "obs_audio_mute"], [True, False])
        hide = next(i for i, (n, _) in enumerate(calls) if n == "obs_source_visibility")
        volume = next(i for i, (n, _) in enumerate(calls) if n == "obs_audio_volume")
        self.assertLess(hide, volume)
        self.assertEqual(calls[volume][1]["volume_db"], -12)
        self.assertEqual(trace[0]["reaction"], "welcome")

    def test_ambiguous_or_boolean_item_ids_refuse(self):
        for rows in ([], [{"sourceName": "x", "sceneItemId": True}],
                     [{"sourceName": "x", "sceneItemId": 1}, {"sourceName": "x", "sceneItemId": 2}]):
            with self.subTest(rows=rows), self.assertRaises(RuntimeError): tournament.scene_item_id(rows, "x")


if __name__ == "__main__":
    unittest.main()
