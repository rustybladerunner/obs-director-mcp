"""Optional decorative motion must preserve snapshots and atomic rendering."""
from datetime import datetime, timezone
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
from obs_director.tournament import SCENES, TournamentValidationError, demo_snapshot, render_tournament

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class MotionRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.motion = self.root / "motion"
        self.motion.mkdir()
        (self.motion / "motion.js").write_text("// local fixture\n", encoding="utf-8")
        (self.motion / "motion.css").write_text(".motion-layer{pointer-events:none}\n", encoding="utf-8")
        self.snapshot = demo_snapshot(now=NOW)

    def render(self, destination, **kwargs):
        return render_tournament(self.snapshot, destination, now=NOW,
            template_dir=ROOT / "examples/starter", **kwargs)

    def test_opt_in_only_intermission_pages_and_manifest_covers_assets(self):
        plain, moving = self.root / "plain", self.root / "moving"
        self.render(plain)
        manifest = self.render(moving, motion_dir=self.motion)
        for name in ("table", "standings", "replay"):
            self.assertEqual((plain / (name + ".html")).read_bytes(), (moving / (name + ".html")).read_bytes())
        for name, preset in (("starting-soon", "fibonacci"), ("break", "table"), ("ending", "market")):
            content = (moving / (name + ".html")).read_text()
            self.assertIn('data-motion-loop="' + preset + '"', content)
            self.assertEqual(1, content.count('src="motion.js"'))
            self.assertEqual(1, content.count('href="motion.css"'))
            self.assertIn("connect-src 'none'", content)
            self.assertIn('data-motion-treatment="ambient"', content)
        for name in ("snapshot.json", "broadcast.js", "broadcast.css"):
            self.assertEqual((plain / name).read_bytes(), (moving / name).read_bytes())
        for name, digest in manifest["hashes"].items():
            self.assertEqual(digest, hashlib.sha256((moving / name).read_bytes()).hexdigest())
        self.assertEqual((self.motion / "motion.js").read_bytes(), (moving / "motion.js").read_bytes())
        self.assertNotIn("motion.js", self.render(self.root / "again")["hashes"])

    def test_missing_oversized_or_indirect_asset_fails_before_output(self):
        output = self.root / "output"
        (self.motion / "motion.css").unlink()
        with self.assertRaises(TournamentValidationError):
            self.render(output, motion_dir=self.motion)
        self.assertFalse(output.exists())
        (self.motion / "motion.css").write_bytes(b"x" * (128 * 1024 + 1))
        with self.assertRaises(TournamentValidationError):
            self.render(output, motion_dir=self.motion)
        self.assertFalse(output.exists())
        with self.assertRaises(TournamentValidationError):
            self.render(output, motion_dir=self.motion / ".." / "motion")
        self.assertFalse(output.exists())

    def test_bad_html_marker_fails_before_output(self):
        template = self.root / "template"
        shutil.copytree(ROOT / "examples/starter", template)
        page = template / "starting-soon.html"
        page.write_text(page.read_text().replace("</head>", "</head></head>"))
        with self.assertRaises(TournamentValidationError):
            render_tournament(self.snapshot, self.root / "output", now=NOW,
                template_dir=template, motion_dir=self.motion)
        self.assertFalse((self.root / "output").exists())

    def test_motion_preserves_crlf_bytes_and_rejects_invalid_utf8(self):
        data = b"// two lines\r\n// exact bytes\r\n"
        (self.motion / "motion.js").write_bytes(data)
        self.render(self.root / "output", motion_dir=self.motion)
        self.assertEqual(data, (self.root / "output/motion.js").read_bytes())
        (self.motion / "motion.js").write_bytes(b"\xff")
        with self.assertRaises(TournamentValidationError):
            self.render(self.root / "bad", motion_dir=self.motion)
        self.assertFalse((self.root / "bad").exists())

    def test_asset_growth_between_stat_and_read_is_bounded(self):
        original = Path.open
        def grow(path, mode="r", *args, **kwargs):
            if path == self.motion / "motion.css" and mode == "rb":
                with original(path, "wb") as stream:
                    stream.write(b"x" * (128 * 1024 + 1))
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, "open", grow), self.assertRaises(TournamentValidationError):
            self.render(self.root / "output", motion_dir=self.motion)
        self.assertFalse((self.root / "output").exists())

    def test_write_failure_removes_partial_motion_bundle(self):
        original = Path.open
        def fail(path, mode="r", *args, **kwargs):
            if path.name == "motion.css" and mode == "xb":
                raise OSError("simulated write failure")
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, "open", fail), self.assertRaises(OSError):
            self.render(self.root / "output", motion_dir=self.motion)
        self.assertFalse((self.root / "output").exists())

    def test_cli_motion_keeps_supplied_snapshot_and_copies_offline_assets(self):
        source = self.root / "input.json"
        source.write_text(json.dumps(self.snapshot), encoding="utf-8")
        result = subprocess.run([sys.executable, str(ROOT / "tools/render_tournament.py"),
            "--snapshot", str(source), "--motion", "--output", str(self.root / "output")],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(self.snapshot, json.loads((self.root / "output/snapshot.json").read_text()))
        self.assertEqual((ROOT / "examples/motion/motion.js").read_bytes(),
                         (self.root / "output/motion.js").read_bytes())

    def test_demo_roster_exercises_six_seats_tie_loss_and_missing(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools/render_tournament.py"),
            "--demo", "--demo-roster", "--output", str(self.root / "roster")],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        from obs_director.tournament import rank_snapshot
        snapshot = json.loads((self.root / "roster/snapshot.json").read_text())
        rows = rank_snapshot(snapshot)["standings"]
        self.assertEqual("demo", snapshot["mode"])
        self.assertEqual([1, 2, 3, 3, 5, None], [row["rank"] for row in rows])
        self.assertEqual([False, False, True, True, False, False], [row["tied"] for row in rows])
        self.assertLess(rows[4]["net_minor"], 0)
        self.assertEqual("missing", rows[5]["eligibility"])
        self.assertTrue(all(row["name"] in {"ORBIT", "RIVET", "LANTERN", "MARBLE", "FLINT", "NOVA"} for row in rows))

    def test_demo_roster_cannot_rewrite_supplied_snapshot(self):
        source = self.root / "input.json"
        source.write_text(json.dumps(self.snapshot), encoding="utf-8")
        result = subprocess.run([sys.executable, str(ROOT / "tools/render_tournament.py"),
            "--snapshot", str(source), "--demo-roster", "--output", str(self.root / "output")],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(2, result.returncode)
        self.assertFalse((self.root / "output").exists())


if __name__ == "__main__":
    unittest.main()
