# Existing transitions and stingers

`TransitionService(production)` shares the production lock and uses one local
OBS connection per operation. Its methods configure transitions that already
exist in the selected scene collection. They do not select a program scene,
trigger a transition, start an output, or edit OBS configuration files.

| Method | Purpose |
| --- | --- |
| `transitions()` | List installed kinds, existing names, fixed/configurable flags, and the current transition's duration. |
| `select_transition(transition_name, duration_ms=None, dry_run=True, allow_live=False)` | Preview or select an existing transition, with an optional duration from 50 through 20000 milliseconds. |
| `configure_stinger(transition_name, video_path, transition_point_ms, dry_run=True, allow_live=False)` | Preview or configure an already-selected native stinger using a local clip and a timing point from 0 through 20000 milliseconds. |

The public WebSocket v5 request list includes selection and settings updates,
but no request to create a scene transition. Add a native stinger in the OBS
interface first. `CreateInput` is not a transition-creation substitute. See the
[official transition requests](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md#transitions-requests).

For a native stinger, select its existing name first. Then pass an absolute,
readable local MOV, WebM, MP4 or MKV file of at most 1 GiB. Configuration updates
only `path`, `tp_type=0` and `transition_point`, with `overlay=true`; other
settings remain intact. Track-matte mode is refused. These setting names and
the native kind `obs_stinger_transition` follow the
[OBS 32.1 stinger implementation](https://github.com/obsproject/obs-studio/blob/32.1.0/plugins/obs-transitions/transition-stinger.c).
No upstream implementation code is included in this package.

```python
service = TransitionService(production)
plan = service.select_transition("Fade", duration_ms=300)
receipt = service.select_transition("Fade", duration_ms=300, dry_run=False)

# After an operator has added and selected this native transition:
plan = service.configure_stinger("Demo Stinger", absolute_local_clip, 700)
```

Changes require known recording/streaming status. An active output requires
`allow_live=True`; this does not authorize an output start. An active or unknown
transition cursor is refused even with live opt-in. Missing capabilities,
ambiguous names, stale identities, and profile/collection changes stop execution.
Fixed-duration transitions cannot accept `duration_ms`.

Each operation has a 20-second request budget and at most 160 transport calls.
Readback polls for at most three seconds per accepted write, within that total
budget. It never repeats a setter after an uncertain acknowledgement and never
rolls back earlier changes automatically. A failed second step can leave the
first step applied. Receipts retain `applied`, `verified`, `uncertain`, and the
per-step results. Inspect current OBS state before another attempt.

Results omit transition settings, file paths and arbitrary error text. An
unchanged request sends no setters. File checks establish local readability and
basic metadata stability only; they do not decode or hash the clip. OBS state
readback does not prove alpha, audio, timing, or rendered frames. Watch a local
recording of the complete reveal before claiming visual acceptance.

A rehearsal may instead show a local media source above the scene, change the
program scene under its cover, then hide the source. Label this an **overlay
transition demo**. It does not create or verify a native OBS stinger. Keep such
rehearsals in an isolated collection/profile and preserve their restoration
evidence.
