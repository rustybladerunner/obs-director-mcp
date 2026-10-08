"""Synthetic release corruption must fail before any archive is installed."""
from __future__ import annotations

import base64
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import verify_dist as verify


class ReleaseVerificationTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="release-test-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.stem = "obs_director_mcp-0.1.0a1"
        self.info = self.stem + ".dist-info/"
        self.egg = "src/obs_director_mcp.egg-info/"
        self.wheel_path = self.dist / (self.stem + "-py3-none-any.whl")
        self.sdist_path = self.dist / (self.stem + ".tar.gz")
        project = '''[project]
name = "obs-director-mcp"
version = "0.1.0a1"
requires-python = ">=3.11"
dependencies = ["mcp>=1.21.1,<2"]
[project.scripts]
obs-director-mcp = "obs_director.server:main"
'''
        self.sources = {"LICENSE": b"MIT test license\n", "README.md": b"Public example.\n",
                        "MANIFEST.in": b"include LICENSE README.md\n", "pyproject.toml": project.encode(),
                        "run_server.py": b"# entry point\n", "requirements-ci.lock": b"# test fixture\n",
                        "src/obs_director/__init__.py": b'__version__ = "0.1.0a1"\n',
                        "src/obs_director/server.py": b"def main(): pass\n",
                        ".github/workflows/ci.yml": b"name: Fixture\n"}
        for name, data in self.sources.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        metadata = ("Metadata-Version: 2.4\nName: obs-director-mcp\nVersion: 0.1.0a1\n"
                    "Requires-Python: >=3.11\nLicense-Expression: MIT\nLicense-File: LICENSE\n"
                    "Description-Content-Type: text/markdown\nRequires-Dist: mcp<2,>=1.21.1\n\nPublic example.\n").encode()
        entry = b"[console_scripts]\nobs-director-mcp = obs_director.server:main\n"
        self.wheel = {name[4:]: data for name, data in self.sources.items() if name.startswith("src/")}
        self.wheel.update({self.info + "METADATA": metadata, self.info + "licenses/LICENSE": self.sources["LICENSE"],
                           self.info + "WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                           self.info + "entry_points.txt": entry, self.info + "top_level.txt": b"obs_director\n"})
        self.sdist = dict(self.sources)
        self.sdist.update({"PKG-INFO": metadata, "setup.cfg": b"[egg_info]\ntag_build = \ntag_date = 0\n",
                           self.egg + "PKG-INFO": metadata, self.egg + "dependency_links.txt": b"\n",
                           self.egg + "entry_points.txt": entry, self.egg + "requires.txt": b"mcp<2,>=1.21.1\n",
                           self.egg + "top_level.txt": b"obs_director\n"})
        self.sdist[self.egg + "SOURCES.txt"] = "\n".join(sorted(set(self.sources) | {self.egg + n for n in verify.EGG_FILES})).encode()
        self.write_wheel()
        self.write_sdist()

    def write_wheel(self, *, record=True):
        if record:
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer)
            for name, data in sorted(self.wheel.items()):
                if name != self.info + "RECORD":
                    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                    writer.writerow((name, "sha256=" + digest, str(len(data))))
            writer.writerow((self.info + "RECORD", "", ""))
            self.wheel[self.info + "RECORD"] = buffer.getvalue().encode()
        with zipfile.ZipFile(self.wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.wheel.items():
                archive.writestr(name, data)

    def write_sdist(self):
        with tarfile.open(self.sdist_path, "w:gz") as archive:
            for name, data in self.sdist.items():
                member = tarfile.TarInfo(self.stem + "/" + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def rejected(self, reason):
        with self.assertRaisesRegex(verify.VerificationError, reason):
            verify.verify(self.root, self.dist)

    def test_valid_pair_reports_source_member_and_archive_hashes(self):
        result = verify.verify(self.root, self.dist)
        self.assertEqual(result["version"], "0.1.0a1")
        self.assertEqual(result["sources"]["LICENSE"], hashlib.sha256(self.sources["LICENSE"]).hexdigest())
        self.assertEqual(result["artifacts"][self.wheel_path.name]["sha256"], hashlib.sha256(self.wheel_path.read_bytes()).hexdigest())
        self.assertEqual(len(result["artifacts"]), 2)

    def test_resealed_changed_wheel_code_is_not_source_parity(self):
        self.wheel["obs_director/server.py"] += b"# changed after testing\n"
        self.write_wheel()
        self.rejected("differs from release source")

    def test_utf8_readme_metadata_is_compared_as_utf8(self):
        readme = "Public example: café and an arrow \u2192.\n".encode("utf-8")
        (self.root / "README.md").write_bytes(readme)
        self.sdist["README.md"] = readme
        for files, name in ((self.wheel, self.info + "METADATA"), (self.sdist, "PKG-INFO"),
                            (self.sdist, self.egg + "PKG-INFO")):
            files[name] = files[name].replace(b"Public example.\n", readme)
        self.write_wheel()
        self.write_sdist()
        self.assertEqual(verify.verify(self.root, self.dist)["version"], "0.1.0a1")

    def test_changed_sdist_source_is_rejected(self):
        self.sdist["run_server.py"] += b"# changed\n"
        self.write_sdist()
        self.rejected("differs from release source")

    def test_extra_wheel_payload_is_rejected_even_with_valid_record(self):
        self.wheel["unexpected.py"] = b"# extra code\n"
        self.write_wheel()
        self.rejected("unexpected or missing wheel content")

    def test_missing_source_file_is_rejected(self):
        del self.sdist["LICENSE"]
        self.write_sdist()
        self.rejected("unexpected or missing source archive content")

    def test_wrong_record_hash_is_rejected(self):
        self.wheel[self.info + "RECORD"] = self.wheel[self.info + "RECORD"].replace(b"sha256=", b"sha512=", 1)
        self.write_wheel(record=False)
        self.rejected("RECORD digest mismatch")

    def test_duplicate_record_row_is_rejected(self):
        self.wheel[self.info + "RECORD"] += self.wheel[self.info + "RECORD"].splitlines(keepends=True)[0]
        self.write_wheel(record=False)
        self.rejected("RECORD membership mismatch")

    def test_private_source_fails_even_when_archives_match_it(self):
        value = b"sk-" + b"a" * 32
        data = b"# " + value + b"\n"
        (self.root / "src/obs_director/server.py").write_bytes(data)
        self.wheel["obs_director/server.py"] = data
        self.sdist["src/obs_director/server.py"] = data
        self.write_wheel()
        self.write_sdist()
        self.rejected("public-content check failed")

    def test_path_traversal_is_rejected_without_extracting(self):
        self.wheel["../escape.py"] = b"# never extracted\n"
        self.write_wheel()
        self.rejected("unsafe archive path")
        self.assertFalse((self.root.parent / "escape.py").exists())

    def test_windows_path_and_backslash_are_rejected(self):
        for path in ("C:" + "/outside.py", "obs_director\\server.py", "/absolute.py"):
            with self.subTest(path=path), self.assertRaises(verify.VerificationError):
                verify.safe_name(path)

    def test_duplicate_case_alias_is_rejected(self):
        self.wheel["OBS_DIRECTOR/server.py"] = self.wheel["obs_director/server.py"]
        self.write_wheel()
        self.rejected("duplicate archive path")

    def test_duplicate_zip_member_is_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.wheel_path, "a") as archive:
                archive.writestr("obs_director/server.py", b"duplicate")
        self.rejected("duplicate archive path")

    def test_wheel_symlink_is_rejected(self):
        with zipfile.ZipFile(self.wheel_path, "a") as archive:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"outside")
        self.rejected("link or encrypted")

    def test_sdist_symlink_is_rejected(self):
        with tarfile.open(self.sdist_path, "w:gz") as archive:
            info = tarfile.TarInfo(self.stem + "/link")
            info.type = tarfile.SYMTYPE
            info.linkname = "../outside"
            archive.addfile(info)
        self.rejected("nonregular source archive member")

    def test_member_limit_prevents_expansion(self):
        with patch.object(verify, "MAX_BYTES", 2):
            with self.assertRaisesRegex(verify.VerificationError, "member limit"):
                verify.read_archive(self.wheel_path)

    def test_resealed_entrypoint_change_is_rejected(self):
        self.wheel[self.info + "entry_points.txt"] = b"[console_scripts]\nobs-director-mcp = unexpected:main\n"
        self.write_wheel()
        self.rejected("console entry point mismatch")

    def test_resealed_wrong_version_is_rejected(self):
        self.wheel[self.info + "METADATA"] = self.wheel[self.info + "METADATA"].replace(b"Version: 0.1.0a1", b"Version: 9.9.9")
        self.write_wheel()
        self.rejected("metadata mismatch")

    def test_unexpected_sdist_build_configuration_is_rejected(self):
        self.sdist["setup.cfg"] += b"\n[options]\npackages = unexpected\n"
        self.write_sdist()
        self.rejected("generated build configuration")

    def test_stale_distribution_is_not_silently_ignored(self):
        (self.dist / "old-0.0.1-py3-none-any.whl").write_bytes(b"old")
        self.rejected("exactly one current")

    def test_manifest_detects_changed_candidate_and_cli_does_not_echo_payload(self):
        manifest = self.dist / "release-manifest.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(verify.main(["--root", str(self.root), "--write-manifest", str(manifest)]), 0)
            self.assertEqual(verify.main(["--root", str(self.root), "--check-manifest", str(manifest)]), 0)
        altered = json.loads(manifest.read_text())
        altered["version"] = "0.0.0"
        manifest.write_text(json.dumps(altered))
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(verify.main(["--root", str(self.root), "--check-manifest", str(manifest)]), 1)
        self.assertIn("release manifest mismatch", output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())


if __name__ == "__main__":
    unittest.main()
