"""Synthesize an original lounge loop and restrained effects, without samples or services."""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import stat
import subprocess
import sys
import wave

RATE = 48000
BPM = 96
BARS = 8
SEED = 481516
MUSIC_SECONDS = BARS * 4 * 60 / BPM
TAU = math.tau


def _buffer(seconds, rate):
    return array("d", [0.0]) * (round(seconds * rate) * 2)


def _mix(data, offset, samples, gain=1.0, pan=0.0, circular=False):
    frames = len(data) // 2
    left, right = math.sqrt((1 - pan) / 2) * gain, math.sqrt((1 + pan) / 2) * gain
    for i, value in enumerate(samples):
        position = offset + i
        if circular:
            position %= frames
        elif not 0 <= position < frames:
            continue
        data[position * 2] += value * left
        data[position * 2 + 1] += value * right


def _frequency(midi):
    return 440 * 2 ** ((midi - 69) / 12)


def _voice(midi, seconds, rate, kind="pluck", phase=0.0):
    frequency = _frequency(midi)
    count = round(seconds * rate)
    result = array("d")
    for i in range(count):
        t = i / rate
        if kind == "pad":
            attack = min(1.0, t / 0.48)
            release = min(1.0, max(0.0, (seconds - t) / 1.05))
            envelope = math.sin(attack * math.pi / 2) ** 2 * math.sin(release * math.pi / 2) ** 2
            value = (math.sin(TAU * frequency * 0.998 * t + phase)
                     + math.sin(TAU * frequency * 1.002 * t + phase + 0.32)
                     + 0.16 * math.sin(TAU * frequency * 2 * t + phase)) / 2.16
        elif kind == "bass":
            envelope = (1 - math.exp(-t * 75)) * math.exp(-t * 2.3) * min(1.0, (seconds - t) / 0.12)
            value = math.sin(TAU * frequency * t) + 0.19 * math.sin(TAU * frequency * 2 * t)
        else:
            envelope = (1 - math.exp(-t * 210)) * math.exp(-t * 3.8) * min(1.0, (seconds - t) / 0.1)
            # Short, rounded electric-key/plucked-string voice, not a bare beep.
            value = (math.sin(TAU * frequency * t + 0.34 * math.sin(TAU * frequency * 2 * t) * math.exp(-t * 7))
                     + 0.21 * math.sin(TAU * frequency * 2 * t) * math.exp(-t * 5)
                     + 0.07 * math.sin(TAU * frequency * 3 * t) * math.exp(-t * 10))
        result.append(value * envelope)
    return result


def _noise(seconds, rate, rng, kind="brush"):
    low = 0.0
    result = array("d")
    for i in range(round(seconds * rate)):
        t = i / rate
        raw = rng.uniform(-1, 1)
        low += 0.12 * (raw - low)
        if kind == "whoosh":
            envelope = math.sin(math.pi * t / seconds) ** 2
            value = low * 2.3 + (raw - low) * 0.13
        else:
            envelope = (1 - math.exp(-t * 700)) * math.exp(-t * 35)
            value = (raw - low) * 0.34 + low * 0.4
        result.append(value * envelope)
    return result


def _kick(seconds, rate):
    phase = 0.0
    result = array("d")
    for i in range(round(seconds * rate)):
        t = i / rate
        phase += TAU * (47 + 42 * math.exp(-t * 32)) / rate
        result.append(math.sin(phase) * (1 - math.exp(-t * 700)) * math.exp(-t * 17))
    return result


def _finish(data, peak_db, rate, one_shot=False):
    frames = len(data) // 2
    for channel in (0, 1):
        mean = math.fsum(data[channel::2]) / frames
        for i in range(channel, len(data), 2):
            value = data[i] - mean
            if one_shot:
                position = i // 2
                fade = min(1.0, position / (rate * 0.008), (frames - 1 - position) / (rate * 0.055))
                value *= max(0.0, fade) ** 2
            data[i] = value
    peak = max(map(abs, data))
    if not math.isfinite(peak) or peak <= 0:
        raise ValueError("Synthesis produced invalid or silent samples")
    scale = 10 ** (peak_db / 20) / peak
    return array("d", (value * scale for value in data))


