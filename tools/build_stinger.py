"""Build an original silent alpha stinger. No OBS or network connection is made."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

FPS = 30
FRAMES = 66
WIDTH, HEIGHT = 1920, 1080
COVER_START_MS, COVER_END_MS = 500, 1733
CUT_MS = 900


def build(output: Path, ffmpeg: str, font_file: Path) -> dict:
    output = output.resolve()
    font_file = font_file.resolve(strict=True)
    executable = shutil.which(ffmpeg)
    if not executable or not font_file.is_file():
        raise ValueError("Supply FFmpeg and a readable local font file")
    if output.exists() or output.with_suffix(".json").exists() or not output.parent.is_dir() or output.suffix.lower() != ".webm":
        raise ValueError("Choose a new .webm file in an existing local directory")
    if str(output).startswith(("\\\\", "//")) or str(font_file).startswith(("\\\\", "//")):
        raise ValueError("Network paths are not supported")
    if any(c in str(font_file) for c in "'[],;\n\r"):
        raise ValueError("The font path contains unsupported filter characters")
    font = font_file.as_posix().replace(":", r"\:")
    # A diagonal leading edge enters, holds opaque, and exits. The point is
    # deliberately within a wide hold, so scene selection can be read back.
    span = "(W+H*0.35)"
    edge = f"if(lt(T,0.5),{span}*T/0.5,if(lt(T,1.7666667),{span},{span}*(T-1.7666667)/0.4))"
    mask = f"if(lte(T,0),0,if(lt(T,0.5),if(lt(X+Y*0.35,{edge}),255,0),if(lt(T,1.7666667),255,if(gt(X+Y*0.35,{edge}),255,0))))"
    filters = (
        "format=rgba,"
        "drawbox=x=0:y=484:w=iw:h=4:color=0xC5A56A:t=fill,"
        "drawbox=x=0:y=712:w=iw:h=2:color=0xC5A56A:t=fill,"
        f"drawtext=fontfile='{font}':text='PIPHOUND':fontsize=116:fontcolor=0xF0EBDD:x=(w-tw)/2:y=530,"
        f"drawtext=fontfile='{font}':text='THE TRADING TABLE':fontsize=34:fontcolor=0xC5A56A:x=(w-tw)/2:y=424,"
        f"drawtext=fontfile='{font}':text='DEMO BROADCAST':fontsize=24:fontcolor=0xA8AAAF:x=(w-tw)/2:y=766,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{mask}',format=yuva420p"
    )
    command = [executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
               f"color=c=0x12171D:s={WIDTH}x{HEIGHT}:r={FPS}", "-vf", filters,
               "-frames:v", str(FRAMES), "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
               "-b:v", "0", "-lossless", "1", "-auto-alt-ref", "0", "-row-mt", "1", "-deadline", "good",
               "-cpu-used", "4", "-map_metadata", "-1", "-n", str(output)]
    subprocess.run(command, check=True, timeout=180, stdin=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    manifest = {"schema": "obs.overlay-stinger.v1", "file": output.name, "frames": FRAMES, "fps": FPS,
                "width": WIDTH, "height": HEIGHT, "duration_ms": 2200,
                "opaque_start_ms": COVER_START_MS, "opaque_end_ms": COVER_END_MS,
                "transition_point_ms": CUT_MS, "audio": False,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
    with output.with_suffix(".json").open("x", encoding="utf-8") as target:
        target.write(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--font-file", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.ffmpeg, args.font_file), indent=2))
