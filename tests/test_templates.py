"""Template copying is offline. Fixtures never open OBS or an image service."""
from copy import deepcopy
import io
import itertools
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

    def test_catalog_is_portable_data_and_lists_every_bundled_template(self):
        with patch("obs_director.transport.ObsClient", side_effect=AssertionError("No OBS")):
            catalog = templates.list_templates()
            self.assertEqual(["funded-desk", "pack-break-table", "pack-break-closeup", "pack-break-vertical"],
                             [entry["name"] for entry in catalog])
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
        for theme in ("sepia", "../light", "DARK", "", False, {}):
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

    def test_cli_theme_routes_dark_and_default_defers_to_the_template(self):
        args = ["add", "funded-desk", "--output", str(self.output)]
        for key, value in BINDINGS.items():
            args.extend(["--bind", key + "=" + value])
        with patch.object(templates, "add_template", return_value={"copied": True}) as copy, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, templates.main(args))
            self.assertIsNone(copy.call_args.kwargs["theme"])  # the template picks its first theme (light for funded-desk)
            self.assertEqual(0, templates.main([*args, "--theme", "dark"]))
            self.assertEqual("dark", copy.call_args.kwargs["theme"])
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
            templates.main([*args, "--theme", "sepia"])
        self.assertEqual(2, error.exception.code)
        self.assertFalse(self.output.exists())


PACK_BINDINGS = {
    "pack-break-table": {"TABLE_CAM": "Synthetic table", "FACE_CAM": "Synthetic face", "TITLE": "Synthetic title",
                         "NOW_OPENING": "Synthetic opening"},
    "pack-break-closeup": {"CLOSEUP_CAM": "Synthetic closeup", "TABLE_CAM": "Synthetic table", "HIT_CARD": "Synthetic hit"},
    "pack-break-vertical": {"TABLE_CAM": "Synthetic table", "CLOSEUP_CAM": "Synthetic closeup", "FACE_CAM": "Synthetic face",
                            "TITLE": "Synthetic title", "NOW_OPENING": "Synthetic opening", "HIT_CARD": "Synthetic hit"},
}
PACK_BREAK_ITEMS = list(PACK_BINDINGS.items())
CANVAS = {"pack-break-table": (1920, 1080), "pack-break-closeup": (1920, 1080), "pack-break-vertical": (1080, 1920)}


def _area(layer):
    return layer["rect"]["width"] * layer["rect"]["height"]


