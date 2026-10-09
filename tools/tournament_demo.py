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
import subprocess
import time

from mcp import ClientSession, StdioServerParameters
import demo_stream as demo

SCENES = ("starting-soon", "table", "standings", "replay", "break", "table", "ending")
PHASE_SECONDS = 6
STINGER = "Tournament Stinger"
CLASSIC = {"frames": 66, "duration_ms": 2200, "opaque_start_ms": 500,
           "opaque_end_ms": 1733, "transition_point_ms": 900}
DELUXE = {"frames": 90, "duration_ms": 3000, "opaque_start_ms": 700,
          "opaque_end_ms": 2066, "transition_point_ms": 1100}
INTERMISSIONS = frozenset({"starting-soon", "break", "ending"})
# Major changes use the show identity. Routine desk changes use a short dissolve.
HERO_CHANGES = frozenset({("starting-soon", "table"), ("standings", "replay"), ("table", "ending")})
REACTION_PHASES = {1: "welcome", 3: "rethink", 4: "break"}
REACTION_SCENES = {"table": "welcome", "replay": "rethink", "break": "break"}
REACTION_RECTS = {
    "table": {"x": 1344, "y": 320, "width": 512, "height": 288},
    "replay": {"x": 1420, "y": 820, "width": 400, "height": 225},
    "break": {"x": 720, "y": 735, "width": 400, "height": 225},
}


def reaction_assets(directory: Path) -> dict:
    """Validate the exact local reaction pack before connecting to OBS."""
    directory = demo._local_path(directory.absolute())
    manifest_path = demo._local_path(directory / "reactions-manifest.json")
    if manifest_path.parent != directory or not manifest_path.is_file() or manifest_path.stat().st_size > 128 * 1024:
        raise ValueError("Supply a bounded local reactions manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    clips = manifest.get("clips") if isinstance(manifest, dict) else None
    names = {name + ".webm" for name in REACTION_PHASES.values()}
    if not isinstance(manifest, dict) or manifest.get("schema") != "obs.puppet-reactions.v1" or not isinstance(clips, dict) or set(clips) != names:
        raise ValueError("Supply the complete three-clip reaction pack")
    paths = {}
    expected = {"duration_seconds": 3, "frames": 90, "fps": 30, "width": 960, "height": 540}
    for name in sorted(names):
        row = clips[name]
        path = demo._local_path(directory / name)
        if (not isinstance(row, dict) or any(type(row.get(k)) not in (int, float) or row[k] != v for k, v in expected.items())
                or path.parent != directory or not path.is_file() or not 0 < path.stat().st_size <= 4 * 1024 * 1024
                or type(row.get("bytes")) is not int or path.stat().st_size != row["bytes"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != row.get("sha256")):
            raise ValueError("Reaction asset changed or has an unexpected format")
        verification = row.get("verification")
        if (not isinstance(verification, dict) or verification.get("alpha_mode") != "1"
                or not isinstance(verification.get("audio"), dict)
                or type(verification["audio"].get("frames")) is not int
                or verification["audio"]["frames"] != 144000):
            raise ValueError("Reaction alpha or audio verification is missing")
        paths[Path(name).stem] = path
    return paths


def reaction_source(name: str) -> str:
    if name not in REACTION_PHASES.values():
        raise ValueError("Unknown tournament reaction")
    return "Tournament Reaction " + name.title()


def phase_seconds(files: dict) -> int:
    return 8 if files.get("reactions") else PHASE_SECONDS


def reaction_layer(key: str, files: dict) -> dict | None:
    name = REACTION_SCENES.get(key)
    if not files.get("reactions") or name is None:
        return None
    return {"id": "reaction", "type": "existing", "source_name": reaction_source(name),
            "rect": dict(REACTION_RECTS[key]), "visible": False}


def browser_css(key: str, files: dict) -> str:
    """Reserve chart-right space only in the optional reaction-enabled table."""
    return ".chart-panel:not(.replay-panel) .chart{width:calc(100% - 440px)}" if key == "table" and files.get("reactions") else ""


def audio_assets(directory: Path) -> dict:
    """Verify bounded local soundtrack bytes before any OBS operation."""
    directory = demo._local_path(directory.resolve())
    manifest = json.loads((directory / "audio-manifest.json").read_text(encoding="utf-8"))
    names = {"intermission.ogg": 960000, "stinger.wav": 144000, "alert.wav": 33600}
    artifacts = manifest.get("artifacts")
    if (manifest.get("schema") != "obs.original-audio.v1" or not isinstance(artifacts, dict)
            or set(artifacts) != set(names) or manifest.get("stinger_impact_seconds") != 1.1):
        raise ValueError("Supply the original audio pack and its manifest")
    paths = {}
    for name, frames in names.items():
        path = demo._local_path(directory / name)
        row = artifacts[name]
        if (path.parent != directory or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024
                or row.get("sample_rate") != 48000 or row.get("channels") != 2
                or row.get("decoded", {}).get("frames") != frames
                or path.stat().st_size != row.get("bytes")
                or hashlib.sha256(path.read_bytes()).hexdigest() != row.get("sha256")):
            raise ValueError("Audio asset changed or has an unexpected format")
        paths[name] = path
    return paths


def mux_soundtrack(files, output: Path, ffmpeg: str) -> Path | None:
    """Use one playback clock for the stinger's picture and original effect."""
    if not files.get("audio"):
        return files["stinger"]
    target = output / "stinger-with-sound.webm"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                    "-i", str(files["stinger"]), "-i", str(files["audio"]["stinger.wav"]),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "libopus",
                    "-b:a", "160k", "-map_metadata", "-1", "-metadata:s:v:0", "alpha_mode=1", str(target)],
                   check=True, timeout=30, capture_output=True)
    return target


