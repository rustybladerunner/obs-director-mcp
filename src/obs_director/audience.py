"""Pure audience projections and trusted alert cues; no provider connections."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import re
import unicodedata
from typing import Any

from .events import EventValidationError, _event, _registry


COMMON_FIELDS = {"platform", "kind", "event_id", "occurred_at", "display_name"}
MAX_AMOUNT = 1_000_000
PLATFORM_LABELS = {"twitch": "Twitch", "youtube": "YouTube"}
KINDS = {
    ("twitch", "subscription"): ("Subscription", "subscription support"),
    ("twitch", "subscription_gift"): ("Gift subscriptions", "gift subscriptions"),
    ("twitch", "cheer"): ("Cheer", "Bits"),
    ("twitch", "raid"): ("Raid", "viewers"),
    ("twitch", "follow"): ("Follow", "follow"),
    ("youtube", "membership"): ("Membership", "membership support"),
    ("youtube", "super_chat"): ("Super Chat", "Super Chat"),
    ("youtube", "super_sticker"): ("Super Sticker", "Super Sticker"),
}
COUNT_KINDS = {("twitch", kind) for kind in ("subscription_gift", "cheer", "raid")}
MONEY_KINDS = {("youtube", kind) for kind in ("super_chat", "super_sticker")}
OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+=/-]{0,255}\Z")


class AudienceValidationError(EventValidationError):
    """An audience projection is unsupported or outside its local bounds."""


def _plain(value: Any, limit: int, field: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(unicodedata.category(char).startswith("C") or unicodedata.category(char) in {"Zl", "Zp"}
                   for char in value)
            or "<" in value or ">" in value or "://" in value or "www." in value.casefold()):
        raise AudienceValidationError(f"{field} requires bounded plain text without controls, markup or URLs")
    return value


def normalize_audience_event(platform_event: dict) -> dict:
    """Map one caller-supplied projection to obs.event.v1 without any I/O.

    This validates shape, not origin or authenticity. Monetary amounts use major
    currency units with at most six decimal places. Count amounts are integers.
    """
    if not isinstance(platform_event, dict):
        raise AudienceValidationError("Audience event must be an object")
    platform, kind = platform_event.get("platform"), platform_event.get("kind")
    if not isinstance(platform, str) or not isinstance(kind, str) or (platform, kind) not in KINDS:
        raise AudienceValidationError("Unsupported audience platform or kind")
    identity = (platform, kind)
    fields = COMMON_FIELDS | ({"amount", "currency"} if identity in MONEY_KINDS else
                              {"amount"} if identity in COUNT_KINDS else set())
    if set(platform_event) != fields:
        raise AudienceValidationError("Audience event has missing or unsupported fields")
    raw_id = platform_event["event_id"]
    if not isinstance(raw_id, str) or not OPAQUE_ID.fullmatch(raw_id) or "://" in raw_id:
        raise AudienceValidationError("event_id must be a bounded opaque provider identifier")
    name = _plain(platform_event["display_name"], 60, "display_name")
    title, label = KINDS[identity]
    if identity in COUNT_KINDS:
        amount = platform_event["amount"]
        if type(amount) is not int or not 1 <= amount <= MAX_AMOUNT:
            raise AudienceValidationError("Count amount must be an integer from 1 to 1000000")
        summary = f"{name}: {amount} {label}"
    elif identity in MONEY_KINDS:
        amount, currency = platform_event["amount"], platform_event["currency"]
        if type(amount) not in (int, float) or not 0 < amount <= MAX_AMOUNT:
            raise AudienceValidationError("Money amount must be finite, positive and at most 1000000")
        decimal = Decimal(str(amount))
        if decimal.as_tuple().exponent < -6:
            raise AudienceValidationError("Money amount cannot exceed six decimal places")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise AudienceValidationError("currency must be a three-letter uppercase code")
        # Check syntax only; this module does not keep a currency registry or rates.
        summary = f"{name}: {format(decimal, 'f')} {currency} {label}"
    else:
        summary = f"{name}: {label}"
    event_id = "audience:" + platform + ":" + hashlib.sha256(raw_id.encode("ascii")).hexdigest()
    try:
        return _event({"schema": "obs.event.v1", "event_id": event_id,
            "event_type": "audience." + platform + "." + kind,
            "occurred_at": platform_event["occurred_at"],
            "payload": {"title": PLATFORM_LABELS[platform] + " / " + title, "summary": summary,
                        "reference": PLATFORM_LABELS[platform] + " audience event"}})
    except EventValidationError:
        raise AudienceValidationError("Audience event does not satisfy the bounded event contract") from None


def build_audience_registry(scene_name: str, text_source: str, panel_item_id: int,
                            text_item_id: int, duration_seconds: float = 4) -> dict:
    """Bind eight alert routes to explicit existing scene items and one text source.

    Callers must provide dedicated overlay items. A failed cue can leave these
    items visible; EventService retains the consumed ID and never retries it.
    """
    scene = _plain(scene_name, 200, "scene_name")
    source = _plain(text_source, 200, "text_source")
    if (any(type(item) is not int or not 0 <= item <= 2**31 - 1 for item in (panel_item_id, text_item_id))
            or panel_item_id == text_item_id):
        raise AudienceValidationError("Panel and text require distinct non-negative scene item IDs")
    if type(duration_seconds) not in (int, float) or not 0.1 <= duration_seconds <= 15:
        raise AudienceValidationError("Alert duration must be finite and between 0.1 and 15 seconds")

    def visible(item, enabled):
        return {"action": "source_visibility", "parameters": {
            "scene_name": scene, "scene_item_id": item, "enabled": enabled}}

    return _registry({"audience." + platform + "." + kind: {"text_step": 0, "cue": {
        "name": PLATFORM_LABELS[platform] + " " + title + " alert", "expected_scene": scene,
        "steps": [
            {"action": "source_settings", "parameters": {"source_name": source, "settings": {"text": "Audience alert"}}},
            visible(panel_item_id, True), visible(text_item_id, True),
            {"action": "wait", "seconds": duration_seconds},
            visible(text_item_id, False), visible(panel_item_id, False),
        ]}} for (platform, kind), (title, _) in KINDS.items()})
