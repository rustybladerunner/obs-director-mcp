# Original broadcast audio

The included music and effects are original procedural synthesis. They contain
no downloaded samples, recordings, borrowed melodies, voices or external-service
output. The score and generator are supplied under the repository's MIT license.

| File | Use | Timing |
| --- | --- | --- |
| `examples/tournament/audio/intermission.ogg` | Warm, sparse instrumental bed for Starting Soon, Break and Ending | 20 seconds, 8 bars, 96 BPM, 4/4 |
| `examples/tournament/audio/stinger.wav` | Paper/card whoosh, soft low impact and short sparkle | 3 seconds; impact at 1.1 seconds |
| `examples/tournament/audio/alert.wav` | Restrained short audience cue | 0.7 seconds |

All files are stereo at 48 kHz. The loop uses E-minor extended voicings with
modal ninths, a soft bass, sparse electric-key/pluck notes, pads and minimal
brush-like percussion. Tails wrap into the opening rather than fading the music
to silence. The stinger has no spoken words. Its impact matches a 1.1-second
visual reveal; changing the visual timing requires a new synchronization check.

## Rebuild

Use Python's standard library plus local FFmpeg and FFprobe executables. No Python
audio dependency or account is required. The output directory must not exist;
its parent must already exist. Choose a new output instead of overwriting a copy.

```sh
python tools/build_audio.py --output ./my-audio
```

The seeded waveform synthesis is deterministic. Encoded container bytes can vary
with FFmpeg/codec versions. The builder uses exclusive file creation and refuses
existing output directories, links and network paths. It leaves partial output
visible on failure; a success manifest is written only after all media checks.
Inspect a failed build and choose a new destination before retrying.

`audio-manifest.json` records the score, provenance, timing, file hashes and size,
plus source and decoded sample peak, RMS, DC mean, endpoints and seam step.
The music source peak is -8 dBFS; stinger and alert source peaks are -6 and -11
dBFS. These are sample peaks, not LUFS or true-peak measurements. Encoded music
can differ slightly; use the manifest's decoded measurements for that file.

FFprobe checks each file before FFmpeg decodes it. The build refuses incorrect
sample counts, channel/rate mismatches, clipping, insufficient headroom or an
excessive loop seam step. These checks establish measurable signal properties;
they do not establish that a listener likes the composition or that OBS loops it
without a gap. Listen to at least two loops and test the actual transition before
claiming audio acceptance.

## OBS integration

Use distinct named music, stinger and alert inputs. Keep music off in the live
desk by default. Starting Soon, Break and Ending can opt into the music bed.
Keep live microphone audio and audience support separate from these sources.
Source settings are shared across scenes: avoid overlapping copies that double
the same music, and do not use a music change to mute a presenter's microphone.

Choose an initial gain during rehearsal, then listen at ordinary volume with
speech and scene changes. Fade or duck the music using supported volume controls;
do not turn a source's normalized peak into a recommended broadcast gain.
Verify stinger onset/reveal, end silence, loop wrap, alert overlap and clipping in
the actual mix. Configuration receipts are not proof of audible output.

These files do not configure devices, start OBS, connect accounts or authorize
a public broadcast. The supplied files were synthesized and numerically checked;
listening and actual OBS-mix acceptance must be reported separately.
