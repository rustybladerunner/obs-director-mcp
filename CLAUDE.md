# OBS MCP operating notes

The project is a Python package using MCP over stdio and OBS WebSocket v5 on
loopback. The public name and distribution name are provisional until release.
Read AGENTS.md for scope, ownership, privacy and verification requirements.

The transport validates authentication and correlated responses. Production
controls expose bounded typed operations. Capture keeps file provenance and a
live recording lease. Director cues validate a complete plan before executing
any step; local code handles timing, and partial failures stay visible.

No source files depend on sibling repositories. Development evidence, local
credentials, captures, package caches and build output stay ignored. No public
release or production-stream test is authorized by the current build request.
