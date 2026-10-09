"""Animate the original cutout as a 2D puppet with synthesized woofs and captions."""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import random
import shutil

import build_audio as audio

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, FPS, FRAMES = 960, 540, 30, 90
SECONDS = 3.0
MARGIN = 24
REACTIONS = {
    "welcome": {"caption": "Welcome to the table", "woofs": [(0.52, 0.24, 205), (1.12, 0.27, 175)],
                "angle": "0.042*sin(2*PI*t/1.5)*exp(-0.3*t)",
                "y": "8-11*exp(-24*pow(t-0.6,2))-7*exp(-24*pow(t-1.2,2))"},
    "rethink": {"caption": "Let’s look again", "woofs": [(0.63, 0.23, 180), (1.15, 0.25, 235), (1.72, 0.22, 195)],
                "angle": "-0.060*sin(PI*t/3)",
                "y": "8-5*exp(-20*pow(t-1.2,2))"},
    "break": {"caption": "Back in a moment", "woofs": [(0.55, 0.27, 190), (1.26, 0.29, 155)],
              "angle": "0.038*sin(2*PI*t/1.3)*exp(-0.25*t)",
              "y": "8-9*exp(-22*pow(t-0.65,2))-6*exp(-22*pow(t-1.35,2))"},
}


def synthesize_woofs(name, rate=audio.RATE):
    """Original nonverbal cartoon syllables: soft harmonic throat + filtered breath."""
    if name not in REACTIONS:
        raise ValueError("Unknown reaction")
    data = audio._buffer(SECONDS, rate)
    rng = random.Random(audio.SEED + list(REACTIONS).index(name) + 20)
    for index, (onset, duration, pitch) in enumerate(REACTIONS[name]["woofs"]):
        values, phase, breath = array("d"), 0.0, 0.0
        for i in range(round(duration * rate)):
            t = i / rate
            position = t / duration
            frequency = pitch * (1.18 - 0.40 * position + 0.04 * math.sin(math.tau * 9 * t))
            phase += math.tau * frequency / rate
            envelope = math.sin(math.pi * position) ** 1.7
            value = 0.0
            # Broad /u/-to-/o/ resonances provide a woody woof rather than a tone.
            for harmonic in range(1, 13):
                hz = harmonic * frequency
                first = math.exp(-((hz - (360 + 180 * position)) / 260) ** 2)
                second = 0.55 * math.exp(-((hz - 1050) / 430) ** 2)
                weight = (first + second + 0.06) / harmonic ** 0.75
                value += weight * math.sin(harmonic * phase + 0.12 * math.sin(phase * 0.5))
            breath += 0.10 * (rng.uniform(-1, 1) - breath)
            values.append(math.tanh(value * 1.35) * envelope + breath * 0.22 * envelope)
        audio._mix(data, round(onset * rate), values, 0.7, (-0.08 if index % 2 else 0.08))
    normalized = audio._finish(data, -9.0, rate, one_shot=True)
    # DC removal can leave a tiny offset in silent spans. Gate each syllable with
    # a short ramp so the gaps stay exactly silent without boundary clicks.
    result = audio._buffer(SECONDS, rate)
    ramp = max(1, round(0.004 * rate))
    for onset, duration, _ in REACTIONS[name]["woofs"]:
        start, count = round(onset * rate), round(duration * rate)
        for frame in range(count):
            gain = min(1.0, frame / ramp, (count - 1 - frame) / ramp)
            for channel in (0, 1):
                index = 2 * (start + frame) + channel
                result[index] = normalized[index] * gain
    return result


def _local_file(path):
    raw = str(path)
    if raw.startswith(("\\\\", "//")):
        raise ValueError("Use a local source file")
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Source file is missing or oversized")
    return path


