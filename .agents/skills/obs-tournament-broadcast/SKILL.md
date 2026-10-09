---
name: obs-tournament-broadcast
description: Design tournament-style OBS shows or actual competition broadcasts with participant seats, action cards, shared boards, prominent results, standings and transition rules. Use when the show needs tournament information hierarchy or competition semantics, including trading demonstrations.
---

# OBS tournament broadcast

Design a readable show and an honest result display. A tournament appearance does
not establish a competition, verified performance, or a connected data feed.
Use [OBS layout design](../obs-layout-design/SKILL.md) for recipe construction and
pixel verification. Read [inspected tournament references](../../../docs/TOURNAMENT-REFERENCES.md)
when those examples help; adapt their information hierarchy without copying
broadcaster artwork or forcing their palette onto another brief.

## Decide what the show represents

Distinguish a tournament-style presentation from an actual competition. For a
presentation, label fixtures `DEMO` and keep participants unranked unless a real
ranking rule and valid data are supplied. For a competition, establish the rules
before displaying standings: eligible participants, round/window, starting basis,
scoring metric, fees, ties, limits, disqualification and finalization authority.
Record unresolved rules; do not silently invent them to fill a graphic.

Use [the show contract](references/show-contract.md) to capture only the fields
needed for this task. Preserve a stable seat identity through camera and scene
changes. A participant's current action is different from their accumulated score.

## Allocate the information

- **Seats:** name, identity, mode and relevant status. Portraits are optional;
  supplied transparent presenters need clear silhouettes and a reserved area.
- **Action cards:** the observed action, instrument or subject, amount with units,
  event time and source. Separate executed activity from proposals or commentary.
- **Shared board:** the common market, match state or round context. Use one
  consistent observation time; label delayed, disconnected and replay views.
- **Prominent results:** make the current result easy to locate without hiding
  the main action. Keep realized, unrealized and net figures distinct. For trading
  displays, use the [P&L contract](../../../docs/PNL.md): fees must not be deducted
  twice, mode and currency remain visible, and supplied values are not verified
  account balances. Keep [audience support](../../../docs/AUDIENCE.md) separate.
- **Risk and standings:** name the measure and units. Exposure, loss limit and
  realized loss are not interchangeable. Compare only eligible, fresh observations
  under the declared scoring basis. Never rank raw mixed-currency P&L. Any currency
  conversion or normalized score needs an explicit rule and source, rate/time or
  denominator. Otherwise show separate groups or an unranked board.

Do not manufacture win probabilities, returns, fills, rankings or timestamps.
An account refresh time does not make an old trade or price current. Missing is
not zero; stale is not live. Show `UNAVAILABLE`, `STALE`, or `PENDING` explicitly,
retain the last observation time where known, and withhold unsupported rankings.
`DEMO`, `PAPER`, `LIVE` and `REPLAY` must describe the supplied material accurately.
The formatter cannot authenticate those labels.

## Direct the show

Use the [neutral starter](../../../docs/STARTER.md) for a portable first show.
Use the [PipHound showcase](../../../docs/SHOW-DEMO.md) to study a fuller treatment.
Both have six scene states and use the same snapshot contract. Keep each custom
show pack separate from credentials, account connections and private data.

For a complete broadcast, map Starting Soon -> Live -> Standings -> Replay ->
Break -> Live -> Ending. Adapt the order to the event and the user's scope.
Starting Soon needs a truthful scheduled time or an explicit waiting state, not
a looping false countdown. Ending distinguishes provisional from final results
and gives a clear end card; selecting it must not start or stop an output.

Define a small transition grammar: what changes, why, destination, reveal point,
audio behavior and fallback. For example, cut between active decisions; dissolve
into a discussion; reserve a stinger for a round boundary or replay entry. These
are options, not mandatory effects. Preserve persistent standings or status only
where their meaning remains valid across the change.

Specify stinger duration, transparency, sound, transition point and destination
readiness. Use the [supported transition workflow](../../../docs/TRANSITIONS.md).
Do not assume selecting a transition proves its media renders. A replay needs a
visible `REPLAY` label and original event time before the audience sees its data.
An overlay stinger is a media cue, not a configured native OBS transition; label
the implementation accurately and verify its reveal and cleanup.
Keep alerts clear of prices, participant identities, P&L and replay/status labels.

The [original audio pack](../../../docs/AUDIO.md) and
[mascot reaction clips](../../../docs/REACTIONS.md) are optional. Give music,
stinger sound and mascot audio independent level controls. Keep desk music off
unless the brief calls for it. Use reactions sparingly and preserve readable data.
After muxing VP9 alpha with sound, verify the alpha-mode stream metadata as well
as decoded alpha frames and actual OBS playback. Inspect a full music-loop join
and listen to the mixed output before claiming audible acceptance.

## Prove the states

Exercise opening, a featured decision, standings, break, replay, ending and
recovery where the brief calls for them. Include gain/loss/flat, long participant names,
missing and stale feeds, tied scores, a late correction, and an alert during a
transition when applicable. With invalid inputs, show the defined unknown state
instead of a plausible invented result.

Validate recipes and cues before authorized application. Capture actual OBS
pixels and watch complete transitions. Check that the result stays legible at
the intended viewing size and that scene cuts do not relabel old data as live.
Deliver a scene map, show contract, run-of-show, source bindings and evidence with
unobserved behavior marked pending. A local rehearsal or synthetic competition
does not prove provider ingestion, real returns, public broadcast, or official
tournament results. This skill never authorizes starting those services.