def synthesize_music(rate=RATE):
    """Eight-bar periodic E-minor lounge sketch, with modal ninths and no borrowed melody."""
    beat = 60 / BPM
    data = _buffer(MUSIC_SECONDS, rate)
    rng = random.Random(SEED)
    chords = [(52, 55, 59, 62, 66), (48, 52, 55, 59, 62),
              (45, 48, 52, 55, 59), (47, 50, 54, 57, 61)]
    roots = [40, 36, 33, 35]
    for section, chord in enumerate(chords):
        start = section * 8 * beat
        for j, note in enumerate(chord):
            _mix(data, round(start * rate), _voice(note, 8 * beat + 0.85, rate, "pad", j * 0.37),
                 0.034, (j - 2) * 0.24, circular=True)
    # Sparse, syncopated voicing changes leave room for a presenter.
    motifs = [(2, 4, 3, 1), (3, 1, 4, 2), (4, 2, 1, 3), (2, 3, 4, 1)]
    for bar in range(BARS):
        chord, root = chords[bar // 2], roots[bar // 2]
        start = bar * 4 * beat
        for j, location in enumerate((0.5, 1.75, 2.5, 3.5)):
            note = chord[motifs[bar % 4][j]] + (12 if j == 2 and bar % 2 else 0)
            tone = _voice(note, 1.65, rate)
            onset = start + location * beat + (0.018 if j % 2 else 0)
            pan = -0.28 if (bar + j) % 2 else 0.28
            _mix(data, round(onset * rate), tone, 0.055 if j != 2 else 0.043, pan, True)
            _mix(data, round((onset + beat * 0.75) * rate), tone, 0.012, -pan, True)
        for location, note, gain in ((0, root, 0.12), (2.5, root + 7, 0.072)):
            _mix(data, round((start + location * beat) * rate), _voice(note, 1.0, rate, "bass"), gain, circular=True)
        for location, gain in ((0, 0.043), (2.75, 0.025)):
            _mix(data, round((start + location * beat) * rate), _kick(0.26, rate), gain, circular=True)
        for j, location in enumerate((0.5, 1.5, 2.5, 3.5)):
            _mix(data, round((start + location * beat + 0.018) * rate), _noise(0.13, rate, rng),
                 0.012 if j % 2 else 0.008, -0.2 if j % 2 else 0.2, True)
    # Tails wrap into the opening rather than fading the loop to silence.
    return _finish(data, -8.0, rate)


def synthesize_stinger(rate=RATE):
    data = _buffer(3.0, rate)
    rng = random.Random(SEED + 1)
    _mix(data, round(0.11 * rate), _noise(0.98, rate, rng, "whoosh"), 0.32, -0.18)
    _mix(data, round(0.25 * rate), _noise(0.88, rate, rng, "whoosh"), 0.20, 0.24)
    _mix(data, round(1.1 * rate), _kick(0.38, rate), 0.5)
    _mix(data, round(1.08 * rate), _noise(0.16, rate, rng), 0.08)
    for i, note in enumerate((76, 83, 86)):
        _mix(data, round((1.13 + i * 0.13) * rate), _voice(note, 1.25, rate), 0.06 - i * 0.011, (i - 1) * 0.35)
    return _finish(data, -6.0, rate, one_shot=True)


def synthesize_alert(rate=RATE):
    data = _buffer(0.7, rate)
    _mix(data, round(0.025 * rate), _voice(76, 0.6, rate), 0.16, -0.15)
    _mix(data, round(0.13 * rate), _voice(83, 0.5, rate), 0.10, 0.15)
    _mix(data, round(0.014 * rate), _noise(0.065, rate, random.Random(SEED + 2)), 0.028)
    return _finish(data, -11.0, rate, one_shot=True)


def pcm16(samples):
    """Refuse clipping/non-finite data instead of silently truncating it."""
    converted = array("h")
    for value in samples:
        if not math.isfinite(value) or abs(value) >= 1:
            raise ValueError("PCM sample is non-finite or would clip")
        converted.append(round(value * 32767))
    if sys.byteorder != "little":
        converted.byteswap()
    return converted.tobytes()


def metrics(samples, rate=RATE):
    if len(samples) < 4 or len(samples) % 2:
        raise ValueError("Metrics require nonempty interleaved stereo")
    peak = max(map(abs, samples))
    rms = math.sqrt(math.fsum(value * value for value in samples) / len(samples))
    db = lambda value: round(20 * math.log10(value), 4) if value > 0 else None
    return {"frames": len(samples) // 2, "duration_seconds": len(samples) / (rate * 2),
            "sample_peak_dbfs": db(peak), "rms_dbfs": db(rms),
            "dc_mean": [math.fsum(samples[c::2]) / (len(samples) // 2) for c in (0, 1)],
            "first_samples": list(samples[:2]), "last_samples": list(samples[-2:]),
            "loop_seam_step": max(abs(samples[c] - samples[-2 + c]) for c in (0, 1)),
            "max_adjacent_step": max(abs(samples[i] - samples[i - 2]) for i in range(2, len(samples))),
            "clipped_samples": sum(abs(value) >= 1 for value in samples)}


def _destination(output):
    raw = str(output)
    if raw.startswith(("\\\\", "//")):
        raise ValueError("Output must be a local directory")
    path = Path(output).absolute()
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise ValueError("Output cannot use links or reparse points")
    if path.exists() or not path.parent.is_dir():
        raise ValueError("Choose a new output directory with an existing parent")
    return path


def _run(command, **kwargs):
    return subprocess.run(command, check=True, timeout=90, stdin=subprocess.DEVNULL if "input" not in kwargs else None,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs)


def build(output: Path, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    destination = _destination(output)
    encoder, probe = shutil.which(ffmpeg), shutil.which(ffprobe)
    if not encoder or not probe:
        raise ValueError("FFmpeg and FFprobe are required")
    scores = {"intermission.ogg": synthesize_music(), "stinger.wav": synthesize_stinger(), "alert.wav": synthesize_alert()}
    destination.mkdir(exist_ok=False)
    records = {}
    for filename, samples in scores.items():
        path = destination / filename
        encoded = pcm16(samples)
        if path.suffix == ".ogg":
            _run([encoder, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "s16le", "-ar", str(RATE),
                  "-ac", "2", "-i", "pipe:0", "-c:a", "libvorbis", "-q:a", "5", "-fflags", "+bitexact",
                  "-flags:a", "+bitexact", "-map_metadata", "-1", "-n", str(path)], input=encoded, capture_output=True)
        else:
            with path.open("xb") as handle, wave.open(handle, "wb") as target:
                target.setnchannels(2)
                target.setsampwidth(2)
                target.setframerate(RATE)
                target.writeframes(encoded)
        if path.stat().st_size >= 4 * 1024 * 1024:
            raise ValueError("Audio artifact exceeds the 4 MiB bound")
        # Probe first; then decode to measure actual delivered audio, not just source floats.
        inspection = json.loads(_run([probe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True).stdout)
        streams = [item for item in inspection["streams"] if item.get("codec_type") == "audio"]
        if len(streams) != 1 or streams[0].get("channels") != 2 or int(streams[0].get("sample_rate", 0)) != RATE:
            raise ValueError("Encoded audio stream shape is unexpected")
        decoded = _run([encoder, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
                        "-map", "0:a:0", "-f", "f64le", "-acodec", "pcm_f64le", "pipe:1"], capture_output=True).stdout
        delivered = array("d")
        delivered.frombytes(decoded)
        if sys.byteorder != "little":
            delivered.byteswap()
        measured = metrics(delivered)
        if measured["frames"] != len(samples) // 2 or measured["clipped_samples"] or measured["sample_peak_dbfs"] > -4:
            raise ValueError("Decoded audio failed duration or headroom checks")
        if filename == "intermission.ogg" and measured["loop_seam_step"] > 0.02:
            raise ValueError("Decoded loop boundary has an excessive discontinuity")
        records[filename] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size,
                             "codec": streams[0]["codec_name"], "sample_rate": RATE, "channels": 2,
                             "source_pcm": metrics(samples), "decoded": measured}
    manifest = {"schema": "obs.original-audio.v1", "source": "Original procedural composition and synthesis; no external samples, recordings or services",
                "generator": "tools/build_audio.py", "seed": SEED, "license": "MIT", "tempo_bpm": BPM,
                "key": "E minor with modal ninths", "bars": BARS, "meter": "4/4", "music_seconds": MUSIC_SECONDS,
                "stinger_impact_seconds": 1.1, "intended_scenes": ["Starting Soon", "Break", "Ending"],
                "live_desk_music_default": "off", "artifacts": records,
                "limits": "Sample peak/RMS and seam measurements are not perceived-loudness, listening-quality, OBS-looping or broadcast acceptance."}
    with (destination / "audio-manifest.json").open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.ffmpeg, args.ffprobe), indent=2))
