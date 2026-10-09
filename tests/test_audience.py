"""Synthetic audience events only; no provider accounts, network or OBS."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from obs_director.audience import AudienceValidationError, KINDS, build_audience_registry, normalize_audience_event
from obs_director.cues import CueService
from obs_director.events import EventService, EventValidationError, _event, _registry
from test_cues import FakeProduction


def audience(platform="twitch", kind="subscription", **extra):
    return {"platform": platform, "kind": kind, "event_id": "synthetic-001",
            "occurred_at": "2026-01-01T12:00:00Z", "display_name": "Synthetic Viewer", **extra}


class AudienceTests(unittest.TestCase):
    def test_all_supported_kinds_produce_valid_registered_events_offline(self):
        registry = build_audience_registry("Program", "Alert Text", 20, 21)
        self.assertEqual(8, len(registry))
        self.assertEqual(registry, _registry(registry))
        for platform, kind in KINDS:
            extra = {"amount": 5} if kind in ("subscription_gift", "cheer", "raid") else {}
            if kind in ("super_chat", "super_sticker"):
                extra = {"amount": 1.250001, "currency": "USD"}
            value = audience(platform, kind, **extra)
            original = deepcopy(value)
            with patch.object(Path, "open", side_effect=AssertionError("No files")), patch("obs_director.transport.ObsClient.__enter__", side_effect=AssertionError("No OBS")):
                event = normalize_audience_event(value)
            self.assertEqual(event, _event(event))
            self.assertIn(event["event_type"], registry)
            self.assertEqual(value, original)

    def test_platform_namespaces_ids_and_preserves_delivery_deduplication(self):
        first = normalize_audience_event(audience())
        self.assertEqual(first, normalize_audience_event(audience()))
        second = normalize_audience_event(audience("youtube", "membership"))
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertNotIn("synthetic-001", first["event_id"])
        self.assertEqual(first["event_id"], normalize_audience_event(audience(kind="follow"))["event_id"])

    def test_opaque_provider_ids_allow_base64_characters_but_not_urls_or_unbounded_values(self):
        self.assertTrue(normalize_audience_event(audience(event_id="opaque/+_=-id"))["event_id"])
        for value in ("", "x" * 257, "https://example.invalid", "bad\nvalue", True, None, {}, "unicode-\u00e9"):
            with self.subTest(value=value), self.assertRaises(AudienceValidationError):
                normalize_audience_event(audience(event_id=value))

    def test_unknown_fields_actions_and_native_envelopes_are_not_interpreted(self):
        for value in ({}, [], {"subscription": {}, "event": {}}, audience(action="StartStream"),
                      audience(url="https://example.invalid"), audience(platform="other"),
                      audience(platform=[]), audience(kind="subscription.message"),
                      audience("youtube", "subscription"), audience("twitch", "membership")):
            with self.subTest(value=value), self.assertRaises(AudienceValidationError):
                normalize_audience_event(value)

    def test_names_reject_controls_bidi_markup_urls_and_overflow_without_echoing(self):
        for name in ("", " ", "x" * 61, "<script>bad</script>", "https://example.invalid", "www.example.invalid",
                     "line\nfeed", "\x7f", "\x85", "text\u202e", "line\u2028feed", "bad\ud800", 9, None):
            with self.subTest(name=repr(name)), self.assertRaises(AudienceValidationError) as error:
                normalize_audience_event(audience(display_name=name))
            self.assertNotIn("<script>", str(error.exception))
        event = normalize_audience_event(audience(display_name="Caf\u00e9 & Friends \U0001f44b"))
        self.assertIn("Caf\u00e9 & Friends", event["payload"]["summary"])

    def test_time_requires_valid_utc_event_contract(self):
        for value in ("2026-02-30T12:00:00Z", "2026-01-01T12:00:00+00:00", "2026-01-01T12:00:00.123456789Z", "now", None):
            with self.subTest(value=value), self.assertRaises(AudienceValidationError):
                normalize_audience_event(audience(occurred_at=value))

    def test_counts_require_exact_positive_bounded_integers_and_no_currency(self):
        for kind in ("subscription_gift", "cheer", "raid"):
            for value in (True, 0, -1, 1.0, 1_000_001, float("nan"), float("inf"), "2"):
                with self.subTest(kind=kind, value=value), self.assertRaises(AudienceValidationError):
                    normalize_audience_event(audience(kind=kind, amount=value))
            with self.assertRaises(AudienceValidationError):
                normalize_audience_event(audience(kind=kind, amount=2, currency="USD"))
            self.assertIn("1000000", normalize_audience_event(audience(kind=kind, amount=1_000_000))["payload"]["summary"])

    def test_money_is_finite_bounded_with_six_decimal_places_and_explicit_currency(self):
        for kind in ("super_chat", "super_sticker"):
            for amount in (True, 0, -1, 1_000_001, 10**1000, float("nan"), float("inf"), "2", 0.0000001):
                with self.subTest(kind=kind, amount=str(amount)[:20]), self.assertRaises(AudienceValidationError):
                    normalize_audience_event(audience("youtube", kind, amount=amount, currency="USD"))
            for currency in ("usd", "US", "USDD", "$", None, 1):
                with self.subTest(currency=currency), self.assertRaises(AudienceValidationError):
                    normalize_audience_event(audience("youtube", kind, amount=1, currency=currency))
            event = normalize_audience_event(audience("youtube", kind, amount=0.000001, currency="JPY"))
            self.assertIn("0.000001 JPY", event["payload"]["summary"])

    def test_missing_money_or_unexpected_amount_is_rejected(self):
        for value in (audience("youtube", "super_chat"), audience("youtube", "super_chat", amount=1),
                      audience(amount=1), audience("youtube", "membership", amount=1, currency="USD")):
            with self.assertRaises(AudienceValidationError):
                normalize_audience_event(value)

    def test_builder_rejects_bad_names_duplicate_ids_and_wait_bounds(self):
        cases = [("", "Text", 1, 2, 4), ("Program", "<Text>", 1, 2, 4),
                 ("Program", "Text", True, 2, 4), ("Program", "Text", 1, 1, 4),
                 ("Program", "Text", -1, 2, 4), ("Program", "Text", 2**31, 2, 4)]
        cases += [("Program", "Text", 1, 2, duration) for duration in (True, 0, -1, 16, "4", float("nan"), float("inf"), 10**1000)]
        for values in cases:
            with self.subTest(values=str(values)[:100]), self.assertRaises(AudienceValidationError):
                build_audience_registry(*values)

    def test_preview_does_not_write_or_sleep_and_dispatch_shows_then_hides_only_bound_items(self):
        production = FakeProduction()
        registry = build_audience_registry("Program", "Alert Text", 20, 21)
        service = EventService(CueService(production), registry)
        event = normalize_audience_event(audience())
        with patch("obs_director.cues.time.sleep") as sleep:
            self.assertEqual(service.preview_event(event)["state"], "preview")
            self.assertEqual(production.mutations, [])
            sleep.assert_not_called()
            result = service.dispatch_event(event, dry_run=False)
            sleep.assert_called_once_with(4)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(production.mutations[0][1]["source_name"], "Alert Text")
        self.assertIn("Synthetic Viewer", production.mutations[0][1]["settings"]["text"])
        self.assertEqual([(item[1]["scene_item_id"], item[1]["enabled"]) for item in production.mutations[1:]],
                         [(20, True), (21, True), (21, False), (20, False)])
        before = deepcopy(production.mutations)
        self.assertTrue(service.dispatch_event(event, dry_run=False)["duplicate"])
        self.assertEqual(production.mutations, before)

    def test_active_outputs_require_explicit_opt_in_and_partial_failure_consumes_id(self):
        production = FakeProduction()
        production.live = True
        service = EventService(CueService(production), build_audience_registry("Program", "Alert Text", 20, 21))
        event = normalize_audience_event(audience())
        with self.assertRaises(EventValidationError):
            service.dispatch_event(event, dry_run=False)
        self.assertEqual(production.mutations, [])
        production.fail_apply_at = 3
        with patch("obs_director.cues.time.sleep"):
            result = service.dispatch_event(event, dry_run=False, allow_live=True)
        self.assertEqual(result["state"], "failed")
        production.fail_apply_at = None
        self.assertTrue(service.dispatch_event(event, dry_run=False, allow_live=True)["duplicate"])

    def test_registry_routes_are_independent_and_expected_scene_is_enforced(self):
        registry = build_audience_registry("Program", "Alert Text", 20, 21)
        keys = list(registry)
        registry[keys[0]]["cue"]["steps"][0]["parameters"]["settings"]["text"] = "Changed"
        self.assertEqual(registry[keys[1]]["cue"]["steps"][0]["parameters"]["settings"]["text"], "Audience alert")
        production = FakeProduction()
        production.current_scene = "Different scene"
        service = EventService(CueService(production), registry)
        with self.assertRaises(EventValidationError):
            service.dispatch_event(normalize_audience_event(audience()), dry_run=False)
        self.assertEqual(production.mutations, [])


if __name__ == "__main__":
    unittest.main()
