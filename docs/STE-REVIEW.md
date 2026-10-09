# Technical-English review

Review date: 2026-10-08.

This review revised selected documentation with ASD-STE100 Issue 9 as the primary language reference.
It is a scoped review, not a certificate of compliance.
No ASD endorsement or automated compliance claim applies to this project.

## Authority and method

The reference is [ASD-STE100, Issue 9, January 2025](https://www.asd-ste100.org/assets/files/ASD-STE100_ISSUE9.pdf).
The review read the official 434-page PDF through HTTPS and extracted selected pages in memory.
The repository contains neither the PDF nor a copy of its dictionary.

The review compared the existing prose with current source behavior before replacement.
It retained commands, API identifiers, requirements, exclusions, and uncertainty.
It then examined sentence structure and selected words against the source below.
The review did not classify every word in every sentence.

| Reference inspected | Application in this revision |
| --- | --- |
| Rules 1.1–1.14, printed pages 1-1-1, 1-1-7, 1-1-8, 1-1-12, 1-1-16 | Distinguish dictionary words from project technical terms. Keep one term for each concept. |
| Rules 2.1–2.2, page 1-2-1 | Shorten noun groups and explain necessary technical terms. |
| Rules 3.1–3.7, pages 1-3-1 and 1-3-5 | Use direct verbs and active voice. Keep technical nouns separate from verb use. |
| Rules 4.1–4.5, page 1-4-1 | Use short sentences, explicit connections, and lists where useful. |
| Rules 5.1–5.5, pages 1-5-1 through 1-5-5 | Use command form for procedures. Put necessary conditions before dependent instructions. |
| Rules 6.1–6.6, pages 1-6-1, 1-6-2, 1-6-5, 1-6-7 | Keep related descriptions in short paragraphs. |
| Rule 7.2, page 1-7-3 | Put the relevant condition or command first in protective instructions. |
| Rules 8.1–8.7, pages 1-8-1 and 1-8-7 | Treat identifiers and fixed UI labels separately from editable prose. Avoid semicolons in authored prose. |
| Rules 9.1–9.4, pages 1-9-1, 1-9-5, 1-9-7 | Change sentence construction when simple word substitution would change meaning. |
| Dictionary introduction, pages 2-0-3 through 2-0-5 | Examine part of speech and meaning, not only spelling. |

The dictionary spot review included these entries:

| Printed page | Entries considered | Documentation decision |
| --- | --- | --- |
| 2-1-A12 | `allow` | Describe the condition explicitly. Retain `allow_live` as an API identifier. |
| 2-1-C6–C7 | `CHECK`, `check`, `choose` | Use "check" as a noun. Use "examine", "make sure", or "select" where applicable. |
| 2-1-R5 | `RECORD` | Use the verb for saved data and media. |
| 2-1-R12–R13 | `require`, `request`, `restart`, `restrict` | Rewrite general prose. Keep exact API names and specific software terms. |
| 2-1-S6 | `SELECT` | Keep scene selection distinct from setting a parameter. |
| 2-1-S23–S24 | `stationary`, `STOP`, `store` | Use a static-slide description and direct stop/keep wording. |
| 2-1-V1–V2 | `valid`, `verify`, `via` | Avoid general claims of validity. Name the actual result check or connection. |

This table records review choices. It does not reproduce dictionary definitions or establish approval for every use.

## File inventory

| File or surface | Status | Limit |
| --- | --- | --- |
| `README.md` | Rewritten and compared with source | The maintainer separately verifies the public destination and release result. |
| `docs/SECURITY.md` | Rewritten and compared with controls and scanner | Security instructions do not prove that all possible data disclosures are prevented. |
| `docs/TERMINOLOGY.md` | Added | Terms define this project's use. They do not extend the ASD dictionary. |
| `docs/STE-REVIEW.md` | Added | Records this review and its exceptions. |
| Tool docstrings | Selected wording applied by the source owner | Source tests and MCP discovery check the resulting interfaces. This is not a complete docstring review. |
| `AGENTS.md`, `CLAUDE.md`, `docs/PLAN.md` | Outside the language review | PLAN records the current authorization and supersedes the historical build-only restriction. |
| License, commands, code, schemas, environment names, fixed UI text | Preserved as contracts | These strings are not ordinary prose for replacement. |
| `docs/DEMO.md` | Written by a separate contributor | This review compared its interfaces with the README; it does not certify that document. |
| Other documents, generated files, and future release notes | Not reviewed | No repository-wide compliance statement applies. |
| Layout, template, artwork, and stream-demo additions in 0.2.0a1 | Not included in this original Issue 9 review | These additions use short instructions, but have no formal STE compliance claim. |

## Requirement-preservation review

The revision preserves these operating requirements and limits:

- Python 3.11 or later, WebSocket v5, and WebSocket 5.3 recording-directory requests.
- The source installation, server, test, scanner, smoke, live-read, and package-build commands.
- MCP over `stdio`, with no HTTP server and no ordinary log text on standard output.
- Local password discovery and a loopback-only OBS connection.
- Dry-run defaults, the separate `allow_live` condition, and local output-control permission.
- No stream start at startup or from a cue.
- Full cue validation before changes, partial-failure reporting, and no automatic reversal of completed steps.
- The difference between OBS readback, file finalization, and actual picture/audio acceptance.
- Recording ownership tied to the original process, connection, and observed events.
- The lack of an atomic recording-identifier comparison for stop.
- Separate Git-index and working-file inspection, including ignored files that Git tracks.
- The scanner's limits and the need for exact staged-diff and package review.
- Original implementation provenance and the separate licenses of dependencies.
- Optional local event routes, payload limits, complete cue validation, and the separate live-operation condition.
- Process-local event identity records, capacity refusal, consumption after uncertain execution, and loss of those records after restart.

The revision also makes existing source limits explicit: 20 cue steps, 15 seconds of combined waits, and 4096 text characters.
The wait limit does not bound total elapsed execution time.
The event review preserves the 16-route and 128 KiB configuration limits, and the 96-character identifier limit.
It preserves the 80/240/96-character payload limits and the default capacity of 1024 consumed event identifiers.
The public server uses this default capacity. The library can separately accept a capacity from 1 through 4096.

## Checks performed

A local documentation check examined the four assigned files, eight local links, and the JSON client-configuration example.
It also compared command strings, tool names, environment names, and stated numeric limits with the current source.
These checks passed for the reviewed source snapshot.
The public-content check passed after the documentation changes.

A sentence-length heuristic found no review candidates above 25 words in the selected body paragraphs.
It excluded tables, bullet lists, code blocks, and headings. Its count is not the standard's complete word-count method.
Manual review covered the command sequence and its conditions, including separate cue validation during active recording or streaming.
These results do not establish full language compliance, runtime acceptance, or publication success.

## Exceptions and unresolved review

The glossary contains software nouns and verbs under the technical-term provisions of rules 1.5 and 1.12.
Their applicability still needs review by a qualified technical author and the project maintainer.
Some terms have both noun and verb forms with different project meanings.
This review keeps those meanings explicit rather than treating spelling alone as approval.

Fixed identifiers, command blocks, URLs, table fragments, and quoted UI labels remain unchanged where they identify an interface.
This is a documented exception to prose replacement, not an exemption for surrounding instructions.

Sentence-length inspection can identify review candidates. It cannot establish dictionary use, correct grammar, or preserved technical meaning.
The review does not claim a complete application of the standard's word-count rules.
The resulting documentation is **reviewed against selected Issue 9 rules and dictionary entries**.
It is **not certified as fully ASD-STE100 compliant**.
