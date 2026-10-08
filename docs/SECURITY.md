# Security and private data

## Local access

This server controls OBS on the same computer. Its tools can change recordings, broadcasts, visible scenes, and audible sources.
The loopback connection does not isolate the server from other processes with the same operating-system account.

1. Keep OBS WebSocket access local to the computer.
2. Keep OBS authentication enabled.
3. Use an MCP client that you trust.
4. Keep passwords and stream keys in local configuration or environment variables.

The OBS connection is local. An OBS source can separately use a network address or a local file.
Only supply source settings that you intend OBS to use.

## Changes to a production

Use a dry run before execution. A dry run can validate a command, but it does not grant permission to execute it.
It does not prove that the resulting picture and audio are correct.

If recording or streaming is active, production changes need `allow_live=true`.
Execution also needs `dry_run=false`. Output start/stop commands need the local `OBS_MCP_ALLOW_OUTPUT_CONTROL=1` setting.
Cues cannot start public streams. A public broadcast must not be a test.

Do not stop a recording that belongs to another session.
Before a recording stop, make sure that the adapter owns the recording.
If the connection fails or a command times out, examine OBS before further changes.
A failed command does not prove that the command had no effect.

A recording start can finish after a command timeout.
An unresolved start keeps `may_be_recording` in its local capture manifest.
The server preserves that recording directory and refuses a new capture in the same evidence root.
It does not adopt the recording after a restart or automatically clear the unresolved manifest.
Preserve this evidence during manual recovery.

Other controllers can change OBS state during this server's work.
Cue validation does not provide an OBS transaction or exclude other controllers.
The server does not reverse changes from completed cue steps when a later step fails.

## Event routes

The operator supplies the local event-route file through `OBS_MCP_EVENT_ROUTES`.
Keep this file private if it contains private scene names, source names, or cue text.
The server has no event routes unless the operator supplies this configuration.

An event can select only a registered route. Its payload cannot select arbitrary actions, paths, or scenes.
The server validates the cue before execution. A dry run does not consume the event identifier.
After successful validation, an execution attempt consumes the identifier even if execution fails or its result is uncertain.

The server keeps consumed event identifiers only in the current process. A restart removes this record.
Keep a persistent producer record when duplicate execution after a restart could cause a problem.
The event timestamp does not provide an execution schedule, freshness guarantee, or proof of origin.

## Data in results and files

MCP responses must not contain passwords, stream keys, source settings, screenshot pixels, or recording contents.
Error messages and diagnostic logs must not contain authentication payloads or unfiltered OBS response bodies.

Capture manifests, local file paths, and source names can identify private material.
Tool responses can contain this metadata even though pixels stay local.
Do not send private metadata to an unapproved client or model provider.

Keep recordings, account views, credentials, OBS configuration, and runtime evidence out of the repository and distribution.
Git ignore rules help prevent accidental additions. They do not protect files that Git tracks.

## Public-content check

Before a commit or release, run `python tools/public_check.py`.
The scanner examines candidate source files and Git index blobs separately.
It can find a staged secret even after you remove that secret from the working file.
It rejects tracked or staged runtime artifacts, including files in ignored directories.

The check fails if Git inspection fails, the index has conflicts, or a file exceeds the scan limit.
It also fails for content that the scanner cannot read.
It rejects these categories:

- Credential files and OBS configuration files
- Private keys and common API-token formats
- Literal credential assignments
- Absolute machine paths
- Binary files and media files.

Ordinary code identifiers, environment lookups, and clear placeholders can pass.
Findings show the relative file, line, and category. The scanner does not print matched content or rewrite files.

To reject private project names, repeat the `--forbid-text` option with each name.
The scanner does not save these option values. Shell history and local process tools can show command arguments.
Do not supply credentials through `--forbid-text`.

To inspect a separate source export, set `--root` to its directory.
The scanner does not inspect a parent repository's index for an export without its own Git metadata.

The scanner can miss new token formats, encoded secrets, and private prose outside the supplied terms.
Examine the exact staged diff and distribution contents as part of release review.
Do not hide a finding in an ignored directory while Git continues to track the file.
Do not weaken the check to accept a real secret.

## Publication authority

A passing check does not grant publication authority.
Publish only to a destination that the project maintainer authorizes.
Authorization for a GitHub alpha does not itself confirm that an upload succeeded.
Report the actual publication result separately from local checks.
