# OBS MCP

Read this file and CLAUDE.md before changes. This is a standalone repository.
The operator-authorized objective is a portable OBS MCP with live production
controls, deterministic director cues, verified capture ownership, and a clean
future public release. No publication has been authorized.

- One writer per file. Inspect current Git changes before editing or staging.
- Read-only OBS inspection is allowed. Do not mutate the operator's active
  recording or stream during development. Test changes with synthetic clients.
- Use the public OBS WebSocket v5 protocol and installed MCP SDK. Never copy
  upstream GPL implementation into this original project.
- No personal paths, recordings, account data, stream keys or credentials in Git.
  Credential discovery is runtime-only; never print passwords or source settings.
- Keep defaults local and dry-run. Live layout changes require explicit opt-in.
  Starting a public stream must never be a test or a cue side effect.
- Verify the affected code with standalone test commands. Record what was
  actually exercised; live read-only success is not recorded-media acceptance.
- Stage explicit paths, inspect the staged set, and commit only this project.
  Do not publish to GitHub or a package registry without a user request.

Checks: `python -m unittest discover -s tests -v`,
`python tools/public_check.py`, and `python tools/smoke.py`.
`python tools/smoke.py --live` is read-only against configured OBS.
