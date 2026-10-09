"""Neutral starter exports and data-semantic parity with the public showcase."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from obs_director.tournament import SCENES, demo_snapshot, rank_snapshot, render_tournament

NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


class StarterTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def cli(self, *arguments):
        return subprocess.run([sys.executable, "-B", str(ROOT / "tools" / "render_tournament.py"), *map(str, arguments)],
                              text=True, capture_output=True, timeout=15)

    def assert_manifest(self, output):
        manifest = json.loads((output / "scenes.json").read_text())
        self.assertEqual(set(SCENES), {scene["id"] for scene in manifest["scenes"]})
        for name, digest in manifest["hashes"].items():
            self.assertEqual(digest, hashlib.sha256((output / name).read_bytes()).hexdigest())
        return manifest

    def test_cli_default_is_six_neutral_scenes_without_personal_art(self):
        output = self.root / "starter"
        result = self.cli("--demo", "--output", output)
        self.assertEqual(0, result.returncode, result.stderr)
        manifest = self.assert_manifest(output)
        self.assertNotIn("presenter.png", manifest["hashes"])
        self.assertFalse(list(output.glob("*.png")))
        snapshot = json.loads((output / "snapshot.json").read_text())
        self.assertEqual(["HOST", "GUEST"], [row["name"] for row in snapshot["participants"]])
        self.assertEqual(["host", "guest"], [row["id"] for row in snapshot["participants"]])
        self.assertEqual("demo", snapshot["mode"])
        for name in manifest["hashes"]:
            text = (output / name).read_text(encoding="utf-8")
            for forbidden in ("piphound", "housebot", "tradefunded", "funded desk", "presenter.png"):
                self.assertNotIn(forbidden, text.lower(), name)
        for scene in manifest["scenes"]:
            page = (output / scene["file"]).read_text(encoding="utf-8")
            self.assertIn('data-view="' + scene["id"] + '"', page)
            self.assertIn("connect-src 'none'", page)
            self.assertNotIn("__TOURNAMENT_DATA__", page)
            self.assertIn('src="broadcast.js"', page)

    def test_explicit_showcase_preserves_illustrated_demo(self):
        output = self.root / "showcase"
        result = self.cli("--demo", "--template", "piphound", "--output", output)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assert_manifest(output)
        self.assertEqual((ROOT / "examples/funded-desk/presenter.png").read_bytes(), (output / "presenter.png").read_bytes())
        snapshot = json.loads((output / "snapshot.json").read_text())
        self.assertEqual(["PIPHOUND", "HOUSEBOT"], [row["name"] for row in snapshot["participants"]])

    def test_supplied_snapshot_keeps_identity_timestamp_and_unknown_values(self):
        snapshot = demo_snapshot(now=NOW)
        snapshot["mode"] = "paper"
        snapshot["participants"][0].update(name="MY OWN HOST", realized_minor=None)
        source = self.root / "input.json"
        source.write_text(json.dumps(snapshot), encoding="utf-8")
        output = self.root / "paper"
        result = self.cli("--snapshot", source, "--template", "starter", "--output", output)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(snapshot, json.loads((output / "snapshot.json").read_text()))
        self.assertFalse((output / "presenter.png").exists())

    def test_starter_is_directly_compatible_with_template_dir_contract_and_safe_text(self):
        snapshot = demo_snapshot(now=NOW)
        snapshot["label"] = "</script><script>unsafe()</script>"
        output = self.root / "direct"
        render_tournament(snapshot, output, template_dir=ROOT / "examples/starter", now=NOW)
        self.assert_manifest(output)
        for filename in SCENES:
            page = (output / (filename + ".html")).read_text(encoding="utf-8")
            self.assertNotIn("<script>unsafe()", page)
            encoded = page.split('id="tournament-data">', 1)[1].split("</script>", 1)[0]
            self.assertEqual(snapshot, json.loads(encoded)["snapshot"])
        javascript = (output / "broadcast.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", javascript)
        self.assertNotIn("fetch(", javascript)

    def test_invalid_template_or_snapshot_creates_no_output(self):
        for template in ("unknown", "../../outside", "STARTER"):
            output = self.root / ("bad-" + str(len(list(self.root.iterdir()))))
            result = self.cli("--demo", "--template", template, "--output", output)
            self.assertEqual(2, result.returncode)
            self.assertFalse(output.exists())
        snapshot = demo_snapshot(now=NOW)
        snapshot["participants"][1]["fees_minor"] = -1
        source = self.root / "invalid.json"
        source.write_text(json.dumps(snapshot))
        output = self.root / "invalid-output"
        result = self.cli("--snapshot", source, "--output", output)
        self.assertEqual(2, result.returncode)
        self.assertFalse(output.exists())

    def test_existing_output_is_preserved(self):
        output = self.root / "existing"
        output.mkdir()
        original = output / "keep.txt"
        original.write_text("User-owned content", encoding="utf-8")
        result = self.cli("--demo", "--output", output)
        self.assertEqual(2, result.returncode)
        self.assertEqual([original], list(output.iterdir()))
        self.assertEqual("User-owned content", original.read_text())

    def test_data_helpers_are_exactly_shared_with_showcase_semantics(self):
        def helpers(path):
            source = path.read_text(encoding="utf-8")
            return source[source.index("function parseUTC("):source.index('if (typeof document !== "undefined")')]
        self.assertEqual(helpers(ROOT / "examples/tournament/broadcast.js"), helpers(ROOT / "examples/starter/broadcast.js"))

    def test_neutral_browser_ranking_matches_python_for_two_to_eight_and_time_boundaries(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is not installed; browser parity was not exercised")
        snapshot = demo_snapshot(now=NOW)
        rows = snapshot["participants"]
        rows[1].update(realized_minor=rows[0]["realized_minor"], unrealized_minor=rows[0]["unrealized_minor"], fees_minor=rows[0]["fees_minor"])
        cases = [(deepcopy(snapshot), NOW + timedelta(seconds=value)) for value in (0, 120, 121)]
        for index in range(2, 8):
            row = deepcopy(rows[0])
            row.update(id="seat" + str(index), name="SEAT " + str(index), realized_minor=-10000*index, unrealized_minor=0)
            rows.append(row)
        rows[-1]["observed_at"] = None
        rows[-2]["fees_minor"] = None
        cases.append((snapshot, NOW))
        script = "const fs=require('fs');const a=require(process.argv[1]);const d=JSON.parse(fs.readFileSync(0,'utf8'));console.log(JSON.stringify(a.rankRows(d.snapshot,d.now,120)))"
        for value, clock in cases:
            completed = subprocess.run([node, "-e", script, str(ROOT / "examples/starter/broadcast.js")],
                                       input=json.dumps({"snapshot": value, "now": clock.timestamp()*1000}), text=True,
                                       capture_output=True, timeout=10, check=True)
            actual = json.loads(completed.stdout)
            expected = rank_snapshot(value, now=clock)
            self.assertEqual(expected["participants"], actual["rows"])
            self.assertEqual(expected["standings"], actual["standings"])


if __name__ == "__main__":
    unittest.main()
