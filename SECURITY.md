# Security policy

## Reporting a vulnerability

Report it privately through GitHub:
[Report a vulnerability](https://github.com/rustybladerunner/obs-director-mcp/security/advisories/new).
Do not open a public issue for a security problem.

Include the version, your operating system, the OBS and OBS WebSocket versions, and the smallest
cue or command that shows the problem. Reports are handled on a best-effort basis by one
maintainer; a confirmed issue is fixed in the next release and credited in its notes unless you ask
otherwise.

## Supported versions

This project is an alpha. Only the latest release on
[GitHub Releases](https://github.com/rustybladerunner/obs-director-mcp/releases) and the `main`
branch receive fixes.

## Scope

In scope: anything that lets an MCP client, a cue, or another local process make this server do
something its checks should refuse, such as acting while live without `allow_live=true`, starting
output without `OBS_MCP_ALLOW_OUTPUT_CONTROL=1`, starting a public stream from a cue, or exposing
the OBS WebSocket password or a stream key in output or logs.

Out of scope: OBS Studio and OBS WebSocket themselves (report those to the
[OBS project's security policy](https://github.com/obsproject/obs-studio/blob/master/SECURITY.md)), and a malicious process already
running under the same operating-system account, which the server cannot isolate itself from.

How to run the server safely: [docs/SECURITY.md](docs/SECURITY.md).