def video_filter(name, font_file):
    if name not in REACTIONS:
        raise ValueError("Unknown reaction")
    if any(char in str(font_file) for char in "'[],;\n\r"):
        raise ValueError("Unsupported font path characters")
    font = Path(font_file).as_posix().replace(":", r"\:")
    spec = REACTIONS[name]
    return (
        "[0:v]scale=640:360:flags=lanczos,format=rgba,pad=800:460:80:50:color=black@0,"
        f"rotate='{spec['angle']}':ow=iw:oh=ih:c=none:bilinear=1[puppet];"
        f"[1:v][puppet]overlay=x=80:y='{spec['y']}':format=auto,format=rgba,"
        f"drawtext=fontfile='{font}':text='{spec['caption']}':fontsize=36:fontcolor=0xF0EBDD:"
        "borderw=2:bordercolor=0x15191F:x=(w-tw)/2:y=462,"
        "fade=t=in:st=0:d=0.2:alpha=1,fade=t=out:st=2.65:d=0.3:alpha=1,format=yuva420p[out]"
    )


def verify_alpha(raw, width=WIDTH, height=HEIGHT, expected_frames=FRAMES, margin=MARGIN):
    if expected_frames != FRAMES or not 0 < margin < min(width, height) / 2:
        raise ValueError("Alpha verification requires 90 frames and a bounded margin")
    frame_size = width * height
    if len(raw) != frame_size * expected_frames:
        raise ValueError("Decoded alpha has the wrong frame count or dimensions")
    facts = []
    for index in range(expected_frames):
        frame = raw[index * frame_size:(index + 1) * frame_size]
        border = frame[:margin * width] + frame[-margin * width:]
        for row in range(margin, height - margin):
            border += frame[row * width:row * width + margin] + frame[(row + 1) * width - margin:(row + 1) * width]
        fact = {"frame": index, "alpha_min": min(frame), "alpha_max": max(frame), "safe_border_max": max(border)}
        if fact["safe_border_max"] != 0:
            raise ValueError("Puppet or caption crossed the safe transparent margin")
        facts.append(fact)
    if facts[0]["alpha_max"] != 0 or facts[-1]["alpha_max"] != 0:
        raise ValueError("Reaction endpoints must be fully transparent")
    if any(facts[index]["alpha_max"] != 255 for index in (15, 30, 45, 60, 75)):
        raise ValueError("Reaction disappeared during its intended visible interval")
    return {"decoded_frames": expected_frames, "safe_margin_px": margin,
            "endpoint_alpha_max": [facts[0]["alpha_max"], facts[-1]["alpha_max"]], "frames": facts}


