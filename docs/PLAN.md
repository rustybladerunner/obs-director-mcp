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
