# Funded Desk local stream

This rehearsal sends a 25-second OBS stream to an FFmpeg receiver on the same
computer. It cannot accept a public streaming destination. The receiver saves
the received video and audio into `receiver.mkv` for inspection.

Use the source checkout or source distribution, Python dependencies, FFmpeg,
and a running OBS WebSocket server. Keep all OBS outputs inactive.
The bundled example uses synthetic prices and the fictional PipHound host.
It submits no orders and connects to no market-data service.

First, examine the offline plan:

```sh
python tools/demo_stream.py --assets-directory examples/funded-desk --output runtime/funded-demo --ffmpeg ffmpeg
```

To run the rehearsal, add `--execute`:

```sh
python tools/demo_stream.py --assets-directory examples/funded-desk --output runtime/funded-demo --ffmpeg ffmpeg --execute
```

Supply the full FFmpeg executable path if it is not on your PATH.
Choose a theme and presenter style before execution:

```sh
python tools/demo_stream.py --assets-directory examples/funded-desk/dark --theme dark --presenter cutout --output runtime/funded-dark --ffmpeg ffmpeg --execute
```

The default is `--theme light --presenter framed`. Use `examples/funded-desk`
for light HTML or `examples/funded-desk/dark` for dark HTML. `--presenter cutout`
uses `host-cutout.html`, which has a transparent background and no camera frame.
The same alpha PNG works as a standalone OBS image source. It needs no chroma key.
The framed option uses a studio backdrop and name plate.

Use a new output directory for each run. Create its parent directory first.
The fixed local receiver uses port 19351. The rehearsal refuses an occupied port.

The script creates its own OBS profile and scene collection. It mutes their
desktop and microphone inputs. It copies the template, binds the demo inputs,
previews the recipe, applies it, and confirms that a repeated apply needs zero
changes. Each scene sends a quiet generated test tone.

The program cuts from the main desk to the chart at approximately 8 seconds,
then to the replay at 15 seconds. It returns to the desk at 21 seconds.
OBS command latency can extend these times.

The main scene displays sample [P&L](PNL.md): first USD +245.00, then USD -85.00.
Both states include the DEMO label and a UTC timestamp. Dedicated audience panels
show a synthetic Twitch subscription and YouTube membership through actual MCP
event dispatch. Each alert hides after its cue. The rehearsal checks duplicate
suppression and rejects an alert dispatched from the chart scene without changes.
The alert routes load in a fresh MCP process before the local stream starts.
See [audience contracts and provider limits](AUDIENCE.md). No provider account is
connected and no real audience events are received.

The stream uses a 1920 by 1080 base canvas and a 1280 by 720 output at 30 FPS.
After its own stream stops, the script restores the original profile,
collection, selected scene, and video settings. Created demo profiles and
collections remain available for inspection. Runtime evidence stays ignored.

The script saves OBS screenshots, stream byte samples, receiver output,
and restoration results. Inspect the received media as well as the receipt.
An uncertain start or lost stream ownership prevents an automatic stop or
profile switch. In that case, inspect OBS before starting another rehearsal.

Use only trusted local example HTML. The initial URL check catches ordinary
remote links. It is not a browser network sandbox and cannot prove arbitrary
JavaScript safe. The bundled examples contain no network calls.

The PipHound portrait is a still image with a small CSS motion.
The replay panel is a synthetic illustration, not a replay of a real trade.
The generated tone tests the audio path. There is no speech or lip sync.
See [artwork provenance](DEMO-ART.md) and [template instructions](TEMPLATES.md).
The scenario strip uses tournament-broadcast information grouping. Its entry,
stop and target are illustrative values, not submitted orders. See the inspected
[tournament references](TOURNAMENT-REFERENCES.md) for the design rationale.
