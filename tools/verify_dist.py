"""Inspect release archives without extraction; require source parity and public content."""
from __future__ import annotations

import argparse
import base64
import configparser
import csv
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import sys
import tarfile
import tomllib
import zipfile

from public_check import MAX_BYTES, scan_blob

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 512
EGG_FILES = {"PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "requires.txt", "top_level.txt"}
DIST_FILES = {"METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD", "licenses/LICENSE"}


class VerificationError(ValueError):
    """A release candidate failed a bounded, non-executing check."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise VerificationError(reason)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> None:
    parts = name.rstrip("/").split("/")
    require(bool(name) and "\\" not in name and ":" not in name
            and not name.startswith("/") and all(p not in {"", ".", ".."} for p in parts)
            and not any(ord(c) < 32 for c in name), "unsafe archive path")


def read_archive(path: Path) -> dict[str, bytes]:
    """Read bounded regular files only; reject links, aliases and duplicate names."""
    require(path.is_file() and path.stat().st_size <= MAX_ARCHIVE_BYTES, "missing or oversized archive")
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0

    def admit(name: str, size: int, directory: bool) -> None:
        nonlocal total
        safe_name(name)
        folded = name.rstrip("/").casefold()
        require(folded not in seen, "duplicate archive path")
        seen.add(folded)
        require(len(seen) <= MAX_MEMBERS and 0 <= size <= MAX_BYTES, "archive member limit")
        total += size
        require(total <= MAX_ARCHIVE_BYTES, "archive expansion limit")
        if directory:
            require(size == 0, "invalid directory member")

    try:
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    mode = member.external_attr >> 16
                    require(not stat.S_ISLNK(mode) and not (member.flag_bits & 1), "link or encrypted wheel member")
                    require(stat.S_IFMT(mode) in {0, stat.S_IFREG, stat.S_IFDIR}, "nonregular wheel member")
                    admit(member.filename, member.file_size, member.is_dir())
                    if not member.is_dir():
                        files[member.filename] = archive.read(member)
        else:
            with tarfile.open(path, "r:gz") as archive:
                for member in archive:
                    require(member.isfile() or member.isdir(), "nonregular source archive member")
                    admit(member.name, member.size, member.isdir())
                    if member.isfile():
                        stream = archive.extractfile(member)
                        require(stream is not None, "unreadable source archive member")
                        files[member.name] = stream.read(MAX_BYTES + 1)
                        require(len(files[member.name]) == member.size, "source archive size mismatch")
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile, RuntimeError) as exc:
        raise VerificationError("archive could not be read") from exc
    require(bool(files), "empty archive")
    return files


def source_files(root: Path) -> dict[str, bytes]:
    """The approved source-distribution surface, independent of archive metadata."""
    names = {"LICENSE", "README.md", "MANIFEST.in", "pyproject.toml", "run_server.py", "requirements-ci.lock"}
    for pattern in ("src/obs_director/**/*.py", "src/obs_director/template_data/**/*.json",
                    "src/obs_director/template_data/**/*.md", "src/obs_director/template_data/**/*.png",
                    "docs/**/*.md", "examples/**/*.json",
                    "examples/**/*.html", "examples/**/*.css", "examples/**/*.js", "examples/**/*.png",
                    "tools/**/*.py", "tests/**/*.py", ".github/**/*.yml"):
        names.update(p.relative_to(root).as_posix() for p in root.glob(pattern))
    result = {}
    for name in sorted(names):
        path = root / name
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()),
                "missing or indirect release source")
        require(path.stat().st_size <= MAX_BYTES, "oversized release source")
        result[name] = path.read_bytes()
    require("src/obs_director/__init__.py" in result, "missing package source")
    return result


def normalized_requirement(value: str) -> tuple[str, tuple[str, ...]]:
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)(.*)", value.replace(" ", ""))
    require(match is not None, "invalid dependency metadata")
    return re.sub(r"[-_.]+", "-", match[1]).lower(), tuple(sorted(match[2].split(",")))


def check_metadata(blob: bytes, project: dict, readme: bytes) -> None:
    metadata = BytesParser().parsebytes(blob)
    for key, expected in {"Name": project["name"], "Version": project["version"],
                          "Requires-Python": project["requires-python"], "License-Expression": "MIT",
                          "License-File": "LICENSE", "Description-Content-Type": "text/markdown"}.items():
        require(metadata.get_all(key) == [expected], "distribution metadata mismatch")
    actual = sorted(normalized_requirement(x) for x in metadata.get_all("Requires-Dist", []))
    require(actual == sorted(normalized_requirement(x) for x in project["dependencies"]), "dependency metadata mismatch")
    require(metadata.get_payload(decode=True).decode("utf-8").replace("\r\n", "\n").rstrip() == readme.decode("utf-8").replace("\r\n", "\n").rstrip(),
            "README metadata mismatch")


def check_entrypoint(blob: bytes, scripts: dict) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(blob.decode("utf-8"))
    require(parser.sections() == ["console_scripts"] and dict(parser["console_scripts"]) == scripts,
            "console entry point mismatch")


def check_record(files: dict[str, bytes], record_name: str) -> None:
    rows = list(csv.reader(io.StringIO(files[record_name].decode("utf-8"))))
    require(all(len(row) == 3 for row in rows), "invalid wheel RECORD row")
    require(len(rows) == len(files) and {row[0] for row in rows} == set(files), "wheel RECORD membership mismatch")
    for name, digest, size in rows:
        if name == record_name:
            require(digest == size == "", "wheel RECORD self-hash must be empty")
        else:
            expected = base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b"=").decode("ascii")
            require(digest == "sha256=" + expected and size == str(len(files[name])), "wheel RECORD digest mismatch")


def verify(root: Path, dist: Path) -> dict:
    """Require one expected wheel/sdist, exact source bytes, safe content and metadata."""
    root = root.resolve()
    sources = source_files(root)
    project = tomllib.loads(sources["pyproject.toml"].decode("utf-8"))["project"]
    package = re.sub(r"[-_.]+", "_", project["name"])
    stem = package + "-" + project["version"]
    wheel_name, sdist_name = stem + "-py3-none-any.whl", stem + ".tar.gz"
    require({p.name for p in dist.iterdir() if p.suffix == ".whl" or p.name.endswith(".tar.gz")} == {wheel_name, sdist_name},
            "expected exactly one current wheel and source archive")
    wheel = read_archive(dist / wheel_name)
    raw_sdist = read_archive(dist / sdist_name)
    require(all(name.startswith(stem + "/") for name in raw_sdist), "source archive root mismatch")
    sdist = {name[len(stem) + 1:]: data for name, data in raw_sdist.items()}
    info, egg = stem + ".dist-info/", "src/" + package + ".egg-info/"
    package_sources = {name[4:]: data for name, data in sources.items() if name.startswith("src/obs_director/")}
    require(set(wheel) == set(package_sources) | {info + name for name in DIST_FILES}, "unexpected or missing wheel content")
    require(set(sdist) == set(sources) | {"PKG-INFO", "setup.cfg"} | {egg + name for name in EGG_FILES},
            "unexpected or missing source archive content")
    for files, expected in ((wheel, package_sources), (sdist, sources)):
        require(all(files[name] == data for name, data in expected.items()), "archive differs from release source")
        for name, data in files.items():
            # Generated egg-info is intentional; other excluded runtime paths cannot
            # enter the exact content sets above. Still scan every byte of metadata.
            require(not scan_blob(name, data, origin="distribution"), "distribution public-content check failed")
    require(wheel[info + "licenses/LICENSE"] == sources["LICENSE"], "wheel license mismatch")
    for blob in (wheel[info + "METADATA"], sdist["PKG-INFO"], sdist[egg + "PKG-INFO"]):
        check_metadata(blob, project, sources["README.md"])
    require(wheel[info + "METADATA"] == sdist["PKG-INFO"] == sdist[egg + "PKG-INFO"], "archive metadata disagreement")
    for prefix, files in ((info, wheel), (egg, sdist)):
        check_entrypoint(files[prefix + "entry_points.txt"], project["scripts"])
        require(files[prefix + "top_level.txt"].strip() == b"obs_director", "top-level package mismatch")
    wheel_metadata = BytesParser().parsebytes(wheel[info + "WHEEL"])
    require(wheel_metadata.get_all("Tag") == ["py3-none-any"] and wheel_metadata.get("Root-Is-Purelib") == "true"
            and wheel_metadata.get("Wheel-Version") == "1.0", "wheel compatibility mismatch")
    check_record(wheel, info + "RECORD")
    require(set(sdist[egg + "SOURCES.txt"].decode("utf-8").splitlines()) == set(sources) | {egg + name for name in EGG_FILES},
            "source manifest membership mismatch")
    require(not sdist[egg + "dependency_links.txt"].strip(), "unexpected dependency links")
    require(sorted(normalized_requirement(x) for x in sdist[egg + "requires.txt"].decode("utf-8").splitlines()) ==
            sorted(normalized_requirement(x) for x in project["dependencies"]), "source dependency mismatch")
    setup = configparser.ConfigParser(interpolation=None)
    setup.read_string(sdist["setup.cfg"].decode("utf-8"))
    require(setup.sections() == ["egg_info"] and dict(setup["egg_info"]) == {"tag_build": "", "tag_date": "0"},
            "unexpected generated build configuration")
    return {"schema": "obs.release.v1", "name": project["name"], "version": project["version"],
            "artifacts": {name: {"sha256": sha256((dist / name).read_bytes()), "size": (dist / name).stat().st_size,
                                  "members": {key: sha256(value) for key, value in sorted(files.items())}}
                          for name, files in ((wheel_name, wheel), (sdist_name, raw_sdist))},
            "sources": {name: sha256(data) for name, data in sorted(sources.items())}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dist", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write-manifest", type=Path)
    mode.add_argument("--check-manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root, args.dist or args.root / "dist")
        if args.check_manifest:
            require(json.loads(args.check_manifest.read_text(encoding="utf-8")) == result, "release manifest mismatch")
        if args.write_manifest:
            args.write_manifest.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (VerificationError, OSError, ValueError, KeyError, configparser.Error) as exc:
        reason = str(exc) if isinstance(exc, VerificationError) else "unreadable or malformed release input"
        print("Distribution verification failed: " + reason, file=sys.stderr)
        return 1
    print(f"Verified {result['name']} {result['version']}: 2 archives; exact source, metadata, public content and hashes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
