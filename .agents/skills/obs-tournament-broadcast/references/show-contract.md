# Show contract

Fill the relevant rows before deriving graphics or standings. This is a design
worksheet, not a new runtime event schema or an account connector.

| Decision | Record |
| --- | --- |
| Show | Presentation or competition; intended viewer and display size |
| Participants | Stable seat IDs, displayed names, eligibility, supplied portraits |
| Basis | Round start/end, comparable starting basis, currency or score units |
| Score | Exact metric, realized/unrealized treatment, fee inclusion, ties |
| Rules | Permitted actions, limits, elimination/finalization authority, corrections |
| Data | Producer for each field, observation time, receipt time, mode, freshness limit |
| Unknown state | Missing, stale, delayed, disconnected; whether ranking is withheld |
| Display | Main action, seats, shared board, result, risk and alert reservations |
| Motion | Trigger, destination, cut/dissolve/stinger, reveal point, audio, fallback |
| Proof | Synthetic cases, actual captures, watched intervals, unresolved requirements |

For trading P&L, preserve integer minor units and the documented fee convention
from [P&L display](../../../../docs/PNL.md). If the source already supplies net
values, reconcile its meaning before calling the formatter. If any component is
missing, display the missing state instead of passing zero to obtain a number.

For an actual competition, define late-event and correction handling before the
final board. A last-known score can remain visible with its age, but must not look
like a confirmed current leader. Do not silently replace a finalized result with
new data or discard an adverse result because it arrived late.

Example boundary: two fictional seats report USD +120 and EUR +130. One feed has
exceeded the agreed freshness limit. Without an agreed conversion/scoring rule,
show currency-labelled rows in seat order, mark the stale row, and withhold rank.
Do not infer the winner from the larger raw number. Fixtures stay visibly `DEMO`.
