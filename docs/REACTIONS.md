# PipHound puppet reactions

The showcase includes three short 2D puppet reactions. Each clip tilts and
bounces the existing cutout, shows a caption, and plays original synthesized
woofs. The animation moves the whole cutout. It does not animate the mouth or
claim articulated lip sync. The source image is unchanged.

| File | Caption | Woof onsets |
| --- | --- | --- |
| `welcome.webm` | Welcome to the table | 0.52 and 1.12 seconds |
| `rethink.webm` | Let’s look again | 0.63, 1.15 and 1.72 seconds |
| `break.webm` | Back in a moment | 0.55 and 1.26 seconds |

Each clip has 90 frames at 30 fps on a transparent 960 × 540 canvas. Its video
lasts 3 seconds. The first and last frames are fully transparent. All frames
keep a transparent margin of at least 24 pixels. The audio is stereo Opus at
48 kHz. Woofs come from deterministic harmonic and noise synthesis, without
animal recordings, voice clones, external samples, or intelligible speech.

## Rebuild

Run this command from the repository root. Select a local font file and a new
output directory whose parent already exists:

```powershell
python tools/build_reactions.py --output runtime/new-reactions --font-file path/to/font.ttf
```

The builder uses Python's standard library, the sibling `build_audio.py`
helper, and local FFmpeg/FFprobe executables. FFmpeg needs `libvpx-vp9`,
`libopus`, and the `drawtext` filter. Use `--ffmpeg` and `--ffprobe` to select
executable paths. The font is rendered into the video; its file is not copied.
The default cutout is [the presenter image](../examples/funded-desk/presenter.png).
`--source` can select another local RGBA PNG. Its digest is recorded, and an
external source path is not exposed in the manifest.

Existing output directories are refused. A failed build can leave partial
clips for inspection, but it does not write a success manifest. Keep that
directory and use a new one for a retry. Each clip must be smaller than 4 MiB.
The score, motions, and encoder options are fixed. Bitexact flags suppress
variable container metadata; byte identity across different FFmpeg versions,
font files, or systems is not promised.

## Check and use

`reactions-manifest.json` records source provenance, SHA-256 digests, captions,
timing, dimensions, decoded alpha for every frame, and audio measurements.
The builder probes each file before it decodes it. It verifies 90 video
timestamps, fully transparent endpoints, the safe margin, 144,000 decoded
audio frames, and headroom without clipped samples.

Keep the WebM `alpha_mode=1` video tag. Metadata stripping can remove that tag
even when alpha data remains in the bitstream. For inspection, explicitly
select the `libvpx-vp9` decoder; FFmpeg's native VP9 decoder can omit alpha.
The builder restores the tag after metadata stripping and checks both the
tag and the decoded pixels.

Use a separate OBS media input and volume control for reactions. Keep it
hidden until a deliberate cue needs it, play it once, and hide it after the
clip finishes. A reaction does not authorize a public broadcast or a live
trading assertion. These reusable captions contain no trading result.

Signal measurements do not establish listening preference, perceived loudness,
or correct OBS playback. Check the actual scene, transparency, sound level,
and completion before a public show. The authored clips are a simple puppet
treatment; they are not generated talking-character footage.