def _probe(executable, path, extra=()):
    return json.loads(audio._run([executable, "-v", "error", *extra, "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True).stdout)


def inspect_clip(path, ffmpeg, ffprobe):
    # Metadata before any decode. alpha_mode must survive into the WebM itself.
    probe = _probe(ffprobe, path)
    videos = [s for s in probe["streams"] if s.get("codec_type") == "video"]
    sounds = [s for s in probe["streams"] if s.get("codec_type") == "audio"]
    alpha_mode = {key.lower(): value for key, value in videos[0].get("tags", {}).items()}.get("alpha_mode") if videos else None
    if (len(videos) != 1 or videos[0].get("codec_name") != "vp9"
            or (videos[0].get("width"), videos[0].get("height")) != (WIDTH, HEIGHT)
            or alpha_mode != "1"
            or len(sounds) != 1 or sounds[0].get("codec_name") != "opus"
            or sounds[0].get("channels") != 2 or int(sounds[0].get("sample_rate", 0)) != audio.RATE):
        raise ValueError("Unexpected reaction video/audio stream contract")
    timing = json.loads(audio._run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=pts_time,width,height", "-of", "json", str(path)], capture_output=True).stdout)["frames"]
    timestamps = [float(row["pts_time"]) for row in timing]
    if (len(timing) != FRAMES or not 0 <= timestamps[0] <= 0.01
            or not 2.965 <= timestamps[-1] <= 2.977
            or any((row["width"], row["height"]) != (WIDTH, HEIGHT) for row in timing)
            or any(not 0.032 <= b - a <= 0.035 for a, b in zip(timestamps, timestamps[1:]))):
        raise ValueError("Decoded reaction timing is not 90 frames at 30 fps")
    alpha = audio._run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-c:v", "libvpx-vp9",
                       "-i", str(path), "-map", "0:v:0", "-vf", "alphaextract", "-fps_mode", "passthrough",
                       "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True).stdout
    alpha_facts = verify_alpha(alpha)
    decoded = audio._run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
                         "-map", "0:a:0", "-f", "f64le", "-acodec", "pcm_f64le", "pipe:1"], capture_output=True).stdout
    samples = array("d")
    samples.frombytes(decoded)
    if audio.sys.byteorder != "little":
        samples.byteswap()
    sound = audio.metrics(samples)
    if sound["frames"] != 144000 or sound["clipped_samples"] or sound["sample_peak_dbfs"] > -5:
        raise ValueError("Reaction audio has incorrect length or insufficient headroom")
    return {"container_duration_seconds": float(probe["format"]["duration"]),
            "first_video_pts": timestamps[0], "last_video_pts": timestamps[-1],
            "alpha_mode": alpha_mode, "alpha": alpha_facts, "audio": sound}


def build(output: Path, source: Path, font_file: Path, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    destination = audio._destination(output)
    source, font_file = _local_file(source), _local_file(font_file)
    encoder, probe = shutil.which(ffmpeg), shutil.which(ffprobe)
    if not encoder or not probe:
        raise ValueError("FFmpeg and FFprobe are required")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    image_probe = _probe(probe, source)
    images = [s for s in image_probe["streams"] if s.get("codec_type") == "video"]
    if len(images) != 1 or images[0].get("codec_name") != "png" or images[0].get("pix_fmt") != "rgba":
        raise ValueError("Supply the original RGBA PNG cutout")
    filters = {name: video_filter(name, font_file) for name in REACTIONS}
    destination.mkdir(exist_ok=False)
    records = {}
    for name, spec in REACTIONS.items():
        target = destination / (name + ".webm")
        samples = synthesize_woofs(name)
        command = [encoder, "-hide_banner", "-loglevel", "error", "-nostdin", "-loop", "1", "-framerate", str(FPS),
                   "-i", str(source), "-f", "lavfi", "-i", "color=c=black@0:s=960x540:r=30:d=3,format=rgba",
                   "-f", "s16le", "-ar", str(audio.RATE), "-ac", "2", "-i", "pipe:0",
                   "-filter_complex", filters[name], "-map", "[out]", "-map", "2:a:0", "-t", "3",
                   "-frames:v", str(FRAMES), "-c:v", "libvpx-vp9", "-crf", "24", "-b:v", "0",
                   "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4",
                   "-c:a", "libopus", "-b:a", "96k", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
                   "-map_metadata", "-1", "-metadata:s:v:0", "alpha_mode=1", "-n", str(target)]
        audio._run(command, input=audio.pcm16(samples), capture_output=True)
        if target.stat().st_size >= 4 * 1024 * 1024:
            raise ValueError("Reaction exceeds 4 MiB")
        facts = inspect_clip(target, encoder, probe)
        records[target.name] = {"caption": spec["caption"], "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                                "bytes": target.stat().st_size, "duration_seconds": SECONDS, "frames": FRAMES,
                                "fps": FPS, "width": WIDTH, "height": HEIGHT, "woof_onsets_seconds": [v[0] for v in spec["woofs"]],
                                "verification": facts}
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise ValueError("Source cutout changed while reactions were rendered")
    source_label = source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else "operator-supplied RGBA PNG"
    manifest = {"schema": "obs.puppet-reactions.v1", "technique": "2D whole-cutout tilt and bounce; no articulated lip sync",
                "source_art": source_label, "source_art_sha256": source_hash,
                "sound_provenance": "Original deterministic formant/noise woofs; no recordings, voice clones or external samples",
                "generator": "tools/build_reactions.py", "license": "MIT", "clips": records,
                "limitations": "Decoded alpha, timing and signal checks do not establish listening preference or actual OBS playback."}
    with (destination / "reactions-manifest.json").open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=ROOT / "examples/funded-desk/presenter.png")
    parser.add_argument("--font-file", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.source, args.font_file, args.ffmpeg, args.ffprobe), indent=2))
