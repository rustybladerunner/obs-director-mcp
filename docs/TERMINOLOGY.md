# Project terminology

This glossary defines terms for OBS Director documentation.
It does not reproduce the ASD-STE100 dictionary or claim approval from ASD.
The [review record](STE-REVIEW.md) identifies the standard and review limits.

## Actors and interfaces

| Term | Project meaning |
| --- | --- |
| Operator | The person who authorizes production changes and controls the OBS session. |
| Maintainer | The person who authorizes project publication and accepts source changes. |
| MCP client | The application that sends Model Context Protocol (MCP) tool calls to this server. |
| Server | This OBS Director MCP process. This term does not mean the OBS WebSocket server. |
| OBS | OBS Studio, the application that renders scenes and produces outputs. |
| OBS WebSocket | The local control interface in OBS. |
| Loopback | The network connection to the same computer. It does not prevent access by other local processes. |
| `stdio` | The standard-input and standard-output transport for MCP messages. |

## Production and evidence

| Term | Project meaning and use |
| --- | --- |
| Cue | A data object with a name and an ordered list of permitted production steps. |
| Step | One command or explicit wait in a cue. |
| Dry run | Validation that returns a plan without changes to OBS. It can read OBS state. |
| Preview scene | The OBS scene prepared separately from the program scene. It is not a dry run. |
| Program scene | The scene that OBS uses for its program output. |
| Source | The OBS content element. A scene item places a source in a particular scene. |
| Scene item | A source placement with its own identifier, visibility, and transform in one scene. |
| Input | An OBS source with settings for content such as audio, video, text, or a browser page. |
| Filter | An OBS operation attached to a source. The filter can change picture or audio. |
| Transform | The position, rotation, scale, crop, alignment, and bounds of a scene item. |
| Output | An OBS recording, stream, replay buffer, or virtual camera. Tool support differs for each output. |
| Live change | In these controls, a change while recording or streaming is active. The `allow_live` gate tests those two states. |
| Capture session | One recording started and tracked by this server process. |
| Recording ownership | The server's evidence that it can stop the recording it started. A saved manifest alone does not supply ownership. |
| Manifest | A local file with capture identity, state, timestamps, paths, and evidence. |
| Receipt | The structured result of a command or cue, including failures and incomplete steps. |
| Event | A production message that selects a registered cue and supplies bounded text. |
| Route | A local mapping from one event type to one cue, with an optional text step. |
| Registry | The local set of permitted event routes. No routes exist by default. |
| Producer | The application that creates an event and controls its identifier. |
| Event identifier | The identity used to prevent repeated event execution within the current server process. |
| Duplicate execution | A further attempt to execute the same event. A server restart removes the server's prevention record. |
| Readback | A second OBS request used to compare the observed state with the requested state. |
| Completed file | A nonempty local file that passes the adapter's finalization checks. This does not prove correct media content. |
| Hash | A computed digest that identifies file contents. It does not measure picture or audio quality. |
| Frame health | Measurements of sampled frames, including near-black pixels and unchanged images. These measurements need interpretation. |
| Git index | The staged file content that Git would use for the next commit. It can differ from working files. |
| Source export | A selected directory of source files prepared for review or distribution. |
| Alpha | An early release for evaluation. This label does not imply production acceptance. |

## Technical verbs

These verbs have specific software meanings in this project.
Their use is limited to that subject field. The list is a project terminology decision, not a claim of dictionary approval.

| Verb | Meaning in this project |
| --- | --- |
| Configure | Set software parameters through a supported interface. |
| Run | Start a program or execute the steps of a cue. |
| Validate | Test a command or cue against its schema, supported operations, and applicable OBS state. |
| Preview | Produce a dry-run plan. Use "preview scene" when referring to the OBS scene instead. |
| Mute | Set an audio input's mute state to true. |
| Seek | Set a media input's playback position. |
| Hash | Compute a digest from file contents. |
| Publish | Upload an authorized release to the specified public destination. |

The noun "hash" names a digest. The verb "hash" describes its computation.
The documentation keeps these meanings distinct.
Code identifiers such as `allow_live`, `dry_run`, and `verified` keep their exact spelling.
