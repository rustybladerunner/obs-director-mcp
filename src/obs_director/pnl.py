"""Format an explicitly labelled P&L snapshot without accessing an account."""
from __future__ import annotations

from datetime import datetime
import re

_KEYS = {"mode", "currency", "realized_minor", "unrealized_minor", "fees_minor", "as_of"}
_CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD"}
_LIMIT = 10**12
_COLORS = {"gain": 0xFFA2CC9A, "loss": 0xFF929AE6, "flat": 0xFF83C9E5}


def _amount(value: int, *, signed: bool = True) -> str:
    sign = ("+" if value >= 0 else "-") if signed else ""
    magnitude = abs(value)
    return f"{sign}{magnitude // 100:,}.{magnitude % 100:02d}"


def render_pnl(snapshot: dict) -> dict:
    """Return text, OBS ABGR color, net_minor and state.

    Inputs are integer hundredths in one supported currency. Realized and open
    P&L exclude the separately supplied fees. Net = realized + open - fees.
    Values are caller-supplied: this function does not verify broker balances,
    fills or freshness. The mode and observation time remain visible.
    """
    if not isinstance(snapshot, dict) or set(snapshot) != _KEYS:
        raise ValueError("P&L requires mode, currency, realized_minor, unrealized_minor, fees_minor and as_of")
    mode, currency = snapshot["mode"], snapshot["currency"]
    if not isinstance(mode, str) or mode not in {"demo", "paper", "live"}:
        raise ValueError("P&L mode must be demo, paper or live")
    if not isinstance(currency, str) or currency not in _CURRENCIES:
        raise ValueError("P&L currency must be USD, EUR, GBP, CAD or AUD")
    for key in ("realized_minor", "unrealized_minor", "fees_minor"):
        value = snapshot[key]
        if type(value) is not int or not -_LIMIT <= value <= _LIMIT:
            raise ValueError(f"{key} must be a bounded integer in hundredths")
    if snapshot["fees_minor"] < 0:
        raise ValueError("fees_minor must be non-negative")
    timestamp = snapshot["as_of"]
    if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?Z", timestamp):
        raise ValueError("as_of must be a UTC timestamp ending in Z")
    try:
        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("as_of must be a valid UTC timestamp") from None
    realized, opened, fees = (snapshot[key] for key in ("realized_minor", "unrealized_minor", "fees_minor"))
    net = realized + opened - fees
    state = "gain" if net > 0 else "loss" if net < 0 else "flat"
    text = (f"{mode.upper()} P&L  {currency} {_amount(net)}\n"
            f"R {_amount(realized)}  U {_amount(opened)}  Fees {_amount(fees, signed=False)}\n"
            f"AS OF {observed.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    return {"text": text, "color": _COLORS[state], "net_minor": net, "state": state,
            "label": f"{mode.upper()} SESSION P&L", "headline": f"{currency} {_amount(net)}",
            "details": f"R {_amount(realized)}  U {_amount(opened)}  Fees {_amount(fees, signed=False)}",
            "as_of": observed.strftime('%Y-%m-%d %H:%M:%S UTC')}
