"""Builder file-ownership regressions; no FFmpeg or valid media required."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tested_build_stinger", ROOT / "tools/build_stinger.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class BuildStingerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.output = self.base / "synthetic.webm"
        self.sidecar = self.output.with_suffix(".json")
        self.font = self.base / "synthetic-font.ttf"
        self.font.write_bytes(b"Synthetic fixture; encoder is mocked")
        self.enterContext(patch.object(builder.shutil, "which", return_value="synthetic-ffmpeg"))

    def emblem(self, *, mode="RGBA", opaque=False):
        path = self.base / "test-only-emblem.png"
        image = Image.new(mode, (32, 32), (20, 30, 40, 255 if opaque else 0) if mode == "RGBA" else (20, 30, 40))
        if mode == "RGBA": image.putpixel((16, 16), (100, 80, 40, 255))
        image.save(path)
        return path

    def successful_encoder(self, *_args, **_kwargs):
        self.output.write_bytes(b"Synthetic encoded bytes; media validation is separate")
        return subprocess.CompletedProcess([], 0)

    def test_legacy_positional_call_retains_classic_contract(self):
        with patch.object(builder.subprocess, "run", side_effect=self.successful_encoder) as encoder:
            result = builder.build(self.output, "ffmpeg", self.font)
        self.assertEqual((result["frames"], result["duration_ms"], result["transition_point_ms"]), (66, 2200, 900))
        self.assertNotIn("style", result)
        self.assertEqual(result["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertIn("-n", encoder.call_args.args[0])

    def test_deluxe_manifest_is_timed_silent_and_bound_to_emblem_without_paths(self):
        emblem = self.emblem()
        with patch.object(builder.subprocess, "run", side_effect=self.successful_encoder) as encoder:
            result = builder.build(self.output, "ffmpeg", self.font, style="deluxe", emblem=emblem)
        self.assertEqual((result["frames"], result["fps"], result["duration_ms"]), (90, 30, 3000))
        self.assertEqual((result["opaque_start_ms"], result["opaque_end_ms"], result["transition_point_ms"]), (700, 2066, 1100))
        self.assertGreaterEqual(result["opaque_end_ms"] - result["opaque_start_ms"], 1000)
        self.assertIs(result["audio"], False)
        self.assertEqual(result["emblem_sha256"], hashlib.sha256(emblem.read_bytes()).hexdigest())
        self.assertNotIn(str(self.base), json.dumps(result))
        self.assertEqual(json.loads(self.sidecar.read_text()), result)
        arguments = encoder.call_args.args[0]
        self.assertIn("-n", arguments)
        self.assertIn("-an", arguments)
        self.assertEqual(arguments[arguments.index("-pix_fmt") + 1], "yuva420p")
        self.assertEqual(arguments[arguments.index("-frames:v") + 1], "90")

    def test_missing_or_opaque_emblem_refuses_before_encoder(self):
        for emblem in (None, self.emblem(opaque=True)):
            with self.subTest(emblem=emblem), patch.object(builder.subprocess, "run") as encoder:
                with self.assertRaises(ValueError):
                    builder.build(self.output, "ffmpeg", self.font, style="deluxe", emblem=emblem)
                encoder.assert_not_called()

    def test_rgb_emblem_without_alpha_refuses_before_encoder(self):
        with patch.object(builder.subprocess, "run") as encoder:
            with self.assertRaises(ValueError):
                builder.build(self.output, "ffmpeg", self.font, style="deluxe", emblem=self.emblem(mode="RGB"))
            encoder.assert_not_called()

    def test_unknown_style_and_classic_emblem_refuse_before_encoder(self):
        with patch.object(builder.subprocess, "run") as encoder:
            with self.assertRaises(ValueError): builder.build(self.output, "ffmpeg", self.font, style="unknown")
            with self.assertRaises(ValueError): builder.build(self.output, "ffmpeg", self.font, emblem=self.emblem())
            encoder.assert_not_called()

    def test_emblem_changed_during_encoding_cannot_get_success_manifest(self):
        emblem = self.emblem()
        def changed(*args, **kwargs):
            self.successful_encoder(*args, **kwargs)
            emblem.write_bytes(b"changed after input inspection")
        with patch.object(builder.subprocess, "run", side_effect=changed):
            with self.assertRaises(RuntimeError):
                builder.build(self.output, "ffmpeg", self.font, style="deluxe", emblem=emblem)
        self.assertFalse(self.sidecar.exists())

    def test_empty_successful_encoder_output_is_not_a_success(self):
        def empty(*_args, **_kwargs): self.output.write_bytes(b"")
        with patch.object(builder.subprocess, "run", side_effect=empty):
            with self.assertRaises(RuntimeError): builder.build(self.output, "ffmpeg", self.font)
        self.assertFalse(self.sidecar.exists())

    def test_existing_sidecar_refuses_before_encoder_and_preserves_bytes(self):
        original = b'{"existing":"operator-owned"}\n'
        self.sidecar.write_bytes(original)
        with patch.object(builder.subprocess, "run") as encoder:
            with self.assertRaises(ValueError):
                builder.build(self.output, "ffmpeg", self.font)
            encoder.assert_not_called()
        self.assertEqual(self.sidecar.read_bytes(), original)
        self.assertFalse(self.output.exists())

    def test_sidecar_created_during_encode_is_not_overwritten(self):
        original = b'{"concurrent":"operator-owned"}\n'

        def simulated_encoder(*_args, **_kwargs):
            self.output.write_bytes(b"Synthetic encoded bytes, not real video")
            self.sidecar.write_bytes(original)
            return subprocess.CompletedProcess([], 0)

        with patch.object(builder.subprocess, "run", side_effect=simulated_encoder) as encoder:
            with self.assertRaises(FileExistsError):
                builder.build(self.output, "ffmpeg", self.font)
            encoder.assert_called_once()
        self.assertEqual(self.sidecar.read_bytes(), original)

    def test_failed_encoder_does_not_create_a_success_manifest(self):
        with patch.object(builder.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["synthetic-ffmpeg"])):
            with self.assertRaises(subprocess.CalledProcessError):
                builder.build(self.output, "ffmpeg", self.font)
        self.assertFalse(self.sidecar.exists())

    def test_existing_video_refuses_before_encoder_and_preserves_bytes(self):
        original = b"Existing operator-owned bytes"
        self.output.write_bytes(original)
        with patch.object(builder.subprocess, "run") as encoder:
            with self.assertRaises(ValueError):
                builder.build(self.output, "ffmpeg", self.font)
            encoder.assert_not_called()
        self.assertEqual(self.output.read_bytes(), original)
        self.assertFalse(self.sidecar.exists())


if __name__ == "__main__":
    unittest.main()
