"""Rehearse Funded Desk using an isolated OBS profile and a loopback RTMP receiver.

Default execution is an offline plan. --execute authorizes isolated OBS setup,
scene selection and a 25-second local stream. No public streaming destination is
accepted. Evidence remains in the new output directory; never commit that folder.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client import stdio as mcp_stdio

import rehearse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from obs_director.templates import add_template
from obs_director.transport import ObsClient, ObsError, ObsSettings, _local_path


LOOPBACK_URL = "rtmp://127.0.0.1:19351/live/demo"
SERVICE_SETTINGS = {"server": LOOPBACK_URL.rsplit("/", 1)[0], "key": LOOPBACK_URL.rsplit("/", 1)[1], "use_auth": False}
SECONDS = 25
FILES = {"chart": (1296, 729), "host": (464, 464), "replay": (464, 261),
         "status": (1296, 96), "banner": (1920, 96)}
VIDEO = {"baseWidth": 1920, "baseHeight": 1080, "outputWidth": 1280, "outputHeight": 720,
         "fpsNumerator": 30, "fpsDenominator": 1}


def demo_options(theme="light", presenter="framed"):
    if theme not in ("light", "dark") or presenter not in ("framed", "cutout"):
        raise RuntimeError("Unsupported demo theme or presenter")


def local_assets(directory: Path, presenter="framed") -> dict[str, Path]:
    """Read supplied local synthetic HTML; this static check is not a browser network sandbox."""
    try:
        demo_options(presenter=presenter)
        directory = _local_path(directory)
        result = {}
        for name in FILES:
            filename = "host-cutout" if name == "host" and presenter == "cutout" else name
            path = _local_path(directory / (filename + ".html"))
            if path.parent != directory or not path.is_file():
                raise ValueError()
            with path.open("rb") as source:
                content = source.read(2 * 1024 * 1024 + 1)
            if not content or len(content) > 2 * 1024 * 1024:
                raise ValueError()
            text = html.unescape(content.decode("utf-8"))
            if re.search(r"(?:https?|wss?)://|(?:src|href)\s*=\s*['\"]//|url\(\s*['\"]?//", text, re.I):
                raise ValueError()
            result[name] = path
        return result
    except Exception:
        raise RuntimeError("Supply bounded local chart, host, replay, status and banner HTML with no network URLs") from None


def port_available() -> bool:
    """A bind probe never consumes FFmpeg's one accepted RTMP connection."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", 19351))
        return True
    except OSError:
        return False
    finally:
        probe.close()


class Receiver:
    """Own one FFmpeg child; never probe it by making a TCP connection."""
    def __init__(self, executable: str, output: Path):
        self.executable = executable
        self.output = output
        self.process = None
        self.log = None

    def start(self):
        if self.process is not None:
            raise RuntimeError("This receiver instance has already been started")
        if not port_available():
            raise RuntimeError("The fixed local RTMP receiver port is already occupied")
        self.log = (self.output / "receiver.log").open("xb")
        try:
            self.process = subprocess.Popen([self.executable, "-hide_banner", "-loglevel", "warning", "-nostdin",
                "-listen", "1", "-i", LOOPBACK_URL, "-map", "0", "-c", "copy", "-f", "matroska", "-n",
                str(self.output / "receiver.mkv")], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=self.log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                self.check()
                if not port_available():
                    time.sleep(0.1)
                    self.check()
                    return
                time.sleep(0.05)
            raise RuntimeError("The local RTMP receiver did not become ready")
        except Exception:
            self.close()
            raise

    def check(self):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("The local RTMP receiver is not running")

    def finish(self) -> dict:
        if self.process is None:
            raise RuntimeError("The local receiver was not started")
        try:
            code = self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Local receiver finalization did not complete") from None
        if self.log:
            self.log.close()
            self.log = None
        path = self.output / "receiver.mkv"
        if code != 0 or not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError("The local receiver did not produce a finalized recording")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"file": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest(), "receiver_exit_code": code}

    def close(self):
        # Only this instance's Popen child is eligible for termination.
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if self.log:
            self.log.close()
            self.log = None


