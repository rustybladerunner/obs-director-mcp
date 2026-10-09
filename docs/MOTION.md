# Original trading and tournament motion loops

The motion pack uses warm gold and bronze on transparent surfaces. It contains
three original SVG animations. It has no sound, external fonts, network calls,
prices, orders or data feed. These are decorative Browser Sources, not videos.

| Page | Motion | Cycle |
| --- | --- | --- |
| `examples/motion/fibonacci.html` | Fibonacci construction lines, arcs and ratio geometry | 18 seconds |
| `examples/motion/market.html` | Slowly drifting stylized candlestick forms | 24 seconds |
| `examples/motion/table.html` | Poker chips and card geometry | 20 seconds |

## Use a standalone loop

Copy the entire `examples/motion` folder from the source checkout or source
archive. Add a local Browser Source in OBS. Select one of the HTML files and set
its size to 1920 by 1080. Keep the local CSS and JavaScript beside the HTML files.
Place the source behind your text, portraits and result display.

The background is transparent. For a browser preview, use `?background=dark`.
For inspection, `?freeze=5` renders the pose at five seconds without animation.
The reduced-motion preference also gives a static composition.

## Add motion to the complete show

```sh
python tools/render_tournament.py --demo --template piphound --motion --output runtime/motion-show
```

`--motion` is optional and also works with the neutral starter. Starting Soon
uses Fibonacci geometry, Break uses the poker motif, and Ending uses the market
motif. The ambient treatment reserves the header and footer and dims decoration
behind the copy. The table, standings and replay pages retain their original
content. A snapshot refresh can rebuild the page; the motion layer is reattached.

The renderer includes both motion files in the scene hash manifest. It validates
all local inputs before creating the output folder. The library accepts an
explicit `motion_dir`; template JavaScript is trusted local code, not sandboxed
third-party content. Installed callers must supply the example paths themselves.

Use the [local rehearsal](SHOW-DEMO.md) to check text and motion at the receiving
resolution. Inspect a complete cycle and its wrap. A still image does not prove
a smooth loop. Keep ornament separate from real market or account information.

## Continuous tournament direction

A future PipHound-hosted bot tournament could use this show pack. Each bot would
have a stable seat and a paper account under one declared scoring contract.
Decisions would form replayable segments: setup, entry, risk, outcome and recap.
Quiet periods would show standings, strategy identities, session recaps and an
explicit waiting or market-closed state. The show must preserve data provenance,
fees, observation times and missing or stale status.

Bots may use public or private strategies. Private execution and public
presentation have different boundaries. A future producer must expose a
deliberate public projection with approved identity, results and activity fields.
It must exclude private indicator levels, signal logic and internal reasoning.
Commentary, chart annotations and replays must use that same filtered view;
access to the execution engine must not grant access to the broadcast.
Support for an individual creator's stream remains a separate show pack.
The current fixtures contain no private strategy implementation. Their example
entry, stop and target annotations do not define the disclosure policy for a
private-method bot. That policy must explicitly allow any displayed trade detail.

The first acceptance target would be a recorded deterministic paper tournament,
followed by a continuous local rehearsal with recovery and stale-feed checks.
This repository supplies presentation and OBS controls. It does not yet supply
a continuous trading engine, broker execution, platform authentication or a
24-hour production operator. Public stream activation remains a separate action.

Keep the branded show separate from a reusable conversational host. A future
host layer can receive speech, manage interruption and response timing, and
request validated presentation cues from OBS Director. The branded show supplies
its character, tournament rules and public-data policy. Private operator speech
and public program output need distinct channels. The first useful milestone is
one complete spoken interaction with a caption or voice response and an OBS cue.
None of these listening or conversation services run as part of this example.
