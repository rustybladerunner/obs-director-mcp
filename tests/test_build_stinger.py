"""Builder file-ownership regressions; no FFmpeg or valid media required."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

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