class StreamClient(ObsClient):
    """Retain stream events on the original authenticated connection."""
    def __init__(self, settings):
        super().__init__(settings)
        self.stream_events = []

    def _receive(self, deadline):
        message = super()._receive(deadline)
        if message.get("op") == 5 and message["d"].get("eventType") == "StreamStateChanged":
            if len(self.stream_events) >= 128:
                self.close()
                raise ObsError("Stream event history exceeded its bound")
            self.stream_events.append(message["d"].get("eventData"))
        return message


def local_service(client):
    value = client.request("GetStreamServiceSettings")
    settings = value.get("streamServiceSettings")
    if (value.get("streamServiceType") != "rtmp_custom" or not isinstance(settings, dict)
            or settings.get("server") != SERVICE_SETTINGS["server"]
            or settings.get("key") != SERVICE_SETTINGS["key"] or settings.get("use_auth") is not False
            or settings.get("username", "") or settings.get("password", "")):
        raise RuntimeError("Streaming is refused: the exact unauthenticated loopback service is not configured")


class LocalStreamLease:
    """Never adopt a running output or reconnect to stop one after lost continuity."""
    def __init__(self, client, profile, collection, receiver, trace):
        self.client, self.profile, self.collection = client, profile, collection
        self.receiver, self.trace = receiver, trace
        self.offset = 0
        self.start_requested = self.confirmed = self.stop_requested = self.stopped = False

    def guard(self):
        if self.client.closed:
            raise RuntimeError("Stream connection is lost; ownership is unproven")
        rehearse.require_owned_selection(self.client, self.profile, self.collection)
        local_service(self.client)

    def states(self, *, stopping=False):
        if self.client.closed:
            raise RuntimeError("Stream connection is lost; ownership is unproven")
        states = []
        for event in self.client.stream_events[self.offset:]:
            if not isinstance(event, dict):
                raise RuntimeError("Stream continuity is unproven")
            state = event.get("outputState")
            expected = {"OBS_WEBSOCKET_OUTPUT_STARTING": False, "OBS_WEBSOCKET_OUTPUT_STARTED": True,
                        "OBS_WEBSOCKET_OUTPUT_STOPPING": False, "OBS_WEBSOCKET_OUTPUT_STOPPED": False}
            if state not in expected or event.get("outputActive") is not expected[state]:
                raise RuntimeError("Stream continuity is unproven")
            states.append(state.rsplit("_", 1)[1])
        if states.count("STARTING") > 1 or states.count("STARTED") > 1:
            raise RuntimeError("An intervening stream start invalidated ownership")
        if "STARTING" in states and "STARTED" in states and states.index("STARTING") > states.index("STARTED"):
            raise RuntimeError("Stream event ordering invalidated ownership")
        if not stopping and any(state in ("STOPPING", "STOPPED") for state in states):
            raise RuntimeError("An intervening stream stop invalidated ownership")
        if stopping:
            if "STARTED" not in states or any(state in ("STARTING", "STARTED") for state in states[states.index("STARTED") + 1:]):
                raise RuntimeError("Stream stop continuity is unproven")
            tail = states[states.index("STARTED") + 1:]
            if tail not in ([], ["STOPPING"], ["STOPPED"], ["STOPPING", "STOPPED"]):
                raise RuntimeError("Stream stop continuity is unproven")
        return states

    def wait(self, active):
        deadline = time.monotonic() + 10
        original = self.client.settings
        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                self.client.settings = ObsSettings(original.port, original.password, min(original.timeout, remaining))
                status = self.client.request("GetStreamStatus")
                states = self.states(stopping=not active)
                if time.monotonic() >= deadline:
                    break
                if type(status.get("outputActive")) is not bool or status.get("outputReconnecting") is not False:
                    raise RuntimeError("Stream status is unknown")
                if status["outputActive"] is active and ("STARTED" if active else "STOPPED") in states:
                    return status
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        finally:
            self.client.settings = original
        raise RuntimeError("Stream transition was not confirmed within its deadline")

    def start(self):
        if self.start_requested:
            raise RuntimeError("A stream start is never retried by this lease")
        # A false status can precede a pending external start. Never discard
        # evidence already observed on this connection when taking the lease.
        if self.client.stream_events:
            last = self.client.stream_events[-1]
            if (not isinstance(last, dict) or last.get("outputState") != "OBS_WEBSOCKET_OUTPUT_STOPPED"
                    or last.get("outputActive") is not False):
                raise RuntimeError("Prior stream state is unresolved; start is refused")
        initial_offset = len(self.client.stream_events)
        self.guard()
        rehearse.idle(self.client)
        self.receiver.check()
        self.guard()
        status = self.client.request("GetStreamStatus")
        if status.get("outputActive") is not False or status.get("outputReconnecting") is not False:
            raise RuntimeError("An existing stream cannot be adopted")
        if len(self.client.stream_events) != initial_offset:
            raise RuntimeError("Stream state changed during start preflight; start is refused")
        self.offset = len(self.client.stream_events)
        self.start_requested = True
        self.trace.append({"step": "stream_start_requested", "may_be_streaming": True})
        self.client.request("StartStream")
        status = self.wait(True)
        self.confirmed = True
        self.trace.append({"step": "stream_started", "confirmed": True, "output_bytes": status.get("outputBytes")})

    def observe(self):
        self.guard()
        self.receiver.check()
        status = self.client.request("GetStreamStatus")
        self.states()
        if not self.confirmed or status.get("outputActive") is not True or status.get("outputReconnecting") is not False:
            raise RuntimeError("The owned local stream is not continuously active")
        if type(status.get("outputBytes")) is not int or status["outputBytes"] < 0:
            raise RuntimeError("Stream byte progression is unknown")
        result = {"elapsed": round(time.monotonic(), 3), "output_bytes": status["outputBytes"],
                  "output_duration": status.get("outputDuration"), "active": True}
        for key in ("outputSkippedFrames", "outputTotalFrames", "outputCongestion"):
            value = status.get(key)
            if type(value) in (int, float) and 0 <= value <= 1e18 and math.isfinite(value):
                result[key] = value
        return result

    def stop(self):
        if not self.confirmed or self.stop_requested or self.stopped:
            raise RuntimeError("No confirmed original stream lease can authorize StopStream")
        self.guard()
        status = self.client.request("GetStreamStatus")
        self.states()
        if status.get("outputActive") is not True or status.get("outputReconnecting") is not False:
            raise RuntimeError("Original stream is no longer active; stop is refused")
        self.stop_requested = True
        self.trace.append({"step": "stream_stop_requested", "may_be_streaming": True})
        self.client.request("StopStream")
        self.wait(False)
        self.stopped = True
        self.trace.append({"step": "stream_stopped", "confirmed": True, "may_be_streaming": False})


