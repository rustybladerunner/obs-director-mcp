# PipHound deluxe overlay stinger

This is an original three-second media overlay, not a native OBS transition.
It assembles four staggered charcoal panels around a layered card crest and a
decorative market motif, holds for the scene change, then opens outward. The
unlabeled candles and line are artwork, not observations or performance data.
The original `piphound-stinger.webm` remains unchanged.

## Asset provenance

`piphound-emblem.png` was generated with the built-in image-generation tool from
the existing original `../funded-desk/presenter.png` reference. The reference was
visually inspected first. True transparency was requested and retained; no
background-removal script, stock emblem or third-party brand art was used.

The exact prompt was:

> Use case: original broadcast emblem. Asset type: a polished transparent PNG emblem for a premium trading-table show transition. Reference image is the supplied PipHound dog: preserve its friendly white bulldog face, single warm tan patch over its left eye (viewer right), and signature brass aviator goggles resting on its forehead. Transform the reference into a compact sculptural head-and-collar crest, front-facing, centered, with a narrow brushed antique-gold rim shaped around the head silhouette. Premium restrained black enamel, champagne gold and warm ivory, subtle dimensional bevels and controlled highlights, sophisticated editorial sports-broadcast identity. Head only, no body, no large circular badge or shield backing; retain the recognizable ears. Bold readable shapes at 280px, clean crisp edges, generous empty space around the complete crest. TRUE transparent alpha background everywhere outside the emblem including inside open spaces. No text, letters, numbers, watermark, market logos or outside brands. Square composition.

The resulting PNG is 1254 × 1254 RGBA. Its alpha spans 0–255, including 905,721
fully transparent pixels. SHA-256:
`ca1b11d9d13aa1f4ef008968d013028294bdeb06e69b76f1a25ddbc6a19b8c6d`.

The video uses original procedural FFmpeg geometry, typography and motion.
The checked render used FFmpeg 7.1.1 and a locally installed Bahnschrift font;
the font file is not distributed. No upstream implementation was copied.

## Build and timing

```text
python tools/build_stinger.py --output NEW.webm --font-file FONT.ttf --style deluxe --emblem examples/tournament/piphound-emblem.png
```

Add `--ffmpeg` when FFmpeg is not on the executable search path. `NEW.webm` and
its JSON sidecar must not already exist. The legacy three-argument Python call
and default `--style classic` retain the original 2.2-second design.

The deluxe contract is 1920 × 1080, 90 frames at 30 fps, 3000 ms, with no audio
stream. The conservative opaque interval is 700–2066 ms; the requested scene
change point is 1100 ms. The first and last frames are fully transparent. Sound
may be synchronized separately; the video itself makes no sound.

The final video SHA-256 is
`df39f82ed3e49626367054ee4fbb878e83b189df97a7dd927fff5a4f9fa9a169`.
Its sidecar binds this video and the emblem by their hashes. Container metadata
may vary between otherwise identical builds; verify each new output separately.

## Verification boundary

The checked public file was fully decoded with FFmpeg's `libvpx-vp9` decoder,
then `alphaextract`. All 90 frames were examined. Frames 0 and 89 contain only
alpha 0; every pixel in frames 21–62 is alpha 255. Decoded timestamps contain 90
frames at 30 fps within the container's millisecond precision. ffprobe found one
video stream and no audio stream. Motion-phase PNGs were visually inspected.

Force `libvpx-vp9` when checking transparency: a decoder that discards the WebM
alpha plane can falsely make the clip appear opaque. These are file-level checks;
they do not prove OBS playback, the destination scene, synchronized sound, or an
actual covered cut. Verify those in a watched local rehearsal.
