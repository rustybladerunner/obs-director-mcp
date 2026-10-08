# First standalone version

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

Not part of this version: streaming-platform accounts/chat integrations,
automatic public broadcasting, trained visual understanding, or a rendered
browser-overlay designer. These can consume the control/cue APIs later.

MIT is the chosen public license. Do not publish yet.