def assets(directory: Path, stinger: Path | None = None, audio_directory: Path | None = None,
           reactions_directory: Path | None = None, reaction_volume_db: float = -6) -> dict:
    if type(reaction_volume_db) not in (int, float) or not -60 <= reaction_volume_db <= 0:
        raise ValueError("Reaction volume must be between -60 and 0 dB")
    reactions = reaction_assets(reactions_directory) if reactions_directory is not None else None
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
    if stinger is None:
        if audio_directory is not None:
            raise ValueError("The audio showcase requires the deluxe stinger")
        return {"directory": directory, "scenes": {row["id"]: row for row in scenes},
                "stinger": None, "timing": None, "audio": None,
                "reactions": reactions, "reaction_volume_db": reaction_volume_db}
    stinger = demo._local_path(stinger.resolve())
    metadata = json.loads(stinger.with_suffix(".json").read_text(encoding="utf-8"))
    # Only our generated timing contract is accepted by this demonstration.
    timing = DELUXE if metadata.get("style") == "deluxe" else CLASSIC
    expected = {"schema": "obs.overlay-stinger.v1", "file": stinger.name, "fps": 30,
                "width": 1920, "height": 1080, "audio": False, **timing}
    if (any(metadata.get(k) != v for k, v in expected.items()) or not stinger.is_file()
            or stinger.stat().st_size > 16 * 1024 * 1024
            or hashlib.sha256(stinger.read_bytes()).hexdigest() != metadata.get("sha256")):
        raise ValueError("Supply the unchanged stinger and manifest from build_stinger.py")
    audio = audio_assets(audio_directory) if audio_directory is not None else None
    if audio and timing != DELUXE:
        raise ValueError("The three-second soundtrack requires the deluxe stinger")
    return {"directory": directory, "scenes": {row["id"]: row for row in scenes},
            "stinger": stinger, "timing": dict(timing), "audio": audio,
            "reactions": reactions, "reaction_volume_db": reaction_volume_db}


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


