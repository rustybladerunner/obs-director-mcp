"""Rehearse the complete synthetic show over loopback. Offline plan unless --execute."""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import time

from mcp import ClientSession, StdioServerParameters
import demo_stream as demo

SCENES = ("starting-soon", "table", "standings", "replay", "break", "table", "ending")
PHASE_SECONDS = 6
STINGER = "Tournament Stinger"


def assets(directory: Path, stinger: Path) -> dict:
    directory = demo._local_path(directory)
    manifest = json.loads((directory / "scenes.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "obs.tournament-scenes.v1" or manifest.get("canvas") != {"width": 1920, "height": 1080}:
        raise ValueError("Supply a rendered tournament scene manifest")
    scenes = manifest.get("scenes", [])
    if len(scenes) != 6 or {row.get("id") for row in scenes} != set(SCENES):
        raise ValueError("The complete six-scene tournament show is required")
    for row in scenes:
        if row.get("file") != row["id"] + ".html" or row.get("name") != "Tournament " + {
                "table": "Table", "standings": "Standings", "replay": "Replay", "starting-soon": "Starting Soon",
                "break": "Break", "ending": "Ending"}[row["id"]]:
            raise ValueError("Unexpected tournament scene identity")
    hashes = manifest.get("hashes")
    if not isinstance(hashes, dict) or not 8 <= len(hashes) <= 16:
        raise ValueError("Tournament asset hashes are missing")
    for name, digest in hashes.items():
        if not isinstance(name, str) or Path(name).name != name or "\\" in name:
            raise ValueError("Tournament asset names must be local file names")
        path = demo._local_path(directory / name)
        if path.parent != directory or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("Tournament asset is missing or oversized")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Tournament assets changed after rendering")
    if any(row["file"] not in hashes for row in scenes) or not {"broadcast.js", "broadcast.css", "snapshot.json"} <= hashes.keys():
        raise ValueError("Tournament pages and scripts must have hashes")
    stinger = demo._local_path(stinger.resolve())
    metadata = json.loads(stinger.with_suffix(".json").read_text(encoding="utf-8"))
    # Only our generated timing contract is accepted by this demonstration.
    expected = {"schema": "obs.overlay-stinger.v1", "file": stinger.name, "frames": 66, "fps": 30,
                "width": 1920, "height": 1080, "duration_ms": 2200, "opaque_start_ms": 500,
                "opaque_end_ms": 1733, "transition_point_ms": 900, "audio": False}
    if (any(metadata.get(k) != v for k, v in expected.items()) or not stinger.is_file()
            or stinger.stat().st_size > 16 * 1024 * 1024
            or hashlib.sha256(stinger.read_bytes()).hexdigest() != metadata.get("sha256")):
        raise ValueError("Supply the unchanged stinger and manifest from build_stinger.py")
    return {"directory": directory, "scenes": {row["id"]: row for row in scenes}, "stinger": stinger}


def media_cursor(client, deadline=None) -> float:
    original = client.settings
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Stinger cursor deadline expired")
        client.settings = replace(original, timeout=min(original.timeout, remaining))
    try:
        state = client.request("GetMediaInputStatus", {"inputName": STINGER})
        if deadline is not None and time.monotonic() >= deadline:
            raise RuntimeError("Stinger cursor reply arrived after its deadline")
    finally:
        client.settings = original
    cursor = state.get("mediaCursor")
    if (state.get("mediaState") != "OBS_MEDIA_STATE_PLAYING" or type(cursor) not in (int, float)
            or not math.isfinite(cursor) or cursor < 0):
        raise RuntimeError("Overlay stinger playback is unproven")
    return cursor


async def stinger_cut(client, lease, call, previous, selected, ids, trace):
    """Switch only during the generated clip's opaque hold; do not retry a missed cut."""
    def guard():
        lease.observe()
        if client.request("GetCurrentProgramScene").get("currentProgramSceneName") != previous:
            raise RuntimeError("Program scene changed outside the tournament show")

    for scene in (previous, selected):
        guard()
        await call("obs_source_visibility", scene_name=scene, scene_item_id=ids[scene], enabled=True,
                   dry_run=False, allow_live=True)
    guard()
    # Ended FFmpeg media can expose a null cursor. Establish STOPPED first so
    # restart has a distinguishable, verified state transition on every play.
    await call("obs_media_action", source_name=STINGER, action="stop", dry_run=False, allow_live=True)
    guard()
    await call("obs_media_action", source_name=STINGER, action="restart", dry_run=False, allow_live=True)
    deadline = time.monotonic() + 4
    while True:
        guard()
        observed_at = time.monotonic()
        before = media_cursor(client, deadline)
        if before >= 900:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Stinger did not reach its transition point")
        await asyncio.sleep(0.02)
    if before > 1200:
        raise RuntimeError("Stinger cut window was missed; no scene change was sent")
    guard()
    if time.monotonic() >= deadline or before + (time.monotonic() - observed_at) * 1000 > 1200:
        raise RuntimeError("The final scene guard consumed the stinger cut window")
    await call("obs_select_scene", scene_name=selected, dry_run=False, allow_live=True)
    after = media_cursor(client, deadline)
    if not 500 <= before <= after <= 1733:
        raise RuntimeError("Scene change did not fit inside the stinger's opaque hold")
    trace.append({"step": "overlay_stinger_cut", "from": previous, "to": selected,
                  "cursor_before_ms": before, "cursor_after_ms": after, "covered": True})
    # The shared source stays active across the cut. Its transparent tail reveals
    # the next scene, then both scene items are hidden for a later restart.
    await asyncio.sleep(max(0, (2300 - after) / 1000))
    lease.observe()
    for scene in (previous, selected):
        lease.observe()
        if client.request("GetCurrentProgramScene").get("currentProgramSceneName") != selected:
            raise RuntimeError("Program scene changed during the stinger tail")
        await call("obs_source_visibility", scene_name=scene, scene_item_id=ids[scene], enabled=False,
                   dry_run=False, allow_live=True)


async def build_and_stream(client, receiver, lease, output, files, trace, profile, collection, **_options):
    env = dict(os.environ, OBS_MCP_DATA_DIR=str(output / "mcp-state"))
    env.pop("OBS_MCP_EVENT_ROUTES", None)
    params = StdioServerParameters(command=demo.sys.executable, args=["-B", str(demo.ROOT / "run_server.py")], env=env)
    with (output / "mcp-stderr.log").open("w", encoding="utf-8") as log:
        async with demo.hidden_stdio(params, log) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=60)) as mcp:
                await mcp.initialize()

                async def call(name, **arguments):
                    demo.rehearse.require_owned_selection(client, profile, collection)
                    result = await mcp.call_tool(name, arguments)
                    if result.isError:
                        raise RuntimeError("Tournament MCP operation failed: " + name)
                    value = result.structuredContent or json.loads(result.content[0].text)
                    trace.append({"tool": name, "result": value})
                    if value.get("verified") is False and not value.get("dry_run", False):
                        raise RuntimeError("Tournament readback was unverified: " + name)
                    return value

                async def edit(name, **arguments):
                    return await call(name, **arguments, dry_run=False, allow_live=False)

                await edit("obs_create_scene", scene_name="Tournament Inputs")
                await edit("obs_select_scene", scene_name="Tournament Inputs")
                await edit("obs_add_source", scene_name="Tournament Inputs", source_name=STINGER,
                           input_kind="ffmpeg_source", settings={"is_local_file": True, "local_file": str(files["stinger"]),
                           "looping": False, "restart_on_activate": False, "close_when_inactive": False,
                           "clear_on_media_end": False})
                await edit("obs_media_action", source_name=STINGER, action="stop")
                await edit("obs_media_action", source_name=STINGER, action="restart")
                await asyncio.sleep(2.4)
                if client.request("GetMediaInputStatus", {"inputName": STINGER}).get("mediaState") != "OBS_MEDIA_STATE_ENDED":
                    raise RuntimeError("Stinger decode did not reach its transparent final frame")
                ids = {}
                for key, row in files["scenes"].items():
                    name = "Tournament Page " + key
                    await edit("obs_add_source", scene_name="Tournament Inputs", source_name=name, input_kind="browser_source",
                               settings={"is_local_file": True, "local_file": str(files["directory"] / row["file"]),
                                         "width": 1920, "height": 1080, "fps": 30, "shutdown": False, "css": ""})
                    rect = {"x": 0, "y": 0, "width": 1920, "height": 1080}
                    recipe = {"schema": "obs.layout.v1", "scene_name": row["name"], "canvas": {"width": 1920, "height": 1080},
                              "layers": [{"id": "page", "type": "existing", "source_name": name, "rect": rect},
                                         {"id": "stinger", "type": "existing", "source_name": STINGER, "rect": rect, "visible": False}]}
                    await edit("obs_apply_layout", recipe=recipe)
                    tone = "Tournament Tone " + key
                    await edit("obs_add_source", scene_name=row["name"], source_name=tone,
                               input_kind="ffmpeg_source", settings={"is_local_file": True, "local_file": str(output / "tone.wav"), "looping": True})
                    await edit("obs_audio_volume", source_name=tone, volume_db=-24)
                    items = (await call("obs_scene_sources", scene_name=row["name"]))["sources"]
                    matches = [item for item in items if item.get("sourceName") == STINGER]
                    if len(matches) != 1 or type(matches[0].get("sceneItemId")) is not int:
                        raise RuntimeError("The overlay stinger item is unproven")
                    ids[row["name"]] = matches[0]["sceneItemId"]
                transitions = await call("obs_transitions")
                cuts = [row for row in transitions["transitions"] if row["kind"] == "cut_transition"]
                if len(cuts) != 1:
                    raise RuntimeError("A unique existing Cut transition is required for the overlay demo")
                await edit("obs_select_transition", transition_name=cuts[0]["name"])
                first = files["scenes"][SCENES[0]]["name"]
                await edit("obs_select_scene", scene_name=first)
                await asyncio.sleep(2)
                receiver.start()
                lease.start()
                samples = []
                previous = first
                started = time.monotonic()
                for index, key in enumerate(SCENES):
                    row = files["scenes"][key]
                    if index:
                        await stinger_cut(client, lease, call, previous, row["name"], ids, trace)
                    demo.program_screenshot(client, row["name"], output / f"phase-{index + 1}-{key}.png", profile, collection)
                    trace.append({"step": "show_phase", "scene": key, "elapsed_seconds": round(time.monotonic() - started, 3)})
                    previous = row["name"]
                    end = started + (index + 1) * PHASE_SECONDS
                    while time.monotonic() < end:
                        sample = lease.observe()
                        sample["elapsed_seconds"] = round(time.monotonic() - started, 3)
                        samples.append(sample)
                        await asyncio.sleep(min(0.5, max(0, end - time.monotonic())))
                trace.append({"step": "stream_samples", "samples": samples})
                if len(samples) < 2 or samples[-1]["output_bytes"] <= samples[0]["output_bytes"]:
                    raise RuntimeError("Tournament stream byte progression was not observed")
                lease.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-directory", required=True, type=Path)
    parser.add_argument("--stinger", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    asyncio.run(demo.run(args, asset_loader=lambda directory, **_: assets(directory, args.stinger),
                        builder=build_and_stream, show_name="tournament", duration_seconds=len(SCENES) * PHASE_SECONDS))
