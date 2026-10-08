"""Run an isolated, local OBS rehearsal through the real MCP stdio connection.

Use --execute to create a new scene collection and profile. The script refuses
active outputs and restores the original selection after its own recordings end.
The output folder contains local evidence. Do not commit that folder.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import wave

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from obs_director.transport import ObsClient, ObsError, ObsSettings
from obs_director.events import build_demo_registry


def idle(client):
    for request in ("GetRecordStatus", "GetStreamStatus", "GetVirtualCamStatus"):
        if client.request(request).get("outputActive") is not False:
            raise RuntimeError("OBS must have no active recording, stream, or virtual camera")
    try:
        active = client.request("GetReplayBufferStatus").get("outputActive")
    except ObsError as exc:
        # OBS RequestHandler_Outputs.cpp: unavailable replay output ->
        # InvalidResourceState (604). The transport exposes a sanitized message.
        # Every other failure leaves output activity unknown and must refuse.
        if str(exc) == "OBS rejected GetReplayBufferStatus (code 604)":
            return
        raise
    if active is not False:
        raise RuntimeError("Stop the existing replay buffer before this rehearsal")


def require_owned_selection(client, profile, collection):
    """Creation acknowledgements do not authorize editing another profile/collection."""
    if (client.request("GetProfileList").get("currentProfileName") != profile
            or client.request("GetSceneCollectionList").get("currentSceneCollectionName") != collection):
        raise RuntimeError("Rehearsal profile and collection are not selected; configuration is refused")


def wait_for_selection(client, profile, collection, timeout=10.0):
    """Observe completion of an asynchronous selection within a bounded deadline."""
    deadline = time.monotonic() + timeout
    settings = client.settings

    def read(request):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Requested OBS profile and collection were not selected before the timeout")
        client.settings = ObsSettings(settings.port, settings.password, min(settings.timeout, remaining))
        try:
            return client.request(request)
        finally:
            client.settings = settings

    while True:
        selected_profile = read("GetProfileList").get("currentProfileName")
        selected_collection = read("GetSceneCollectionList").get("currentSceneCollectionName")
        remaining = deadline - time.monotonic()
        if remaining > 0 and selected_profile == profile and selected_collection == collection:
            return
        if remaining <= 0:
            raise RuntimeError("Requested OBS profile and collection were not selected before the timeout")
        time.sleep(min(0.1, remaining))


def verified_receipt(value, name):
    """A lost acknowledgement is uncertain even if applied is null."""
    if value.get("verified") is False:
        raise RuntimeError("OBS did not verify the change: " + name)
    return value


def wait_for_replay_geometry(client, profile, collection, scene_item_id, *, transformed=False, timeout=10.0):
    """Observe loaded replay dimensions, then its actual layout, in our selection."""
    started = time.monotonic()
    deadline = started + timeout
    settings = client.settings
    expected = {"sourceWidth": 1280, "sourceHeight": 720}
    if transformed:
        expected.update(width=480, height=270, positionX=760, positionY=380,
                        scaleX=0.375, scaleY=0.375, alignment=5)
    polls = 0

    def read(request, data=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Replay geometry was not confirmed before the timeout")
        client.settings = ObsSettings(settings.port, settings.password, min(settings.timeout, remaining))
        try:
            result = client.request(request, data)
            if time.monotonic() >= deadline:
                raise RuntimeError("Replay geometry was not confirmed before the timeout")
            return result
        finally:
            client.settings = settings

    def owned():
        if (read("GetProfileList").get("currentProfileName") != profile
                or read("GetSceneCollectionList").get("currentSceneCollectionName") != collection):
            raise RuntimeError("Rehearsal profile and collection are not selected; geometry is refused")

    while True:
        owned()
        state = read("GetSceneItemTransform", {"sceneName": "Director Demo", "sceneItemId": scene_item_id}).get("sceneItemTransform")
        owned()
        polls += 1
        if (not isinstance(state, dict) or any(type(state.get(key)) not in (int, float)
                or not -32768 <= state[key] <= 32768 for key in expected)):
            raise RuntimeError("OBS returned malformed replay geometry")
        if any(state[key] < 0 for key in ("sourceWidth", "sourceHeight")):
            raise RuntimeError("OBS returned malformed replay geometry")
        if all(state[key] == value for key, value in expected.items()):
            return {"step": "replay_layout_ready" if transformed else "replay_source_ready",
                    "geometry": {key: state[key] for key in expected}, "polls": polls,
                    "elapsed_ms": round((time.monotonic() - started) * 1000), "verified": True}
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def restore_original(settings, original, profile, collection, client_factory=None, *, pending_selection=None):
    """Use a fresh bounded connection; never stop or adopt an active output."""
    result = {"profile": False, "collection": False, "scene": False, "video": False}
    fresh_settings = ObsSettings(settings.port, settings.password, min(settings.timeout, 3))
    try:
        with (client_factory or ObsClient)(fresh_settings) as client:
            idle(client)
            if pending_selection is not None:
                # An acknowledged create/switch can still complete later. Never
                # claim restoration from a snapshot taken before that change.
                wait_for_selection(client, *pending_selection)
            current_profile = client.request("GetProfileList").get("currentProfileName")
            current_collection = client.request("GetSceneCollectionList").get("currentSceneCollectionName")
            if (current_profile not in {profile, original["profile"]}
                    or current_collection not in {collection, original["collection"]}):
                result["reason"] = "operator_selection_changed"
                return result
            if current_profile == profile:
                idle(client)
                if client.request("GetProfileList").get("currentProfileName") != profile:
                    raise RuntimeError("Profile selection changed during restoration")
                client.request("SetCurrentProfile", {"profileName": original["profile"]})
                wait_for_selection(client, original["profile"], current_collection)
            if current_collection == collection:
                idle(client)
                if client.request("GetSceneCollectionList").get("currentSceneCollectionName") != collection:
                    raise RuntimeError("Collection selection changed during restoration")
                client.request("SetCurrentSceneCollection", {"sceneCollectionName": original["collection"]})
                wait_for_selection(client, original["profile"], original["collection"])
            result.update({
                "profile": client.request("GetProfileList").get("currentProfileName") == original["profile"],
                "collection": client.request("GetSceneCollectionList").get("currentSceneCollectionName") == original["collection"],
                "scene": client.request("GetCurrentProgramScene").get("currentProgramSceneName") == original["scene"],
                "video": client.request("GetVideoSettings") == original["video"],
            })
    except Exception:
        # Preserve the uncertain outcome without recording arbitrary OBS errors/settings.
        result["reason"] = "restoration_unverified"
    return result


def tone(path):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(b"".join(struct.pack("<h", round(2400 * math.sin(2 * math.pi * 440 * i / 48000)))
                                   for i in range(48000 * 4)))


async def run(args):
    output = args.output.resolve()
    settings = ObsSettings.from_environment()
    settings = ObsSettings(settings.port, settings.password, 20)
    original = {}
    profile = collection = None
    pending_selection = None
    trace = []
    with ObsClient(settings) as client:
        def select(request, data, expected_profile, expected_collection):
            nonlocal pending_selection
            pending_selection = (expected_profile, expected_collection)
            client.request(request, data)
            wait_for_selection(client, expected_profile, expected_collection)
            pending_selection = None

        idle(client)
        original = {
            "collection": client.request("GetSceneCollectionList")["currentSceneCollectionName"],
            "profile": client.request("GetProfileList")["currentProfileName"],
            "video": client.request("GetVideoSettings"),
            "scene": client.request("GetCurrentProgramScene")["currentProgramSceneName"],
        }
        if not args.execute:
            print(json.dumps({"preview": True, "outputs_idle": True,
                              "plan": "Create isolated synthetic sources, record a seed and event cue, check local replay, then restore the original profile and collection."}))
            return
        output.mkdir(parents=True, exist_ok=False)
        (output / "original-local-state.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
        tone(output / "tone.wav")
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        profile = collection = "OBS Director Rehearsal " + suffix
        try:
            require_owned_selection(client, original["profile"], original["collection"])
            select("CreateSceneCollection", {"sceneCollectionName": collection}, original["profile"], collection)
            require_owned_selection(client, original["profile"], collection)
            select("CreateProfile", {"profileName": profile}, profile, collection)
            idle(client)
            require_owned_selection(client, profile, collection)
            # Reopen through OBS's queued UI operation before configuration. Do
            # not call SetVideoSettings next to CreateProfile: OBS 32.1 can
            # overlap video resets and hang. Configure only our new profile,
            # then reload it through SetCurrentProfile below.
            select("SetCurrentProfile", {"profileName": original["profile"]}, original["profile"], collection)
            require_owned_selection(client, original["profile"], collection)
            select("SetCurrentProfile", {"profileName": profile}, profile, collection)
            for category, name, value in (
                ("Video", "BaseCX", "1280"), ("Video", "BaseCY", "720"),
                ("Video", "OutputCX", "1280"), ("Video", "OutputCY", "720"),
                ("Video", "FPSType", "0"), ("Video", "FPSCommon", "30"),
                ("Output", "Mode", "Simple"), ("SimpleOutput", "RecQuality", "Small"),
                ("SimpleOutput", "RecEncoder", "x264"), ("SimpleOutput", "RecFormat2", "mkv"),
                ("SimpleOutput", "RecRB", "true"), ("SimpleOutput", "RecRBTime", "12"),
                ("SimpleOutput", "RecRBSize", "64"),
            ):
                require_owned_selection(client, profile, collection)
                client.request("SetProfileParameter", {"parameterCategory": category,
                    "parameterName": name, "parameterValue": value})
            # Reopen only our new profile so OBS loads its output configuration.
            require_owned_selection(client, profile, collection)
            select("SetCurrentProfile", {"profileName": original["profile"]}, original["profile"], collection)
            require_owned_selection(client, original["profile"], collection)
            select("SetCurrentProfile", {"profileName": profile}, profile, collection)
            idle(client)
            require_owned_selection(client, profile, collection)
            expected_video = {"baseWidth": 1280, "baseHeight": 720,
                "outputWidth": 1280, "outputHeight": 720, "fpsNumerator": 30, "fpsDenominator": 1}
            if client.request("GetVideoSettings") != expected_video:
                raise RuntimeError("Rehearsal video configuration did not load")
            special = client.request("GetSpecialInputs")
            for source in {value for value in special.values() if isinstance(value, str) and value}:
                require_owned_selection(client, profile, collection)
                client.request("SetInputMute", {"inputName": source, "inputMuted": True})
                if client.request("GetInputMute", {"inputName": source}).get("inputMuted") is not True:
                    raise RuntimeError("A system audio input could not be muted; recording is refused")
            trace.append({"step": "isolated_setup", "profile": profile, "collection": collection})
            await exercise(client, output, trace, profile, collection)
        finally:
            restored = restore_original(settings, original, profile, collection, pending_selection=pending_selection)
            (output / "restoration.json").write_text(json.dumps(restored, indent=2), encoding="utf-8")
            (output / "trace-local.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")
            if not all(restored[key] is True for key in ("profile", "collection", "scene", "video")):
                raise RuntimeError("Original OBS state was not fully restored; inspect restoration.json")
    print(json.dumps({"completed": True, "output": str(output), "restored": restored}, indent=2))


async def exercise(client, output, trace, profile, collection):
    env = dict(os.environ, OBS_MCP_DATA_DIR=str(output),
               OBS_MCP_EVIDENCE_ROOT=str(output / "captures"), OBS_MCP_ALLOW_OUTPUT_CONTROL="1")
    params = StdioServerParameters(command=sys.executable, args=["-B", str(ROOT / "run_server.py")], env=env)
    owned_session = None
    replay_started = False
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=60)) as mcp:
            await mcp.initialize()

            async def call(name, **arguments):
                # A wait or MCP restart can outlive our selected collection.
                # Stop remains authorized only by the capture service's live
                # recording ownership, even after an operator selection change.
                if name != "obs_stop_recording":
                    require_owned_selection(client, profile, collection)
                result = await mcp.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError("MCP tool failed: " + name + ": " + result.content[0].text)
                value = result.structuredContent
                if value is None:
                    value = json.loads(result.content[0].text)
                trace.append({"tool": name, "time": time.monotonic(), "result": value})
                return verified_receipt(value, name)

            async def edit(name, **arguments):
                return await call(name, **arguments, dry_run=False, allow_live=owned_session is not None)

            try:
                await edit("obs_create_scene", scene_name="Director Demo")
                await edit("obs_select_scene", scene_name="Director Demo")
                await edit("obs_add_source", scene_name="Director Demo", source_name="Demo Chart",
                    input_kind="browser_source", settings={"is_local_file": True,
                        "local_file": str(ROOT / "examples/demo/chart.html"), "width": 1280, "height": 720,
                        "fps": 30, "shutdown": False, "css": ""})
                kinds = (await call("obs_inputs"))["input_kinds"]
                text_kind = next(value for value in ("text_gdiplus_v3", "text_ft2_source_v2") if value in kinds)
                await edit("obs_add_source", scene_name="Director Demo", source_name="Demo Evidence",
                    input_kind=text_kind, settings={"text": "SYNTHETIC REPLAY SAMPLE", "font": {
                        "face": "Arial", "size": 20, "style": "Regular", "flags": 0},
                        "color": 16777215, "outline": False, "extents": True, "extents_cx": 660, "extents_cy": 132})
                await edit("obs_add_source", scene_name="Director Demo", source_name="Demo Tone",
                    input_kind="ffmpeg_source", settings={"is_local_file": True,
                        "local_file": str(output / "tone.wav"), "looping": True})
                sources = await call("obs_scene_sources", scene_name="Director Demo")
                ids = {item["sourceName"]: item["sceneItemId"] for item in sources["sources"]}
                await edit("obs_source_transform", scene_name="Director Demo", scene_item_id=ids["Demo Evidence"],
                    transform={"positionX": 64, "positionY": 548, "alignment": 5})
                await edit("obs_audio_volume", source_name="Demo Tone", volume_db=-9)
                await asyncio.sleep(3)
                screenshot(client, output / "prepared.png", profile, collection)
                started = await call("obs_start_recording", title="Synthetic replay seed", category="rehearsal",
                    scene_name="Director Demo", dry_run=False)
                owned_session = started["session_id"]
                await asyncio.sleep(6)
                seed = await call("obs_stop_recording", session_id=owned_session, dry_run=False)
                owned_session = None
                await edit("obs_add_source", scene_name="Director Demo", source_name="Demo Replay",
                    input_kind="ffmpeg_source", settings={"is_local_file": True,
                        "local_file": seed["output_path"], "looping": True, "restart_on_activate": False,
                        "close_when_inactive": False})
                sources = await call("obs_scene_sources", scene_name="Director Demo")
                ids = {item["sourceName"]: item["sceneItemId"] for item in sources["sources"]}
                trace.append(wait_for_replay_geometry(client, profile, collection, ids["Demo Replay"]))
                await edit("obs_source_transform", scene_name="Director Demo", scene_item_id=ids["Demo Replay"],
                    transform={"positionX": 760, "positionY": 380, "scaleX": 0.375, "scaleY": 0.375, "alignment": 5})
                trace.append(wait_for_replay_geometry(client, profile, collection, ids["Demo Replay"], transformed=True))
                await edit("obs_source_visibility", scene_name="Director Demo", scene_item_id=ids["Demo Replay"], enabled=False)
                await edit("obs_source_visibility", scene_name="Director Demo", scene_item_id=ids["Demo Evidence"], enabled=False)
                await edit("obs_media_action", source_name="Demo Replay", action="stop")
                await edit("obs_audio_mute", source_name="Demo Replay", muted=True)
                require_owned_selection(client, profile, collection)
                client.request("CreateSourceFilter", {"sourceName": "Demo Chart", "filterName": "Demo Contrast",
                    "filterKind": "color_filter_v2", "filterSettings": {"contrast": 0.1}})
                await edit("obs_filter_enabled", source_name="Demo Chart", filter_name="Demo Contrast", enabled=False)
                route = build_demo_registry(ids["Demo Chart"], ids["Demo Evidence"], ids["Demo Replay"])
                (output / "event-routes.json").write_text(json.dumps({"schema": "obs.event-routes.v1", "routes": route}, indent=2), encoding="utf-8")
                trace.append({"step": "seed_ready", "seed": seed["output_path"], "ids": ids})
            finally:
                if owned_session:
                    await call("obs_stop_recording", session_id=owned_session, dry_run=False)
                    owned_session = None
    env["OBS_MCP_EVENT_ROUTES"] = str(output / "event-routes.json")
    params.env = env
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=60)) as mcp:
            await mcp.initialize()
            try:
                event = {"schema": "obs.event.v1", "event_id": "demo-evidence-001", "event_type": "evidence.ready",
                    "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "payload": {
                        "title": "EVIDENCE READY", "summary": "Synthetic pattern review complete.", "reference": "DEMO-001"}}
                await call("obs_preview_event", event=event)
                started = await call("obs_start_recording", title="OBS Director event demo", category="rehearsal",
                    scene_name="Director Demo", dry_run=False)
                owned_session = started["session_id"]
                await asyncio.sleep(2)
                await edit("obs_audio_mute", source_name="Demo Tone", muted=True)
                await asyncio.sleep(2)
                await edit("obs_audio_mute", source_name="Demo Tone", muted=False)
                await edit("obs_filter_settings", source_name="Demo Chart", filter_name="Demo Contrast", settings={"contrast": 0.15})
                await edit("obs_filter_enabled", source_name="Demo Chart", filter_name="Demo Contrast", enabled=True)
                task = asyncio.create_task(call("obs_dispatch_event", event=event, dry_run=False, allow_live=True))
                await asyncio.sleep(3)
                screenshot(client, output / "spotlight.png", profile, collection)
                await asyncio.sleep(3)
                screenshot(client, output / "replay.png", profile, collection)
                result = await task
                if result.get("state") != "completed":
                    raise RuntimeError("Event cue did not complete")
                duplicate = await call("obs_dispatch_event", event=event, dry_run=False, allow_live=True)
                assert duplicate.get("duplicate") is True, "Repeated event was not suppressed"
                await asyncio.sleep(2)
                screenshot(client, output / "final.png", profile, collection)
                finished = await call("obs_stop_recording", session_id=owned_session, dry_run=False)
                owned_session = None
                (output / "recording.json").write_text(json.dumps(finished, indent=2), encoding="utf-8")
            finally:
                if owned_session:
                    await call("obs_stop_recording", session_id=owned_session, dry_run=False)


def screenshot(client, path, profile, collection):
    require_owned_selection(client, profile, collection)
    data = client.request("GetSourceScreenshot", {"sourceName": "Director Demo", "imageFormat": "png", "imageWidth": 1280})
    require_owned_selection(client, profile, collection)
    path.write_bytes(base64.b64decode(data["imageData"].split(",", 1)[1], validate=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    asyncio.run(run(parser.parse_args()))