async def _hidden_process(command, args, env=None, errlog=sys.stderr, cwd=None):
    """Pinned SDK process seam: never use its visible-window fallback on Windows."""
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    return await anyio.open_process([command, *args], env=env, stderr=errlog, cwd=cwd, **options)


@asynccontextmanager
async def hidden_stdio(params, errlog):
    original = mcp_stdio._create_platform_compatible_process
    mcp_stdio._create_platform_compatible_process = _hidden_process
    try:
        async with mcp_stdio.stdio_client(params, errlog=errlog) as pipes:
            yield pipes
    finally:
        mcp_stdio._create_platform_compatible_process = original


def prepare(client, original, profile, collection, pending):
    owned = [original["profile"], original["collection"]]

    def guard():
        rehearse.require_owned_selection(client, *owned)
        rehearse.idle(client)
        rehearse.require_owned_selection(client, *owned)

    def select(request, data, expected_profile, expected_collection):
        guard()
        pending[:] = [expected_profile, expected_collection]
        client.request(request, data)
        rehearse.wait_for_selection(client, expected_profile, expected_collection)
        owned[:] = [expected_profile, expected_collection]
        pending.clear()

    rehearse.require_owned_selection(client, original["profile"], original["collection"])
    select("CreateSceneCollection", {"sceneCollectionName": collection}, original["profile"], collection)
    rehearse.require_owned_selection(client, original["profile"], collection)
    select("CreateProfile", {"profileName": profile}, profile, collection)
    rehearse.idle(client)
    rehearse.require_owned_selection(client, profile, collection)
    select("SetCurrentProfile", {"profileName": original["profile"]}, original["profile"], collection)
    rehearse.require_owned_selection(client, original["profile"], collection)
    select("SetCurrentProfile", {"profileName": profile}, profile, collection)
    for category, name, value in (
        ("Video", "BaseCX", "1920"), ("Video", "BaseCY", "1080"), ("Video", "OutputCX", "1280"), ("Video", "OutputCY", "720"),
        ("Video", "FPSType", "0"), ("Video", "FPSCommon", "30"), ("Output", "Mode", "Simple"),
        ("SimpleOutput", "StreamEncoder", "x264"), ("SimpleOutput", "VBitrate", "2500"), ("SimpleOutput", "ABitrate", "96"),
        ("SimpleOutput", "UseAdvanced", "false"), ("SimpleOutput", "RecRB", "false"),
    ):
        guard()
        client.request("SetProfileParameter", {"parameterCategory": category, "parameterName": name, "parameterValue": value})
    rehearse.require_owned_selection(client, profile, collection)
    select("SetCurrentProfile", {"profileName": original["profile"]}, original["profile"], collection)
    rehearse.require_owned_selection(client, original["profile"], collection)
    select("SetCurrentProfile", {"profileName": profile}, profile, collection)
    rehearse.idle(client)
    rehearse.require_owned_selection(client, profile, collection)
    if client.request("GetVideoSettings") != VIDEO:
        raise RuntimeError("The isolated video profile did not load")
    special = client.request("GetSpecialInputs")
    for name in {value for value in special.values() if isinstance(value, str) and value}:
        guard()
        client.request("SetInputMute", {"inputName": name, "inputMuted": True})
        if client.request("GetInputMute", {"inputName": name}).get("inputMuted") is not True:
            raise RuntimeError("System audio isolation was not confirmed")
    guard()
    client.request("SetStreamServiceSettings", {"streamServiceType": "rtmp_custom",
        "streamServiceSettings": dict(SERVICE_SETTINGS, username="", password="")})
    local_service(client)


