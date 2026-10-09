# Neutral broadcast starter

The starter provides six editable scenes in charcoal and silver. It includes no presenter artwork, channel identity, network font, or external connection.

The separate [PipHound showcase](SHOW-DEMO.md) remains available. Both templates use the same snapshot contract and result calculations.

![Neutral starter with synthetic session results](../examples/starter/preview.png)

This preview was captured from the supplied two-participant demo in OBS.

## Create a local bundle

Run the renderer from the repository root. First, create a local output parent directory, such as `runtime`.

Use `python tools/render_tournament.py --demo --output runtime/my-starter` for the neutral starter. The output directory must not exist.

Use `python tools/render_tournament.py --demo --template piphound --output runtime/my-showcase` for the illustrated showcase.

The default `--template` value is `starter`. An explicit `--template starter` produces the same template.

The starter fixture identifies its participants as HOST and GUEST. Its values are synthetic, and its timestamps record creation time.

For your own data, replace `--demo` with `--snapshot path/to/snapshot.json`. The renderer preserves supplied identities and timestamps; template selection does not anonymize data.

Rendering creates local files only. It does not open OBS, start a server, connect a feed, or start an output.

## Scene map

| File | Purpose |
| --- | --- |
| `starting-soon.html` | Waiting state with a typographic presenter identity; no countdown |
| `table.html` | Shared illustrative chart and two prominent participant results |
| `standings.html` | Two-person comparison or a table for three to eight participants |
| `replay.html` | Clearly marked illustrative review, with the reference timestamp |
| `break.html` | Short-break card without a promised return time |
| `ending.html` | End card with the latest eligible result and provisional/final treatment |

Each page uses a 1920×1080 composition that scales to the browser viewport. Set an OBS Browser Source to that size for a 16:9 output.

`scenes.json` lists the six pages and content hashes. `snapshot.json` preserves the supplied data. CSS and JavaScript are local, editable files.

Select the output page as a local Browser Source only within an authorized OBS workflow. Rendering alone does not prove OBS appearance.

To review an offline rehearsal plan, run:

```sh
python tools/tournament_demo.py --assets-directory runtime/my-starter --output runtime/starter-take
```

Add `--execute` only when OBS is idle and you want the local rehearsal. It uses
short dissolves, no mascot stinger and no music. The helper sends only to its
fixed loopback receiver and restores the original OBS selection. The
[show rehearsal](SHOW-DEMO.md) describes the prerequisites and saved evidence.

## Edit the presentation

Change `YOUR CHANNEL` and `SESSION DESK` in the generated `broadcast.js` to select your own identity. Change color variables in `broadcast.css` for your palette.

The neutral starter uses initials instead of image assets. It has no dependency on the showcase portrait or its artwork.

Keep mode, currency, fees, source time, and unavailable-data labels visible. Read the [tournament contract](TOURNAMENT.md) before changing result semantics.

Net P&L equals realized plus unrealized minus fees. Equal net results share a rank. Missing or stale values remain unranked.

The chart and replay are illustrative. The renderer has no market-series input or recorded-execution replay connection.

## Code and verification boundary

The library accepts `template_dir=Path("examples/starter")` with the existing `render_tournament` function. Its library default remains the original tournament template.

The showcase renderer currently embeds its brand. The starter therefore keeps a separate presentation file without changing the core renderer.

`parseUTC`, `rankRows`, `formatMoney`, and their exports match the showcase exactly. Tests reject helper drift and compare browser rankings with Python.

When changing those helpers, update both templates together. Keep their timestamp, freshness, fee, and tie behavior identical.

Automated tests cover exports, hashes, neutral assets, escaping, template selection, overwrite refusal, and ranking parity. Real OBS appearance requires a separate capture review.
