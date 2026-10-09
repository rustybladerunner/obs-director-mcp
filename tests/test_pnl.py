from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from obs_director.pnl import render_pnl


class PnlTests(unittest.TestCase):
    def snapshot(self, **changes):
        return {"mode": "demo", "currency": "USD", "realized_minor": 25000,
                "unrealized_minor": -1000, "fees_minor": 500,
                "as_of": "2026-10-09T00:00:00Z", **changes}

    def test_net_deducts_fees_once_and_keeps_mode_time_visible(self):
        result = render_pnl(self.snapshot())
        self.assertEqual(result["net_minor"], 23500)
        self.assertEqual(result["state"], "gain")
        self.assertIn("DEMO P&L  USD +235.00", result["text"])
        self.assertIn("R +250.00  U -10.00  Fees 5.00", result["text"])
        self.assertIn("AS OF 2026-10-09 00:00:00 UTC", result["text"])
        self.assertEqual(result["color"], 0xFFA2CC9A)

    def test_gain_loss_and_flat_have_signed_amount_and_different_colors(self):
        results = [render_pnl(self.snapshot(realized_minor=x, unrealized_minor=0, fees_minor=0)) for x in (1, -1, 0)]
        self.assertEqual([r["state"] for r in results], ["gain", "loss", "flat"])
        self.assertEqual(len({r["color"] for r in results}), 3)
        for result, amount in zip(results, ("+0.01", "-0.01", "+0.00")):
            self.assertIn(amount, result["text"])

    def test_integer_arithmetic_at_boundaries(self):
        result = render_pnl(self.snapshot(realized_minor=10**12, unrealized_minor=10**12, fees_minor=1))
        self.assertEqual(result["net_minor"], 1999999999999)
        self.assertIn("+19,999,999,999.99", result["text"])

    def test_declared_mode_and_supported_currencies(self):
        for mode in ("demo", "paper", "live"):
            for currency in ("USD", "EUR", "GBP", "CAD", "AUD"):
                self.assertIn(f"{mode.upper()} P&L  {currency}", render_pnl(self.snapshot(mode=mode, currency=currency))["text"])

    def test_rejects_ambiguous_or_unbounded_inputs(self):
        for field, values in {
            "mode": (None, "", "real", [], "LIVE"),
            "currency": (None, "JPY", "usd", [], "USD\nHIDE"),
            "realized_minor": (True, 1.5, "100", 10**12 + 1),
            "unrealized_minor": (False, float("nan"), -10**12 - 1),
            "fees_minor": (-1, True, 0.0),
            "as_of": (None, "2026-02-30T00:00:00Z", "2026-10-09T24:00:00Z", "2026-10-09", "2026-10-09T00:00:00+00:00"),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    render_pnl(self.snapshot(**{field: value}))

    def test_rejects_missing_unknown_fields_and_nonobjects(self):
        for value in (None, [], {}, {**self.snapshot(), "source_name": "arbitrary"}):
            with self.assertRaises(ValueError):
                render_pnl(value)
        for key in self.snapshot():
            missing = self.snapshot()
            del missing[key]
            with self.assertRaises(ValueError):
                render_pnl(missing)


if __name__ == "__main__":
    unittest.main()
