# Pack break show

A pack break is a live show where the host opens trading-card packs on camera.
This page covers three ready-made layouts, six show moves, and two event routes.
All sample text is generic. The files contain no stream keys or personal paths.

Nothing here starts a stream. Every cue needs `dry_run=false` to change OBS.
If recording or streaming is active, it also needs `allow_live=true`.

## Layouts

Copy a layout with `obs-director-template add NAME --output ./DIR --bind KEY="Source name" ...`.
Each layout has one dark theme and uses flat colors only, so it ships no artwork.
The [template guide](TEMPLATES.md) explains copying, previewing and applying.

| Template | Canvas | Scene it creates | Bindings |
| --- | --- | --- | --- |
| `pack-break-table` | 1920 by 1080 | `Pack Break Table` | `TABLE_CAM`, `FACE_CAM`, `TITLE`, `NOW_OPENING` |
| `pack-break-closeup` | 1920 by 1080 | `Pack Break Closeup` | `CLOSEUP_CAM`, `TABLE_CAM`, `HIT_CARD` |
| `pack-break-vertical` | 1080 by 1920 | `Pack Break Vertical` | all six bindings |

The canvas must match your OBS base canvas, or the layout is refused.
Use a separate OBS profile for the vertical layout.

## Sources to create in OBS

The cue files expect these exact names. Bind your layouts to them.
If your names differ, edit the names in the JSON files.

| OBS source | Kind | Used for |
| --- | --- | --- |
| `Table Camera` | video capture | wide overhead camera, `TABLE_CAM` |
| `Closeup Camera` | video capture | card close-up camera, `CLOSEUP_CAM` |
| `Face Camera` | video capture | host camera, `FACE_CAM` |
| `Title Text` | text | title strip, `TITLE` |
| `Now Opening Text` | text | now-opening card, `NOW_OPENING` |
| `Hit Card Text` | text | hit name card, `HIT_CARD` |
| `Microphone` | audio input | muted by be-right-back and panic |

Also make a scene named `Pack Break Holding`. Give it a still image or a color,
and add `Title Text` so the cues can label it. It shows no live camera.
Starting soon, be right back and panic all cut to it.

## Show moves

The cue files are in `examples/pack-break/`. They are data only.
Run one with `obs_run_cue`, passing the file's contents. Preview it first.

| A producer types | Cue file | What happens |
| --- | --- | --- |
| "starting soon" | `starting-soon.json` | Title reads "Starting Soon" and the holding scene goes live. |
| "open the pack" | `open-pack.json` | The now-opening card reads "Booster Pack 12", the table scene goes live, the microphone is unmuted. |
| "reveal the hit" | `reveal-hit.json` | The hit card reads "Card of the Night" and "Holo Rare", the close-up goes live for 8 seconds, then the table scene returns. |
| "be right back" | `be-right-back.json` | Title reads "Be Right Back", the holding scene goes live, the microphone is muted. |
| "wrap up" | `wrap-up.json` | Title reads "Thanks for Watching", the card says to come back, the table scene goes live. |
| "panic" or "kill it" | `panic.json` | See below. |

Edit the sample text in the JSON files, or use events to supply it.
No cue sets `expected_scene`, so every move works from whatever scene is live.
`obs_cancel_cue` stops a reveal during its hold. The scene then stays on the close-up,
so type "open the pack" or cut back by hand.

The vertical layout shows every source at once, so it needs no scene cuts.
Use `open-pack.json`, `reveal-hit.json` and `wrap-up.json` there only for their text steps.
To change the card text without changing scenes, edit those files to drop the `scene_select` steps.

## Events

`examples/pack-break/event-routes.json` follows `obs.event-routes.v1`.
Point `OBS_MCP_EVENT_ROUTES` at its absolute path, then restart the server.

| Event type | Cue it runs | What fills the card |
| --- | --- | --- |
| `pack.opened` | `open-pack.json` | The event title, summary and reference go to `Now Opening Text`. |
| `hit.revealed` | `reveal-hit.json` | The event title, summary and reference go to `Hit Card Text`. |

Both routes use `text_step` 0, the first step of the cue.
The card shows `title`, then `summary`, then `SOURCE / reference`.
Titles are at most 80 characters and summaries at most 240.
A repeated `event_id` runs once. A producer sends events with `obs_preview_event` and `obs_dispatch_event`.

Example `hit.revealed` event:

```json
{"schema": "obs.event.v1", "event_id": "break-12-hit-1", "event_type": "hit.revealed",
 "occurred_at": "2026-01-01T12:00:00Z",
 "payload": {"title": "Card of the Night", "summary": "Holo Rare", "reference": "Booster Pack 12"}}
```

## Panic

Set `OBS_MCP_PANIC_CUE` to the absolute path of `examples/pack-break/panic.json`, then restart the server.
`obs_panic` with `dry_run=false` does two things in order.
It stops the cue that is running now, then runs the panic cue.
The panic cue mutes `Microphone` first, then cuts to `Pack Break Holding`.
It has no wait steps and no `expected_scene`, so it works from any scene.
It does not stop a stream or recording. Pass `allow_live=true` while either is active.
`obs_panic` with the default `dry_run=true` changes nothing and previews the cue.
Without the environment variable, `obs_panic` only stops the running cue.

## Limits

These layouts and cues are checked against synthetic OBS fixtures.
They were not rendered in a real OBS. Check the look at your own canvas size.
