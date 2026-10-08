"""The public MCP surface. Tool discovery never requires a running OBS."""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .capture import CaptureService
from .controls import ProductionService
from .cues import CueService


def create_server(production=None, capture=None, cues=None) -> FastMCP:
    production = production or ProductionService()
    capture = capture or CaptureService()
    cues = cues or CueService(production)
    server = FastMCP("OBS Director", instructions=(
        "Local OBS production controls. Inspect capabilities and state first. "
        "Mutations preview by default. On-air changes require explicit allow_live. "
        "Output lifecycle control requires the local operator enable flag. "
        "Validate an entire director cue before execution. Partial failures do not "
        "mean rollback. Never return credentials, source settings, or captured pixels."
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
    ]
    for handler, name, annotations in definitions:
        server.add_tool(handler, name=name, annotations=annotations)
    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
