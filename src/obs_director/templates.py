"""Copy bundled layout templates for local ownership. Never connects to OBS."""
from __future__ import annotations

import argparse
from copy import deepcopy
from importlib import resources
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any
import zipfile

from PIL import Image


MAX_TEXT_BYTES = 64 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_COPY_BYTES = 32 * 1024 * 1024
NAME = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
BINDING = re.compile(r"[A-Z][A-Z0-9_]{0,31}\Z")
THEME_RECIPES = {"light": "template.json", "dark": "template.dark.json"}


class TemplateError(ValueError):
    """A template or destination cannot be copied safely."""


def _no_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)):
            raise TemplateError("Symbolic links and reparse points are not supported")


def _relative(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise TemplateError("Template resource paths must be portable relative paths")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(value).is_absolute():
        raise TemplateError("Template resource paths cannot traverse directories")
    return tuple(parts)


def _read(root, value: str, limit: int) -> bytes:
    item = root
    for part in (None, *_relative(value)):
        if part is not None:
            item = item.joinpath(part)
        if isinstance(item, Path):
            _no_links(item)
        elif callable(getattr(item, "is_symlink", None)) and item.is_symlink():
            raise TemplateError("Bundled symbolic links are not supported")
        elif isinstance(item, zipfile.Path) and item.at and item.exists():
            # ZipPath.is_symlink was added after Python 3.11. Examine the
            # portable Unix mode too, so supported Python versions agree.
            if stat.S_ISLNK(item.root.getinfo(item.at).external_attr >> 16):
                raise TemplateError("Bundled symbolic links are not supported")
    try:
        if not item.is_file():
            raise TemplateError("A bundled template file is missing")
        with item.open("rb") as handle:
            content = handle.read(limit + 1)
    except (OSError, KeyError) as error:
        raise TemplateError("Cannot read a bundled template file") from error
    if len(content) > limit:
        raise TemplateError("A bundled template file exceeds its size limit")
    return content


def _json(content: bytes) -> dict:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise TemplateError("Duplicate JSON keys are not supported")
            result[key] = value
        return result
    try:
        value = json.loads(content, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(TemplateError("Non-finite JSON value")))
    except (ValueError, UnicodeDecodeError) as error:
        raise TemplateError("Invalid template JSON") from error
    if not isinstance(value, dict):
        raise TemplateError("Template JSON must contain an object")
    return value


def _root():
    return resources.files("obs_director").joinpath("template_data")


def _catalog(root) -> list[dict]:
    catalog = _json(_read(root, "catalog.json", MAX_TEXT_BYTES))
    if set(catalog) != {"schema", "templates"} or catalog["schema"] != "obs.template-catalog.v1":
        raise TemplateError("Unsupported template catalog")
    entries = catalog["templates"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
        raise TemplateError("The template catalog must contain one to sixteen templates")
    names = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "title", "description", "bindings", "references", "themes"}:
            raise TemplateError("Invalid template catalog entry")
        name = entry["name"]
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            raise TemplateError("Invalid or duplicate template name")
        names.add(name)
        themes = entry["themes"]
        if (not isinstance(themes, list) or not themes or len(themes) > len(THEME_RECIPES)
                or any(not isinstance(theme, str) or theme not in THEME_RECIPES for theme in themes)
                or len(set(themes)) != len(themes) or "light" not in themes):
            raise TemplateError("Invalid template theme declaration")
        for field, limit in (("title", 80), ("description", 300)):
            if not isinstance(entry[field], str) or not 1 <= len(entry[field]) <= limit:
                raise TemplateError("Invalid template description")
        bindings = entry["bindings"]
        if not isinstance(bindings, list) or not 1 <= len(bindings) <= 4:
            raise TemplateError("Templates require one to four source bindings")
        keys, layers = set(), set()
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {"key", "layer_id", "description"}:
                raise TemplateError("Invalid source binding declaration")
            key, layer_id = binding["key"], binding["layer_id"]
            if (not isinstance(key, str) or not BINDING.fullmatch(key) or key in keys
                    or not isinstance(layer_id, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", layer_id)
                    or layer_id in layers or not isinstance(binding["description"], str)
                    or not 1 <= len(binding["description"]) <= 200):
                raise TemplateError("Invalid or duplicate source binding declaration")
            keys.add(key)
            layers.add(layer_id)
        if (not isinstance(entry["references"], list) or len(entry["references"]) > 4
                or any(not isinstance(ref, str) or len(ref) > 300 or not ref.startswith("https://") for ref in entry["references"])):
            raise TemplateError("Invalid template reference metadata")
    return entries


def list_templates() -> list[dict[str, Any]]:
    """Return portable metadata. This operation reads no OBS state."""
    return deepcopy(_catalog(_root()))


def get_template(name: str, theme: str = "light") -> dict[str, Any]:
    """Return metadata and an unbound portable recipe, never machine-local paths."""
    from .layouts import validate_layout_recipe

    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise TemplateError("Invalid template name")
    if not isinstance(theme, str) or theme not in THEME_RECIPES:
        raise TemplateError("Theme must be light or dark")
    for entry in list_templates():
        if entry["name"] == name:
            if theme not in entry["themes"]:
                raise TemplateError("Theme is not available for this template")
            recipe = _json(_read(_root(), name + "/" + THEME_RECIPES[theme], MAX_TEXT_BYTES))
            if not isinstance(recipe.get("layers"), list) or not isinstance(recipe.get("assets", {}), dict):
                raise TemplateError("Invalid template recipe")
            validation = deepcopy(recipe)
            existing = {layer.get("id"): layer for layer in validation["layers"]
                        if isinstance(layer, dict) and layer.get("type") == "existing"}
            if set(existing) != {binding["layer_id"] for binding in entry["bindings"]}:
                raise TemplateError("Template source declarations do not match its layers")
            for binding in entry["bindings"]:
                layer = existing[binding["layer_id"]]
                if layer.get("source_name") != "":
                    raise TemplateError("Bundled templates must leave source names unbound")
                layer["source_name"] = "Template validation " + binding["key"]
            for relative in validation.get("assets", {}).values():
                parts = _relative(relative)
                if len(parts) != 2 or parts[0] != "assets" or not parts[1].endswith(".png"):
                    raise TemplateError("Template assets must name PNG files in assets/")
            validate_layout_recipe(validation)
            return {**entry, "theme": theme, "recipe": recipe}
    raise TemplateError("Unknown template name")


def _destination(output) -> Path:
    raw = os.fspath(output)
    if not isinstance(raw, str) or not raw or raw.startswith(("\\\\", "//")) or "\x00" in raw:
        raise TemplateError("Output must be an explicit local directory")
    path = Path(raw)
    if ".." in path.parts:
        raise TemplateError("Output cannot contain parent traversal")
    path = Path(os.path.abspath(path))
    _no_links(path)
    if path.exists():
        raise TemplateError("Output already exists; choose a new directory")
    if not path.parent.is_dir():
        raise TemplateError("The output parent directory must already exist")
    return path


def _png(content: bytes) -> None:
    try:
        with Image.open(io.BytesIO(content)) as picture:
            if (picture.format != "PNG" or getattr(picture, "n_frames", 1) != 1
                    or not 1 <= picture.width <= 8192 or not 1 <= picture.height <= 8192
                    or picture.width * picture.height > 16_777_216):
                raise TemplateError("Template artwork must be a bounded PNG image")
            picture.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise TemplateError("Invalid template artwork") from error


def add_template(name: str, output, bindings: dict[str, str], theme: str = "light") -> dict[str, Any]:
    """Validate all input, then copy into a new directory without connecting to OBS."""
    from .layouts import validate_layout_recipe

    metadata = get_template(name, theme=theme)
    destination = _destination(output)
    expected = {binding["key"] for binding in metadata["bindings"]}
    if not isinstance(bindings, dict) or set(bindings) != expected:
        raise TemplateError("Supply exactly these source bindings: " + ", ".join(sorted(expected)))
    for value in bindings.values():
        if (not isinstance(value, str) or not value.strip() or len(value) > 200
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise TemplateError("Source bindings require nonempty source names with no control characters")
    root = _root()
    recipe = deepcopy(metadata["recipe"])
    if not isinstance(recipe.get("layers"), list) or not isinstance(recipe.get("assets", {}), dict):
        raise TemplateError("Invalid template recipe")
    existing = {layer.get("id"): layer for layer in recipe["layers"]
                if isinstance(layer, dict) and layer.get("type") == "existing"}
    if set(existing) != {binding["layer_id"] for binding in metadata["bindings"]}:
        raise TemplateError("Template source declarations do not match its layers")
    for binding in metadata["bindings"]:
        layer = existing[binding["layer_id"]]
        if layer.get("source_name") != "":
            raise TemplateError("Bundled templates must leave source names unbound")
        layer["source_name"] = bindings[binding["key"]]
    files = {}
    for asset_id, relative in recipe.get("assets", {}).items():
        parts = _relative(relative)
        if len(parts) != 2 or parts[0] != "assets" or not parts[1].endswith(".png"):
            raise TemplateError("Template assets must name PNG files in assets/")
        content = _read(root, relative, MAX_ASSET_BYTES)
        _png(content)
        files[relative] = content
        recipe["assets"][asset_id] = str(destination.joinpath(*parts))
    # This validator checks only schema and geometry. Source existence, files,
    # and OBS capabilities remain the layout preview/apply preflight's work.
    recipe = validate_layout_recipe(recipe)
    files["template.json"] = (json.dumps(recipe, indent=2) + "\n").encode("utf-8")
    for filename in ("README.md", "prompts.md"):
        files[filename] = _read(root, name + "/" + filename, MAX_TEXT_BYTES)
    if sum(len(content) for content in files.values()) > MAX_COPY_BYTES:
        raise TemplateError("Template copy exceeds its total size limit")

    created_files, created_dirs = [], []
    try:
        destination.mkdir(exist_ok=False)
        created_dirs.append(destination)
        if any(relative.startswith("assets/") for relative in files):
            asset_dir = destination / "assets"
            asset_dir.mkdir(exist_ok=False)
            created_dirs.append(asset_dir)
        for relative, content in files.items():
            path = destination.joinpath(*_relative(relative))
            with path.open("xb") as handle:
                created_files.append(path)
                handle.write(content)
    except BaseException:
        # Remove only paths created by this invocation, never an existing tree.
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        for path in reversed(created_dirs):
            path.rmdir()
        raise
    return {"template": name, "theme": theme, "directory": str(destination),
            "recipe_path": str(destination / "template.json"), "files": sorted(files),
            "next_step": "Edit your copy, then preview the layout against your existing OBS sources"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List bundled templates and required source bindings")
    add = commands.add_parser("add", help="Copy one template into a new local directory")
    add.add_argument("name")
    add.add_argument("--theme", choices=tuple(THEME_RECIPES), default="light")
    add.add_argument("--output", required=True, type=Path)
    add.add_argument("--bind", action="append", default=[], metavar="KEY=SOURCE_NAME")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            result = list_templates()
        else:
            bindings = {}
            for value in args.bind:
                key, separator, source = value.partition("=")
                if not separator or key in bindings:
                    raise TemplateError("Use each --bind KEY=SOURCE_NAME exactly once")
                bindings[key] = source
            result = add_template(args.name, args.output, bindings, theme=args.theme)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"state": "refused", "reason": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
