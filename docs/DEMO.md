# Event-driven production demo

This demo turns one synthetic event into an evidence card, a chart spotlight, and a local replay.
The chart contains fixed samples in arbitrary units.
It contains no market, account, or personal data.

The model or local producer chooses an event.
The local route defines the OBS actions.
The cue runtime validates the complete sequence, then controls its order and timing.
The demo does not establish adoption by an external event producer.

## Run the isolated rehearsal

Use the installed package dependencies and an authenticated local OBS WebSocket connection.
Keep all existing OBS outputs inactive before rehearsal.
The rehearsal refuses an active recording, stream, virtual camera, or replay buffer.

Preview the rehearsal:

```sh
python tools/rehearse.py --output runtime/rehearsal
```

Execute the rehearsal:

```sh
python tools/rehearse.py --output runtime/rehearsal --execute
```

Use a new output directory for each execution.
The script creates an isolated scene collection and profile.
It records a local seed clip, runs the event cue, and captures local evidence.
It then restores the previous collection and profile.
It keeps the created rehearsal profile and collection for inspection.
It does not start a public stream.
Keep the output directory outside the public export.

Inspect the saved recording and screenshots after execution.
A successful receipt proves the OBS state checks, not the quality of the rendered image or audio.
The script records failures and attempts restoration when an operation fails.
OBS profile selection can finish after the command response.
The script waits for the requested selection before further configuration.
If a recording start remains uncertain, inspect OBS before another recording.

## Scene layout

The canvas and browser source use 1280 × 720 pixels.
The browser source loads [chart.html](../examples/demo/chart.html) and its adjacent CSS and JavaScript files.
The page has no network requests, fonts, binary assets, or event dispatcher.
Its moving marker shows a fixed synthetic animation.
The marker does not cause the evidence card or replay to appear.

| Item | OBS source | Initial state |
| --- | --- | --- |
| Scene | `Director Demo` | Current program scene |
| Browser input | `Demo Chart` | Visible; x 0, y 0, scale 1 |
| Text input | `Demo Evidence` | Hidden; x 64, y 548; 660 × 132 text extent |
| Media input | `Demo Replay` | Hidden; local seed clip ready for restart |

Use a 20-pixel text font for the supplied short fixture.
Place the replay at x 760, y 380, with a width of 480 pixels.
Place the evidence and replay sources above the browser source in the scene order.
The local route uses the actual scene item IDs returned by OBS.

| Time after cue start | Action |
| --- | --- |
| 0 seconds | Set the evidence text and show its source |
| 2 seconds | Set the chart to x −48, y −65, scale 1.12 |
| 5 seconds | Show the replay source, then restart its local clip |
| 9 seconds | Hide the replay and restore the chart transform |

The evidence card remains visible after the cue.
An OBS FFmpeg media source must be showing before its restart command.
The server waits for this state before it sends the command.
The times describe explicit waits.
Network latency and OBS processing can extend elapsed time.
An unverified step stops the cue and leaves later steps skipped.
Earlier changes are not rolled back.

## Register a trusted route

The server starts with no event routes by default.
Set `OBS_MCP_EVENT_ROUTES` to an absolute local JSON file before starting the server.
The route file must contain this envelope:

```json
{
  "schema": "obs.event-routes.v1",
  "routes": {
    "evidence.ready": {
      "text_step": 0,
      "cue": {
        "name": "Show an evidence card",
        "expected_scene": "Director Demo",
        "steps": [
          {
            "action": "source_settings",
            "parameters": {
              "source_name": "Demo Evidence",
              "settings": {"text": "Replaced by the event payload"}
            }
          }
        ]
      }
    }
  }
}
```

`text_step` is optional and uses a zero-based index.
It must select a text-only `source_settings` step.
The adapter writes the event title, summary, and reference to that registered text source.
It does not substitute event data into scene names, item IDs, paths, or actions.

Use `build_demo_registry(chart_item_id, evidence_item_id, replay_item_id)` from `obs_director.events` for the complete demo route.
Pass the actual IDs from the `Director Demo` scene.
Place the returned dictionary in the envelope's `routes` field.
The rehearsal performs these steps automatically.

Route files are limited to 128 KiB and 16 routes.
Each cue remains limited to 20 steps and 15 seconds of explicit waits.
Routes cannot contain arbitrary OBS requests, scripts, or output lifecycle actions.

## Event contract

Use [evidence-ready.json](../examples/demo/evidence-ready.json) as the synthetic fixture.
Each event accepts exactly these fields:

| Field | Requirement |
| --- | --- |
| `schema` | Exactly `obs.event.v1` |
| `event_id` | 1–96 identifier characters; stable for the same producer event |
| `event_type` | A registered type; the demo uses `evidence.ready` |
| `occurred_at` | Valid UTC timestamp ending in `Z` |
| `payload.title` | 1–80 characters |
| `payload.summary` | 1–240 characters |
| `payload.reference` | 1–96 characters |

Identifiers start with a letter or digit.
They can then contain letters, digits, periods, underscores, colons, and hyphens.
Text fields cannot contain control characters.
The event timestamp is provenance, not a scheduling command or freshness guarantee.
The producer must enforce its own freshness policy.

Call `obs_preview_event` with the event to validate the route against OBS.
Call `obs_dispatch_event` with `dry_run=false` to execute it.
Both tools require `allow_live=true` when a recording or stream is active.
Preview is the default and does not consume the event ID.
Receipts omit the payload text.

## Duplicate delivery and failures

The adapter stores consumed event IDs in the current process.
It consumes an ID after successful preflight, before the execution attempt.
Completed, failed, and uncertain attempts remain consumed.
A duplicate returns its previous state and sends no OBS actions.
Reuse of an ID with different content is rejected.

The default capacity is 1,024 consumed IDs.
The adapter refuses new executions when the store is full.
It does not evict IDs to permit old events to replay.
Library callers can select a capacity from 1 to 4,096.

A process restart clears this store.
A durable producer must keep its own attempt ledger across restarts.
The producer must inspect uncertain outcomes before it issues another event.
Changing an ID to bypass duplicate handling can replay an action.

For an existing data feed, establish an initial baseline before emitting events.
Emit only new validated transitions with stable source identities.
Suppress stale, future-dated, unconfirmed, and repeated observations.
Keep private adapters, route names, and source data outside this public repository.

## Verification boundary

The event tests exercise schema rejection, complete preflight, duplicate delivery, concurrency, capacity, live opt-in, and partial failures.
The chart JavaScript has a standalone syntax check.
Browser pixels and real OBS recordings require separate inspection.
Synthetic rehearsal acceptance does not establish a real producer connection or continuous operation.