def wait_reaction_geometry(client, profile, collection, scene_item_id, timeout=5.0) -> dict:
    """Require decoded dimensions in the isolated, idle selection before layout."""
    if type(scene_item_id) is not int or scene_item_id < 0:
        raise RuntimeError("Reaction scene item is unproven")
    deadline = time.monotonic() + timeout
    settings = client.settings

    def read(name, data=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Reaction geometry deadline expired")
        client.settings = replace(settings, timeout=min(settings.timeout, remaining))
        try:
            result = client.request(name, data)
            if time.monotonic() >= deadline:
                raise RuntimeError("Reaction geometry reply arrived after its deadline")
            return result
        finally:
            client.settings = settings

    def owned_idle():
        if (read("GetProfileList").get("currentProfileName") != profile
                or read("GetSceneCollectionList").get("currentSceneCollectionName") != collection
                or read("GetRecordStatus").get("outputActive") is not False
                or read("GetStreamStatus").get("outputActive") is not False):
            raise RuntimeError("Reaction preparation requires the owned idle selection")

    while True:
        owned_idle()
        transform = read("GetSceneItemTransform", {"sceneName": "Tournament Inputs", "sceneItemId": scene_item_id}).get("sceneItemTransform")
        owned_idle()
        if not isinstance(transform, dict) or any(type(transform.get(k)) not in (int, float)
                or not 0 <= transform[k] <= 32768 for k in ("sourceWidth", "sourceHeight")):
            raise RuntimeError("OBS returned malformed reaction geometry")
        if (transform["sourceWidth"], transform["sourceHeight"]) == (960, 540):
            return {"width": 960, "height": 540, "verified": True}
        if transform["sourceWidth"] > 0 and transform["sourceHeight"] > 0:
            raise RuntimeError("Decoded reaction dimensions differ from its manifest")
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def scene_item_id(sources, source_name):
    matches = [item for item in sources if item.get("sourceName") == source_name]
    if len(matches) != 1 or type(matches[0].get("sceneItemId")) is not int or matches[0]["sceneItemId"] < 0:
        raise RuntimeError("The tournament media scene item is unproven")
    return matches[0]["sceneItemId"]


async def prime_reactions(client, edit, call, files, trace, profile, collection) -> None:
    """Decode each dedicated input while idle, then leave its staging item hidden."""
    for name, path in (files.get("reactions") or {}).items():
        source = reaction_source(name)
        await edit("obs_add_source", scene_name="Tournament Inputs", source_name=source,
                   input_kind="ffmpeg_source", settings={"is_local_file": True, "local_file": str(path),
                   "looping": False, "restart_on_activate": False, "close_when_inactive": False,
                   "clear_on_media_end": False})
        await edit("obs_audio_mute", source_name=source, muted=True)
        items = (await call("obs_scene_sources", scene_name="Tournament Inputs"))["sources"]
        item_id = scene_item_id(items, source)
        await edit("obs_media_action", source_name=source, action="stop")
        await edit("obs_media_action", source_name=source, action="restart")
        geometry = wait_reaction_geometry(client, profile, collection, item_id)
        await asyncio.sleep(3.2)
        demo.rehearse.require_owned_selection(client, profile, collection)
        if client.request("GetMediaInputStatus", {"inputName": source}).get("mediaState") != "OBS_MEDIA_STATE_ENDED":
            raise RuntimeError("Reaction prime did not reach its transparent final frame")
        wait_reaction_geometry(client, profile, collection, item_id)
        await edit("obs_source_visibility", scene_name="Tournament Inputs", scene_item_id=item_id, enabled=False)
        await edit("obs_audio_volume", source_name=source, volume_db=files.get("reaction_volume_db", -6))
        await edit("obs_audio_mute", source_name=source, muted=False)
        trace.append({"step": "reaction_primed", "reaction": name, "geometry": geometry})


async def play_reaction(client, lease, call, selected, name, item_id, trace, capture) -> None:
    """Play once under the current lease; capture visible pixels, then verify end/hide."""
    source = reaction_source(name)

    def guard():
        lease.observe()
        if client.request("GetCurrentProgramScene").get("currentProgramSceneName") != selected:
            raise RuntimeError("Program scene changed during the reaction")
        lease.states()

    for tool, arguments in (
            ("obs_source_visibility", {"scene_name": selected, "scene_item_id": item_id, "enabled": True}),
            ("obs_media_action", {"source_name": source, "action": "stop"}),
            ("obs_media_action", {"source_name": source, "action": "restart"})):
        guard()
        await call(tool, **arguments, dry_run=False, allow_live=True)
    deadline = time.monotonic() + 5
    settings = client.settings
    captured = False

    def read_status():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Reaction completion deadline expired")
        client.settings = replace(settings, timeout=min(settings.timeout, remaining))
        try:
            state = client.request("GetMediaInputStatus", {"inputName": source})
            if time.monotonic() >= deadline:
                raise RuntimeError("Reaction status arrived after its deadline")
            return state
        finally:
            client.settings = settings

    def within_deadline():
        if time.monotonic() >= deadline:
            raise RuntimeError("Reaction completion deadline expired")

    while True:
        guard()
        observed_at = time.monotonic()
        state = read_status()
        guard()
        within_deadline()
        if state.get("mediaState") == "OBS_MEDIA_STATE_ENDED":
            if not captured:
                raise RuntimeError("Reaction ended before visible evidence was captured")
            break
        cursor = state.get("mediaCursor")
        if (state.get("mediaState") != "OBS_MEDIA_STATE_PLAYING" or type(cursor) not in (int, float)
                or not 0 <= cursor <= 3100):
            raise RuntimeError("Reaction playback is unproven")
        if not captured and cursor >= 1400:
            if cursor + (time.monotonic() - observed_at) * 1000 > 2100:
                raise RuntimeError("Reaction screenshot window was missed")
            capture()
            guard()
            after = read_status()
            guard()
            within_deadline()
            post_cursor = after.get("mediaCursor")
            if (after.get("mediaState") != "OBS_MEDIA_STATE_PLAYING" or type(post_cursor) not in (int, float)
                    or not cursor <= post_cursor <= 2500
                    or cursor + (time.monotonic() - observed_at) * 1000 > 2500):
                raise RuntimeError("Reaction screenshot outlasted its visible evidence window")
            trace.append({"step": "reaction_visible", "reaction": name, "scene": selected,
                          "cursor_before_ms": cursor, "cursor_after_ms": post_cursor})
            captured = True
        await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
    guard()
    within_deadline()
    await call("obs_source_visibility", scene_name=selected, scene_item_id=item_id, enabled=False,
               dry_run=False, allow_live=True)
    trace.append({"step": "reaction_completed", "reaction": name, "scene": selected, "verified": True})


async def stinger_cut(client, lease, call, previous, selected, ids, trace, *, timing=None):
    """Switch only during the generated clip's opaque hold; do not retry a missed cut."""
    timing = CLASSIC if timing is None else timing
    if timing not in (CLASSIC, DELUXE):
        raise ValueError("Unknown certified stinger timing")
    # Leave at least half a second for the scene RPC inside the opaque hold.
    latest_start = timing["opaque_end_ms"] - 533
    def guard():
        lease.observe()
        if client.request("GetCurrentProgramScene").get("currentProgramSceneName") != previous:
            raise RuntimeError("Program scene changed outside the tournament show")
        lease.states()  # The final read can also receive a queued stop/reconnect event.

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
        if before >= timing["transition_point_ms"]:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Stinger did not reach its transition point")
        await asyncio.sleep(0.02)
    if before > latest_start:
        raise RuntimeError("Stinger cut window was missed; no scene change was sent")
    guard()
    if time.monotonic() >= deadline or before + (time.monotonic() - observed_at) * 1000 > latest_start:
        raise RuntimeError("The final scene guard consumed the stinger cut window")
    await call("obs_select_scene", scene_name=selected, dry_run=False, allow_live=True)
    after = media_cursor(client, deadline)
    if not timing["opaque_start_ms"] <= before <= after <= timing["opaque_end_ms"]:
        raise RuntimeError("Scene change did not fit inside the stinger's opaque hold")
    trace.append({"step": "overlay_stinger_cut", "from": previous, "to": selected,
                  "cursor_before_ms": before, "cursor_after_ms": after, "covered": True})
    # The shared source stays active across the cut. Its transparent tail reveals
    # the next scene, then both scene items are hidden for a later restart.
    await asyncio.sleep(max(0, (timing["duration_ms"] + 100 - after) / 1000))
    lease.observe()
    for scene in (previous, selected):
        lease.observe()
        if client.request("GetCurrentProgramScene").get("currentProgramSceneName") != selected:
            raise RuntimeError("Program scene changed during the stinger tail")
        lease.states()
        await call("obs_source_visibility", scene_name=scene, scene_item_id=ids[scene], enabled=False,
                   dry_run=False, allow_live=True)


async def build_and_stream(client, receiver, lease, output, files, trace, profile, collection, **_options):
    clip = mux_soundtrack(files, output, receiver.executable)
    env = dict(os.environ, OBS_MCP_DATA_DIR=str(output / "mcp-state"))
    env.pop("OBS_MCP_EVENT_ROUTES", None)
    params = StdioServerParameters(command=demo.sys.executable, args=["-B", str(demo.ROOT / "run_server.py")], env=env)
    with (output / "mcp-stderr.log").open("w", encoding="utf-8") as log:
        async with demo.hidden_stdio(params, log) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=60)) as mcp:
                await mcp.initialize()

                async def call(name, **arguments):
                    demo.rehearse.require_owned_selection(client, profile, collection)
                    if lease.confirmed and not lease.stopped:
                        lease.states()
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
                if clip:
                    await edit("obs_add_source", scene_name="Tournament Inputs", source_name=STINGER,
                               input_kind="ffmpeg_source", settings={"is_local_file": True, "local_file": str(clip),
                               "looping": False, "restart_on_activate": False, "close_when_inactive": False,
                               "clear_on_media_end": False})
                    await edit("obs_media_action", source_name=STINGER, action="stop")
                    await edit("obs_media_action", source_name=STINGER, action="restart")
                    await asyncio.sleep(files["timing"]["duration_ms"] / 1000 + 0.2)
                    if client.request("GetMediaInputStatus", {"inputName": STINGER}).get("mediaState") != "OBS_MEDIA_STATE_ENDED":
                        raise RuntimeError("Stinger decode did not reach its transparent final frame")
                if files.get("audio"):
                    await edit("obs_audio_volume", source_name=STINGER, volume_db=-6)
                    await edit("obs_audio_mute", source_name=STINGER, muted=False)
                await prime_reactions(client, edit, call, files, trace, profile, collection)
                ids = {}
                reaction_ids = {}
                for key, row in files["scenes"].items():
                    name = "Tournament Page " + key
                    await edit("obs_add_source", scene_name="Tournament Inputs", source_name=name, input_kind="browser_source",
                               settings={"is_local_file": True, "local_file": str(files["directory"] / row["file"]),
                                         "width": 1920, "height": 1080, "fps": 30, "shutdown": False, "css": browser_css(key, files)})
                    rect = {"x": 0, "y": 0, "width": 1920, "height": 1080}
                    recipe = {"schema": "obs.layout.v1", "scene_name": row["name"], "canvas": {"width": 1920, "height": 1080},
                              "layers": [{"id": "page", "type": "existing", "source_name": name, "rect": rect}]}
                    layer = reaction_layer(key, files)
                    if layer:
                        recipe["layers"].append(layer)
                    if clip:
                        recipe["layers"].append({"id": "stinger", "type": "existing", "source_name": STINGER, "rect": rect, "visible": False})
                    await edit("obs_apply_layout", recipe=recipe)
                    if files.get("audio") and key in INTERMISSIONS:
                        music = "Tournament Music " + key
                        await edit("obs_add_source", scene_name=row["name"], source_name=music,
                                   input_kind="ffmpeg_source", settings={"is_local_file": True,
                                   "local_file": str(files["audio"]["intermission.ogg"]), "looping": True,
                                   "restart_on_activate": True, "close_when_inactive": True})
                        await edit("obs_audio_volume", source_name=music, volume_db=-8)
                        await edit("obs_audio_mute", source_name=music, muted=False)
                    if clip or layer:
                        items = (await call("obs_scene_sources", scene_name=row["name"]))["sources"]
                        if clip:
                            ids[row["name"]] = scene_item_id(items, STINGER)
                        if layer:
                            reaction_ids[row["name"]] = scene_item_id(items, layer["source_name"])
                transitions = await call("obs_transitions")
                cuts = [row for row in transitions["transitions"] if row["kind"] == "cut_transition"]
                fades = [row for row in transitions["transitions"] if row["kind"] == "fade_transition"]
                if len(cuts) != 1:
                    raise RuntimeError("A unique existing Cut transition is required for the overlay demo")
                if len(fades) != 1:
                    raise RuntimeError("A unique existing Fade transition is required for routine changes")
                await edit("obs_select_transition", transition_name=cuts[0]["name"])
                first = files["scenes"][SCENES[0]]["name"]
                await edit("obs_select_scene", scene_name=first)
                await asyncio.sleep(2)
                receiver.start()
                lease.start()
                samples = []
                previous = first
                previous_key = SCENES[0]
                started = time.monotonic()
                for index, key in enumerate(SCENES):
                    row = files["scenes"][key]
                    if index:
                        lease.observe()
                        if client.request("GetCurrentProgramScene").get("currentProgramSceneName") != previous:
                            raise RuntimeError("Program changed outside the tournament show")
                        lease.states()
                        if clip and (previous_key, key) in HERO_CHANGES:
                            await call("obs_select_transition", transition_name=cuts[0]["name"], dry_run=False, allow_live=True)
                            await stinger_cut(client, lease, call, previous, row["name"], ids, trace, timing=files["timing"])
                        else:
                            await call("obs_select_transition", transition_name=fades[0]["name"], duration_ms=300,
                                       dry_run=False, allow_live=True)
                            lease.observe()
                            if client.request("GetCurrentProgramScene").get("currentProgramSceneName") != previous:
                                raise RuntimeError("Program changed before routine transition")
                            lease.states()
                            await call("obs_select_scene", scene_name=row["name"], dry_run=False, allow_live=True)
                            await asyncio.sleep(0.4)
                            lease.observe()
                            if client.request("GetCurrentProgramScene").get("currentProgramSceneName") != row["name"]:
                                raise RuntimeError("Routine scene transition was not retained")
                            lease.states()
                            trace.append({"step": "routine_scene_change", "from": previous, "to": row["name"], "duration_ms": 300})
                    reaction = REACTION_PHASES.get(index) if files.get("reactions") else None
                    if reaction:
                        await play_reaction(client, lease, call, row["name"], reaction, reaction_ids[row["name"]], trace,
                            lambda: demo.program_screenshot(client, row["name"], output / f"reaction-{reaction}.png", profile, collection))
                    demo.program_screenshot(client, row["name"], output / f"phase-{index + 1}-{key}.png", profile, collection)
                    trace.append({"step": "show_phase", "scene": key, "elapsed_seconds": round(time.monotonic() - started, 3)})
                    previous = row["name"]
                    previous_key = key
                    end = started + (index + 1) * phase_seconds(files)
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
    parser.add_argument("--stinger", type=Path, help="Optional classic or deluxe overlay; otherwise use short dissolves")
    parser.add_argument("--audio-directory", type=Path, help="Optional original music and deluxe stinger effect pack")
    parser.add_argument("--reactions-directory", type=Path, help="Optional verified three-clip puppet reaction pack")
    parser.add_argument("--reaction-volume-db", type=float, default=-6, help="Reaction gain only, -60 to 0 dB (default -6)")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    asyncio.run(demo.run(args, asset_loader=lambda directory, **_: assets(directory, args.stinger, args.audio_directory,
                        args.reactions_directory, args.reaction_volume_db), builder=build_and_stream,
                        show_name="tournament", duration_seconds=len(SCENES) * (8 if args.reactions_directory else PHASE_SECONDS)))
