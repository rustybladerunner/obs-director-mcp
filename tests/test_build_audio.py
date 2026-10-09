"""Original synthesis and file-ownership checks without FFmpeg, OBS or networking."""
from array import array
import importlib.util
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tested_build_audio", ROOT / "tools/build_audio.py")
audio = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio)


class BuildAudioTests(unittest.TestCase):
    def test_pcm_refuses_clipping_and_nonfinite_samples(self):
        for value in (1.0, -1.0, 1.01, float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audio.pcm16([0.0, value])
        self.assertEqual(audio.pcm16([0.0, 0.5, -0.5]), b"\x00\x00\x00\x40\x00\xc0")

    def test_music_has_eight_exact_bars_headroom_and_a_small_seam(self):
        rate = audio.RATE
        samples = audio.synthesize_music(rate)
        result = audio.metrics(samples, rate)
        self.assertEqual(result["frames"], 960000)
        self.assertEqual(result["duration_seconds"], 8 * 4 * 60 / 96)
        self.assertEqual(result["clipped_samples"], 0)
        self.assertAlmostEqual(result["sample_peak_dbfs"], -8, places=3)
        self.assertLess(result["loop_seam_step"], 0.02)
        self.assertTrue(all(abs(value) < 1e-12 for value in result["dc_mean"]))
        self.assertLess(result["rms_dbfs"], -12)
        self.assertGreater(result["rms_dbfs"], -35)

    def test_effect_durations_and_endpoints_are_exact_without_clipping(self):
        for synth, seconds in ((audio.synthesize_stinger, 3), (audio.synthesize_alert, 0.7)):
            with self.subTest(seconds=seconds):
                result = audio.metrics(synth(4000), 4000)
                self.assertEqual(result["duration_seconds"], seconds)
                self.assertEqual(result["first_samples"], [0.0, 0.0])
                self.assertEqual(result["last_samples"], [0.0, 0.0])
                self.assertEqual(result["clipped_samples"], 0)
                self.assertLessEqual(result["sample_peak_dbfs"], -6)

    def test_seeded_effect_is_deterministic_and_stereo(self):
        first = audio.synthesize_stinger(4000)
        self.assertEqual(first, audio.synthesize_stinger(4000))
        self.assertNotEqual(first[::2], first[1::2])

    def test_circular_tail_wrap_preserves_both_channels(self):
        data = array("d", [0]) * 8
        audio._mix(data, 3, [1.0, 0.5], circular=True)
        self.assertAlmostEqual(data[6], math.sqrt(0.5))
        self.assertAlmostEqual(data[0], math.sqrt(0.5) * 0.5)
        self.assertEqual(data[2:6], array("d", [0.0] * 4))

    def test_existing_directory_and_contents_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "owned"
            output.mkdir()
            existing = output / "intermission.ogg"
            existing.write_bytes(b"operator-owned bytes")
            with patch.object(audio, "synthesize_music") as synth, patch.object(audio.subprocess, "run") as encoder:
                with self.assertRaises(ValueError):
                    audio.build(output)
                synth.assert_not_called()
                encoder.assert_not_called()
            self.assertEqual(existing.read_bytes(), b"operator-owned bytes")

    def test_missing_parent_is_not_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "missing" / "audio"
            with patch.object(audio.subprocess, "run") as encoder:
                with self.assertRaises(ValueError):
                    audio.build(output)
                encoder.assert_not_called()
            self.assertFalse(output.parent.exists())

    def test_missing_encoder_refuses_before_synthesis_or_directory_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "audio"
            with patch.object(audio.shutil, "which", return_value=None), patch.object(audio, "synthesize_music") as synth:
                with self.assertRaises(ValueError):
                    audio.build(output)
                synth.assert_not_called()
            self.assertFalse(output.exists())

    def test_network_destination_refuses_before_any_subprocess(self):
        for path in ("//example.invalid/share/audio", r"\\example.invalid\share\audio"):
            with self.subTest(path=path), patch.object(audio.subprocess, "run") as encoder:
                with self.assertRaises(ValueError):
                    audio.build(path)
                encoder.assert_not_called()

    def test_failed_encoder_leaves_no_success_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "audio"
            samples = array("d", [0.1, -0.1] * 8)
            with patch.object(audio.shutil, "which", return_value="synthetic-encoder"), \
                 patch.object(audio, "synthesize_music", return_value=samples), \
                 patch.object(audio, "synthesize_stinger", return_value=samples), \
                 patch.object(audio, "synthesize_alert", return_value=samples), \
                 patch.object(audio.subprocess, "run", side_effect=RuntimeError("synthetic failure")):
                with self.assertRaises(RuntimeError):
                    audio.build(output)
            self.assertFalse((output / "audio-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
