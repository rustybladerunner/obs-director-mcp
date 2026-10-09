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
DELUXE_FRAMES = 90
DELUXE_COVER_START_MS, DELUXE_COVER_END_MS, DELUXE_CUT_MS = 700, 2066, 1100


def _emblem(path: Path | None) -> Path:
    from PIL import Image
    if path is None:
        raise ValueError("The deluxe style requires a local transparent PNG emblem")
    path = path.resolve(strict=True)
    if str(path).startswith(("\\\\", "//")) or not path.is_file() or path.suffix.lower() != ".png" or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Supply a local PNG emblem of at most 4 MiB")
    with Image.open(path) as image:
        if image.format != "PNG" or image.mode != "RGBA" or not all(16 <= n <= 2048 for n in image.size):
            raise ValueError("The emblem must be a bounded RGBA PNG")
        alpha = image.getchannel("A")
        if alpha.getextrema() != (0, 255) or any(alpha.getpixel(point) != 0 for point in
                ((0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1))):
            raise ValueError("The emblem requires genuine transparent edges and an opaque subject")
    return path


def deluxe_filter(font: str) -> str:
    """Original geometric animation; bitmap identity is supplied separately."""
    market_line = "lt(abs(Y-(820-130*X/W-30*sin(X/160))),2)"
    art = ["[0:v]format=rgba",
           f"geq=r='if({market_line},86,12+7*(1-Y/H))':g='if({market_line},75,17+8*(1-Y/H))':b='if({market_line},51,24+10*(1-Y/H))':a=255"]
    # Quiet financial-chart geometry is decorative, never labeled as live data.
    for x in range(0, WIDTH, 120):
        art.append(f"drawbox=x={x}:y=0:w=1:h=ih:color=0x1C2632:t=fill")
    for y in range(0, HEIGHT, 120):
        art.append(f"drawbox=x=0:y={y}:w=iw:h=1:color=0x1C2632:t=fill")
    for i, x in enumerate(range(65, 1860, 86)):
        y = 850 - ((i * 47 + 37) % 170)
        height = 26 + (i * 17 % 68)
        color = "0x5B523F" if i % 3 else "0x38494D"
        art.extend((f"drawbox=x={x}:y={y-24}:w=2:h={height+48}:color={color}:t=fill",
                    f"drawbox=x={x-9}:y={y}:w=20:h={height}:color={color}:t=fill"))
    art.extend(("drawbox=x=0:y=76:w=iw:h=2:color=0x766448:t=fill",
                "drawbox=x=0:y=1000:w=iw:h=2:color=0x766448:t=fill",
                "drawbox=x=869:y=340:w=3:h=276:color=0xBDA16D:t=fill",
                f"drawtext=fontfile='{font}':text='PIPHOUND':fontsize=114:fontcolor=0xF2EBDA:x=917:y=370",
                f"drawtext=fontfile='{font}':text='THE TRADING TABLE':fontsize=40:fontcolor=0xBDA16D:x=924:y=514",
                "drawbox=x=923:y=601:w=696:h=2:color=0x60563F:t=fill",
                f"drawtext=fontfile='{font}':text='THE NEXT MOVE':fontsize=21:fontcolor=0xA8ADB3:x=925:y=627",
                f"drawtext=fontfile='{font}':text='DEMO BROADCAST':fontsize=18:fontcolor=0x7E8996:x=70:y=43",
                f"drawtext=fontfile='{font}':text='PRECISION  /  TIMING  /  CONTROL':fontsize=18:fontcolor=0x7E8996:x=1280:y=1030",
                "trim=end_frame=1[art]"))
    card = ("color=c=0x10161E:s=520x760:r=30:d=1,format=rgba,"
            "drawbox=x=3:y=3:w=514:h=754:color=0xAE905B:t=3,"
            "drawbox=x=17:y=17:w=486:h=726:color=0x3C3B35:t=1,"
            "drawbox=x=35:y=571:w=450:h=2:color=0x7B694B:t=fill,"
            f"drawtext=fontfile='{font}':text='P I P H O U N D':fontsize=35:fontcolor=0xDDD0B0:x=(w-tw)/2:y=610,"
            f"drawtext=fontfile='{font}':text='THE TRADING TABLE':fontsize=19:fontcolor=0x8C8F90:x=(w-tw)/2:y=665,"
            "trim=end_frame=1[card]")
    rear = ("color=c=0x695B40:s=520x760:r=30:d=1,format=rgba,"
            "drawbox=x=8:y=8:w=504:h=744:color=0xB69861:t=1,"
            "trim=end_frame=1,rotate=0.10:ow=rotw(0.10):oh=roth(0.10):c=none[rear]")
    middle = ("color=c=0x242B32:s=520x760:r=30:d=1,format=rgba,"
              "drawbox=x=6:y=6:w=508:h=748:color=0x88744E:t=2,"
              "trim=end_frame=1,rotate=0.025:ow=rotw(0.025):oh=roth(0.025):c=none[middle]")
    graph = [",".join(art), card, rear, middle,
             "[1:v]scale=450:450:flags=lanczos,format=rgba,trim=end_frame=1[emblem]",
             "[card][emblem]overlay=x=35:y=74:shortest=1:format=auto,rotate=-0.065:ow=rotw(-0.065):oh=roth(-0.065):c=none[crest]",
             "[art][rear]overlay=x=206:y=108:shortest=1:format=auto[stack0]",
             "[stack0][middle]overlay=x=245:y=123:shortest=1:format=auto[stack1]",
             "[stack1][crest]overlay=x=270:y=143:shortest=1:format=auto,loop=loop=89:size=1:start=0,setpts=N/(30*TB),split=4[p0][p1][p2][p3]",
             "color=c=black@0:s=1920x1080:r=30:d=3,format=rgba[clear]"]
    positions = ((-720, -160, -800, -160), (-1100, 180, -1200, 220),
                 (2020, -180, 2200, -220), (2700, 160, 2800, 160))
    for i, (start_x, start_y, exit_x, exit_y) in enumerate(positions):
        x = i * 480
        enter = f"(1-pow(1-clip((t-{i*0.08:.2f})/0.42,0,1),3))"
        leave = f"pow(clip((t-{2.10+i*0.08:.2f})/0.55,0,1),2)"
        move_x = f"if(lt(t,2.1),{start_x}+({x-start_x})*{enter},{x}+({exit_x-x})*{leave})"
        move_y = f"if(lt(t,2.1),{start_y}*(1-{enter}),{exit_y}*{leave})"
        graph.append(f"[p{i}]crop=480:1080:{x}:0[panel{i}]")
        prior = "clear" if i == 0 else f"stage{i-1}"
        graph.append(f"[{prior}][panel{i}]overlay=x='{move_x}':y='{move_y}':eval=frame:shortest=1:format=auto[stage{i}]")
    graph.append("[stage3]format=yuva420p[out]")
    return ";".join(graph)


