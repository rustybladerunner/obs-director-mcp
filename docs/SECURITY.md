# Security and private data

This server controls a local OBS instance. Its tools can affect recordings,
broadcasts, visible scenes and audible sources. Restrict OBS WebSocket to the
local machine, keep authentication enabled, and use a trusted MCP client.
The server is not an authentication boundary against other processes running
as the same operating-system user.

Use previews first. A successful dry run checks a request; it does not authorize
execution or prove that the resulting picture and audio are usable. Live
mutations require explicit opt-in. Do not use a public broadcast as a test,
start streaming from a director cue, or stop a recording owned by another
session. Check capture ownership before output lifecycle changes. A reconnect
or timeout must not be treated as proof that an action failed or completed.

Keep passwords and stream keys in local configuration or environment variables.
Do not return secret values, source settings, screenshot pixels or recording
contents through MCP responses. Error messages and diagnostic logs must not
include authentication payloads or arbitrary OBS response bodies. Local capture
manifests and source names can identify private material; retain them locally.

Recordings, account views, credentials, OBS configuration and generated runtime
evidence do not belong in this repository or a distribution. Ignore rules reduce
accidental staging; they do not protect a file that Git already tracks.

## Public-content check

Run `python tools/public_check.py` before committing or preparing a release.
It scans candidate source files and, in a Git checkout, the index blobs as a
separate surface. A secret staged earlier is still rejected after the working
copy is cleaned. Ignored runtime directories are skipped only for untracked
files; tracked or staged runtime artifacts are rejected. Git inspection errors,
unmerged indexes, oversized files and unreadable content fail closed.

The check rejects credential/configuration filenames, private-key and common
API-token formats, literal credential assignments, absolute machine paths and
binary/media artifacts. Ordinary variable names, environment lookups and clear
placeholder examples are allowed. Findings contain only relative file, line and
category; matched values are never printed. The check does not rewrite files.

For names or phrases private to your deployment, pass `--forbid-text` repeatedly
in a local invocation. Those values are not stored in the scanner or its output.
Command arguments may still be visible to local process inspection and shell
history, so use this option for private project names, never for credentials.
Use `--root` to inspect a separately prepared source-export directory.

Pattern matching cannot prove an export contains no private information. It can
miss new token formats, encoded secrets and sensitive prose not named by the
caller. Inspect the exact staged diff and distribution contents as well. Do not
waive a finding by moving the file into an ignored directory while leaving it
tracked, or by weakening the check to accept a real secret.

No public repository, package upload or release is authorized until the operator
explicitly asks for publication. A passing check is evidence for review, not
permission to publish.
