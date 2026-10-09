"""Real MCP stdio discovery; optional read-only connection to configured OBS."""
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "obs_status", "obs_capabilities", "obs_scenes", "obs_scene_sources", "obs_inputs",
    "obs_create_scene", "obs_select_scene", "obs_add_source", "obs_source_settings",
    "obs_source_visibility", "obs_source_transform", "obs_audio_mute", "obs_audio_volume",
    "obs_filters", "obs_filter_settings", "obs_filter_enabled", "obs_media_action",
    "obs_media_seek", "obs_output_control", "obs_capture_health", "obs_start_recording",
    "obs_stop_recording", "obs_capture_sessions", "obs_validate_cue", "obs_run_cue",
    "obs_preview_event", "obs_dispatch_event",
    "obs_preview_layout", "obs_apply_layout", "obs_list_templates", "obs_get_template",
}


def installed_command(python, environment, cwd):
    """Resolve a real installed console entry point outside the source checkout."""
    probe = (
        "import importlib.metadata as m,json,pathlib,sysconfig,os; import obs_director; "
        "d=m.distribution('obs-director-mcp'); "
        "assert not json.loads(d.read_text('direct_url.json') or '{}').get('dir_info',{}).get('editable',False); "
        "e=[x for x in d.entry_points if x.group=='console_scripts' and x.name=='obs-director-mcp']; "
        "assert len(e)==1 and e[0].value=='obs_director.server:main'; "
        "p=pathlib.Path(sysconfig.get_path('scripts'))/('obs-director-mcp.exe' if os.name=='nt' else 'obs-director-mcp'); "
        "assert p.is_file(); "
        "print(json.dumps({'version':d.version,'module':str(pathlib.Path(obs_director.__file__).resolve()),'command':str(p)}))"
    )
    result = subprocess.run([str(python), "-I", "-B", "-c", probe], check=True,
                            capture_output=True, text=True, timeout=30, env=environment, cwd=cwd)
    details = json.loads(result.stdout)
    expected_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert details["version"] == expected_version, "Installed version differs from release source"
    assert not Path(details["module"]).is_relative_to(ROOT / "src"), "Installed smoke imported project source"
    return details["command"], details["version"]


async def smoke(live=False, wheel=None, installed=False, python=None):
    with tempfile.TemporaryDirectory(prefix="obs-director-smoke-") as temp:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("PYTHON") and (live or not key.startswith("OBS_MCP_"))}
        env.update(OBS_MCP_DATA_DIR=temp, OBS_MCP_EVIDENCE_ROOT=str(Path(temp) / "captures"))
        env.pop("OBS_MCP_ALLOW_OUTPUT_CONTROL", None)
        env.pop("OBS_MCP_EVENT_ROUTES", None)
        if not live:
            env.update(OBS_MCP_PORT="1", OBS_MCP_PASSWORD="not-a-real-secret")
        command = str(Path(python or sys.executable).resolve())
        installed_version = None
        if installed:
            command, installed_version = installed_command(command, env, temp)
            args = []
        elif wheel:
            # Import from the built wheel while the child is outside the checkout.
            args = ["-B", "-c", "import sys; sys.path.insert(0,sys.argv[1]); from obs_director.server import main; main()", str(wheel.resolve())]
        else:
            args = ["-B", str(ROOT / "run_server.py")]
        params = StdioServerParameters(command=command, args=args, env=env, cwd=temp)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=20)) as session:
                initialized = await session.initialize()
                discovered = await session.list_tools()
                names = {tool.name for tool in discovered.tools}
                assert names == EXPECTED, {"missing": sorted(EXPECTED - names), "extra": sorted(names - EXPECTED)}
                for tool in discovered.tools:
                    assert tool.annotations is not None, tool.name
                    assert tool.description and tool.description.strip(), tool.name
                    props = tool.inputSchema.get("properties", {})
                    if "dry_run" in props:
                        assert props["dry_run"].get("default") is True, tool.name
                    if "allow_live" in props:
                        assert props["allow_live"].get("default") is False, tool.name
                status = await session.call_tool("obs_status")
                catalog = await session.call_tool("obs_list_templates")
                assert not catalog.isError, "Offline template catalog failed"
                template = await session.call_tool("obs_get_template", {"name": "funded-desk"})
                assert not template.isError, "Packaged template could not be read"
                rendered = status.model_dump_json()
                assert "not-a-real-secret" not in rendered
                if live:
                    assert not status.isError, rendered
                    capabilities = await session.call_tool("obs_capabilities")
                    assert not capabilities.isError, capabilities.model_dump_json()
                else:
                    assert status.isError, "Disconnected OBS must return an MCP tool error"
                assert not list(Path(temp).iterdir()), "Read-only discovery/status wrote state"
                return {"passed": True, "mode": "live-read-only" if live else "disconnected-negative-control",
                        "transport": "stdio", "from_wheel": bool(wheel), "installed": installed,
                        "installed_version": installed_version, "tool_count": len(names),
                        "tools": sorted(names), "server": initialized.serverInfo.model_dump(),
                        "status": status.model_dump(mode="json")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--wheel", type=Path, help="Import directly from a wheel (legacy packaging probe)")
    source.add_argument("--installed", action="store_true", help="Launch an already installed console entry point outside the checkout")
    parser.add_argument("--python", type=Path, help="Child interpreter; defaults to this interpreter")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(smoke(args.live, args.wheel, args.installed, args.python)), indent=2))
