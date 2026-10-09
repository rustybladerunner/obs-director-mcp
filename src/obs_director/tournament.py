"""Bounded, offline competition snapshots and editable broadcast pages."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata


MAX_AGE_SECONDS = 120
MAX_MINOR = 10**12
CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z")
UTC_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?Z\Z")
SCENES = {"table": "Tournament Table", "standings": "Tournament Standings", "replay": "Tournament Replay",
          "starting-soon": "Tournament Starting Soon", "break": "Tournament Break", "ending": "Tournament Ending"}
STATIC_FILES = (*(name + ".html" for name in SCENES), "broadcast.css", "broadcast.js")


class TournamentValidationError(ValueError):
    """A supplied snapshot or rendering destination is outside the contract."""


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise TournamentValidationError("Object has missing or unsupported fields")


def _text(value, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(unicodedata.category(c).startswith("C") or unicodedata.category(c) in {"Zl", "Zp"} for c in value)):
        raise TournamentValidationError("Text requires a bounded nonempty value without control characters")
    return value


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise TournamentValidationError("Identifier is outside the bounded portable format")
    return value


def _utc(value):
    if not isinstance(value, str) or not UTC_TIME.fullmatch(value):
        raise TournamentValidationError("Timestamp requires an explicit UTC time ending in Z")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise TournamentValidationError("Timestamp is not a valid calendar time") from None


def _now(value):
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TournamentValidationError("Reference time must be an aware datetime")
    return value.astimezone(timezone.utc)


def _minor(value, *, fee=False):
    if value is not None and (type(value) is not int or not (-MAX_MINOR if not fee else 0) <= value <= MAX_MINOR):
        raise TournamentValidationError("Money requires bounded integer minor units; fees cannot be negative")


def validate_snapshot(snapshot: dict, *, now: datetime | None = None) -> dict:
    """Return a detached strict snapshot; this does not authenticate its origin."""
    _fields(snapshot, {"schema", "mode", "currency", "session_id", "label", "as_of", "round", "stage", "participants"})
    if snapshot["schema"] != "obs.tournament.v1":
        raise TournamentValidationError("Unsupported tournament schema")
    if not isinstance(snapshot["mode"], str) or snapshot["mode"] not in {"demo", "paper"}:
        raise TournamentValidationError("Only demo and paper snapshots are supported")
    if not isinstance(snapshot["currency"], str) or snapshot["currency"] not in CURRENCIES:
        raise TournamentValidationError("Currency must be a supported two-decimal currency")
    _identifier(snapshot["session_id"])
    _text(snapshot["label"], 64)
    as_of = _utc(snapshot["as_of"])
    if (as_of - _now(now)).total_seconds() > 5:
        raise TournamentValidationError("Snapshot time is in the future")
    if type(snapshot["round"]) is not int or not 1 <= snapshot["round"] <= 999:
        raise TournamentValidationError("Round must be an integer from 1 to 999")
    if not isinstance(snapshot["stage"], str) or snapshot["stage"] not in {"qualifying", "semifinal", "final"}:
        raise TournamentValidationError("Unsupported competition stage")
    participants = snapshot["participants"]
    if not isinstance(participants, list) or not 2 <= len(participants) <= 8:
        raise TournamentValidationError("A snapshot requires two to eight participants")
    ids = set()
    for participant in participants:
        _fields(participant, {"id", "name", "realized_minor", "unrealized_minor", "fees_minor", "status", "observed_at"}, {"trade"})
        identifier = _identifier(participant["id"])
        if identifier in ids:
            raise TournamentValidationError("Participant identifiers must be unique")
        ids.add(identifier)
        _text(participant["name"], 32)
        for field in ("realized_minor", "unrealized_minor", "fees_minor"):
            _minor(participant[field], fee=field == "fees_minor")
        if not isinstance(participant["status"], str) or participant["status"] not in {"active", "paused", "complete"}:
            raise TournamentValidationError("Unsupported participant status")
        observed = participant["observed_at"]
        if observed is not None and _utc(observed) > as_of:
            raise TournamentValidationError("Participant time cannot follow snapshot time")
        if "trade" in participant:
            trade = participant["trade"]
            _fields(trade, {"instrument", "direction", "entry", "stop", "target"})
            _text(trade["instrument"], 24)
            if not isinstance(trade["direction"], str) or trade["direction"] not in {"long", "short", "flat"}:
                raise TournamentValidationError("Unsupported trade direction")
            for field in ("entry", "stop", "target"):
                value = trade[field]
                if type(value) not in (int, float) or not -1_000_000_000 <= value <= 1_000_000_000:
                    raise TournamentValidationError("Trade levels require finite bounded points")
            if trade["direction"] == "long" and not trade["stop"] < trade["entry"] < trade["target"]:
                raise TournamentValidationError("Long levels require stop below entry below target")
            if trade["direction"] == "short" and not trade["target"] < trade["entry"] < trade["stop"]:
                raise TournamentValidationError("Short levels require target below entry below stop")
    return deepcopy(snapshot)


def rank_snapshot(snapshot: dict, *, now: datetime | None = None) -> dict:
    """Rank complete, fresh same-session net P&L with competition-rank ties."""
    clock = _now(now)
    validated = validate_snapshot(snapshot, now=clock)
    global_stale = (clock - _utc(validated["as_of"])).total_seconds() > MAX_AGE_SECONDS
    rows = []
    for participant in validated["participants"]:
        row = deepcopy(participant)
        missing = participant["observed_at"] is None or any(participant[key] is None for key in ("realized_minor", "unrealized_minor", "fees_minor"))
        stale = global_stale or (not missing and (clock - _utc(participant["observed_at"])).total_seconds() > MAX_AGE_SECONDS)
        row["eligibility"] = "missing" if missing else "stale" if stale else "ranked"
        row["net_minor"] = None if missing else participant["realized_minor"] + participant["unrealized_minor"] - participant["fees_minor"]
        row["rank"] = None
        row["tied"] = False
        rows.append(row)
    eligible = sorted((row for row in rows if row["eligibility"] == "ranked"), key=lambda row: (-row["net_minor"], row["id"]))
    counts = {}
    previous = None
    rank = 0
    for index, row in enumerate(eligible, 1):
        if row["net_minor"] != previous:
            rank = index
        row["rank"] = rank
        previous = row["net_minor"]
        counts[rank] = counts.get(rank, 0) + 1
    for row in eligible:
        row["tied"] = counts[row["rank"]] > 1
    ordered = eligible + sorted((row for row in rows if row["rank"] is None), key=lambda row: row["id"])
    return {"snapshot": validated, "participants": rows, "standings": ordered,
            "ranked_count": len(eligible), "as_of": validated["as_of"], "max_age_seconds": MAX_AGE_SECONDS,
            "ranking_basis": "realized + unrealized - fees; same session and currency"}


def demo_snapshot(*, now: datetime | None = None) -> dict:
    """Create clearly synthetic values with their actual creation timestamp."""
    timestamp = _now(now).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {"schema": "obs.tournament.v1", "mode": "demo", "currency": "USD", "session_id": "synthetic-final-01",
            "label": "The closing session", "as_of": timestamp, "round": 3, "stage": "final",
            "participants": [
                {"id": "piphound", "name": "PIPHOUND", "realized_minor": 1248000, "unrealized_minor": 21750,
                 "fees_minor": 1250, "status": "active", "observed_at": timestamp,
                 "trade": {"instrument": "DEMO / POINTS", "direction": "long", "entry": 5124.25, "stop": 5118.0, "target": 5142.0}},
                {"id": "housebot", "name": "HOUSEBOT", "realized_minor": 934000, "unrealized_minor": -8400,
                 "fees_minor": 1050, "status": "active", "observed_at": timestamp,
                 "trade": {"instrument": "DEMO / POINTS", "direction": "short", "entry": 5131.0, "stop": 5137.25, "target": 5114.0}},
            ]}


def read_snapshot(path: Path) -> dict:
    """Read bounded JSON and reject duplicate keys and nonfinite JSON values."""
    path = _local_path(path)
    with path.open("rb") as stream:
        data = stream.read(64 * 1024 + 1)
    if len(data) > 64 * 1024:
        raise TournamentValidationError("Snapshot exceeds 64 KiB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise TournamentValidationError("Snapshot has duplicate JSON keys")
            result[key] = value
        return result
    def nonfinite(_):
        raise TournamentValidationError("Nonfinite JSON values are not supported")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise TournamentValidationError("Snapshot is not valid bounded JSON") from None


def _safe_script(value):
    # HTML script elements are terminated by </script> even for JSON MIME types.
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _local_path(path):
    path = Path(path).absolute()
    if str(path).startswith(("\\\\", "//")) or ".." in path.parts:
        raise TournamentValidationError("Paths must be local and cannot traverse parents")
    for part in (path, *path.parents):
        if part.exists() and (part.is_symlink() or bool(getattr(part.lstat(), "st_file_attributes", 0) & 0x400)):
            raise TournamentValidationError("Symlinks and reparse points are not supported")
    return path


def render_tournament(snapshot: dict, output: Path, *, template_dir: Path | None = None,
                      portrait_path: Path | None = None, now: datetime | None = None) -> dict:
    """Write a new editable local scene bundle after full validation.

    The default artwork path is source-checkout relative. Installed callers can
    pass template_dir and portrait_path explicitly. No OBS or network is used.
    """
    clock = _now(now)
    ranked = rank_snapshot(snapshot, now=clock)
    destination = _local_path(output)
    if destination.exists() or not destination.parent.is_dir():
        raise TournamentValidationError("Output must be a new directory with an existing parent")
    source = _local_path(template_dir or Path(__file__).resolve().parents[2] / "examples" / "tournament")
    prepared = {}
    payload = {"snapshot": ranked["snapshot"], "max_age_seconds": MAX_AGE_SECONDS, "rendered_at": clock.isoformat(),
               "portrait": portrait_path is not None}
    encoded = _safe_script(payload)
    for filename in STATIC_FILES:
        path = _local_path(source / filename)
        if not path.is_file() or path.stat().st_size > 128 * 1024:
            raise TournamentValidationError("A required local template is missing or too large")
        content = path.read_text(encoding="utf-8")
        if filename.endswith(".html"):
            if content.count("__TOURNAMENT_DATA__") != 1:
                raise TournamentValidationError("HTML template requires exactly one data marker")
            content = content.replace("__TOURNAMENT_DATA__", encoded)
        prepared[filename] = content.encode("utf-8")
    if portrait_path is not None:
        portrait = _local_path(portrait_path)
        if not portrait.is_file() or portrait.stat().st_size > 8 * 1024 * 1024:
            raise TournamentValidationError("Portrait must be a bounded local PNG")
        data = portrait.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise TournamentValidationError("Portrait must be a local PNG")
        prepared["presenter.png"] = data
    prepared["snapshot.json"] = (json.dumps(ranked["snapshot"], indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    manifest = {"schema": "obs.tournament-scenes.v1", "session_id": ranked["snapshot"]["session_id"],
                "mode": ranked["snapshot"]["mode"], "canvas": {"width": 1920, "height": 1080},
                "scenes": [{"id": key, "name": name, "file": key + ".html"} for key, name in SCENES.items()],
                "hashes": {key: hashlib.sha256(value).hexdigest() for key, value in prepared.items()}}
    prepared["scenes.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    created = []
    destination.mkdir()
    try:
        for name, contents in prepared.items():
            target = destination / name
            with target.open("xb") as stream:
                created.append(target)
                stream.write(contents)
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        destination.rmdir()
        raise
    return manifest
