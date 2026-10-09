# Complete tournament show

This original example uses the Funded Desk dark palette and a transparent
PipHound portrait. It includes Starting Soon, the live table, standings, an
illustrative replay, a break screen and Stream Ending. The local rehearsal
returns to the table before the ending scene.

Each table card gives the signed net P&L the largest type. Realized P&L, open
P&L, fees, instrument, direction and entry/stop/target values remain distinct.
Standings use one session and one currency. Missing and stale entries are
unranked. See the [snapshot contract](TOURNAMENT.md).

## Render editable scenes

Run from a source checkout. The output parent must exist. The output directory
must be new. `--demo` creates fresh, explicitly synthetic values.

```sh
python tools/render_tournament.py --demo --output runtime/show-scenes
```

The result contains six HTML pages, local CSS and JavaScript, the portrait,
the snapshot and a hash manifest. It makes no OBS or network connection.
To display caller-supplied paper data, use `--snapshot path/to/snapshot.json`
instead of `--demo`. Rendering does not authenticate the data source.

## Rehearse through OBS

OBS must be open, its WebSocket server must be enabled, and all outputs must be
idle. FFmpeg must be available. Review this offline plan first:

```sh
python tools/tournament_demo.py --assets-directory runtime/show-scenes --stinger examples/tournament/piphound-stinger.webm --output runtime/show-take
```

Add `--execute` to run the 42-second rehearsal. Add `--ffmpeg path/to/ffmpeg`
when FFmpeg is not on PATH. The helper creates an isolated profile and scene
collection, mutes system audio and sends a quiet test tone with the scene video
to its fixed loopback RTMP receiver. It accepts no public destination. It
restores the original profile, collection, scene and video settings afterward.

The new output folder holds the receiver recording, scene screenshots, receipts
and restoration result. These files are private runtime evidence, not source
assets. Preserve a failed run when diagnosing a later run. If stream ownership
or restoration is unproven, the helper stops with an explicit error.

## Stinger

The included 1920 by 1080, 30 fps WebM has alpha transparency and no audio.
Its diagonal charcoal reveal holds the gold title across the cut, then clears
to the new scene. It is an original procedural clip; no poker broadcast asset
was copied. The adjacent JSON gives the clip hash and timing contract.

The example uses one shared media input across scenes. It verifies playback,
switches near 900 ms inside the 500–1733 ms opaque window, checks the cursor
after the switch, and hides the overlay after the transparent tail. A missed
window is an error, not a late cut or an automatic retry. The helper establishes
a stopped media state before each restart to make playback verification clear.

This is an **overlay stinger**. Native OBS stingers must already exist before
the MCP can select and configure them. See [native transitions](TRANSITIONS.md).
For a regular show, use stingers for major segment changes and short fades or
cuts for routine camera changes. The demo repeats the stinger to exercise it.

To generate a new copy, supply an installed font file and a new output path:

```sh
python tools/build_stinger.py --font-file path/to/font.ttf --output runtime/my-stinger.webm
```

The generator uses lossless VP9 to preserve the opaque hold and transparent
endpoints. It refuses to overwrite the video or its JSON sidecar. Rebuilding
with another font or FFmpeg build can change the file hash. Check decoded alpha
and actual OBS footage before using the resulting clip on a public stream.

## Limits

- All demo results and chart movement are synthetic. The replay is labelled
  illustrative. The intermission screens have no promised countdown.
- No account, broker, platform subscription or prize system is connected.
- The receiver uses 1280 by 720 output from a 1920 by 1080 base canvas.
- Source and cursor readback do not prove rendered pixels. Inspect the recording.
- Native stinger creation is outside the WebSocket v5 request set.
