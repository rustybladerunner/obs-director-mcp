#!/usr/bin/env python3
"""Check an export tree and, when present, its Git index without printing secrets."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable


MAX_BYTES = 4 * 1024 * 1024
# Original generated artwork inspected for this release. No directory-wide media exemption.
# Both source-tree and installed-wheel paths must match these exact bytes.
REVIEWED_ASSETS = {
    "examples/tournament/piphound-stinger.webm": "d3a1d5d9fecb31b3be279de8d30e41640723488ff81f1c062b63500b14d18b49",
    "obs_director/template_data/assets/background-dark.png": "54e78c9bf5eb37d59ea068c9ed598bd433ce61ec9129831fd4cf84080d5a5db9",
    "examples/funded-desk/presenter.png": "1c17047ec2118451e7a21b6a474be049d8dc6f48e749bafeaafc007a883dfce9",
    "obs_director/template_data/assets/background.png": "9e964d04e951d9e23d1a4693209888aa3495ea992666a87490f889055002e929",
    "obs_director/template_data/assets/frame.png": "905e48696a7b03a75f68b7ec28a67970dd160e35dd5b52a425dd5b64cb73352b",
}
EXCLUDED_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".cache", ".tmp", ".tox", ".nox", "node_modules", "build", "dist",
    "outputs", "runtime", "captures", "recordings", "local-data", ".obs-mcp",
    ".eggs",
})
MEDIA_SUFFIXES = frozenset({
    ".mkv", ".mp4", ".mov", ".avi", ".webm", ".flv", ".mp3",
    ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".png", ".jpg",
    ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".ico",
})
CREDENTIAL_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".keystore"})
PRIVATE_NAMES = frozenset({
    "credentials.json", "credentials.toml", "secrets.json", "secrets.toml",
    "obs-websocket.json", "obs-websocket.ini", "global.ini", "basic.ini",
})
CONFIG_SUFFIXES = frozenset({
    ".env", ".ini", ".toml", ".yaml", ".yml", ".json", ".cfg", ".conf",
    ".md", ".txt", ".example", ".sample", ".template",
})
SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
    ("api_token", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("api_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("api_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{15,}\b")),
    ("api_token", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bAIza[A-Za-z0-9_-]{35}\b")),
    ("absolute_machine_path", re.compile(r"(?i)(?<![A-Za-z0-9])[a-z]:[\\/]+(?![<{])[A-Za-z0-9_. -]+(?:[\\/]|(?=[\"'\s]|$))")),
    ("absolute_machine_path", re.compile(r"/(?:home|Users|Volumes|media|mnt)/(?![<{])[^/\s\"'<>]+")),
)
ASSIGNMENT = re.compile(
    r"(?<![\w])(?P<key>[A-Za-z_][A-Za-z0-9_-]*)[\"']?\s*"
    r"(?::\s*(?:str|Optional\[str\]|str\s*\|\s*None)\s*)?"
    r"(?::|=(?!=))\s*"
)
SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:password|passwd|pwd|secret|api_key|apikey|access_token|auth_token|"
    r"refresh_token|client_secret|server_password|stream_key|token|serverpassword|"
    r"streamkey|accesstoken|authtoken|clientsecret|refreshtoken)$"
)
QUOTED = re.compile(r"^([\"'])(.*?)(?<!\\)\1")
PLACEHOLDERS = frozenset({
    "", "example", "placeholder", "dummy", "change_me", "changeme", "replace_me",
    "your_password", "your_token", "your_api_key", "test_only", "not_a_real_secret",
})


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    category: str


def excluded_dir(name: str) -> bool:
    name = name.casefold()
    return name in EXCLUDED_DIRS or name.endswith(".egg-info")


def placeholder(value: str) -> bool:
    value = value.strip()
    return (
        value.casefold().replace("-", "_") in PLACEHOLDERS
        or bool(re.fullmatch(r"<[^>]+>|\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*|%[^%]+%", value))
        or bool(re.fullmatch(r"YOUR_[A-Z0-9_]+|REPLACE_[A-Z0-9_]+", value))
    )


def credential_assignment(line: str, filename: str) -> bool:
    # Only literals are evidence. Identifiers, environment reads and function calls are not.
    for match in ASSIGNMENT.finditer(line):
        key = match["key"].casefold().replace("-", "_")
        if not SENSITIVE_KEY.search(key):
            continue
        rhs = line[match.end():].strip()
        quoted = QUOTED.match(rhs)
        if quoted:
            if not placeholder(quoted[2]):
                return True
        elif PurePosixPath(filename).suffix.casefold() in CONFIG_SUFFIXES or PurePosixPath(filename).name == ".env":
            value = rhs.split("#", 1)[0].strip().rstrip(",")
            if value and re.fullmatch(r"[A-Za-z0-9_./:@%${}<>+-]+", value) and not placeholder(value):
                return True
    return False


def path_categories(relative: str, tracked: bool) -> set[str]:
    path = PurePosixPath(relative)
    name = path.name.casefold()
    categories: set[str] = set()
    if name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"}):
        categories.add("credential_file")
    if path.suffix.casefold() in CREDENTIAL_SUFFIXES or name in PRIVATE_NAMES:
        categories.add("credential_file")
    if path.suffix.casefold() in MEDIA_SUFFIXES:
        categories.add("media_artifact")
    if any(part.casefold() in {"obs-studio", "plugin_config"} for part in path.parts):
        categories.add("obs_configuration")
    if tracked and any(excluded_dir(part) for part in path.parts[:-1]):
        categories.add("tracked_runtime_artifact")
    return categories


def scan_blob(relative: str, data: bytes, *, tracked: bool = False,
              origin: str = "tree", forbid_text: Iterable[str] = ()) -> list[Finding]:
    findings = {Finding(relative, 1, origin + "/" + c) for c in path_categories(relative, tracked)}
    forbidden = tuple(value.casefold() for value in forbid_text if value)
    if any(value in relative.casefold() for value in forbidden):
        findings.add(Finding(relative, 1, origin + "/private_text"))
    if len(data) > MAX_BYTES:
        findings.add(Finding(relative, 1, origin + "/oversized_unchecked_file"))
        return sorted(findings)
    asset_name = relative[4:] if relative.startswith("src/obs_director/") else relative
    if REVIEWED_ASSETS.get(asset_name) == hashlib.sha256(data).hexdigest():
        findings.discard(Finding(relative, 1, origin + "/media_artifact"))
        return sorted(findings)
    media_magic = (data.startswith((b"\x89PNG\r\n", b"\xff\xd8\xff", b"GIF8", b"\x1aE\xdf\xa3", b"OggS", b"ID3"))
                   or (data.startswith(b"RIFF") and data[8:12] in {b"WAVE", b"WEBP", b"AVI "})
                   or data[4:8] == b"ftyp")
    if media_magic:
        findings.add(Finding(relative, 1, origin + "/media_artifact"))
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        findings.add(Finding(relative, 1, origin + "/binary_artifact"))
        return sorted(findings)
    if "\x00" in content:
        findings.add(Finding(relative, 1, origin + "/binary_artifact"))
    for number, line in enumerate(content.splitlines(), 1):
        for category, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings.add(Finding(relative, number, origin + "/" + category))
        if credential_assignment(line, relative):
            findings.add(Finding(relative, number, origin + "/credential_assignment"))
        if any(value in line.casefold() for value in forbidden):
            findings.add(Finding(relative, number, origin + "/private_text"))
    return sorted(findings)


def git(root: Path, *args: str) -> bytes:
    environment = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR"):
        environment.pop(name, None)
    result = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False, timeout=30, env=environment)
    if result.returncode:
        raise RuntimeError("Git index could not be inspected")
    return result.stdout


def read_index(root: Path) -> dict[str, tuple[str, str]]:
    top = Path(git(root, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
    if os.path.normcase(str(top)) != os.path.normcase(str(root.resolve())):
        raise RuntimeError("Git root does not match candidate root")
    entries = {}
    for record in git(root, "ls-files", "--stage", "-z").split(b"\x00"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split()
        if stage != "0":
            raise RuntimeError("Unmerged index")
        entries[raw_path.decode("utf-8")] = (mode, oid)
    return entries


def scan_tree(root: Path, forbid_text: Iterable[str] = ()) -> list[Finding]:
    try:
        root = root.resolve()
    except (OSError, RuntimeError):
        return [Finding(".", 1, "tree/root_unreadable")]
    forbidden = tuple(forbid_text)
    findings: set[Finding] = set()
    tracked: dict[str, tuple[str, str]] = {}
    if not root.is_dir():
        return [Finding(".", 1, "tree/root_unreadable")]
    # An export inside another repository must not accidentally scan the parent's index.
    if (root / ".git").exists():
        try:
            tracked = read_index(root)
            for relative, (mode, oid) in tracked.items():
                if mode != "100644" and mode != "100755":
                    findings.add(Finding(relative, 1, "index/nonregular_entry"))
                    continue
                size = int(git(root, "cat-file", "-s", oid))
                if size > MAX_BYTES:
                    findings.add(Finding(relative, 1, "index/oversized_unchecked_file"))
                    continue
                findings.update(scan_blob(relative, git(root, "cat-file", "blob", oid),
                                          tracked=True, origin="index", forbid_text=forbidden))
        except (OSError, RuntimeError, ValueError, UnicodeError, subprocess.SubprocessError):
            findings.add(Finding(".", 1, "index/unreadable"))
    candidates = set(tracked)
    def walk_error(_error: OSError) -> None:
        findings.add(Finding(".", 1, "tree/unreadable_directory"))
    for directory, subdirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        base = Path(directory)
        for name in list(subdirs):
            child = base / name
            if excluded_dir(name):
                subdirs.remove(name)
            elif child.is_symlink() or getattr(child, "is_junction", lambda: False)():
                findings.add(Finding(child.relative_to(root).as_posix(), 1, "tree/symlink"))
                subdirs.remove(name)
        candidates.update((base / name).relative_to(root).as_posix() for name in files if name != ".git")
    for relative in sorted(candidates):
        path = root / relative
        try:
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                findings.add(Finding(relative, 1, "tree/symlink"))
                continue
            if not path.resolve().is_relative_to(root):
                findings.add(Finding(relative, 1, "tree/external_path"))
                continue
            # A staged file may have intentionally been removed from the working tree.
            if not path.exists() and relative in tracked:
                continue
            if path.stat().st_size > MAX_BYTES:
                findings.add(Finding(relative, 1, "tree/oversized_unchecked_file"))
                continue
            findings.update(scan_blob(relative, path.read_bytes(), tracked=relative in tracked,
                                      forbid_text=forbidden))
        except (OSError, RuntimeError):
            findings.add(Finding(relative, 1, "tree/unreadable_file"))
    return sorted(findings)


def nonempty(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("forbidden text must not be empty")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--forbid-text", action="append", type=nonempty, default=[],
                        help="local private text to reject; repeat as needed (never saved)")
    args = parser.parse_args(argv)
    findings = scan_tree(args.root, args.forbid_text)
    for finding in findings:
        # Keep output structured and single-line, including unusual Git filenames.
        safe_path = finding.path.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        print(f"{safe_path}:{finding.line}: {finding.category}")
    if not findings:
        print("Public-content check passed.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
