"""Layout planning against stateful OBS protocol fixtures; no live OBS calls."""
from copy import deepcopy
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from obs_director.controls import ProductionService
from obs_director.layouts import LayoutError, LayoutService, READS, SETTERS, validate_layout_recipe
from obs_director.transport import ObsError, ObsSettings


def recipe(layers=None, **extra):
    return {"schema": "obs.layout.v1", "scene_name": "Stage", "canvas": {"width": 1280, "height": 720},
            "layers": layers or [{"id": "panel", "type": "color", "color": "#102030FF",
                "rect": {"x": 20, "y": 30, "width": 400, "height": 300}}], **extra}


def geometry(width=1280, height=720):
    return {"positionX": 0, "positionY": 0, "rotation": 0, "scaleX": 1, "scaleY": 1, "alignment": 5,
            "boundsType": "OBS_BOUNDS_NONE", "boundsAlignment": 0, "boundsWidth": 0, "boundsHeight": 0,
            "cropLeft": 0, "cropRight": 0, "cropTop": 0, "cropBottom": 0,
            "sourceWidth": width, "sourceHeight": height}


class FakeOBS:
    def __init__(self):
        self.settings = ObsSettings(timeout=5)
        self.canvas = {"baseWidth": 1280, "baseHeight": 720}
        self.scenes = {"Main": []}
        self.inputs = {"Camera": {"kind": "camera_input", "settings": {"device": "test-only"}, "size": (1280, 720), "uuid": "camera-1"}}
        self.groups = []
        self.kinds = ["image_source", "color_source_v3", "camera_input"]
        self.profile, self.collection = "Profile", "Collection"
        self.recording = self.streaming = False
        self.missing = set()
        self.calls = []
        self.noop = set()
        self.before = self.after = None
        self.next_id = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def require_capabilities(self, names):
        if set(names) & self.missing:
            raise ObsError("not-a-real-secret")

    def writes(self):
        return [(name, data) for name, data in self.calls if not name.startswith("Get")]

    def add_item(self, scene, name, enabled=True):
        item = {"id": self.next_id, "name": name, "enabled": enabled,
                "transform": geometry(*self.inputs[name]["size"])}
        self.next_id += 1
        self.scenes[scene].append(item)
        return item

    def request(self, name, data=None):
        self.require_capabilities([name])
        data = data or {}
        self.calls.append((name, deepcopy(data)))
        if self.before:
            self.before(name, data)
        result = self._request(name, data)
        if self.after:
            self.after(name, data, result)
        return deepcopy(result)

    def _request(self, name, data):
        if name in self.noop:
            return {}
        if name == "GetRecordStatus":
            return {"outputActive": self.recording}
        if name == "GetStreamStatus":
            return {"outputActive": self.streaming}
        if name == "GetVideoSettings":
            return self.canvas
        if name == "GetProfileList":
            return {"currentProfileName": self.profile}
        if name == "GetSceneCollectionList":
            return {"currentSceneCollectionName": self.collection}
        if name == "GetSceneList":
            return {"scenes": [{"sceneName": value, "sceneUuid": value + "-uuid"} for value in self.scenes]}
        if name == "GetGroupList":
            return {"groups": self.groups}
        if name == "GetInputList":
            return {"inputs": [{"inputName": key, "inputKind": value["kind"], "inputUuid": value["uuid"]}
                               for key, value in self.inputs.items()]}
        if name == "GetInputKindList":
            return {"inputKinds": self.kinds}
        if name == "GetInputSettings":
            value = self.inputs[data["inputName"]]
            return {"inputKind": value["kind"], "inputSettings": value["settings"]}
        if name == "CreateScene":
            if data["sceneName"] in self.scenes:
                raise ObsError("test-only collision")
            self.scenes[data["sceneName"]] = []
            return {}
        if name == "GetSceneItemList":
            return {"sceneItems": [{"sceneItemId": value["id"], "sceneItemIndex": index,
                    "sourceName": value["name"], "sceneItemEnabled": value["enabled"], "isGroup": None,
                    "sourceType": "OBS_SOURCE_TYPE_INPUT",
                    "inputKind": self.inputs[value["name"]]["kind"], "sourceUuid": self.inputs[value["name"]]["uuid"]}
                    for index, value in enumerate(self.scenes[data["sceneName"]])]}
        if name == "CreateInput":
            source = data["inputName"]
            if source in self.inputs:
                raise ObsError("test-only collision")
            settings = deepcopy(data["inputSettings"])
            size = (settings["width"], settings["height"]) if data["inputKind"] == "color_source_v3" else (8, 4)
            self.inputs[source] = {"kind": data["inputKind"], "settings": settings,
                                   "size": size, "uuid": source + "-uuid"}
            return {"sceneItemId": self.add_item(data["sceneName"], source, data["sceneItemEnabled"])["id"]}
        if name == "CreateSceneItem":
            return {"sceneItemId": self.add_item(data["sceneName"], data["sourceName"], data["sceneItemEnabled"])["id"]}
        item = next(value for value in self.scenes[data["sceneName"]] if value["id"] == data["sceneItemId"])
        if name == "GetSceneItemTransform":
            transform = deepcopy(item["transform"])
            transform.update(width=transform["sourceWidth"] * transform["scaleX"], height=transform["sourceHeight"] * transform["scaleY"])
            return {"sceneItemTransform": transform}
        if name == "SetSceneItemTransform":
            item["transform"].update(data["sceneItemTransform"])
        elif name == "SetSceneItemEnabled":
            item["enabled"] = data["sceneItemEnabled"]
        elif name == "SetSceneItemIndex":
            self.scenes[data["sceneName"]].remove(item)
            self.scenes[data["sceneName"]].insert(data["sceneItemIndex"], item)
        else:
            raise AssertionError("Unexpected fixture request")
        return {}


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.obs = FakeOBS()
        self.service = LayoutService(ProductionService(lambda: self.obs))
        self.clock = 0.0
        self.enterContext(patch("obs_director.layouts.time.monotonic", side_effect=lambda: self.clock))
        self.enterContext(patch("obs_director.layouts.time.sleep", side_effect=self.advance))

    def advance(self, seconds):
        self.clock += seconds

    def image_recipe(self, directory):
        path = Path(directory) / "private-local-image.png"
        Image.new("RGBA", (8, 4), "red").save(path)
        value = recipe([{"id": "art", "type": "image", "asset": "frame",
                         "rect": {"x": 10, "y": 20, "width": 600, "height": 400},
                         "border": {"width": 4, "color": "#FF000080"}}], assets={"frame": str(path.resolve())})
        return value, path

    def test_pure_validator_copies_defaults_without_files_or_obs(self):
        value = recipe()
        normalized = validate_layout_recipe(value)
        self.assertEqual(normalized["assets"], {})
        self.assertTrue(normalized["layers"][0]["visible"])
        normalized["layers"][0]["rect"]["x"] = 999
        self.assertEqual(value["layers"][0]["rect"]["x"], 20)
        self.assertEqual(self.obs.calls, [])

    def test_dry_run_plans_all_operations_without_writes(self):
        result = self.service.preview_layout(recipe())
        self.assertEqual(result["state"], "preview")
        self.assertEqual([op["action"] for op in result["operations"]], ["create_scene", "create_input", "transform"])
        self.assertEqual(self.obs.writes(), [])
        self.assertEqual(set(self.obs.scenes), {"Main"})

    def test_second_apply_has_zero_writes_and_no_duplicate_items(self):
        first = self.service.apply_layout(recipe(), False)
        self.assertEqual(first["state"], "completed", first)
        before = deepcopy(self.obs.scenes)
        self.obs.calls.clear()
        second = self.service.apply_layout(recipe(), False)
        self.assertEqual(second["state"], "unchanged", second)
        self.assertTrue(second["verified"])
        self.assertFalse(second["applied"])
        self.assertEqual(self.obs.writes(), [])
        self.assertEqual(self.obs.scenes, before)

    def test_real_input_shape_with_null_group_flag_creates_image_and_repeats_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            value, _ = self.image_recipe(temp)
            result = self.service.apply_layout(value, False)
            self.assertTrue(result["verified"], result)
            observed = self.obs.request("GetSceneItemList", {"sceneName": "Stage"})["sceneItems"][0]
            self.assertIsNone(observed["isGroup"])
            self.assertEqual(observed["sourceType"], "OBS_SOURCE_TYPE_INPUT")
            self.assertEqual(observed["inputKind"], "image_source")
            self.assertTrue(observed["sceneItemEnabled"])
            self.assertTrue(observed["sourceUuid"])
            self.obs.calls.clear()
            self.assertTrue(self.service.apply_layout(value, False)["verified"])
            self.assertEqual(self.obs.writes(), [])

    def test_null_group_flag_requires_explicit_input_identity(self):
        self.obs.scenes["Stage"] = []
        self.obs.add_item("Stage", "Camera")
        value = recipe([{"id": "camera", "type": "existing", "source_name": "Camera",
                         "rect": {"x": 0, "y": 0, "width": 400, "height": 300}}])
        cases = [("sourceType", None), ("sourceType", "OBS_SOURCE_TYPE_SCENE"),
                 ("sourceType", "OBS_SOURCE_TYPE_TRANSITION"), ("inputKind", None),
                 ("inputKind", ""), ("inputKind", "  "), ("inputKind", 0), ("isGroup", "missing")]
        for key, replacement in cases:
            with self.subTest(key=key, replacement=replacement):
                def malformed(name, data, result):
                    if name == "GetSceneItemList":
                        if key == "isGroup":
                            result["sceneItems"][0].pop(key)
                        else:
                            result["sceneItems"][0][key] = replacement
                self.obs.after = malformed
                self.obs.calls.clear()
                with self.assertRaises(LayoutError):
                    self.service.apply_layout(value, False)
                self.assertEqual(self.obs.writes(), [])

    def test_explicit_group_still_refuses_managed_input(self):
        self.obs.scenes["Stage"] = []
        self.obs.add_item("Stage", "Camera")
        def grouped(name, data, result):
            if name == "GetSceneItemList":
                result["sceneItems"][0].update(isGroup=True, sourceType="OBS_SOURCE_TYPE_SCENE", inputKind=None)
        self.obs.after = grouped
        value = recipe([{"id": "camera", "type": "existing", "source_name": "Camera",
                         "rect": {"x": 0, "y": 0, "width": 400, "height": 300}}])
        with self.assertRaises(LayoutError):
            self.service.apply_layout(value, False)
        self.assertEqual(self.obs.writes(), [])

    def test_image_border_geometry_and_rgba_without_returning_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            value, path = self.image_recipe(temp)
            result = self.service.apply_layout(value, False)
            self.assertTrue(result["verified"], result)
            items = self.obs.scenes["Stage"]
            self.assertEqual([item["name"] for item in items], ["Layout Stage/art"] + ["Layout Stage/art/border-" + edge for edge in ("top", "right", "bottom", "left")])
            self.assertEqual(len(items), 5)
            self.assertEqual(sum(name == "CreateInput" for name, _ in self.obs.writes()), 5)
            self.assertFalse(any(name == "CreateSceneItem" for name, _ in self.obs.writes()))
            bounds = [(item["transform"]["positionX"], item["transform"]["positionY"], item["transform"]["boundsWidth"], item["transform"]["boundsHeight"]) for item in items]
            self.assertEqual(bounds, [(10, 20, 600, 400), (10, 20, 596, 4), (606, 20, 4, 396), (14, 416, 596, 4), (10, 24, 4, 396)])
            self.assertEqual(self.obs.inputs["Layout Stage/art/border-top"]["settings"]["color"], 0x800000FF)
            self.assertNotIn(str(path), json.dumps(result))
            self.assertNotIn("private-local-image", json.dumps(result))
            self.assertNotIn("inputSettings", json.dumps(result))
            self.obs.calls.clear()
            self.assertEqual(self.service.apply_layout(value, False)["state"], "unchanged")
            self.assertEqual(self.obs.writes(), [])

    def test_existing_shared_input_settings_preserved_when_added_to_scene(self):
        self.obs.add_item("Main", "Camera")
        original = deepcopy(self.obs.inputs["Camera"])
        original_main = deepcopy(self.obs.scenes["Main"])
        value = recipe([{"id": "camera", "type": "existing", "source_name": "Camera",
                         "visible": False, "rect": {"x": 800, "y": 400, "width": 400, "height": 300}}])
        result = self.service.apply_layout(value, False)
        self.assertTrue(result["verified"], result)
        self.assertEqual(self.obs.inputs["Camera"], original)
        self.assertEqual(self.obs.scenes["Main"], original_main)
        self.assertEqual(sum(name == "CreateSceneItem" for name, _ in self.obs.writes()), 1)
        self.assertFalse(any(name in {"SetInputSettings", "SetCurrentProgramScene", "StartRecord"} for name, _ in self.obs.writes()))

    def test_ordering_preserves_unmanaged_items_and_handles_shifted_indices(self):
        self.obs.scenes["Stage"] = []
        for source in ("One", "UnmanagedA", "Two", "UnmanagedB"):
            self.obs.inputs[source] = deepcopy(self.obs.inputs["Camera"])
            self.obs.inputs[source]["uuid"] = source
            self.obs.add_item("Stage", source)
        original_unmanaged = [deepcopy(item) for item in self.obs.scenes["Stage"] if item["name"].startswith("Unmanaged")]
        value = recipe([{"id": name.lower(), "type": "existing", "source_name": name,
                         "rect": {"x": index * 400, "y": 0, "width": 400, "height": 300}} for index, name in enumerate(("One", "Two"))])
        result = self.service.apply_layout(value, False)
        self.assertTrue(result["verified"], result)
        self.assertEqual([item["name"] for item in self.obs.scenes["Stage"]], ["UnmanagedA", "UnmanagedB", "One", "Two"])
        self.assertEqual(self.obs.scenes["Stage"][:2], original_unmanaged)
        self.obs.calls.clear()
        self.assertTrue(self.service.apply_layout(value, False)["verified"])
        self.assertEqual(self.obs.writes(), [])

    def test_changed_visibility_is_reconciled_without_duplicate_or_settings_write(self):
        self.service.apply_layout(recipe(), False)
        value = recipe()
        value["layers"][0]["visible"] = False
        self.obs.calls.clear()
        result = self.service.apply_layout(value, False)
        self.assertTrue(result["verified"], result)
        self.assertEqual([name for name, _ in self.obs.writes()], ["SetSceneItemEnabled"])

    def test_invalid_schema_is_rejected_before_connection(self):
        cases = []
        for field, value in (("schema", "wrong"), ("canvas", {"width": True, "height": 720}), ("layers", [])):
            invalid = recipe()
            invalid[field] = value
            cases.append(invalid)
        for value in (float("nan"), 10**1000, 1.2, True, -1, 1200):
            invalid = recipe()
            invalid["layers"][0]["rect"]["x"] = value
            cases.append(invalid)
        duplicate = recipe()
        duplicate["layers"].append(deepcopy(duplicate["layers"][0]))
        cases.append(duplicate)
        unknown = recipe()
        unknown["layers"][0]["settings"] = {"url": "https://example.invalid"}
        cases.append(unknown)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(LayoutError):
                self.service.apply_layout(value, False)
        self.assertEqual(self.obs.calls, [])

    def test_expanded_item_limit_and_duplicate_existing_source_fail_offline(self):
        value = recipe([{"id": "item" + str(index), "type": "color", "color": "#FFFFFFFF",
                         "rect": {"x": 0, "y": 0, "width": 100, "height": 100},
                         "border": {"width": 4, "color": "#FFFFFFFF"}} for index in range(7)])
        with self.assertRaises(LayoutError):
            validate_layout_recipe(value)
        value = recipe([{"id": identity, "type": "existing", "source_name": "Camera",
                         "rect": {"x": 0, "y": 0, "width": 100, "height": 100}} for identity in ("a", "b")])
        with self.assertRaises(LayoutError):
            validate_layout_recipe(value)

    def test_canvas_missing_kind_missing_source_and_capability_fail_before_writes(self):
        for fault in ("canvas", "kind", "source", "capability"):
            with self.subTest(fault=fault):
                self.setUp()
                value = recipe()
                if fault == "canvas": self.obs.canvas["baseWidth"] = 1920
                if fault == "kind": self.obs.kinds.remove("color_source_v3")
                if fault == "capability": self.obs.missing.add("CreateInput")
                if fault == "source": value = recipe([{"id": "missing", "type": "existing", "source_name": "Missing", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}}])
                with self.assertRaises(LayoutError):
                    self.service.apply_layout(value, False)
                self.assertEqual(self.obs.writes(), [])

    def test_missing_asset_and_remote_or_relative_bindings_fail_before_obs(self):
        value = recipe([{"id": "image", "type": "image", "asset": "image", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}}], assets={"image": "missing.png"})
        for binding in ("missing.png", "https://example.invalid/picture.png", str((ROOT / "missing.png").resolve()), "\\\\server\\share\\image.png"):
            value["assets"]["image"] = binding
            with self.assertRaises(LayoutError) as raised:
                self.service.apply_layout(value, False)
            self.assertNotIn(binding, str(raised.exception))
        self.assertEqual(self.obs.calls, [])

    def test_source_scene_group_and_shared_settings_collisions_refuse(self):
        for fault in ("scene", "group", "kind", "settings", "target"):
            with self.subTest(fault=fault):
                self.setUp()
                name = "Layout Stage/panel"
                if fault == "scene": self.obs.scenes[name] = []
                elif fault == "group": self.obs.groups.append(name)
                elif fault == "target": self.obs.inputs["Stage"] = deepcopy(self.obs.inputs["Camera"])
                else:
                    self.obs.inputs[name] = {"kind": "camera_input" if fault == "kind" else "color_source_v3",
                        "settings": {"color": 1, "width": 400, "height": 300}, "size": (400, 300), "uuid": "other"}
                before = deepcopy(self.obs.inputs)
                with self.assertRaises(LayoutError):
                    self.service.apply_layout(recipe(), False)
                self.assertEqual(self.obs.writes(), [])
                self.assertEqual(self.obs.inputs, before)

    def test_matching_generated_input_reuses_settings_and_adds_only_one_item(self):
        self.service.apply_layout(recipe(), False)
        self.obs.scenes["Stage"].clear()
        self.obs.calls.clear()
        result = self.service.apply_layout(recipe(), False)
        self.assertTrue(result["verified"], result)
        self.assertEqual([name for name, _ in self.obs.writes()], ["CreateSceneItem", "SetSceneItemTransform"])

    def test_duplicate_existing_scene_items_fail_preflight(self):
        self.obs.scenes["Stage"] = []
        self.obs.add_item("Stage", "Camera")
        self.obs.add_item("Stage", "Camera")
        value = recipe([{"id": "camera", "type": "existing", "source_name": "Camera", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}}])
        with self.assertRaises(LayoutError):
            self.service.apply_layout(value, False)
        self.assertEqual(self.obs.writes(), [])

    def test_scene_capacity_is_checked_before_any_mutation(self):
        self.obs.scenes["Stage"] = []
        for _ in range(128):
            self.obs.add_item("Stage", "Camera")
        with self.assertRaisesRegex(LayoutError, "inventory"):
            self.service.apply_layout(recipe(), False)
        self.assertEqual(self.obs.writes(), [])

    def test_global_inventory_growth_is_checked_before_any_mutation(self):
        for kind in ("scenes", "inputs"):
            with self.subTest(kind=kind):
                self.obs = FakeOBS()
                self.service = LayoutService(ProductionService(lambda: self.obs))
                if kind == "scenes":
                    self.obs.scenes = {"Other" + str(index): [] for index in range(1024)}
                else:
                    self.obs.inputs = {"Other" + str(index): deepcopy(self.obs.inputs["Camera"]) for index in range(1024)}
                with self.assertRaisesRegex(LayoutError, "inventory"):
                    self.service.apply_layout(recipe(), False)
                self.assertEqual(self.obs.writes(), [])

    def test_malformed_creation_acknowledgement_never_authorizes_followup_geometry(self):
        for value in (True, 1.0, "1", None, -1):
            with self.subTest(value=value):
                self.obs = FakeOBS()
                self.service = LayoutService(ProductionService(lambda: self.obs))

                def malformed(name, _data, result):
                    if name == "CreateInput":
                        result["sceneItemId"] = value

                self.obs.after = malformed
                result = self.service.apply_layout(recipe(), False)
                self.assertEqual(result["state"], "partial")
                self.assertTrue(result["applied"])
                self.assertTrue(result["uncertain"])
                self.assertFalse(result["verified"])
                self.assertEqual([name for name, _ in self.obs.writes()], ["CreateScene", "CreateInput"])

    def test_translucent_border_has_no_overlapping_corner_pixels(self):
        value = recipe()
        value["layers"][0]["rect"] = {"x": 0, "y": 0, "width": 8, "height": 8}
        value["layers"][0]["border"] = {"width": 4, "color": "#FF000080"}
        result = self.service.apply_layout(value, False)
        self.assertTrue(result["verified"], result)
        pixels = set()
        for item in self.obs.scenes["Stage"][1:]:
            shape = item["transform"]
            part = {(x, y) for x in range(shape["positionX"], shape["positionX"] + shape["boundsWidth"])
                    for y in range(shape["positionY"], shape["positionY"] + shape["boundsHeight"])}
            self.assertFalse(pixels & part)
            pixels |= part
        self.assertEqual(len(pixels), 64)

    def test_malformed_obs_reads_refuse_preflight(self):
        for request, payload in (("GetSceneList", {"scenes": {}}), ("GetInputKindList", {"inputKinds": [None]}),
                                 ("GetInputList", {"inputs": [{"inputName": "Camera"}]}),
                                 ("GetGroupList", {"groups": [1]}), ("GetRecordStatus", {"outputActive": "false"})):
            with self.subTest(request=request):
                original = self.obs._request
                with patch.object(self.obs, "_request", side_effect=lambda name, data: payload if name == request else original(name, data)):
                    with self.assertRaises(LayoutError):
                        self.service.apply_layout(recipe(), False)
                self.assertEqual(self.obs.writes(), [])

    def test_stale_inventory_before_first_write_stops_without_mutation(self):
        reads = 0

        def stale(name, _data):
            nonlocal reads
            if name == "GetInputList":
                reads += 1
                if reads == 2:
                    self.obs.inputs["Operator input"] = deepcopy(self.obs.inputs["Camera"])

        self.obs.before = stale
        result = self.service.apply_layout(recipe(), False)
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["applied"])
        self.assertEqual(self.obs.writes(), [])

    def test_live_state_is_rechecked_immediately_before_each_write(self):
        reads = 0

        def live(name, _data):
            nonlocal reads
            if name == "GetRecordStatus":
                reads += 1
                if reads >= 2:
                    self.obs.recording = True

        self.obs.before = live
        result = self.service.apply_layout(recipe(), False)
        self.assertFalse(result["applied"])
        self.assertEqual(self.obs.writes(), [])
        allowed = self.service.apply_layout(recipe(), False, True)
        self.assertTrue(allowed["verified"], allowed)

    def test_lost_create_ack_stops_uncertain_without_retry_or_cleanup(self):
        def lost(name, _data, _result):
            if name == "CreateInput":
                raise ObsError("not-a-real-secret")

        self.obs.after = lost
        result = self.service.apply_layout(recipe(), False)
        self.assertEqual(result["state"], "partial")
        self.assertIsNone(result["applied"])
        self.assertTrue(result["uncertain"])
        self.assertEqual([name for name, _ in self.obs.writes()], ["CreateScene", "CreateInput"])
        self.assertEqual(len(self.obs.scenes["Stage"]), 1)
        self.assertNotIn("not-a-real-secret", json.dumps(result))

    def test_noop_transform_fails_readback_and_skips_later_layers(self):
        self.obs.noop.add("SetSceneItemTransform")
        value = recipe()
        value["layers"].append({**deepcopy(value["layers"][0]), "id": "second"})
        result = self.service.apply_layout(value, False)
        self.assertEqual(result["state"], "partial")
        self.assertTrue(result["applied"])
        self.assertFalse(result["verified"])
        self.assertTrue(result["uncertain"])
        self.assertNotIn("Layout Stage/second", self.obs.inputs)
        self.assertAlmostEqual(self.clock, 3.0)

    def test_delayed_image_geometry_is_ready_before_transform(self):
        reads = 0

        def not_ready(name, _data, result):
            nonlocal reads
            if name == "GetSceneItemTransform":
                reads += 1
                if reads < 3:
                    result["sceneItemTransform"].update(sourceWidth=0, sourceHeight=0)

        self.obs.after = not_ready
        result = self.service.apply_layout(recipe(), False)
        self.assertTrue(result["verified"], result)
        self.assertAlmostEqual(self.clock, 0.1)

    def test_deadline_rejects_late_reply_and_restores_client_timeout(self):
        settings = self.obs.settings
        budgets = []

        def late(name, _data):
            budgets.append(self.obs.settings.timeout)
            self.advance(0.1)

        self.obs.before = late
        with patch("obs_director.layouts.REQUEST_DEADLINE", 0.25):
            with self.assertRaises(LayoutError):
                self.service.apply_layout(recipe(), False)
        self.assertTrue(all(0 < value <= 0.25 for value in budgets))
        self.assertIs(self.obs.settings, settings)
        self.assertEqual(self.obs.writes(), [])

    def test_asset_change_before_first_write_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as temp:
            value, path = self.image_recipe(temp)

            def changed(name, _data):
                if name == "GetInputKindList":
                    Image.new("RGBA", (8, 4), "blue").save(path)

            self.obs.before = changed
            result = self.service.apply_layout(value, False)
            self.assertFalse(result["applied"])
            self.assertEqual(self.obs.writes(), [])
            self.assertNotIn(str(path), json.dumps(result))


if __name__ == "__main__":
    unittest.main()
