"""Template copying is offline. Fixtures never open OBS or an image service."""
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from obs_director import templates
from obs_director.layouts import LayoutError, validate_layout_recipe


DATA = Path(templates.__file__).with_name("template_data")
BINDINGS = {"CHART": "Synthetic chart", "HOST": "Synthetic host",
            "HOST_B": "Synthetic second host", "STATUS": "Synthetic status"}


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.output = self.base / "my-layout"

    def altered_resources(self):
        root = self.base / "resources"
        shutil.copytree(DATA, root)
        return root

    def test_catalog_is_portable_data_and_has_only_the_selected_template(self):
        with patch("obs_director.transport.ObsClient", side_effect=AssertionError("No OBS")):
            catalog = templates.list_templates()
            self.assertEqual(["funded-desk"], [entry["name"] for entry in catalog])
            self.assertEqual(["light", "dark"], catalog[0]["themes"])
            self.assertEqual(set(BINDINGS), {item["key"] for item in catalog[0]["bindings"]})
            self.assertEqual(["https://tradefunded.com/"], catalog[0]["references"])
            catalog[0]["title"] = "Caller edit"
            self.assertEqual("Funded Desk", templates.get_template("funded-desk")["title"])
            self.assertNotIn("recipe", catalog[0])
        self.assertEqual([], list(self.base.iterdir()))

    def test_get_exposes_portable_unbound_recipe_without_machine_paths(self):
        result = templates.get_template("funded-desk")
        recipe = result["recipe"]
        self.assertEqual("obs.layout.v1", recipe["schema"])
        self.assertEqual({"width": 1920, "height": 1080}, recipe["canvas"])
        self.assertTrue(all(not Path(value).is_absolute() and value.startswith("assets/") for value in recipe["assets"].values()))
        self.assertTrue(all(layer["source_name"] == "" for layer in recipe["layers"] if layer["type"] == "existing"))
        with self.assertRaises(LayoutError):
            validate_layout_recipe(recipe)
        self.assertEqual([], list(self.base.iterdir()))

    def test_default_calls_stay_light_and_dark_changes_only_scene_and_background(self):
        default = templates.get_template("funded-desk")
        light = templates.get_template("funded-desk", theme="light")
        dark = templates.get_template("funded-desk", theme="dark")
        self.assertEqual(default, light)
        self.assertEqual("light", default["theme"])
        self.assertEqual("dark", dark["theme"])
        self.assertEqual("Funded Desk Dark", dark["recipe"]["scene_name"])
        self.assertEqual("assets/background-dark.png", dark["recipe"]["assets"]["background"])
        comparison = deepcopy(dark["recipe"])
        comparison["scene_name"] = light["recipe"]["scene_name"]
        comparison["assets"]["background"] = light["recipe"]["assets"]["background"]
        self.assertEqual(light["recipe"], comparison)

    def test_invalid_themes_refuse_without_writes(self):
        for theme in ("sepia", "../light", "DARK", "", None, False, {}):
            with self.subTest(theme=theme), self.assertRaises(templates.TemplateError):
                templates.get_template("funded-desk", theme=theme)
            with self.subTest(theme=theme), self.assertRaises(templates.TemplateError):
                templates.add_template("funded-desk", self.output, BINDINGS, theme=theme)
        self.assertEqual([], list(self.base.iterdir()))

    def test_dark_copy_uses_real_dark_art_and_distinct_scene(self):
        result = templates.add_template("funded-desk", self.output, BINDINGS, theme="dark")
        recipe = json.loads(Path(result["recipe_path"]).read_text())
        self.assertEqual("dark", result["theme"])
        self.assertEqual("Funded Desk Dark", recipe["scene_name"])
        self.assertEqual(recipe, validate_layout_recipe(recipe))
        self.assertEqual(set(BINDINGS.values()), {item["source_name"] for item in recipe["layers"] if item["type"] == "existing"})
        self.assertFalse((self.output / "assets/background.png").exists())
        for filename in ("background-dark.png", "frame.png"):
            self.assertEqual((DATA / "assets" / filename).read_bytes(), (self.output / "assets" / filename).read_bytes())
        self.assertEqual(str(self.output / "assets/background-dark.png"), recipe["assets"]["background"])

    def test_copy_binds_sources_and_preserves_artwork_bytes(self):
        original = (DATA / "funded-desk/template.json").read_bytes()
        with patch("obs_director.transport.ObsClient", side_effect=AssertionError("No OBS")):
            result = templates.add_template("funded-desk", self.output, BINDINGS)
        recipe = json.loads(Path(result["recipe_path"]).read_text())
        self.assertEqual(recipe, validate_layout_recipe(recipe))
        self.assertEqual({"width": 1920, "height": 1080}, recipe["canvas"])
        self.assertEqual(set(BINDINGS.values()), {item["source_name"] for item in recipe["layers"] if item["type"] == "existing"})
        self.assertLessEqual(sum(5 if "border" in layer else 1 for layer in recipe["layers"]), 32)
        for filename in ("background.png", "frame.png"):
            self.assertEqual((DATA / "assets" / filename).read_bytes(), (self.output / "assets" / filename).read_bytes())
        self.assertTrue(all(Path(path).is_absolute() and Path(path).is_relative_to(self.output) for path in recipe["assets"].values()))
        self.assertEqual(original, (DATA / "funded-desk/template.json").read_bytes())
        self.assertIn("This copy belongs to you", (self.output / "README.md").read_text())
        self.assertIn("original", (self.output / "prompts.md").read_text().lower())

    def test_unbound_bundled_recipe_cannot_target_placeholder_sources(self):
        recipe = json.loads((DATA / "funded-desk/template.json").read_text())
        with self.assertRaises(LayoutError):
            validate_layout_recipe(recipe)

    def test_existing_output_is_not_overwritten_even_when_empty(self):
        self.output.mkdir()
        with self.assertRaisesRegex(templates.TemplateError, "already exists"):
            templates.add_template("funded-desk", self.output, BINDINGS)
        self.assertEqual([], list(self.output.iterdir()))
        (self.output / "keep.txt").write_text("keep exactly")
        with self.assertRaises(templates.TemplateError):
            templates.add_template("funded-desk", self.output, BINDINGS)
        self.assertEqual("keep exactly", (self.output / "keep.txt").read_text())

    def test_missing_extra_duplicate_or_empty_bindings_leave_no_output(self):
        cases = [{}, {**BINDINGS, "EXTRA": "Unexpected"}, {**BINDINGS, "HOST": ""},
                 {**BINDINGS, "HOST": "bad\nname"}, {**BINDINGS, "HOST_B": BINDINGS["HOST"]}]
        for bindings in cases:
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                templates.add_template("funded-desk", self.output, bindings)
            self.assertFalse(self.output.exists())

    def test_names_and_destination_parent_traversal_are_rejected(self):
        for name in ("../funded-desk", "funded-desk/../../", "missing", "/absolute"):
            with self.subTest(name=name), self.assertRaises(templates.TemplateError):
                templates.add_template(name, self.output, BINDINGS)
        with self.assertRaises(templates.TemplateError):
            templates.add_template("funded-desk", self.base / ".." / "escape", BINDINGS)
        self.assertEqual([], list(self.base.iterdir()))

    def test_missing_output_parent_is_not_created(self):
        with self.assertRaises(templates.TemplateError):
            templates.add_template("funded-desk", self.base / "missing" / "layout", BINDINGS)
        self.assertEqual([], list(self.base.iterdir()))

    def test_late_invalid_geometry_is_rejected_before_any_write(self):
        root = self.altered_resources()
        path = root / "funded-desk/template.json"
        recipe = json.loads(path.read_text())
        recipe["layers"][-1]["rect"]["x"] = 1921
        path.write_text(json.dumps(recipe))
        with patch.object(templates, "_root", return_value=root), self.assertRaises(LayoutError):
            templates.add_template("funded-desk", self.output, BINDINGS)
        self.assertFalse(self.output.exists())

    def test_asset_traversal_and_corrupt_artwork_are_rejected_before_writes(self):
        root = self.altered_resources()
        path = root / "funded-desk/template.json"
        original = json.loads(path.read_text())
        recipe = deepcopy(original)
        recipe["assets"]["frame"] = "assets/../../outside.png"
        path.write_text(json.dumps(recipe))
        with patch.object(templates, "_root", return_value=root), self.assertRaises(templates.TemplateError):
            templates.add_template("funded-desk", self.output, BINDINGS)
        path.write_text(json.dumps(original))
        (root / "assets/frame.png").write_bytes(b"not an image")
        with patch.object(templates, "_root", return_value=root), self.assertRaises(templates.TemplateError):
            templates.add_template("funded-desk", self.output, BINDINGS)
        self.assertFalse(self.output.exists())

    def test_asset_byte_limit_is_enforced_before_writes(self):
        with patch.object(templates, "MAX_ASSET_BYTES", 1), self.assertRaises(templates.TemplateError):
            templates.add_template("funded-desk", self.output, BINDINGS)
        self.assertFalse(self.output.exists())

    def test_windows_reparse_parent_is_rejected_without_following_it(self):
        parent = self.base / "junction"
        parent.mkdir()
        original = Path.lstat
        def fake_lstat(path, *args, **kwargs):
            if path == parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=1024)
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", autospec=True, side_effect=fake_lstat), self.assertRaises(templates.TemplateError):
            templates.add_template("funded-desk", parent / "layout", BINDINGS)
        self.assertEqual([], list(parent.iterdir()))

    def test_real_symlink_destination_parent_is_rejected(self):
        target = self.base / "target"
        target.mkdir()
        linked = self.base / "linked"
        try:
            linked.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit this test process to create symbolic links")
        with self.assertRaises(templates.TemplateError):
            templates.add_template("funded-desk", linked / "layout", BINDINGS)
        self.assertEqual([], list(target.iterdir()))

    def test_package_resources_work_from_a_zip_without_repository_paths(self):
        archive = self.base / "installed-resources.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for path in DATA.rglob("*"):
                if path.is_file():
                    output.write(path, "obs_director/template_data/" + path.relative_to(DATA).as_posix())
        with zipfile.ZipFile(archive) as installed:
            package = zipfile.Path(installed, "obs_director/")
            with patch.object(templates.resources, "files", return_value=package):
                for theme in ("light", "dark"):
                    with self.subTest(theme=theme):
                        result = templates.add_template("funded-desk", self.base / theme, BINDINGS, theme=theme)
                        self.assertTrue(Path(result["recipe_path"]).is_file())
                        copied = json.loads(Path(result["recipe_path"]).read_text())
                        self.assertEqual(theme, result["theme"])
                        self.assertTrue(all(str(DATA) not in value for value in copied["assets"].values()))

    def test_write_failure_removes_only_this_calls_partial_copy(self):
        keep = self.base / "keep.txt"
        keep.write_text("owned elsewhere")
        original = Path.open
        def fail_late(path, mode="r", *args, **kwargs):
            if mode == "xb" and path.name == "README.md":
                raise OSError("Synthetic write failure")
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, "open", autospec=True, side_effect=fail_late), self.assertRaises(OSError):
            templates.add_template("funded-desk", self.output, BINDINGS)
        self.assertFalse(self.output.exists())
        self.assertEqual("owned elsewhere", keep.read_text())

    def test_zip_symlink_resource_is_rejected_before_output(self):
        archive = self.base / "linked-resources.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for path in DATA.rglob("*"):
                if path.is_file():
                    name = "obs_director/template_data/" + path.relative_to(DATA).as_posix()
                    if path.name == "frame.png":
                        info = zipfile.ZipInfo(name)
                        info.create_system = 3
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                        output.writestr(info, "outside.png")
                    else:
                        output.write(path, name)
        with zipfile.ZipFile(archive) as installed:
            package = zipfile.Path(installed, "obs_director/")
            with patch.object(templates.resources, "files", return_value=package), self.assertRaises(templates.TemplateError):
                templates.add_template("funded-desk", self.output, BINDINGS)
        self.assertFalse(self.output.exists())

    def test_cli_requires_explicit_bindings_and_rejects_repeated_key(self):
        args = ["add", "funded-desk", "--output", str(self.output), "--bind", "HOST=a", "--bind", "HOST=b"]
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(1, templates.main(args))
            self.assertEqual("refused", json.loads(output.getvalue())["state"])
        self.assertFalse(self.output.exists())

    def test_cli_theme_routes_dark_and_default_stays_light(self):
        args = ["add", "funded-desk", "--output", str(self.output)]
        for key, value in BINDINGS.items():
            args.extend(["--bind", key + "=" + value])
        with patch.object(templates, "add_template", return_value={"copied": True}) as copy, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, templates.main(args))
            self.assertEqual("light", copy.call_args.kwargs["theme"])
            self.assertEqual(0, templates.main([*args, "--theme", "dark"]))
            self.assertEqual("dark", copy.call_args.kwargs["theme"])
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
            templates.main([*args, "--theme", "sepia"])
        self.assertEqual(2, error.exception.code)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
