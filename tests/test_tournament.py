"""Offline tournament contract, ranking, generated pages and browser parity."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from obs_director.tournament import (
    SCENES, TournamentValidationError, demo_snapshot, rank_snapshot,
    read_snapshot, render_tournament, validate_snapshot,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class TournamentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = demo_snapshot(now=NOW)

    def test_strict_projection_is_detached_and_has_no_io(self):
        original = deepcopy(self.snapshot)
        with patch.object(Path, "open", side_effect=AssertionError("Unexpected files")):
            result = validate_snapshot(self.snapshot, now=NOW)
            ranked = rank_snapshot(self.snapshot, now=NOW)
        result["participants"][0]["name"] = "changed"
        self.assertEqual(original, self.snapshot)
        self.assertEqual([1268500, 924550], [row["net_minor"] for row in ranked["participants"]])

    def test_fees_change_rank_and_negative_results_are_ranked(self):
        for index, values in enumerate(((100, 0, 110), (0, 0, 5))):
            row = self.snapshot["participants"][index]
            row.update(dict(zip(("realized_minor", "unrealized_minor", "fees_minor"), values)))
        result = rank_snapshot(self.snapshot, now=NOW)
        self.assertEqual(["housebot", "piphound"], [row["id"] for row in result["standings"]])
        self.assertEqual([-5, -10], [row["net_minor"] for row in result["standings"]])

    def test_ties_are_shared_and_next_rank_skips_without_input_order_tiebreak(self):
        first, second = self.snapshot["participants"]
        for row in (first, second):
            row.update(realized_minor=1000, unrealized_minor=0, fees_minor=0)
        third = deepcopy(second)
        third.update(id="third", name="THIRD", realized_minor=900)
        self.snapshot["participants"].append(third)
        result = rank_snapshot(self.snapshot, now=NOW)
        self.assertEqual([1, 1, 3], [row["rank"] for row in result["standings"]])
        self.assertEqual([True, True, False], [row["tied"] for row in result["standings"]])
        self.assertEqual(["housebot", "piphound", "third"], [row["id"] for row in result["standings"]])

    def test_missing_and_stale_values_are_unranked_and_zero_is_known(self):
        self.snapshot["participants"][0]["fees_minor"] = None
        self.snapshot["participants"][1]["observed_at"] = "2026-01-01T11:57:59Z"
        result = rank_snapshot(self.snapshot, now=NOW)
        self.assertEqual(["missing", "stale"], [row["eligibility"] for row in result["participants"]])
        self.assertTrue(all(row["rank"] is None for row in result["participants"]))
        zero = demo_snapshot(now=NOW)
        zero["participants"][0].update(realized_minor=0, unrealized_minor=0, fees_minor=0)
        self.assertEqual(0, rank_snapshot(zero, now=NOW)["participants"][0]["net_minor"])

    def test_freshness_boundary_and_whole_snapshot_staleness(self):
        self.assertEqual(2, rank_snapshot(self.snapshot, now=NOW + timedelta(seconds=120))["ranked_count"])
        self.assertEqual(0, rank_snapshot(self.snapshot, now=NOW + timedelta(seconds=121))["ranked_count"])
        self.snapshot["participants"][0]["observed_at"] = None
        self.assertEqual("missing", rank_snapshot(self.snapshot, now=NOW)["participants"][0]["eligibility"])

    def test_unknown_mixed_and_unsupported_inputs_refuse(self):
        cases = []
        for field, value in (("schema", "other"), ("mode", "live"), ("mode", {}), ("currency", "JPY"),
                             ("currency", []), ("round", True), ("round", 1000), ("stage", "cash"),
                             ("session_id", "../secret"), ("label", ""), ("label", "x\u202e")):
            value_snapshot = deepcopy(self.snapshot)
            value_snapshot[field] = value
            cases.append(value_snapshot)
        for extra in ("currency", "session_id", "unexpected"):
            value_snapshot = deepcopy(self.snapshot)
            value_snapshot["participants"][1][extra] = "different"
            cases.append(value_snapshot)
        cases += [None, [], {**self.snapshot, "provider": "untrusted"}]
        for value in cases:
            with self.subTest(value=str(value)[:100]), self.assertRaises(TournamentValidationError):
                validate_snapshot(value, now=NOW)

    def test_money_rejects_booleans_floats_nonfinite_huge_values_and_negative_fees(self):
        for key in ("realized_minor", "unrealized_minor", "fees_minor"):
            for value in (True, "100", 1.0, float("nan"), float("inf"), 10**1000, -(10**12) - 1):
                invalid = deepcopy(self.snapshot)
                invalid["participants"][1][key] = value
                with self.subTest(key=key, value=str(value)[:30]), self.assertRaises(TournamentValidationError):
                    validate_snapshot(invalid, now=NOW)
        self.snapshot["participants"][0]["fees_minor"] = -1
        with self.assertRaises(TournamentValidationError):
            validate_snapshot(self.snapshot, now=NOW)

    def test_timestamps_control_characters_duplicates_and_participant_bounds(self):
        for timestamp in ("now", "2026-02-30T00:00:00Z", "2026-01-01T12:00:00+00:00", "2026-01-01T12:00:06Z",
                          "2025-12-31T24:00:00Z", "2026-01-01T11:60:00Z", "2026-01-01T11:59:60Z"):
            invalid = deepcopy(self.snapshot)
            invalid["as_of"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(TournamentValidationError):
                validate_snapshot(invalid, now=NOW)
        self.snapshot["participants"][0]["observed_at"] = "2026-01-01T12:00:01Z"
        with self.assertRaises(TournamentValidationError):
            validate_snapshot(self.snapshot, now=NOW)
        for mutate in (lambda p: p.pop(), lambda p: p.extend(deepcopy(p) * 4),
                       lambda p: p[1].update(id=p[0]["id"]), lambda p: p[1].update(name="bad\nname")):
            invalid = demo_snapshot(now=NOW)
            mutate(invalid["participants"])
            with self.assertRaises(TournamentValidationError):
                validate_snapshot(invalid, now=NOW)

    def test_trade_cards_require_finite_points_and_directional_order(self):
        for field, value in (("entry", float("nan")), ("entry", True), ("stop", 10**1000),
                             ("target", 5000), ("direction", "buy"), ("instrument", "bad\x00symbol")):
            invalid = deepcopy(self.snapshot)
            invalid["participants"][0]["trade"][field] = value
            with self.subTest(field=field), self.assertRaises(TournamentValidationError):
                validate_snapshot(invalid, now=NOW)

    def test_six_scene_export_has_portable_manifest_hashes_and_copies_real_art(self):
        portrait = ROOT / "examples" / "funded-desk" / "presenter.png"
        output = self.root / "bundle"
        result = render_tournament(self.snapshot, output, portrait_path=portrait, now=NOW)
        self.assertEqual(6, len(result["scenes"]))
        self.assertEqual({scene["id"] for scene in result["scenes"]}, set(SCENES))
        self.assertEqual(portrait.read_bytes(), (output / "presenter.png").read_bytes())
        for filename, digest in result["hashes"].items():
            self.assertEqual(digest, hashlib.sha256((output / filename).read_bytes()).hexdigest())
        for scene in result["scenes"]:
            page = (output / scene["file"]).read_text(encoding="utf-8")
            self.assertNotIn("__TOURNAMENT_DATA__", page)
            self.assertIn("connect-src 'none'", page)
            self.assertNotIn(str(self.root), page)
            self.assertIn('src="broadcast.js"', page)
        self.assertEqual(self.snapshot, read_snapshot(output / "snapshot.json"))
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_markup_payload_cannot_close_json_script_and_remains_data(self):
        self.snapshot["label"] = "</script><script>alert(1)</script>"
        self.snapshot["participants"][0]["name"] = '<img src=x onerror=alert(1)>'
        output = self.root / "safe"
        render_tournament(self.snapshot, output, now=NOW)
        page = (output / "table.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img src=x", page)
        self.assertIn("\\u003c/script\\u003e", page)
        payload = page.split('id="tournament-data">', 1)[1].split("</script>", 1)[0]
        self.assertEqual(self.snapshot, json.loads(payload)["snapshot"])
        javascript = (output / "broadcast.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", javascript)
        self.assertNotIn("fetch(", javascript)
        self.assertIn("textContent", javascript)

    def test_invalid_late_participant_or_missing_asset_leaves_no_output(self):
        for invalid in (deepcopy(self.snapshot), deepcopy(self.snapshot)):
            invalid["participants"][1]["fees_minor"] = -1
            output = self.root / "invalid"
            with self.assertRaises(TournamentValidationError):
                render_tournament(invalid, output, now=NOW)
            self.assertFalse(output.exists())
        with self.assertRaises(TournamentValidationError):
            render_tournament(self.snapshot, self.root / "missing", portrait_path=self.root / "missing.png", now=NOW)
        self.assertFalse((self.root / "missing").exists())

    def test_existing_destination_traversal_and_symlinks_refuse(self):
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "mine.txt").write_text("preserve")
        for path in (existing, self.root / "x" / ".." / "unsafe", self.root / "missing" / "child"):
            with self.assertRaises(TournamentValidationError):
                render_tournament(self.snapshot, path, now=NOW)
        self.assertEqual("preserve", (existing / "mine.txt").read_text())
        link = self.root / "link"
        try:
            link.symlink_to(existing, target_is_directory=True)
        except OSError:
            self.skipTest("Host cannot create test symlinks")
        with self.assertRaises(TournamentValidationError):
            render_tournament(self.snapshot, link / "bundle", now=NOW)

    def test_bounded_json_rejects_duplicate_keys_nonfinite_and_excess_size(self):
        for content in ('{"mode":"demo","mode":"paper"}', '{"value":NaN}', "x" * (64*1024+1)):
            path = self.root / "invalid.json"
            path.write_text(content, encoding="utf-8")
            with self.assertRaises(TournamentValidationError):
                read_snapshot(path)

    def test_browser_rankings_match_python_including_ties_stale_and_missing(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is optional and not installed; browser parity was not exercised")
        snapshot = deepcopy(self.snapshot)
        snapshot["participants"][1].update(realized_minor=1248000, unrealized_minor=21750, fees_minor=1250)
        cases = [(snapshot, NOW), (snapshot, NOW + timedelta(seconds=121))]
        missing = deepcopy(snapshot)
        missing["participants"][0]["fees_minor"] = None
        cases.append((missing, NOW))
        script = "const fs=require('fs'); const api=require(process.argv[1]); const d=JSON.parse(fs.readFileSync(0,'utf8')); console.log(JSON.stringify(api.rankRows(d.snapshot,d.now,120)));"
        for value, clock in cases:
            command = [node, "-e", script, str(ROOT / "examples" / "tournament" / "broadcast.js")]
            completed = subprocess.run(command, input=json.dumps({"snapshot": value, "now": clock.timestamp()*1000}), text=True, capture_output=True, timeout=10, check=True)
            actual = json.loads(completed.stdout)
            expected = rank_snapshot(value, now=clock)
            self.assertEqual(expected["participants"], actual["rows"])
            self.assertEqual(expected["standings"], actual["standings"])

    def test_browser_timestamp_parser_rejects_normalized_calendar_and_24_hour_values(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is optional and not installed")
        script = "const api=require(process.argv[1]); const bad=JSON.parse(process.argv[2]); for(const value of bad){let failed=false;try{api.parseUTC(value)}catch{failed=true}if(!failed)throw Error('accepted invalid timestamp')} console.log(api.parseUTC('2026-01-01T12:00:00.123456Z'));"
        bad = ["2025-12-31T24:00:00Z", "2026-02-30T12:00:00Z", "2026-01-01T11:60:00Z", "2026-01-01T11:59:60Z", "2026-01-01T12:00:00+00:00"]
        result = subprocess.run([node, "-e", script, str(ROOT / "examples" / "tournament" / "broadcast.js"), json.dumps(bad)], text=True, capture_output=True, timeout=10, check=True)
        self.assertTrue(result.stdout.strip().isdigit())

    def test_cli_demo_exports_without_network_or_obs_startup(self):
        output = self.root / "cli"
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "render_tournament.py"), "--demo", "--output", str(output)], capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(6, len(json.loads(result.stdout)["scenes"]))
        self.assertEqual("demo", read_snapshot(output / "snapshot.json")["mode"])


if __name__ == "__main__":
    unittest.main()