def cut_recipes(recipe, presenter="framed"):
    """Use the same artwork and existing inputs for two focused scenes."""
    result = []
    for label, source in (("Chart", "Demo Chart"), ("Replay", "Demo Replay")):
        variant = {key: deepcopy(recipe[key]) for key in ("schema", "canvas", "assets")}
        variant["scene_name"] = recipe["scene_name"] + " - " + label
        artwork = [deepcopy(layer) for layer in recipe["layers"] if layer["type"] == "image"]
        variant["layers"] = [layer for layer in artwork if layer["id"] != "frame"] + [
            {"id": "hero", "type": "existing", "source_name": source,
             "rect": {"x": 40, "y": 130, "width": 1600, "height": 900},
             "border": {"width": 3, "color": "#BD852EFF"}},
            {"id": "host-inset", "type": "existing", "source_name": "Demo Host",
             "rect": {"x": 1670, "y": 730, "width": 210, "height": 210},
             "border": {"width": 3, "color": "#BD852EFF"}},
        ] + [layer for layer in artwork if layer["id"] == "frame"] + [
            {"id": "banner", "type": "existing", "source_name": "Demo Banner",
             "rect": {"x": 0, "y": 0, "width": 1920, "height": 96}}]
        if presenter == "cutout":
            next(layer for layer in variant["layers"] if layer["id"] == "host-inset").pop("border", None)
        result.append(variant)
    return result