def build(output: Path, ffmpeg: str, font_file: Path, *, style: str = "classic", emblem: Path | None = None) -> dict:
    if style not in ("classic", "deluxe"):
        raise ValueError("Style must be classic or deluxe")
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
    if style == "classic" and emblem is not None:
        raise ValueError("An emblem is used only by the deluxe style")
    emblem_path = _emblem(emblem) if style == "deluxe" else None
    emblem_hash = hashlib.sha256(emblem_path.read_bytes()).hexdigest() if emblem_path else None
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
    frames = DELUXE_FRAMES if style == "deluxe" else FRAMES
    command = [executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
               f"color=c=0x12171D:s={WIDTH}x{HEIGHT}:r={FPS}"]
    if emblem_path:
        command += ["-i", str(emblem_path), "-filter_complex_threads", "1", "-filter_complex", deluxe_filter(font), "-map", "[out]"]
    else:
        command += ["-vf", filters]
    command += ["-frames:v", str(frames), "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
               "-b:v", "0", "-lossless", "1", "-auto-alt-ref", "0", "-row-mt", "1", "-deadline", "good",
               "-cpu-used", "4", "-map_metadata", "-1", "-n", str(output)]
    subprocess.run(command, check=True, timeout=180, stdin=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("The encoder did not produce a nonempty video")
    if emblem_path and hashlib.sha256(emblem_path.read_bytes()).hexdigest() != emblem_hash:
        raise RuntimeError("The emblem changed during encoding; no success manifest was written")
    manifest = {"schema": "obs.overlay-stinger.v1", "file": output.name, "frames": frames, "fps": FPS,
                "width": WIDTH, "height": HEIGHT, "duration_ms": 3000 if emblem_path else 2200,
                "opaque_start_ms": DELUXE_COVER_START_MS if emblem_path else COVER_START_MS,
                "opaque_end_ms": DELUXE_COVER_END_MS if emblem_path else COVER_END_MS,
                "transition_point_ms": DELUXE_CUT_MS if emblem_path else CUT_MS, "audio": False,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
    if emblem_path:
        manifest.update(style="deluxe", emblem_sha256=emblem_hash)
    with output.with_suffix(".json").open("x", encoding="utf-8") as target:
        target.write(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--font-file", required=True, type=Path)
    parser.add_argument("--style", choices=("classic", "deluxe"), default="classic")
    parser.add_argument("--emblem", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.ffmpeg, args.font_file, style=args.style, emblem=args.emblem), indent=2))
