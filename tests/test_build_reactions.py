"""Offline regression checks for reaction synthesis and publication boundaries."""
from array import array
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("reaction_builder", ROOT / "tools/build_reactions.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def alpha_frames():
    data = bytearray(16 * 12 * 90)
    for frame in range(1, 89):
        data[frame * 16 * 12 + 5 * 16 + 8] = 255
    return data


class ReactionTests(unittest.TestCase):
    def test_woofs_are_deterministic_bounded_and_separated_by_silence(self):
        hashes = []
        for name in builder.REACTIONS:
            samples = builder.synthesize_woofs(name, rate=8000)
            self.assertEqual(samples, builder.synthesize_woofs(name, rate=8000))
            self.assertEqual(len(samples), 48000)
            self.assertEqual(list(samples[:2]) + list(samples[-2:]), [0.0] * 4)
            facts = builder.audio.metrics(samples, rate=8000)
            self.assertEqual(facts["clipped_samples"], 0)
            self.assertAlmostEqual(facts["sample_peak_dbfs"], -9.0, places=3)
            self.assertTrue(all(value == 0 for value in samples[:round(0.45 * 8000) * 2]))
            hashes.append(hashlib.sha256(builder.audio.pcm16(samples)).hexdigest())
        self.assertEqual(len(set(hashes)), 3)

    def test_unknown_reaction_refused(self):
        with self.assertRaises(ValueError):
            builder.synthesize_woofs("untrusted")

    def test_alpha_validates_all_frames_and_margin(self):
        result = builder.verify_alpha(alpha_frames(), 16, 12, 90, 2)
        self.assertEqual(result["endpoint_alpha_max"], [0, 0])
        self.assertEqual(len(result["frames"]), 90)

    def test_alpha_rejects_single_border_pixel_any_frame(self):
        data = alpha_frames()
        data[81 * 192 + 11 * 16 + 6] = 1
        with self.assertRaisesRegex(ValueError, "margin"):
            builder.verify_alpha(data, 16, 12, 90, 2)

    def test_alpha_rejects_opaque_endpoint_and_disappearing_body(self):
        for index in (0, 89):
            data = alpha_frames()
            data[index * 192 + 5 * 16 + 8] = 1
            with self.assertRaisesRegex(ValueError, "endpoints"):
                builder.verify_alpha(data, 16, 12, 90, 2)
        data = alpha_frames()
        data[45 * 192 + 5 * 16 + 8] = 0
        with self.assertRaisesRegex(ValueError, "disappeared"):
            builder.verify_alpha(data, 16, 12, 90, 2)

    def test_alpha_rejects_truncated_or_wrong_frame_count(self):
        for data, count in ((alpha_frames()[:-1], 90), (alpha_frames(), 89)):
            with self.assertRaises(ValueError):
                builder.verify_alpha(data, 16, 12, count, 2)

    def test_existing_directory_and_missing_parent_refuse_before_process(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(builder.audio, "_run") as run:
            root = Path(temporary)
            marker = root / "keep.txt"
            marker.write_text("preserve")
            for target in (root, root / "missing" / "out"):
                with self.assertRaises(ValueError):
                    builder.build(target, root / "source.png", root / "font.ttf")
            self.assertEqual(marker.read_text(), "preserve")
            run.assert_not_called()

    def test_bad_font_filter_path_rejected(self):
        with self.assertRaises(ValueError):
            builder.video_filter("welcome", Path("bad;filter.ttf"))

    def _mock_build(self, root, change_source=False):
        source, font = root / "source.png", root / "font.ttf"
        source.write_bytes(b"original")
        font.write_bytes(b"font")
        commands = []

        def render(command, **kwargs):
            commands.append(command)
            Path(command[-1]).write_bytes(b"synthetic encoded bytes")
            if change_source:
                source.write_bytes(b"changed")

        with patch.object(builder.shutil, "which", side_effect=lambda command: command), \
                patch.object(builder, "_probe", return_value={"streams": [{"codec_type": "video", "codec_name": "png", "pix_fmt": "rgba"}]}), \
                patch.object(builder, "synthesize_woofs", return_value=array("d", [0, 0])), \
                patch.object(builder.audio, "_run", side_effect=render), \
                patch.object(builder, "inspect_clip", return_value={"verified": "synthetic"}):
            result = builder.build(root / "clips", source, font)
        return result, commands

    def test_render_preserves_alpha_tag_and_uses_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, commands = self._mock_build(Path(temporary))
            self.assertEqual(len(commands), 3)
            for command in commands:
                self.assertIn("-n", command)
                self.assertNotIn("-y", command)
                self.assertGreater(command.index("-metadata:s:v:0"), command.index("-map_metadata"))
                self.assertEqual(command[command.index("-metadata:s:v:0") + 1], "alpha_mode=1")
            self.assertEqual(result["source_art"], "operator-supplied RGBA PNG")
            self.assertEqual(len(result["clips"]), 3)
            self.assertNotIn(temporary, str(result))

    def test_changed_source_never_writes_success_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "changed"):
                self._mock_build(root, change_source=True)
            self.assertFalse((root / "clips/reactions-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
