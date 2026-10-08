"""The public MCP surface. Tool discovery never requires a running OBS."""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .capture import CaptureService
from .controls import ProductionService
from .cues import CueService
from .events import EventService


def create_server(production=None, capture=None, cues=None, events=None) -> FastMCP:
    production = production or ProductionService()
    capture = capture or CaptureService()
    cues = cues or CueService(production)
    events = events or EventService(cues)
    server = FastMCP("OBS Director", instructions=(
        "Read capabilities and OBS state first. Tools that change OBS use dry runs by default. "
        "If recording or streaming is active, set allow_live=true for production changes. "
        "Output start/stop commands need OBS_MCP_ALLOW_OUTPUT_CONTROL=1 locally. "
        "Validate every cue step before execution. A failure does not reverse completed changes. "
        "Do not return passwords, source settings, or captured pixels."
    ))
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    output = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
    source = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
    definitions = [
        (production.status, "obs_status", read),
        (production.capabilities, "obs_capabilities", read),
        (production.scenes, "obs_scenes", read),
        (production.scene_sources, "obs_scene_sources", read),
        (production.inputs, "obs_inputs", read),
        (production.create_scene, "obs_create_scene", write),
        (production.select_scene, "obs_select_scene", write),
        (production.add_source, "obs_add_source", source),
        (production.source_settings, "obs_source_settings", source),
        (production.source_visibility, "obs_source_visibility", write),
        (production.source_transform, "obs_source_transform", write),
        (production.audio_mute, "obs_audio_mute", write),
        (production.audio_volume, "obs_audio_volume", write),
        (production.filters, "obs_filters", read),
        (production.filter_settings, "obs_filter_settings", write),
        (production.filter_enabled, "obs_filter_enabled", write),
        (production.media_action, "obs_media_action", write),
        (production.media_seek, "obs_media_seek", write),
        (production.output_control, "obs_output_control", output),
        (capture.capture_health, "obs_capture_health", write),
        (capture.start_recording, "obs_start_recording", output),
        (capture.stop_recording, "obs_stop_recording", output),
        (capture.capture_sessions, "obs_capture_sessions", read),
        (cues.validate_cue, "obs_validate_cue", read),
        (cues.run_cue, "obs_run_cue", write),
        (events.preview_event, "obs_preview_event", read),
        (events.dispatch_event, "obs_dispatch_event", write),
    ]
    for handler, name, annotations in definitions:
        server.add_tool(handler, name=name, annotations=annotations)
    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
