"""Plan bounded local layouts and reconcile only their named scene items.

Recipes describe rectangles, never arbitrary OBS requests or source settings.
The shared ProductionService lock serializes this planner with other controls.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import re
import time
from typing import Any

from PIL import Image

from .transport import _local_path, utc_now


MAX_ITEMS = 32
MAX_REQUESTS = 4096
REQUEST_DEADLINE = 30.0
READBACK_DEADLINE = 3.0
MAX_ASSET_BYTES = 10 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,47}\Z")
COLOR = re.compile(r"#[0-9A-Fa-f]{8}\Z")
READS = {"GetVideoSettings", "GetProfileList", "GetSceneCollectionList", "GetSceneList",
         "GetGroupList", "GetInputList", "GetInputKindList", "GetInputSettings",
         "GetSceneItemList", "GetSceneItemTransform", "GetRecordStatus", "GetStreamStatus"}
SETTERS = {"create_scene": "CreateScene", "create_input": "CreateInput", "add_input": "CreateSceneItem",
           "transform": "SetSceneItemTransform", "visibility": "SetSceneItemEnabled", "order": "SetSceneItemIndex"}
TRANSFORM_KEYS = ("positionX", "positionY", "rotation", "scaleX", "scaleY", "alignment",
                  "boundsType", "boundsAlignment", "boundsWidth", "boundsHeight",
                  "cropLeft", "cropRight", "cropTop", "cropBottom")


class LayoutError(ValueError):
    """A sanitized schema, preflight, or state-continuity failure."""


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise LayoutError("Layout names must contain 1 to 200 printable characters")
    return value


def _integer(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise LayoutError("Layout pixel values are outside their integer bounds")
    return value


def _shape(value: Any, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise LayoutError("Layout contains missing or unsupported fields")


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise LayoutError("Layout IDs must be bounded letters, digits, underscores or hyphens")
    return value


def _color(value: Any) -> str:
    if not isinstance(value, str) or not COLOR.fullmatch(value):
        raise LayoutError("Layout colors must use #RRGGBBAA")
    return value.upper()


def validate_layout_recipe(recipe: dict) -> dict:
    """Normalize a portable recipe without reading files, opening OBS, or mutating it."""
    _shape(recipe, {"schema", "scene_name", "canvas", "layers"}, {"assets"})
    if recipe["schema"] != "obs.layout.v1":
        raise LayoutError("Unsupported layout schema")
    scene = _name(recipe["scene_name"])
    _shape(recipe["canvas"], {"width", "height"})
    canvas = {key: _integer(recipe["canvas"][key], 16, 8192) for key in ("width", "height")}
    layers = recipe["layers"]
    if not isinstance(layers, list) or not 1 <= len(layers) <= MAX_ITEMS:
        raise LayoutError("Layout requires 1 to 32 layers")
    normalized, ids, names, assets_used = [], set(), set(), set()
    count = 0
    for source in layers:
        if not isinstance(source, dict) or source.get("type") not in ("existing", "image", "color"):
            raise LayoutError("Unsupported layout layer type")
        kind = source["type"]
        reference = {"existing": "source_name", "image": "asset", "color": "color"}[kind]
        _shape(source, {"id", "type", "rect", reference}, {"visible", "border"})
        layer = deepcopy(source)
        layer["id"] = _identifier(layer["id"])
        if layer["id"].casefold() in ids:
            raise LayoutError("Duplicate layout layer ID")
        ids.add(layer["id"].casefold())
        _shape(layer["rect"], {"x", "y", "width", "height"})
        rect = layer["rect"]
        for key in ("x", "y", "width", "height"):
            _integer(rect[key], 0 if key in ("x", "y") else 1, 8192)
        if rect["x"] + rect["width"] > canvas["width"] or rect["y"] + rect["height"] > canvas["height"]:
            raise LayoutError("Layout rectangles must remain inside the base canvas")
        layer.setdefault("visible", True)
        if type(layer["visible"]) is not bool:
            raise LayoutError("Layout visibility must be boolean")
        if kind == "existing":
            name = _name(layer["source_name"])
            if name.casefold() in names:
                raise LayoutError("The same existing input cannot occur twice in a layout")
            names.add(name.casefold())
        elif kind == "image":
            assets_used.add(_identifier(layer["asset"]))
        else:
            layer["color"] = _color(layer["color"])
        if "border" in layer:
            _shape(layer["border"], {"width", "color"})
            _integer(layer["border"]["width"], 1, min(64, min(rect["width"], rect["height"]) // 2))
            layer["border"]["color"] = _color(layer["border"]["color"])
            count += 4
        count += 1
        normalized.append(layer)
    if count > MAX_ITEMS:
        raise LayoutError("Borders and layers together cannot exceed 32 scene items")
    assets = recipe.get("assets", {})
    if not isinstance(assets, dict) or set(assets) != assets_used:
        raise LayoutError("Asset bindings must exactly match the recipe's image references")
    for key, value in assets.items():
        _identifier(key)
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise LayoutError("Asset bindings must contain bounded local paths")
    return {"schema": "obs.layout.v1", "scene_name": scene, "canvas": canvas,
            "layers": normalized, "assets": dict(assets)}


def _asset(value: str) -> dict:
    try:
        if not Path(value).is_absolute():
            raise ValueError()
        path = _local_path(value)
        if not path.is_file():
            raise ValueError()
        with path.open("rb") as source:
            raw = source.read(MAX_ASSET_BYTES + 1)
        if not raw or len(raw) > MAX_ASSET_BYTES:
            raise ValueError()
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP") or getattr(image, "n_frames", 1) != 1:
                raise ValueError()
            width, height = image.size
            if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 16_777_216:
                raise ValueError()
            image.verify()
        return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "width": width, "height": height}
    except Exception:
        raise LayoutError("An image asset is not a readable, bounded local PNG, JPEG or WebP") from None


def _rgba(value: str) -> int:
    return int.from_bytes(bytes.fromhex(value[1:]), "little")


def _expand(recipe: dict, assets: dict) -> list[dict]:
    result = []
    for layer in recipe["layers"]:
        name = "Layout " + recipe["scene_name"] + "/" + layer["id"]
        if len(name) + 14 > 200:
            raise LayoutError("Scene and layer names are too long for generated inputs")
        variants = [(layer["id"], name, layer["type"], layer["rect"], layer.get("color"))]
        if "border" in layer:
            x, y, width, height = (layer["rect"][key] for key in ("x", "y", "width", "height"))
            thickness = layer["border"]["width"]
            # Partition the corners so translucent edges never double-composite.
            edges = {"top": (x, y, width - thickness, thickness),
                     "right": (x + width - thickness, y, thickness, height - thickness),
                     "bottom": (x + thickness, y + height - thickness, width - thickness, thickness),
                     "left": (x, y + thickness, thickness, height - thickness)}
            variants.extend((layer["id"] + "/border-" + edge, name + "/border-" + edge, "color",
                             dict(zip(("x", "y", "width", "height"), rect)), layer["border"]["color"])
                            for edge, rect in edges.items())
        for identity, generated, kind, rect, color in variants:
            values = {"id": identity, "name": layer["source_name"] if kind == "existing" else generated,
                      "type": kind, "rect": dict(rect), "visible": layer["visible"], "settings": None, "kind": None}
            if kind == "image":
                values.update(kind="image_source", settings={"file": assets[layer["asset"]]["path"], "unload": False})
            elif kind == "color":
                values.update(kind="color_source_v3", settings={"color": _rgba(color), "width": rect["width"], "height": rect["height"]})
            values["transform"] = {"positionX": rect["x"], "positionY": rect["y"], "rotation": 0,
                "scaleX": 1, "scaleY": 1, "alignment": 5, "boundsType": "OBS_BOUNDS_STRETCH",
                "boundsAlignment": 5, "boundsWidth": rect["width"], "boundsHeight": rect["height"],
                "cropLeft": 0, "cropRight": 0, "cropTop": 0, "cropBottom": 0}
            result.append(values)
    names = [layer["name"].casefold() for layer in result]
    if len(names) != len(set(names)):
        raise LayoutError("Expanded layout input names collide")
    return result


def _same(actual: dict, desired: dict) -> bool:
    return all(type(actual.get(key)) in (int, float) and abs(actual[key] - value) <= 0.0001
               if type(value) in (int, float) else type(actual.get(key)) is type(value) and actual[key] == value
               for key, value in desired.items())


class _Session:
    def __init__(self, client):
        self.client = client
        self.deadline = time.monotonic() + REQUEST_DEADLINE
        self.count = 0

    def call(self, request: str, data: dict | None = None, *, deadline: float | None = None) -> dict:
        limit = min(self.deadline, deadline) if deadline is not None else self.deadline
        remaining = limit - time.monotonic()
        self.count += 1
        if remaining <= 0 or self.count > MAX_REQUESTS:
            raise LayoutError("Layout exceeded its bounded OBS request budget")
        original = self.client.settings
        self.client.settings = replace(original, timeout=min(original.timeout, remaining))
        try:
            result = self.client.request(request, data)
            if time.monotonic() >= limit or not isinstance(result, dict):
                raise LayoutError("Layout OBS readback was late or malformed")
            return result
        finally:
            self.client.settings = original

    def require(self, requests):
        remaining = self.deadline - time.monotonic()
        self.count += 1
        if remaining <= 0 or self.count > MAX_REQUESTS:
            raise LayoutError("Layout exceeded its bounded OBS request budget")
        original = self.client.settings
        self.client.settings = replace(original, timeout=min(original.timeout, remaining))
        try:
            self.client.require_capabilities(list(requests))
            if time.monotonic() >= self.deadline:
                raise LayoutError("Layout capability discovery exceeded its deadline")
        finally:
            self.client.settings = original

    def live(self, allow_live: bool):
        record = self.call("GetRecordStatus").get("outputActive")
        stream = self.call("GetStreamStatus").get("outputActive")
        if type(record) is not bool or type(stream) is not bool or ((record or stream) and not allow_live):
            raise LayoutError("Layout changes require known output states and explicit live opt-in")


def _list(value: Any, limit=1024) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise LayoutError("OBS returned a malformed or oversized inventory")
    return value


def _inventory(values: Any, name: str, extra: tuple[str, ...]) -> dict:
    result = {}
    for item in _list(values):
        if not isinstance(item, dict):
            raise LayoutError("OBS returned malformed identities")
        identity = _name(item.get(name))
        if identity in result:
            raise LayoutError("OBS returned duplicate identities")
        fields = {field: item.get(field) for field in extra}
        if any(value is not None and not isinstance(value, str) for value in fields.values()):
            raise LayoutError("OBS returned malformed identities")
        result[identity] = fields
    return result


def _base(session: _Session) -> dict:
    video = session.call("GetVideoSettings")
    canvas = {"width": _integer(video.get("baseWidth"), 16, 8192), "height": _integer(video.get("baseHeight"), 16, 8192)}
    scenes = _inventory(session.call("GetSceneList").get("scenes"), "sceneName", ("sceneUuid",))
    inputs = _inventory(session.call("GetInputList").get("inputs"), "inputName", ("inputKind", "inputUuid"))
    if any(not item["inputKind"] for item in inputs.values()):
        raise LayoutError("OBS returned an input without its kind")
    groups = _list(session.call("GetGroupList").get("groups"))
    if any(not isinstance(name, str) or not name for name in groups) or len(groups) != len(set(groups)):
        raise LayoutError("OBS returned malformed group identities")
    return {"canvas": canvas, "scenes": scenes, "inputs": inputs, "groups": sorted(groups),
            "profile": _name(session.call("GetProfileList").get("currentProfileName")),
            "collection": _name(session.call("GetSceneCollectionList").get("currentSceneCollectionName"))}


def _items(session: _Session, scene: str) -> list[dict]:
    result = []
    for value in _list(session.call("GetSceneItemList", {"sceneName": scene}).get("sceneItems"), 128):
        if not isinstance(value, dict) or type(value.get("sceneItemEnabled")) is not bool:
            raise LayoutError("OBS returned malformed scene items")
        group = value.get("isGroup")
        # OBS reports group membership only for scene sources. A normal input
        # has an explicit null flag plus its input source type and kind.
        if "isGroup" in value and group is None and value.get("sourceType") == "OBS_SOURCE_TYPE_INPUT":
            _name(value.get("inputKind"))
            group = False
        if type(group) is not bool:
            raise LayoutError("OBS returned malformed scene items")
        result.append({"id": _integer(value.get("sceneItemId"), 0, 2**31 - 1),
                       "index": _integer(value.get("sceneItemIndex"), 0, 127), "name": _name(value.get("sourceName")),
                       "enabled": value["sceneItemEnabled"], "group": group,
                       "kind": value.get("inputKind"), "uuid": value.get("sourceUuid")})
    result.sort(key=lambda item: item["index"])
    if [item["index"] for item in result] != list(range(len(result))) or len({item["id"] for item in result}) != len(result):
        raise LayoutError("OBS scene item IDs or ordering are ambiguous")
    return result


def _transform(session: _Session, scene: str, item_id: int, *, deadline=None) -> dict:
    value = session.call("GetSceneItemTransform", {"sceneName": scene, "sceneItemId": item_id}, deadline=deadline).get("sceneItemTransform")
    if not isinstance(value, dict):
        raise LayoutError("OBS returned malformed scene geometry")
    for key in TRANSFORM_KEYS + ("sourceWidth", "sourceHeight"):
        actual = value.get(key)
        if key == "boundsType":
            if not isinstance(actual, str) or not actual.startswith("OBS_BOUNDS_"):
                raise LayoutError("OBS returned malformed scene geometry")
        elif type(actual) not in (int, float) or not -1e9 <= actual <= 1e9:
            raise LayoutError("OBS returned malformed scene geometry")
    return {key: value[key] for key in TRANSFORM_KEYS + ("sourceWidth", "sourceHeight")}


def _settings_match(session: _Session, layer: dict) -> None:
    value = session.call("GetInputSettings", {"inputName": layer["name"]})
    settings = value.get("inputSettings")
    if value.get("inputKind") != layer["kind"] or not isinstance(settings, dict) or not _same(settings, layer["settings"]):
        raise LayoutError("A generated input name collides with different source settings")


def _snapshot(session: _Session, recipe: dict, layers: list[dict]) -> dict:
    base = _base(session)
    if base["canvas"] != recipe["canvas"]:
        raise LayoutError("Recipe canvas does not match the OBS base canvas")
    scene = recipe["scene_name"]
    if scene in base["inputs"] or scene in base["groups"]:
        raise LayoutError("Target scene name collides with another source")
    if len(base["scenes"]) + (scene not in base["scenes"]) > 1024:
        raise LayoutError("Layout additions would exceed the bounded scene inventory")
    if len(base["inputs"]) + sum(layer["name"] not in base["inputs"] for layer in layers) > 1024:
        raise LayoutError("Layout additions would exceed the bounded input inventory")
    kinds = _list(session.call("GetInputKindList").get("inputKinds"))
    if any(not isinstance(kind, str) for kind in kinds):
        raise LayoutError("OBS returned malformed input kinds")
    items = _items(session, scene) if scene in base["scenes"] else []
    if len(items) + sum(not any(item["name"] == layer["name"] for item in items) for layer in layers) > 128:
        raise LayoutError("Layout additions would exceed the bounded scene inventory")
    transforms = {}
    for layer in layers:
        name = layer["name"]
        found = base["inputs"].get(name)
        if name in base["scenes"] or name in base["groups"]:
            raise LayoutError("A layout input name collides with a scene or group")
        if layer["type"] == "existing":
            if found is None:
                raise LayoutError("An explicitly referenced existing input is missing")
        else:
            if layer["kind"] not in kinds:
                raise LayoutError("A required layout input kind is unavailable")
            if found is not None:
                if found["inputKind"] != layer["kind"]:
                    raise LayoutError("A generated input name collides with another input kind")
                _settings_match(session, layer)
        matches = [item for item in items if item["name"] == name]
        if len(matches) > 1 or (matches and (matches[0]["group"] or found is None or matches[0]["kind"] != found["inputKind"])):
            raise LayoutError("Target scene contains an ambiguous layout source identity")
        if matches:
            geometry = _transform(session, scene, matches[0]["id"])
            if geometry["sourceWidth"] <= 0 or geometry["sourceHeight"] <= 0:
                raise LayoutError("An existing layout item has no ready visual dimensions")
            transforms[name] = geometry
    return {"base": base, "items": items, "transforms": transforms}


def _plan(recipe: dict, layers: list[dict], snapshot: dict) -> list[dict]:
    operations = []
    if recipe["scene_name"] not in snapshot["base"]["scenes"]:
        operations.append({"action": "create_scene", "layer": None})
    ordered = [item["name"] if item["name"] in {layer["name"] for layer in layers} else ("unmanaged", item["id"])
               for item in snapshot["items"]]
    for layer in layers:
        name = layer["name"]
        item = next((value for value in snapshot["items"] if value["name"] == name), None)
        if name not in snapshot["base"]["inputs"]:
            operations.append({"action": "create_input", "layer": layer})
        elif item is None:
            operations.append({"action": "add_input", "layer": layer})
        if item is None:
            ordered.append(name)
        if item is None or not _same(snapshot["transforms"][name], layer["transform"]):
            operations.append({"action": "transform", "layer": layer})
        if item is not None and item["enabled"] != layer["visible"]:
            operations.append({"action": "visibility", "layer": layer})
    desired = [value for value in ordered if isinstance(value, tuple)] + [layer["name"] for layer in layers]
    for layer in reversed(layers):
        name = layer["name"]
        index = desired.index(name)
        if ordered.index(name) != index:
            operations.append({"action": "order", "layer": layer, "index": index})
            ordered.remove(name)
            ordered.insert(index, name)
    return operations


class LayoutService:
    """Reconcile a layout without selecting scenes, changing shared settings, or deleting items."""

    def __init__(self, production):
        self.production = production

    def preview_layout(self, recipe: dict, allow_live: bool = False) -> dict:
        """Validate local assets and OBS state, and return a layout plan without writes."""
        return self.apply_layout(recipe, dry_run=True, allow_live=allow_live)

    def apply_layout(self, recipe: dict, dry_run: bool = True, allow_live: bool = False) -> dict:
        """Preview by default; apply only checked changes and stop on uncertainty.

        Existing inputs retain their shared settings. Source pixels are not inspected.
        """
        if type(dry_run) is not bool or type(allow_live) is not bool:
            raise LayoutError("Layout execution flags must be boolean")
        recipe = validate_layout_recipe(recipe)
        assets = {name: _asset(path) for name, path in recipe["assets"].items()}
        layers = _expand(recipe, assets)
        with self.production.lock, self.production.connection() as client:
            session = _Session(client)
            try:
                session.require(READS)
                session.live(allow_live)
                snapshot = _snapshot(session, recipe, layers)
                operations = _plan(recipe, layers, snapshot)
                session.require({SETTERS[op["action"]] for op in operations})
            except LayoutError:
                raise
            except Exception:
                raise LayoutError("Layout preflight could not verify the required OBS state") from None
            public = [{"action": op["action"], "layer_id": op["layer"]["id"] if op["layer"] else None}
                      for op in operations]
            result = {"schema": "obs.layout.result.v1", "scene_name": recipe["scene_name"], "canvas": recipe["canvas"],
                      "dry_run": dry_run, "state": "preview", "change_count": len(operations), "operations": public,
                      "applied": False, "verified": False, "uncertain": False, "checked_at": utc_now(), "receipts": [],
                      "verification": "OBS state readback only; rendered pixels have not been inspected"}
            if dry_run:
                return result
            try:
                if {name: _asset(path) for name, path in recipe["assets"].items()} != assets or _snapshot(session, recipe, layers) != snapshot:
                    raise LayoutError("Layout preflight became stale before execution")
                for op, description in zip(operations, public):
                    receipt = {**description, "applied": False, "verified": False, "uncertain": False}
                    result["receipts"].append(receipt)
                    self._execute(session, recipe, snapshot, op, allow_live, assets, receipt)
                final = _snapshot(session, recipe, layers)
                if final != snapshot or _plan(recipe, layers, final):
                    raise LayoutError("Final layout state did not match the verified plan")
                if {name: _asset(path) for name, path in recipe["assets"].items()} != assets:
                    raise LayoutError("A local image asset changed during layout execution")
                result.update(state="completed" if operations else "unchanged", applied=bool(operations), verified=True)
            except Exception:
                receipts = result["receipts"]
                uncertain = any(item["uncertain"] for item in receipts)
                applied = None if any(item["applied"] is None for item in receipts) else any(item["applied"] for item in receipts)
                result.update(state="partial" if any(item["verified"] for item in receipts) else "failed",
                              applied=applied, uncertain=uncertain or bool(applied), verified=False,
                              reason="Layout execution stopped because state, acknowledgement or readback was not verified")
            return result

    @staticmethod
    def _execute(session: _Session, recipe: dict, snapshot: dict, op: dict, allow_live: bool,
                 assets: dict, receipt: dict) -> None:
        scene = recipe["scene_name"]
        base = _base(session)
        items = _items(session, scene) if scene in base["scenes"] else []
        if base != snapshot["base"] or items != snapshot["items"]:
            raise LayoutError("OBS layout identities or ordering changed during execution")
        layer = op["layer"]
        item = next((value for value in items if layer and value["name"] == layer["name"]), None)
        if item is not None:
            geometry = _transform(session, scene, item["id"])
            if geometry != snapshot["transforms"][layer["name"]]:
                raise LayoutError("OBS layout geometry changed during execution")
        if layer and layer["type"] != "existing" and layer["name"] in base["inputs"]:
            _settings_match(session, layer)
        if op["action"] == "create_input" and layer["type"] == "image":
            if {name: _asset(path) for name, path in recipe["assets"].items()} != assets:
                raise LayoutError("A local image asset changed before input creation")
        session.live(allow_live)
        action = op["action"]
        if action == "create_scene":
            data = {"sceneName": scene}
        elif action == "create_input":
            data = {"sceneName": scene, "inputName": layer["name"], "inputKind": layer["kind"],
                    "inputSettings": layer["settings"], "sceneItemEnabled": layer["visible"]}
        elif action == "add_input":
            data = {"sceneName": scene, "sourceName": layer["name"], "sceneItemEnabled": layer["visible"]}
        else:
            data = {"sceneName": scene, "sceneItemId": item["id"]}
            data.update({"sceneItemTransform": layer["transform"]} if action == "transform" else
                        {"sceneItemEnabled": layer["visible"]} if action == "visibility" else {"sceneItemIndex": op["index"]})
        receipt.update(applied=None, uncertain=True)
        response = session.call(SETTERS[action], data)
        receipt["applied"] = True
        if action == "create_scene":
            after = _base(session)
            expected = deepcopy(base)
            if scene not in after["scenes"]:
                raise LayoutError("New scene was not observed")
            expected["scenes"][scene] = after["scenes"][scene]
            if expected != after or _items(session, scene):
                raise LayoutError("Scene creation readback changed unexpected state")
            snapshot["base"] = after
        elif action in ("create_input", "add_input"):
            acknowledged_id = _integer(response.get("sceneItemId"), 0, 2**31 - 1)
            after_base = _base(session)
            expected_base = deepcopy(base)
            if action == "create_input":
                created = after_base["inputs"].get(layer["name"])
                if created is None or created["inputKind"] != layer["kind"]:
                    raise LayoutError("New layout input was not observed")
                expected_base["inputs"][layer["name"]] = created
                _settings_match(session, layer)
            after_items = _items(session, scene)
            added = [value for value in after_items if value["id"] not in {old["id"] for old in items}]
            if (after_base != expected_base or len(added) != 1 or added[0]["name"] != layer["name"]
                    or added[0]["enabled"] != layer["visible"] or added[0]["group"]
                    or added[0]["id"] != acknowledged_id or added[0]["index"] != len(items)
                    or after_items[:-1] != items):
                raise LayoutError("New scene item readback was ambiguous")
            snapshot.update(base=after_base, items=after_items)
            deadline = min(session.deadline, time.monotonic() + READBACK_DEADLINE)
            while True:
                geometry = _transform(session, scene, added[0]["id"], deadline=deadline)
                if geometry["sourceWidth"] > 0 and geometry["sourceHeight"] > 0:
                    snapshot["transforms"][layer["name"]] = geometry
                    break
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        elif action == "transform":
            deadline = min(session.deadline, time.monotonic() + READBACK_DEADLINE)
            while True:
                geometry = _transform(session, scene, item["id"], deadline=deadline)
                if _same(geometry, layer["transform"]) and geometry["sourceWidth"] > 0 and geometry["sourceHeight"] > 0:
                    snapshot["transforms"][layer["name"]] = geometry
                    break
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        else:
            expected = deepcopy(items)
            if action == "visibility":
                next(value for value in expected if value["id"] == item["id"])["enabled"] = layer["visible"]
            else:
                moved = expected.pop(item["index"])
                expected.insert(op["index"], moved)
                for index, value in enumerate(expected):
                    value["index"] = index
            if _items(session, scene) != expected:
                raise LayoutError("Layout visibility or ordering was not observed")
            snapshot["items"] = expected
        receipt.update(verified=True, uncertain=False)
