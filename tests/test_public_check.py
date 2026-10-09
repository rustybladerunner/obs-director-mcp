"""Negative controls use generated synthetic values; no live credentials or OBS."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "public_check.py"
SPEC = importlib.util.spec_from_file_location("public_check", MODULE_PATH)
CHECK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECK
SPEC.loader.exec_module(CHECK)


def synthetic_token():
    return "sk-" + "x" * 35


class PublicCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return path

    def categories(self, **kwargs):
        return {finding.category for finding in CHECK.scan_tree(self.root, **kwargs)}

    def test_code_references_and_placeholder_documents_pass(self):
        self.write("client.py", 'import os\npassword = os.getenv("OBS_PASSWORD")\n'
                   'token = settings.token\napi_key: str = ""\n'
                   'def connect(password: str):\n    return password\n'
                   'config = {"password": password, "token": token}\n')
        self.write("README.md", 'Password and token are configuration concepts.\n'
                   'OBS_PASSWORD=<your-password>\napi_key="YOUR_API_KEY"\n'
                   'server_password: ${OBS_PASSWORD}\n')
        self.write(".env.example", 'OBS_PASSWORD=CHANGE_ME\n')
        self.write("tests/fixtures/scene.json", '{"scene": "Preview", "enabled": true}\n')
        self.write("sample.ts", 'const password = process.env.OBS_PASSWORD;\n')
        self.assertEqual(CHECK.scan_tree(self.root), [])

    def test_api_token_rejected_without_echo(self):
        value = synthetic_token()
        self.write("accident.txt", "first line\n" + value + "\n")
        output = io.StringIO()
        with redirect_stdout(output):
            code = CHECK.main(["--root", str(self.root)])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "accident.txt:2: tree/api_token\n")
        self.assertNotIn(value, output.getvalue())

    def test_private_key_rejected(self):
        header = "-----BEGIN " + "PRIVATE KEY-----"
        self.write("notes.txt", header + "\n" + "x" * 32)
        self.assertIn("tree/private_key", self.categories())

    def test_obs_server_password_rejected(self):
        key = "server_" + "password"
        self.write("settings.json", json.dumps({key: "synthetic-unsafe-value"}))
        self.assertIn("tree/credential_assignment", self.categories())

    def test_literal_credential_assignment_rejected(self):
        key = "api_" + "key"
        self.write("client.py", key + ' = "synthetic-unsafe-value"\n')
        self.assertIn("tree/credential_assignment", self.categories())

    def test_env_file_rejected_even_without_secret(self):
        self.write(".env", "MODE=local\n")
        self.assertIn("tree/credential_file", self.categories())

    def test_example_filename_does_not_excuse_real_value(self):
        self.write(".env.example", "{}={}\n".format("OBS_PASSWORD", "unsafe-value"))
        self.assertIn("tree/credential_assignment", self.categories())

    def test_nested_literal_credential_assignment_rejected(self):
        key = "server_" + "password"
        self.write("client.py", "settings = " + repr({key: "synthetic-unsafe-value"}))
        self.assertIn("tree/credential_assignment", self.categories())

    def test_obs_configuration_path_rejected(self):
        self.write("obs-studio/basic/profiles/Test/service.json", "{}")
        self.assertIn("tree/obs_configuration", self.categories())

    def test_personal_windows_path_rejected(self):
        value = "C:" + chr(92) + "Users" + chr(92) + "PrivatePerson" + chr(92) + "Videos"
        self.write("notes.md", "stored at " + value)
        self.assertIn("tree/absolute_machine_path", self.categories())

    def test_absolute_drive_and_unix_profile_paths_rejected(self):
        self.write("config.txt", "T:" + "/recording-project/data\n")
        self.write("notes.md", "/home/" + "private-person/files\n")
        findings = CHECK.scan_tree(self.root)
        self.assertEqual({f.path for f in findings}, {"config.txt", "notes.md"})
        self.assertTrue(all(f.category == "tree/absolute_machine_path" for f in findings))

    def test_media_extension_and_magic_are_rejected(self):
        self.write("recording.mkv", b"synthetic media payload")
        self.write("disguised.data", bytes([137, 80, 78, 71, 13, 10, 26, 10]))
        findings = CHECK.scan_tree(self.root)
        self.assertEqual({f.path for f in findings if f.category == "tree/media_artifact"},
                         {"recording.mkv", "disguised.data"})

    def test_reviewed_art_requires_exact_path_and_bytes(self):
        root = MODULE_PATH.parents[1]
        for name in CHECK.REVIEWED_ASSETS:
            source = root / "src" / name if name.startswith("obs_director/") else root / name
            data = source.read_bytes()
            for prefix in (("", "src/") if name.startswith("obs_director/") else ("",)):
                self.assertEqual(CHECK.scan_blob(prefix + name, data), [])
                self.assertTrue(CHECK.scan_blob(prefix + name, data + b"changed"))
            self.assertTrue(CHECK.scan_blob("captures/" + name, data))
            if name.startswith("examples/"):
                self.assertTrue(CHECK.scan_blob("src/" + name, data))
            self.assertTrue(CHECK.scan_blob(name.upper(), data))
            findings = CHECK.scan_blob(name, data, forbid_text=[Path(name).stem])
            self.assertIn("tree/private_text", {finding.category for finding in findings})

    def test_binary_and_oversized_source_fail_closed(self):
        self.write("binary.data", b"\x00\x01\xff")
        self.write("huge.txt", b"a" * (CHECK.MAX_BYTES + 1))
        self.assertEqual(self.categories(), {"tree/binary_artifact", "tree/oversized_unchecked_file"})

    def test_untracked_runtime_and_git_metadata_excluded(self):
        for folder in ("captures", "runtime", "build", "dist", "__pycache__", ".venv", "pkg.egg-info"):
            self.write(folder + "/private.mkv", synthetic_token())
        # No .git folder: this is an export tree, not a purported Git checkout.
        self.assertEqual(CHECK.scan_tree(self.root), [])

    def test_forbidden_names_are_case_insensitive_and_never_echoed(self):
        phrase = "Private-Campaign-Alpha"
        self.write("notes.md", "about " + phrase.upper() + "\n")
        output = io.StringIO()
        with redirect_stdout(output):
            code = CHECK.main(["--root", str(self.root), "--forbid-text", phrase,
                               "--forbid-text", "second-private-phrase"])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "notes.md:1: tree/private_text\n")
        self.assertNotIn(phrase.casefold(), output.getvalue().casefold())

    def test_forbidden_name_in_filename_rejected(self):
        self.write("private-example.txt", "ordinary content")
        self.assertIn("tree/private_text", self.categories(forbid_text=["private-example"]))

    def test_missing_root_rejected(self):
        self.assertEqual(CHECK.scan_tree(self.root / "missing"),
                         [CHECK.Finding(".", 1, "tree/root_unreadable")])

    def test_invalid_git_metadata_fails_closed(self):
        self.write(".git/not-a-repository", "nothing")
        self.assertIn("index/unreadable", self.categories())


@unittest.skipUnless(shutil.which("git"), "Git is needed for index-boundary controls")
class GitIndexTests(PublicCheckTests):
    # Inherit the export checks so each is also exercised with a real empty index.
    def setUp(self):
        super().setUp()
        self.run_git("init", "--quiet")

    def run_git(self, *args):
        result = subprocess.run(["git", "-C", str(self.root), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, "Synthetic Git fixture setup failed")

    def test_invalid_git_metadata_fails_closed(self):
        # Covered by the export fixture class; do not damage a real test index.
        self.assertEqual(CHECK.scan_tree(self.root), [])

    def test_staged_secret_cleaned_only_in_worktree_still_fails(self):
        self.write("client.txt", synthetic_token())
        self.run_git("add", "--", "client.txt")
        self.write("client.txt", "ordinary source")
        findings = CHECK.scan_tree(self.root)
        self.assertEqual(findings, [CHECK.Finding("client.txt", 1, "index/api_token")])

    def test_clean_index_does_not_hide_dirty_worktree_secret(self):
        self.write("client.txt", "ordinary source")
        self.run_git("add", "--", "client.txt")
        self.write("client.txt", synthetic_token())
        self.assertEqual(CHECK.scan_tree(self.root),
                         [CHECK.Finding("client.txt", 1, "tree/api_token")])

    def test_ignored_but_staged_capture_secret_still_fails(self):
        self.write(".gitignore", "captures/\n")
        key = "server_" + "password"
        self.write("captures/settings.json", json.dumps({key: "unsafe-value"}))
        self.run_git("add", "--force", "--", "captures/settings.json")
        self.write("captures/settings.json", "{}")
        categories = self.categories()
        self.assertIn("index/tracked_runtime_artifact", categories)
        self.assertIn("index/credential_assignment", categories)
        self.assertIn("tree/tracked_runtime_artifact", categories)

    def test_ignored_but_staged_media_still_fails(self):
        self.write(".gitignore", "captures/\n")
        self.write("captures/test.mkv", "synthetic media")
        self.run_git("add", "--force", "--", "captures/test.mkv")
        self.assertIn("index/media_artifact", self.categories())

    def test_export_nested_in_parent_repo_does_not_scan_parent_index(self):
        self.write("private.txt", synthetic_token())
        self.run_git("add", "--", "private.txt")
        export = self.root / "export"
        export.mkdir()
        (export / "README.md").write_text("Public source", encoding="utf-8")
        self.assertEqual(CHECK.scan_tree(export), [])


if __name__ == "__main__":
    unittest.main()
