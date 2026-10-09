# Tournament broadcast scenes

The offline renderer creates six original scenes from one validated competition snapshot.
The pages use the Funded Desk dark palette and a 1920x1080 canvas.
They scale to 1280x720 without changing the layout.
Large opposing trader cards make each participant's net P&L the main result.

The design adapts information groups from [inspected tournament broadcasts](TOURNAMENT-REFERENCES.md).
It does not copy broadcaster artwork, logos, footage, or branding.
The supplied chart is a static illustrative path. It is not a price feed or execution record.

## Generate the pages

From the source checkout, make sure the output parent directory exists.
For the synthetic example, run:

```console
python tools/render_tournament.py --demo --output runtime/tournament-demo
```

For a supplied local snapshot, run:

```console
python tools/render_tournament.py --snapshot snapshot.json --output runtime/tournament-paper
```

The output directory must be new. The renderer validates inputs and reads all required assets before writing files.
The command never starts OBS, a stream, a provider connection, or a web server.
The `--demo` option creates synthetic PIPHOUND and HOUSEBOT values with the current creation time.
It copies the original local PipHound image. Supplied snapshots use participant initials by default.

The output contains editable HTML, `broadcast.css`, `broadcast.js`, `snapshot.json`, and `scenes.json`.
The pages embed their snapshot. After a data change, generate a new bundle.
The renderer does not monitor the JSON file or poll a provider.
All page assets are local. The Content Security Policy disables network connections.

## Scenes

| ID | Suggested OBS name | Page | Purpose |
| --- | --- | --- | --- |
| `table` | Tournament Table | `table.html` | Shared chart and opposing cards with large individual net P&L. |
| `standings` | Tournament Standings | `standings.html` | All participants, component values, net P&L, and shared ranks. |
| `replay` | Tournament Replay | `replay.html` | Clearly marked illustrative replay; not a recording or execution. |
| `starting-soon` | Tournament Starting Soon | `starting-soon.html` | Session title and presenter, without a fabricated countdown. |
| `break` | Tournament Break | `break.html` | Short-break scene without a predicted return time. |
| `ending` | Tournament Ending | `ending.html` | Thanks message and the latest supplied result, with freshness treatment. |

The table scene features the first two participants in the input array.
The standings scene includes all participants.
The manifest supplies portable relative filenames, a canvas size, and SHA256 hashes.
Scene names are suggestions. The renderer does not create OBS scenes or transitions.

The ending scene labels results final only when every participant is complete and fresh.
It does not authenticate the values or declare an independent winner.
Tied leaders remain tied. Stale or missing participants cannot contribute a ranked result.

## Snapshot contract

The top-level object has exactly these fields:

| Field | Required value |
| --- | --- |
| `schema` | `obs.tournament.v1` |
| `mode` | `demo` or `paper`; live-money mode is unsupported. |
| `currency` | `USD`, `EUR`, `GBP`, `CAD`, or `AUD`; all use two decimal places here. |
| `session_id` | 1–48 ASCII letters, digits, underscores, or hyphens; first character alphanumeric. |
| `label` | 1–64 nonblank characters, without Unicode control characters. |
| `as_of` | Valid UTC ISO timestamp ending in `Z`, with at most six fractional digits. |
| `round` | Integer from 1 to 999; booleans are invalid. |
| `stage` | `qualifying`, `semifinal`, or `final`. |
| `participants` | Array of 2–8 participant objects with unique IDs. |

Each participant requires `id`, `name`, `realized_minor`, `unrealized_minor`, `fees_minor`, `status`, and `observed_at`.
The ID follows the session ID format. The display name permits 1–32 nonblank characters without controls.
The status is `active`, `paused`, or `complete`.
Each money field is an integer minor-unit amount or `null` for unavailable data.
The absolute bound is 1,000,000,000,000 minor units per field. Fees must be nonnegative.
Realized and unrealized amounts exclude the separate fee amount.

`observed_at` is a UTC timestamp or `null`.
It cannot follow `as_of`. A snapshot more than five seconds into the future is rejected.
Unknown fields, duplicate JSON keys, mixed participant currencies, and mixed session fields are rejected.
Native provider messages are not accepted directly.

An optional `trade` object has exactly `instrument`, `direction`, `entry`, `stop`, and `target`.
The instrument is a bounded display label. Direction is `long`, `short`, or `flat`.
Levels use declared points, not money. Each level must be finite and within plus or minus 1,000,000,000 points.
For long cards, stop is below entry and entry is below target. Short cards reverse that order.
These cards describe supplied levels. They do not place orders or prove a fill.

The [synthetic JSON fixture](../examples/tournament/synthetic-snapshot.json) shows the complete shape.
Its fixed timestamp is deliberately historical; do not treat it as a current observation.

## Ranking and freshness

Net P&L equals realized P&L plus unrealized P&L minus fees.
This is an absolute same-session result metric. It is not a risk-adjusted performance score.
The interface never sums participant results into an unlabeled pot.
The shared center panel states the formula and declared point units.

Complete values rank from highest to lowest net P&L.
Equal values share a rank: 1, 1, 3.
Participant ID controls tied row order but does not break a tie.
Missing values stay unranked; zero remains a known value.
Values older than 120 seconds stay unranked.
Freshness applies to both the snapshot time and the participant observation time.
The browser reevaluates freshness every second using the local clock.
It does not refresh the data or rewrite timestamps.

Validation proves shape and bounds. It does not prove provider authenticity, account ownership, fills, or trading performance.

## Library calls and validation

`validate_snapshot(snapshot, now=None)` returns a detached normalized snapshot.
`rank_snapshot(snapshot, now=None)` returns participant rows, standings, ranks, and eligibility reasons.
`render_tournament(snapshot, output, template_dir=None, portrait_path=None, now=None)` writes a new local bundle.
An explicit reference time is an aware `datetime`; it exists for deterministic tests and offline analysis.
The browser still evaluates freshness against its actual clock.

Default template paths assume a source checkout.
Installed callers must provide a trusted local `template_dir` containing the HTML, CSS, and JavaScript from `examples/tournament`.
Templates are executable local presentation code. Do not load untrusted third-party template directories.
The optional portrait is a local PNG. Output and source paths reject symlinks and Windows reparse points.

Run the affected offline checks:

```console
python -m unittest discover -s tests -p test_tournament.py -v
python tools/public_check.py
```

The tests cover malformed inputs, missing/stale values, shared ranks, fee arithmetic, safe JSON embedding, portable exports, and Python/JavaScript parity.
Node is optional for the parity test; a missing executable produces an explicit skip.
Unit tests do not establish visual acceptance. Inspect generated pages and capture the real OBS composition before claiming that result.