def _overlap(first, second):
    a, b = first["rect"], second["rect"]
    return (a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"]
            and a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"])


class PackBreakFamilyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def sources(self, name):
        recipe = templates.get_template(name)["recipe"]
        return {layer["id"]: layer for layer in recipe["layers"] if layer["type"] == "existing"}

    def test_each_layout_declares_its_bindings_canvas_and_a_dark_only_theme(self):
        for name, bindings in PACK_BREAK_ITEMS:
            with self.subTest(name=name):
                entry = templates.get_template(name)
                self.assertEqual(["dark"], entry["themes"])
                self.assertEqual("dark", entry["theme"])
                self.assertEqual(set(bindings), {item["key"] for item in entry["bindings"]})
                width, height = CANVAS[name]
                self.assertEqual({"width": width, "height": height}, entry["recipe"]["canvas"])
                self.assertEqual([], entry["references"])
                with self.assertRaises(templates.TemplateError):
                    templates.get_template(name, theme="light")

    def test_each_layout_copies_with_bindings_and_stays_inside_the_item_budget(self):
        for name, bindings in PACK_BREAK_ITEMS:
            with self.subTest(name=name):
                result = templates.add_template(name, self.base / name, bindings)
                recipe = json.loads(Path(result["recipe_path"]).read_text())
                self.assertEqual(recipe, validate_layout_recipe(recipe))
                self.assertEqual(set(bindings.values()), {item["source_name"] for item in recipe["layers"] if item["type"] == "existing"})
                self.assertLessEqual(sum(5 if "border" in layer else 1 for layer in recipe["layers"]), 32)
                self.assertEqual(["README.md", "template.json"], result["files"])
                self.assertEqual({}, recipe["assets"])

    def test_table_layout_makes_the_table_camera_the_hero(self):
        sources = self.sources("pack-break-table")
        hero = sources["table-cam"]
        for other in ("face-cam", "title", "now-opening"):
            self.assertGreater(_area(hero), 3 * _area(sources[other]))
        self.assertGreater(_area(hero) / (1920 * 1080), 0.4)
        for first, second in itertools.combinations(sources.values(), 2):
            self.assertFalse(_overlap(first, second), (first["id"], second["id"]))

    def test_closeup_layout_is_full_frame_with_a_small_table_inset_and_hit_card(self):
        recipe = templates.get_template("pack-break-closeup")["recipe"]
        sources = self.sources("pack-break-closeup")
        self.assertEqual({"x": 0, "y": 0, "width": 1920, "height": 1080}, sources["closeup-cam"]["rect"])
        self.assertEqual("closeup-cam", recipe["layers"][0]["id"])
        self.assertLess(_area(sources["table-cam"]), _area(sources["closeup-cam"]) / 10)
        order = [layer["id"] for layer in recipe["layers"]]
        for overlay in ("table-cam", "hit-card"):
            self.assertGreater(order.index(overlay), order.index("closeup-cam"))

    def test_vertical_layout_is_portrait_with_every_source_and_no_overlap(self):
        sources = self.sources("pack-break-vertical")
        self.assertEqual({"table-cam", "closeup-cam", "face-cam", "title", "now-opening", "hit-card"}, set(sources))
        for first, second in itertools.combinations(sources.values(), 2):
            self.assertFalse(_overlap(first, second), (first["id"], second["id"]))
        # Platform chrome covers the bottom of a portrait frame; keep content above it.
        self.assertTrue(all(layer["rect"]["y"] + layer["rect"]["height"] <= 1920 - 200 for layer in sources.values()))

    def test_scene_names_are_distinct_so_generated_inputs_never_collide(self):
        names = [templates.get_template(name)["recipe"]["scene_name"] for name, _ in PACK_BREAK_ITEMS]
        self.assertEqual(["Pack Break Table", "Pack Break Closeup", "Pack Break Vertical"], names)

    def test_cli_defaults_to_the_templates_own_theme(self):
        name, bindings = PACK_BREAK_ITEMS[0]
        args = ["add", name, "--output", str(self.base / "cli")]
        for key, value in bindings.items():
            args.extend(["--bind", key + "=" + value])
        with patch.object(templates, "add_template", return_value={"copied": True}) as copy, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, templates.main(args))
            self.assertIsNone(copy.call_args.kwargs["theme"])


class CatalogConstraintTests(unittest.TestCase):
    """The catalog allows what real layouts need: many bindings and any theme set."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "resources"
        shutil.copytree(DATA, self.root)
        self.path = self.root / "catalog.json"
        self.catalog = json.loads(self.path.read_text())

    def check(self, entry_edit):
        catalog = deepcopy(self.catalog)
        entry_edit(catalog["templates"][0])
        self.path.write_text(json.dumps(catalog))
        with patch.object(templates, "_root", return_value=self.root):
            return templates.list_templates()

    def extras(self, count):
        return [{"key": "EXTRA_" + "X" * i, "layer_id": "extra-" + "x" * i, "description": "Extra"}
                for i in range(1, count + 1)]

    def test_eight_bindings_are_declared_but_nine_are_refused(self):
        self.check(lambda entry: entry["bindings"].extend(self.extras(4)))
        with self.assertRaises(templates.TemplateError):
            self.check(lambda entry: entry["bindings"].extend(self.extras(5)))

    def test_themes_must_be_known_unique_and_nonempty_but_light_is_not_required(self):
        self.check(lambda entry: entry.update(themes=["dark"]))
        for themes in ([], ["sepia"], ["dark", "dark"], "dark", ["light", 1]):
            with self.subTest(themes=themes), self.assertRaises(templates.TemplateError):
                self.check(lambda entry: entry.update(themes=themes))


if __name__ == "__main__":
    unittest.main()
