# Public alpha

Build a useful original OBS MCP that can be reviewed and packaged independently.

Acceptance:
- Typed controls for scenes, sources, layout, audio, filters, media, replay,
  streaming, recording and virtual camera, checked against OBS capabilities.
- Preview mutations by default; explicit opt-in for active broadcasts and
  output lifecycle control. Preserve existing recordings.
- Deterministic director cues with full validation before any action, bounded
  timing, execution receipts, and honest partial-failure reports.
- Local capture health and capture manifests; do not return screenshot pixels.
- SDK stdio discovery and disconnected negative control; live read-only status.
- Unit/fake-protocol tests, distributable package, and a public-content check
  proven to reject planted credentials and private artifacts.
- Bounded production events, trusted local routes, and duplicate-attempt handling.
- Windows and Linux consumers install and test the same release wheel.
- Selected documentation reviewed against ASD-STE100 Issue 9, with explicit limits.
- Isolated synthetic OBS rehearsal with recorded picture and audio inspection.

Not part of this version: streaming-platform accounts/chat integrations,
automatic public broadcasting, trained visual understanding, or a rendered
browser-overlay designer. These can consume the control/cue APIs later.

MIT is the chosen public license. The maintainer has authorized a public GitHub
alpha at `rustybladerunner/obs-director-mcp`. Package-registry publication is a
separate decision. Older build-only restrictions in CLAUDE.md describe the first
build request; the current maintainer instruction authorizes this GitHub release.

Release evidence must distinguish synthetic tests, MCP protocol checks, real OBS
state checks, rendered media inspection, and continuous producer operation.
Passing one category does not establish the others.

## Current upgrade: 0.2.0a2

The complete [tournament show](SHOW-DEMO.md) adds Starting Soon, the table,
standings, illustrative replay, break and ending scenes. Two opposing trader
cards show large signed net P&L. A strict demo/paper snapshot declares currency,
session, fees and tie rules; missing and stale participants remain unranked.
Repo-owned skills guide general layout design and tournament presentation.

Three transition tools inspect and select existing native transitions and
configure an already-selected native stinger. The example includes an original
silent lossless alpha WebM and a media-overlay stinger rehearsal. Native
transition creation is not exposed by OBS WebSocket v5.

Local OBS acceptance on 2026-10-08: the 42-second complete-show rehearsal reached
all seven phases, including the return to the table. Six cuts passed the
covered-window readback checks. The receiver finalized and original profile,
collection, scene and video were restored. Actual scene screenshots were
inspected. Decoded stinger frames establish transparent endpoints and fully
opaque frames 15–52. This is synthetic local evidence, not a platform broadcast.
The prior failed takes remain in private runtime evidence.

The integrated suite passed 358 tests. MCP discovery exposed 34 tools and passed
the disconnected negative control. Received-show inspection covered all six
distinct scenes and six transition frame sequences. All 1,262 received video
frames decoded; one 66 ms interval prevents a perfect frame-cadence claim.
The 42.12-second recording includes stereo AAC test-tone audio. Timed frame
inspection and audio measurements are not a claim of full-video viewing or
listened-audio acceptance. The larger single-host P&L also passed a separate
25-second rehearsal with gain/loss and audience-alert states.

This upgrade remains local; the following section records the earlier layout
milestone and its own validation scope.

## Previous local milestone: 0.2.0a1

The local upgrade adds complete layout recipes, image and color layers, borders,
and repeatable item order. Funded Desk is one copyable template with light and
dark themes. Original image prompts and reviewed artwork are bundled.
The source demo supplies framed and transparent PipHound presenter options.

The demo includes a tournament-style scenario strip, declared sample P&L, and
temporary audience alerts. [Tournament references](TOURNAMENT-REFERENCES.md)
record inspected EPT and WSOP frames. No broadcaster assets are included.
[Audience](AUDIENCE.md) and [P&L](PNL.md) modules are pure local adapters.
Provider authentication, event ingestion and real account data remain outside
this version. The portrait moves through CSS; it is not a speaking avatar.

Local acceptance on 2026-10-08: 294 unit tests passed. The MCP subprocess exposed
31 tools. Disconnected and live read-only smoke checks passed. Isolated light
and dark OBS rehearsals sent approximately 25 seconds to a loopback receiver.
They exercised layout application, zero-write reapplication, chart/replay cuts,
gain/loss displays, alert display/hide, duplicate suppression and wrong-scene
rejection. Both restored the original profile, collection, scene and video.
Rendered OBS screenshots were inspected, including the framed name-plate fix.
Received files contain 1280 by 720 H.264 video and stereo AAC test-tone audio.
This is local rehearsal evidence, not public-platform streaming acceptance.

The upgrade is prepared locally. Publication of this new version remains a
separate maintainer action. The earlier alpha authorization above records the
existing release, not a claim that this upgrade has been published.
