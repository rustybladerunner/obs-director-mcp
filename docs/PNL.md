# P&L display

The renderer formats caller-supplied values. It does not connect to a broker,
read trades, or verify an account balance. The demo displays simulated values.
Audience support amounts are separate from trading results.

```python
from obs_director.pnl import render_pnl

display = render_pnl({
    "mode": "demo",
    "currency": "USD",
    "realized_minor": 25000,
    "unrealized_minor": 0,
    "fees_minor": 500,
    "as_of": "2026-10-09T00:00:00Z",
})
# display["text"] includes DEMO P&L, USD +245.00 and the observation time.
# display["color"] uses the OBS text source's packed ABGR color format.
```

All three amounts must be integers in hundredths of the declared currency.
Supported currencies are USD, EUR, GBP, CAD and AUD. The absolute input limit is
1,000,000,000,000 minor units per value. Fees must be non-negative. Realized and
unrealized values must exclude those fees: net = realized + unrealized - fees.
Do not supply broker values that already deduct fees without adjusting this
contract. The function does not convert currencies or classify fills.

`mode` is mandatory: `demo`, `paper` or `live`. This label declares the source;
it does not prove that source. The UTC observation time remains visible. It is
not refreshed by this function, and the renderer does not reject old snapshots.
A real feed must handle stale or missing data and provide its actual timestamp.

The result contains `text`, `color`, `net_minor` and `state`. State is `gain`,
`loss` or `flat`. Signed amounts communicate the result without relying on color.
It also returns `label`, `headline`, `details` and `as_of` for separate text
layers. The single-host demo uses a bold 42 px headline, a visible mode label
and smaller detail/time lines. The tournament cards use a 104 px net figure.
Use the text on a dark panel. Large amounts may need a wider panel or smaller font.
The demo shows a gain of USD 245.00 and a loss of USD 85.00 in separate phases.

Send `text` and `color` through `obs_source_settings` for a GDI+ text input.
For a FreeType text input, use the same color for `color1` and `color2`.
As with other production controls, preview first and explicitly opt into changes.