def pnl_display(negative=False):
    from obs_director.pnl import render_pnl
    return render_pnl({"mode": "demo", "currency": "USD", "realized_minor": -8000 if negative else 25000,
                       "unrealized_minor": 0, "fees_minor": 500,
                       "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})


def text_colors(kind, color):
    return {"color": color} if kind == "text_gdiplus_v3" else {"color1": color, "color2": color}


async def prepare_overlays(call, scene, theme):
    """Create dedicated main-scene panels, then bind routes to read-back item IDs."""
    from obs_director.audience import build_audience_registry
    kinds = (await call("obs_inputs"))["input_kinds"]
    kind = next((value for value in ("text_gdiplus_v3", "text_ft2_source_v2") if value in kinds), None)
    if kind is None or "color_source_v3" not in kinds:
        raise RuntimeError("The demo requires a supported text input and color source")
    pnl = pnl_display()
    background = 0xFF211B16 if theme == "dark" else 0xFFF1F3F5
    foreground = 0xFFF3F4F6 if theme == "dark" else 0xFF202020
    definitions = [
        ("Demo PnlPanel", "color_source_v3", {"width": 464, "height": 96, "color": 0xFF211B16}, (1416, 944, 464, 96)),
        ("Demo PnlText", kind, {"text": pnl["text"], **text_colors(kind, pnl["color"])}, (1432, 954, 432, 76)),
        ("Demo AudiencePanel", "color_source_v3", {"width": 1296, "height": 76, "color": background}, (40, 96, 1296, 76)),
        ("Demo AudienceText", kind, {"text": "DEMO AUDIENCE ALERT", **text_colors(kind, foreground)}, (56, 100, 1264, 68)),
    ]
    for name, input_kind, settings, (_, _, width, height) in definitions:
        if input_kind == kind:
            settings.update(font={"face": "Arial", "size": 18, "style": "Regular", "flags": 0}, outline=False)
            if kind == "text_gdiplus_v3":
                settings.update(extents=True, extents_cx=width, extents_cy=height, extents_wrap=True)
            else:
                settings.update(custom_width=width, word_wrap=True)
        await call("obs_add_source", scene_name=scene, source_name=name, input_kind=input_kind,
                   settings=settings, dry_run=False, allow_live=False)
    items = (await call("obs_scene_sources", scene_name=scene))["sources"]
    ids = {}
    for name, _, _, (x, y, width, height) in definitions:
        found = [item for item in items if item.get("sourceName") == name]
        if len(found) != 1 or type(found[0].get("sceneItemId")) is not int or found[0]["sceneItemId"] < 0:
            raise RuntimeError("A dedicated demo overlay identity is unproven")
        ids[name] = found[0]["sceneItemId"]
        await call("obs_source_transform", scene_name=scene, scene_item_id=ids[name],
                   transform={"positionX": x, "positionY": y, "alignment": 5, "boundsType": "OBS_BOUNDS_STRETCH",
                              "boundsWidth": width, "boundsHeight": height, "boundsAlignment": 5},
                   dry_run=False, allow_live=False)
        if name.startswith("Demo Audience"):
            await call("obs_source_visibility", scene_name=scene, scene_item_id=ids[name], enabled=False,
                       dry_run=False, allow_live=False)
    routes = build_audience_registry(scene, "Demo AudienceText", ids["Demo AudiencePanel"], ids["Demo AudienceText"], duration_seconds=4)
    return {"ids": ids, "text_kind": kind, "routes": routes}


def audience_state(client, scene, overlays):
    visible = [client.request("GetSceneItemEnabled", {"sceneName": scene, "sceneItemId": overlays["ids"][name]}).get("sceneItemEnabled")
               for name in ("Demo AudiencePanel", "Demo AudienceText")]
    text = client.request("GetInputSettings", {"inputName": "Demo AudienceText"}).get("inputSettings", {}).get("text")
    if any(type(value) is not bool for value in visible) or not isinstance(text, str):
        raise RuntimeError("Audience overlay readback is unknown")
    return visible, text


def program_screenshot(client, selected, path, profile, collection):
    import base64
    rehearse.require_owned_selection(client, profile, collection)
    actual = client.request("GetCurrentProgramScene").get("currentProgramSceneName")
    if actual != selected:
        raise RuntimeError("Program scene changed during the local rehearsal")
    data = client.request("GetSourceScreenshot", {"sourceName": actual, "imageFormat": "png", "imageWidth": 1920})
    rehearse.require_owned_selection(client, profile, collection)
    encoded = data.get("imageData")
    if not isinstance(encoded, str) or not encoded.startswith("data:image/png;base64,") or len(encoded) > 24 * 1024 * 1024:
        raise RuntimeError("The local program screenshot was not a bounded PNG")
    pixels = base64.b64decode(encoded.split(",", 1)[1], validate=True)
    if not pixels.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("The local program screenshot was not a PNG")
    path.write_bytes(pixels)


async def stream_phases(client, lease, call, scene, output, trace, profile, collection, overlays=None):
    """Cut and dispatch synthetic events only during the confirmed local lease."""
    from obs_director.audience import normalize_audience_event
    phases = [(0, scene), (8, scene + " - Chart"), (15, scene + " - Replay"), (21, scene)]
    samples = []
    started = time.monotonic()
    for index, (begin, selected) in enumerate(phases):
        end = phases[index + 1][0] if index + 1 < len(phases) else SECONDS
        if index:
            lease.observe()
            await call("obs_select_scene", scene_name=selected, dry_run=False, allow_live=True)
        trace.append({"step": "stream_phase", "phase": index + 1, "scene": selected,
                      "elapsed_seconds": round(time.monotonic() - started, 3)})
        lease.observe()
        screenshot = output / ("stream-phase-" + str(index + 1) + ".png")
        if overlays and index in (0, 3):
            pnl = pnl_display(negative=index == 3)
            await call("obs_source_settings", source_name="Demo PnlText",
                       settings={"text": pnl["text"], **text_colors(overlays["text_kind"], pnl["color"])},
                       dry_run=False, allow_live=True)
            trace.append({"step": "demo_pnl", "phase": index + 1, "net_minor": pnl["net_minor"], "state": pnl["state"]})
            event = normalize_audience_event({"platform": "twitch" if index == 0 else "youtube",
                "kind": "subscription" if index == 0 else "membership", "event_id": "demo-support-" + str(index),
                "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "display_name": "Demo Supporter"})
            lease.observe()
            task = asyncio.create_task(call("obs_dispatch_event", event=event, dry_run=False, allow_live=True))
            try:
                await asyncio.sleep(0.5)
                lease.observe()
                if audience_state(client, scene, overlays)[0] != [True, True]:
                    raise RuntimeError("The synthetic audience alert was not visible for capture")
                program_screenshot(client, selected, screenshot, profile, collection)
            finally:
                # A remote cue can continue after caller cancellation. Always
                # receive its bounded MCP result before the lease can stop.
                result = await asyncio.shield(task)
            if result.get("state") != "completed" or audience_state(client, scene, overlays)[0] != [False, False]:
                raise RuntimeError("The synthetic audience alert did not finish and hide")
            if index == 0:
                lease.observe()
                duplicate = await call("obs_dispatch_event", event=event, dry_run=False, allow_live=True)
                if duplicate.get("duplicate") is not True or duplicate.get("applied") is not False:
                    raise RuntimeError("The duplicate audience event was not suppressed")
        else:
            if overlays and index == 1:
                before = audience_state(client, scene, overlays)
                if before[0] != [False, False]:
                    raise RuntimeError("Audience panels must remain hidden outside the main scene")
                rejected_event = normalize_audience_event({"platform": "twitch", "kind": "subscription",
                    "event_id": "demo-wrong-scene", "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "display_name": "Demo Guard Test"})
                lease.observe()
                refused = await call("obs_dispatch_event", event=rejected_event, dry_run=False, allow_live=True, expect_error=True)
                if refused.get("expected_error") is not True or audience_state(client, scene, overlays) != before:
                    raise RuntimeError("Wrong-scene audience dispatch was not refused without changes")
            program_screenshot(client, selected, screenshot, profile, collection)
        while time.monotonic() - started < end:
            sample = lease.observe()
            sample["elapsed_seconds"] = round(time.monotonic() - started, 3)
            sample["phase"] = index + 1
            samples.append(sample)
            await asyncio.sleep(min(1, max(0, end - (time.monotonic() - started))))
    trace.append({"step": "stream_samples", "samples": samples})
    if len(samples) < 2 or samples[-1]["output_bytes"] <= samples[0]["output_bytes"]:
        raise RuntimeError("Stream byte progression was not observed")


async def build_and_stream(client, receiver, lease, output, files, trace, profile, collection, theme="light", presenter="framed"):
    demo_options(theme, presenter)
    bindings = {"CHART": "Demo Chart", "HOST": "Demo Host", "HOST_B": "Demo Replay", "STATUS": "Demo Status"}
    copied = add_template("funded-desk", output / "layout", bindings, theme=theme)
    recipe = json.loads(Path(copied["recipe_path"]).read_text(encoding="utf-8"))
    if presenter == "cutout":
        next(layer for layer in recipe["layers"] if layer["id"] == "host").pop("border", None)
    scene = recipe["scene_name"]
    env = dict(os.environ, OBS_MCP_DATA_DIR=str(output / "mcp-state"))
    env.pop("OBS_MCP_EVENT_ROUTES", None)
    params = StdioServerParameters(command=sys.executable, args=["-B", str(ROOT / "run_server.py")], env=env)
    with (output / "mcp-stderr.log").open("w", encoding="utf-8") as log:
        async with hidden_stdio(params, log) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=60)) as mcp:
                await mcp.initialize()

                async def call(name, expect_error=False, **arguments):
                    rehearse.require_owned_selection(client, profile, collection)
                    result = await mcp.call_tool(name, arguments)
                    if result.isError:
                        if expect_error:
                            trace.append({"tool": name, "expected_error": True})
                            return {"expected_error": True}
                        raise RuntimeError("A rehearsal MCP operation failed: " + name)
                    if expect_error:
                        raise RuntimeError("A rehearsal negative control unexpectedly succeeded: " + name)
                    value = result.structuredContent
                    if value is None:
                        value = json.loads(result.content[0].text)
                    trace.append({"tool": name, "result": value})
                    if value.get("verified") is False and not value.get("dry_run", False):
                        raise RuntimeError("A rehearsal MCP readback was unverified: " + name)
                    return value

                async def edit(name, **arguments):
                    return await call(name, **arguments, dry_run=False, allow_live=False)

                await edit("obs_create_scene", scene_name="Demo Inputs")
                for name in ("chart", "host", "replay", "status"):
                    width, height = FILES[name]
                    css = ""
                    if name == "replay":
                        width, height, css = width * 4, height * 4, "html { zoom: 4; }"
                    await edit("obs_add_source", scene_name="Demo Inputs", source_name="Demo " + name.title(),
                        input_kind="browser_source", settings={"is_local_file": True, "local_file": str(files[name]),
                            "width": width, "height": height, "fps": 30, "shutdown": False, "css": css})
                await call("obs_preview_layout", recipe=recipe)
                applied = await call("obs_apply_layout", recipe=recipe, dry_run=False)
                if applied.get("verified") is not True:
                    raise RuntimeError("The demo layout was not verified")
                repeated = await call("obs_apply_layout", recipe=recipe, dry_run=False)
                if repeated.get("verified") is not True or repeated.get("change_count") != 0 or repeated.get("receipts"):
                    raise RuntimeError("A repeat layout application was not a zero-write operation")
                await edit("obs_add_source", scene_name=scene, source_name="Demo Banner", input_kind="browser_source",
                    settings={"is_local_file": True, "local_file": str(files["banner"]), "width": 1920, "height": 96,
                              "fps": 30, "shutdown": False, "css": ""})
                items = await call("obs_scene_sources", scene_name=scene)
                banner = [item for item in items["sources"] if item["sourceName"] == "Demo Banner"]
                if len(banner) != 1 or type(banner[0].get("sceneItemId")) is not int:
                    raise RuntimeError("The demo banner identity is unproven")
                await edit("obs_source_transform", scene_name=scene, scene_item_id=banner[0]["sceneItemId"],
                           transform={"positionX": 0, "positionY": 0, "alignment": 5})
                variants = cut_recipes(recipe, presenter)
                for variant in variants:
                    value = await call("obs_apply_layout", recipe=variant, dry_run=False)
                    if value.get("verified") is not True:
                        raise RuntimeError("A focused demo scene was not verified")
                for index, tone_scene in enumerate([scene, *[item["scene_name"] for item in variants]]):
                    tone_name = "Demo Tone " + str(index + 1)
                    await edit("obs_add_source", scene_name=tone_scene, source_name=tone_name, input_kind="ffmpeg_source",
                        settings={"is_local_file": True, "local_file": str(output / "tone.wav"), "looping": True})
                    await edit("obs_audio_volume", source_name=tone_name, volume_db=-24)
                    await edit("obs_audio_mute", source_name=tone_name, muted=False)
                overlays = await prepare_overlays(call, scene, theme)
                routes_path = output / "audience-routes.json"
                routes_path.write_text(json.dumps({"schema": "obs.event-routes.v1", "routes": overlays["routes"]}, indent=2), encoding="utf-8")
                trace.append({"step": "audience_routes_ready", "route_count": len(overlays["routes"]), "ids": overlays["ids"]})
        # Route configuration is loaded only at process initialization. Restart
        # the MCP child after source IDs exist; preserve the direct stream lease.
        params.env = dict(env, OBS_MCP_EVENT_ROUTES=str(routes_path))
        async with hidden_stdio(params, log) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=60)) as mcp:
                await mcp.initialize()
                await edit("obs_select_scene", scene_name=scene)
                await asyncio.sleep(2)
                rehearse.require_owned_selection(client, profile, collection)
                receiver.start()
                lease.start()
                await stream_phases(client, lease, call, scene, output, trace, profile, collection, overlays=overlays)
                lease.stop()


async def run(args):
    theme = getattr(args, "theme", "light")
    presenter = getattr(args, "presenter", "framed")
    demo_options(theme, presenter)
    files = local_assets(args.assets_directory.resolve(), presenter=presenter)
    if not args.execute:
        print(json.dumps({"preview": True, "template": "funded-desk", "destination": LOOPBACK_URL,
                          "theme": theme, "presenter": presenter, "duration_seconds": SECONDS, "network_scope": "loopback only", "obs_contacted": False}))
        return
    executable = shutil.which(args.ffmpeg)
    if executable is None:
        raise RuntimeError("FFmpeg is required before the local rehearsal can start")
    output = _local_path(args.output.resolve())
    if output.exists() or not output.parent.is_dir():
        raise RuntimeError("Choose a new local output directory whose parent exists")
    settings = ObsSettings.from_environment()
    settings = ObsSettings(settings.port, settings.password, 5)
    trace, pending = [], []
    restored = None
    with StreamClient(settings) as client:
        rehearse.idle(client)
        original = {"profile": client.request("GetProfileList")["currentProfileName"],
            "collection": client.request("GetSceneCollectionList")["currentSceneCollectionName"],
            "scene": client.request("GetCurrentProgramScene")["currentProgramSceneName"],
            "video": client.request("GetVideoSettings")}
        output.mkdir(exist_ok=False)
        (output / "original-local-state.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
        rehearse.tone(output / "tone.wav")
        profile = collection = "Director Local Demo " + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        receiver = Receiver(executable, output)
        lease = LocalStreamLease(client, profile, collection, receiver, trace)
        try:
            prepare(client, original, profile, collection, pending)
            trace.append({"step": "isolated_setup", "video": VIDEO, "loopback_verified": True, "system_audio_muted": True})
            await build_and_stream(client, receiver, lease, output, files, trace, profile, collection, theme=theme, presenter=presenter)
            trace.append({"step": "receiver_finalized", **receiver.finish()})
        finally:
            if lease.confirmed and not lease.stopped:
                try:
                    lease.stop()
                except Exception:
                    trace.append({"step": "owned_stop_unverified", "may_be_streaming": True})
            receiver_closed = True
            try:
                receiver.close()
            except Exception:
                receiver_closed = False
                trace.append({"step": "receiver_shutdown_unverified", "owned_child_may_be_running": True})
            if lease.start_requested and not lease.stopped:
                restored = {"profile": False, "collection": False, "scene": False, "video": False,
                            "reason": "stream_ownership_or_finalization_unproven"}
            else:
                restored = rehearse.restore_original(settings, original, profile, collection,
                    pending_selection=tuple(pending) if pending else None)
            (output / "restoration.json").write_text(json.dumps(restored, indent=2), encoding="utf-8")
            (output / "trace-local.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")
            if not all(restored[key] is True for key in ("profile", "collection", "scene", "video")):
                raise RuntimeError("Original OBS state was not fully restored; inspect the private restoration evidence")
            if not receiver_closed:
                raise RuntimeError("Owned receiver shutdown was not confirmed; inspect the private trace")
    print(json.dumps({"completed": True, "destination": LOOPBACK_URL, "output": str(output), "restored": restored}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("--presenter", choices=("framed", "cutout"), default="framed")
    parser.add_argument("--execute", action="store_true")
    asyncio.run(run(parser.parse_args()))
