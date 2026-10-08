# OBS Director MCP

**Direct a live production with explicit controls and reusable cues.**

OBS Director connects an MCP client to local OBS Studio. Use individual tools to
inspect or control a production, or send a bounded cue that coordinates several
changes. The model chooses the cue; local code validates and executes its steps.

This is an original MIT-licensed project in early development. The repository
and package name are provisional; nothing has been published to a registry yet.

## What it does

| Surface | Capability |
| --- | --- |
| Scenes | Inspect, create, select program or preview scenes |
| Sources | Add inputs, update settings, show/hide, position, crop and scale |
| Audio | Mute and set volume |
| Filters | Inspect, enable and update existing filters |
| Media | Play, pause, restart, stop and seek |
| Outputs | Explicit streaming, replay-buffer and virtual-camera controls |
| Capture | Session-owned recordings, local frame-health samples, finalized-file hashes |
| Director cues | Validate complete sequences, preview, execute bounded timing, report partial failures |

OBS production changes default to a preview. Changing an active production also requires
`allow_live=true`. Output lifecycle operations require the local
`OBS_MCP_ALLOW_OUTPUT_CONTROL=1` setting. The server never starts a stream on
startup or as a cue step.

## Quick start

Requires Python 3.11+ and OBS with WebSocket v5; session capture requires the
recording-directory requests introduced in WebSocket 5.3. Enable OBS's
**Tools → WebSocket Server Settings**, keeping authentication enabled.

From a source checkout:

```sh
python -m pip install -e .
python run_server.py
```

Or use the installed `obs-director-mcp` command. Both run MCP over stdio, not an
HTTP server. Standard output is reserved for MCP messages.

Example MCP client configuration; replace the interpreter and checkout paths:

```json
{
  "mcpServers": {
    "obs-director": {
      "command": "python",
      "args": ["/path/to/obs-mcp/run_server.py"]
    }
  }
}
```

Runtime credentials come from `OBS_MCP_PASSWORD` or OBS's local WebSocket config.
They are never copied into a release artifact. Connections are loopback-only.
See [security and operating boundaries](docs/SECURITY.md).

## Cues

The [examples](examples/) contain reusable, data-only production cues. Adapt
their scene/source names to your OBS collection, validate them, then preview
with `obs_run_cue`. Execution requires `dry_run=false` and, when outputs are
active, `allow_live=true`.

The complete cue is checked before its first action. There are no arbitrary
commands, scripts or stream-start steps. If an action fails, the receipt names
completed, failed and skipped steps. Earlier changes are not magically rolled
back. Do not use a successful request as proof the audience saw correct content.

## Useful directions

- One cue brings in a replay, changes the layout and adjusts its audio.
- A presentation cue emphasizes one source and hides secondary panels.
- A local application triggers evidence cards or scene changes from real events.
- A custom browser overlay can add animation, annotations and audience graphics.

Browser-overlay rendering, speech recognition and streaming-platform chat are
separate integrations. This package provides production control; it does not
infer what a chart means or provide a streaming account.

## Capture evidence

Capture tools keep screenshots and recordings on the local machine. Returned
data contains paths, hashes, timing and measurements, never screenshot pixels.
Black/unchanged frames are diagnostic evidence, not a content-quality verdict.
A stationary slide can legitimately produce unchanged frames.

Stopping a session recording requires the original live connection and its
observed lifecycle. After restart or a lost lease, stop through OBS itself.
OBS exposes no atomic recording-ID comparison for stop, so avoid simultaneous
manual capture changes while the adapter owns a session.

## Development and release checks

```sh
python -m unittest discover -s tests -v
python tools/public_check.py
python tools/smoke.py
python tools/smoke.py --live
python tools/build_release.py
```

The smoke test performs a real SDK subprocess handshake. Its default mode tests
disconnected OBS; `--live` performs only status/capability reads. Fake-client
tests cover mutations without touching a running production. Build output and
local verification evidence are excluded from Git.

Before any release, review the actual export, run the public-content check and
review dependency licenses. Passing a pattern scanner is useful evidence, not a
guarantee that every sensitive detail has been recognized.

## Implementation provenance

Written against the [official OBS WebSocket protocol](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md)
and the [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk).
An earlier in-house capture prototype supplied the transport and recording
ownership design. Its source snapshot was recorded by content hash before
generalization; this repository has no dependency on the original application.

[aaronckj/obs-studio-mcp](https://github.com/aaronckj/obs-studio-mcp) and
[royshil/obs-mcp](https://github.com/royshil/obs-mcp) were reviewed for feature
coverage. No source from either repository was copied. Dependencies retain
their own licenses. This project's original source is [MIT licensed](LICENSE).
