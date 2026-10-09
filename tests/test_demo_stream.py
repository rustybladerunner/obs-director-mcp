"""Local stream rehearsal boundaries tested without OBS, FFmpeg, or sockets."""
from copy import deepcopy
from contextlib import asynccontextmanager
from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import demo_stream as demo
from obs_director.transport import ObsError, ObsSettings


class FakeClient:
    def __init__(self, settings=None):
        self.settings = settings or ObsSettings()
        self.closed = False
        self.stream_events = []
        self.calls = []
        self.profile, self.collection = "Owned profile", "Owned collection"
        self.service = {"streamServiceType": "rtmp_custom", "streamServiceSettings": deepcopy(demo.SERVICE_SETTINGS)}
        self.active = False
        self.reconnecting = False
        self.recording = False
        self.bytes = 0
        self.pending = None
        self.delay = 2
        self.lost_ack = set()
        self.before = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def event(self, suffix, active):
        self.stream_events.append({"outputState": "OBS_WEBSOCKET_OUTPUT_" + suffix, "outputActive": active})

    def request(self, name, data=None):
        self.calls.append((name, data))
        if self.before:
            self.before(name, data)
        if name == "GetProfileList": return {"currentProfileName": self.profile}
        if name == "GetSceneCollectionList": return {"currentSceneCollectionName": self.collection}
        if name == "GetCurrentProgramScene": return {"currentProgramSceneName": "Original scene"}
        if name == "GetVideoSettings": return {"baseWidth": 1920, "baseHeight": 1080}
        if name == "GetStreamServiceSettings": return deepcopy(self.service)
        if name == "GetRecordStatus": return {"outputActive": self.recording}
        if name in ("GetVirtualCamStatus", "GetReplayBufferStatus"): return {"outputActive": False}
        if name == "GetStreamStatus":
            if self.pending:
                state, left = self.pending
                if left:
                    self.pending = (state, left - 1)
                else:
                    self.active = state == "STARTED"
                    self.event(state, self.active)
                    self.pending = None
            if self.active:
                self.bytes += 100
            return {"outputActive": self.active, "outputReconnecting": self.reconnecting,
                    "outputBytes": self.bytes, "outputDuration": self.bytes}
        if name == "StartStream":
            self.event("STARTING", False)
            self.pending = ("STARTED", self.delay)
        elif name == "StopStream":
            self.event("STOPPING", False)
            self.pending = ("STOPPED", self.delay)
        else:
            raise AssertionError("Unexpected fixture request: " + name)
        if name in self.lost_ack:
            raise ObsError("not-a-real-secret")
        return {}


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.enterContext(patch.object(demo.time, "monotonic", side_effect=lambda: self.clock))
        self.enterContext(patch.object(demo.time, "sleep", side_effect=self.advance))
        self.client = FakeClient()
        self.receiver = Mock()
        self.trace = []
        self.lease = demo.LocalStreamLease(self.client, self.client.profile, self.client.collection, self.receiver, self.trace)

    def advance(self, seconds):
        self.clock += seconds

    def writes(self):
        return [name for name, _ in self.client.calls if name in ("StartStream", "StopStream")]

    def test_delayed_start_and_stop_need_matching_events_and_status(self):
        settings = self.client.settings
        self.lease.start()
        self.assertTrue(self.lease.confirmed)
        first = self.lease.observe()
        later = self.lease.observe()
        self.assertGreater(later["output_bytes"], first["output_bytes"])
        self.lease.stop()
        self.assertTrue(self.lease.stopped)
        self.assertEqual(self.writes(), ["StartStream", "StopStream"])
        self.assertIs(self.client.settings, settings)

    def test_wrong_service_url_key_auth_or_identity_refuses_start(self):
        cases = [({"server": "rtmp://example.invalid/live"}, None), ({"key": "test-only"}, None),
                 ({"use_auth": True}, None), ({"use_auth": 0}, None), ({"username": "test-only"}, None),
                 ({}, "rtmp_common")]
        for changes, kind in cases:
            with self.subTest(changes=changes, kind=kind):
                self.client.service = {"streamServiceType": kind or "rtmp_custom", "streamServiceSettings": dict(demo.SERVICE_SETTINGS, **changes)}
                with self.assertRaises(RuntimeError):
                    self.lease.start()
                self.assertEqual(self.writes(), [])

    def test_existing_or_unknown_outputs_never_get_adopted(self):
        for active in (True, None, 0, "false"):
            with self.subTest(active=active):
                self.client.active = active
                with self.assertRaises(RuntimeError):
                    self.lease.start()
                self.assertFalse(self.lease.start_requested)
                self.assertEqual(self.writes(), [])

    def test_receiver_failure_blocks_start(self):
        self.receiver.check.side_effect = RuntimeError("test-only")
        with self.assertRaises(RuntimeError):
            self.lease.start()
        self.assertEqual(self.writes(), [])

    def test_pending_external_start_seen_during_preflight_blocks_start(self):
        def pending(name, _data):
            if name == "GetStreamServiceSettings":
                self.client.event("STARTING", False)
        self.client.before = pending
        with self.assertRaisesRegex(RuntimeError, "preflight"):
            self.lease.start()
        self.assertEqual(self.writes(), [])

    def test_prior_pending_start_is_not_discarded_and_terminal_history_is_allowed(self):
        for state, active in (("STARTING", False), ("STARTED", True), ("RECONNECTING", False)):
            self.client.stream_events.clear()
            self.client.event(state, active)
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                self.lease.start()
            self.assertEqual(self.writes(), [])
        self.client.stream_events.clear()
        self.client.event("STOPPED", False)
        self.lease.start()
        self.assertTrue(self.lease.confirmed)

    def test_unknown_reconnect_state_cannot_finalize_stop(self):
        self.lease.start()
        def unknown(name, _data):
            if name == "GetStreamStatus" and self.lease.stop_requested:
                self.client.reconnecting = None
        self.client.before = unknown
        with self.assertRaises(RuntimeError):
            self.lease.stop()
        self.assertFalse(self.lease.stopped)
        self.assertEqual(self.writes(), ["StartStream", "StopStream"])

    def test_lost_start_ack_preserves_uncertainty_and_never_retries_or_stops(self):
        self.client.lost_ack.add("StartStream")
        with self.assertRaises(ObsError):
            self.lease.start()
        self.assertTrue(self.lease.start_requested)
        self.assertFalse(self.lease.confirmed)
        for method in (self.lease.start, self.lease.stop):
            with self.assertRaises(RuntimeError):
                method()
        self.assertEqual(self.writes(), ["StartStream"])

    def test_lost_stop_ack_never_retries(self):
        self.lease.start()
        self.client.lost_ack.add("StopStream")
        with self.assertRaises(ObsError):
            self.lease.stop()
        self.assertTrue(self.lease.stop_requested)
        self.assertFalse(self.lease.stopped)
        with self.assertRaises(RuntimeError):
            self.lease.stop()
        self.assertEqual(self.writes(), ["StartStream", "StopStream"])

    def test_intervening_stop_start_or_reconnect_invalidates_lease(self):
        for events in (("STOPPED", "STARTED"), ("RECONNECTING",), ("RECONNECTED",)):
            with self.subTest(events=events):
                self.client = FakeClient()
                self.lease = demo.LocalStreamLease(self.client, self.client.profile, self.client.collection, self.receiver, [])
                self.lease.start()
                for event in events:
                    self.client.event(event, event in ("STARTED", "RECONNECTED"))
                with self.assertRaises(RuntimeError):
                    self.lease.stop()
                self.assertEqual(self.writes(), ["StartStream"])

    def test_reconnecting_status_before_event_arrival_refuses_stop(self):
        self.lease.start()
        self.client.reconnecting = True
        with self.assertRaises(RuntimeError):
            self.lease.stop()
        self.assertEqual(self.writes(), ["StartStream"])

    def test_connection_or_selection_loss_refuses_stop(self):
        self.lease.start()
        self.client.profile = "Operator profile"
        with self.assertRaises(RuntimeError):
            self.lease.stop()
        self.client.profile = self.lease.profile
        self.client.closed = True
        with self.assertRaises(RuntimeError):
            self.lease.stop()
        self.assertEqual(self.writes(), ["StartStream"])

    def test_contradictory_event_cannot_confirm_start(self):
        def malformed(name, _data):
            if name == "GetStreamStatus" and self.lease.start_requested:
                self.client.pending = None
                self.client.active = True
                self.client.event("STARTED", False)
        self.client.before = malformed
        with self.assertRaises(RuntimeError):
            self.lease.start()
        self.assertFalse(self.lease.confirmed)
        self.assertEqual(self.writes(), ["StartStream"])

    def test_transition_budget_caps_rpc_and_rejects_late_success(self):
        settings = self.client.settings
        budgets = []
        def late(name, _data):
            if name == "GetStreamStatus" and self.lease.start_requested:
                budgets.append(self.client.settings.timeout)
                self.advance(10)
                self.client.pending = None
                self.client.active = True
                self.client.event("STARTED", True)
        self.client.before = late
        with self.assertRaises(RuntimeError):
            self.lease.start()
        self.assertFalse(self.lease.confirmed)
        self.assertEqual(budgets, [5])
        self.assertIs(self.client.settings, settings)

    def test_offline_preview_does_not_open_obs_or_create_output(self):
        args = SimpleNamespace(assets_directory=ROOT, output=ROOT / "runtime" / "unused", execute=False, ffmpeg="ffmpeg")
        with patch.object(demo, "local_assets", return_value={}), patch.object(demo, "StreamClient") as obs, \
                patch.object(Path, "mkdir") as mkdir, patch("builtins.print") as output:
            with self.assertRaises(StopIteration):
                demo.run(args).send(None)
        obs.assert_not_called()
        mkdir.assert_not_called()
        self.assertFalse(json.loads(output.call_args.args[0])["obs_contacted"])
        self.assertEqual(json.loads(output.call_args.args[0])["presenter"], "framed")

    def test_missing_ffmpeg_fails_before_obs_connection(self):
        args = SimpleNamespace(assets_directory=ROOT, output=ROOT / "runtime" / "unused", execute=True, ffmpeg="missing")
        with patch.object(demo, "local_assets", return_value={}), patch.object(demo.shutil, "which", return_value=None), \
                patch.object(demo, "StreamClient") as obs:
            with self.assertRaisesRegex(RuntimeError, "FFmpeg"):
                demo.run(args).send(None)
        obs.assert_not_called()

    def test_uncertain_start_blocks_restoration_even_if_current_status_is_false(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "evidence"
            args = SimpleNamespace(assets_directory=Path(temp), output=output, execute=True, ffmpeg="ffmpeg")
            async def uncertain(client, receiver, lease, *_args, **_kwargs):
                lease.start_requested = True
                raise RuntimeError("start uncertain")
            with patch.object(demo, "local_assets", return_value={}), patch.object(demo.shutil, "which", return_value="test-only"), \
                    patch.object(demo.ObsSettings, "from_environment", return_value=self.client.settings), \
                    patch.object(demo, "StreamClient", return_value=self.client), patch.object(demo, "Receiver", return_value=self.receiver), \
                    patch.object(demo, "prepare"), patch.object(demo, "build_and_stream", uncertain), \
                    patch.object(demo.rehearse, "tone"), patch.object(demo.rehearse, "restore_original") as restore:
                with self.assertRaisesRegex(RuntimeError, "not fully restored"):
                    demo.run(args).send(None)
            restore.assert_not_called()
            result = json.loads((output / "restoration.json").read_text(encoding="utf-8"))
            self.assertEqual(result["reason"], "stream_ownership_or_finalization_unproven")
            self.receiver.close.assert_called_once()

    def test_receiver_shutdown_failure_does_not_skip_eligible_restoration_or_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "evidence"
            args = SimpleNamespace(assets_directory=Path(temp), output=output, execute=True, ffmpeg="ffmpeg")
            self.receiver.close.side_effect = RuntimeError("not-a-real-secret")
            restored = dict(profile=True, collection=True, scene=True, video=True)
            with patch.object(demo, "local_assets", return_value={}), patch.object(demo.shutil, "which", return_value="test-only"), \
                    patch.object(demo.ObsSettings, "from_environment", return_value=self.client.settings), \
                    patch.object(demo, "StreamClient", return_value=self.client), patch.object(demo, "Receiver", return_value=self.receiver), \
                    patch.object(demo, "prepare", side_effect=RuntimeError("prepare failed")), \
                    patch.object(demo.rehearse, "tone"), patch.object(demo.rehearse, "restore_original", return_value=restored) as restore:
                with self.assertRaisesRegex(RuntimeError, "receiver shutdown"):
                    demo.run(args).send(None)
            restore.assert_called_once()
            self.assertEqual(json.loads((output / "restoration.json").read_text()), restored)
            evidence = (output / "trace-local.json").read_text()
            self.assertIn("receiver_shutdown_unverified", evidence)
            self.assertNotIn("not-a-real-secret", evidence)

    def test_setup_checks_idle_before_each_configuration_mutation(self):
        class SetupClient(FakeClient):
            def request(client, name, data=None):
                if name in ("CreateProfile", "SetCurrentProfile"):
                    client.calls.append((name, data))
                    client.profile = data["profileName"]
                    return {}
                if name == "CreateSceneCollection":
                    client.calls.append((name, data))
                    client.collection = data["sceneCollectionName"]
                    return {}
                if name == "SetProfileParameter":
                    client.calls.append((name, data))
                    return {}
                if name == "GetVideoSettings": return deepcopy(demo.VIDEO)
                if name == "GetSpecialInputs":
                    client.recording = True
                    return {"desktop1": "Operator Desktop"}
                return super().request(name, data)
        client = SetupClient()
        original = dict(profile=client.profile, collection=client.collection)
        with self.assertRaisesRegex(RuntimeError, "active"):
            demo.prepare(client, original, "Isolated", "Isolated", [])
        names = [name for name, _ in client.calls]
        self.assertNotIn("SetInputMute", names)
        self.assertNotIn("SetStreamServiceSettings", names)
        self.assertNotIn("SetVideoSettings", names)

    def test_loopback_bind_probe_never_connects(self):
        sock = Mock()
        with patch.object(demo.socket, "socket", return_value=sock):
            self.assertTrue(demo.port_available())
        sock.bind.assert_called_once_with(("127.0.0.1", 19351))
        sock.connect.assert_not_called()
        sock.close.assert_called_once()

    def test_receiver_checks_exit_and_uses_fixed_loopback_hidden_command(self):
        with tempfile.TemporaryDirectory() as temp:
            receiver = demo.Receiver("test-only", Path(temp))
            process = Mock()
            process.poll.return_value = None
            with patch.object(demo, "port_available", side_effect=[True, False]), \
                    patch.object(demo.subprocess, "Popen", return_value=process) as popen:
                receiver.start()
            args, kwargs = popen.call_args
            self.assertIn(demo.LOOPBACK_URL, args[0])
            self.assertEqual(args[0].count("-listen"), 1)
            self.assertEqual(kwargs["creationflags"], getattr(demo.subprocess, "CREATE_NO_WINDOW", 0))
            process.poll.return_value = 1
            with self.assertRaises(RuntimeError):
                receiver.check()
            receiver.close()

    def test_receiver_failure_is_observed_without_waiting_for_stream_start(self):
        with tempfile.TemporaryDirectory() as temp:
            receiver = demo.Receiver("test-only", Path(temp))
            process = Mock()
            process.poll.return_value = 1
            with patch.object(demo, "port_available", return_value=True), \
                    patch.object(demo.subprocess, "Popen", return_value=process):
                with self.assertRaises(RuntimeError):
                    receiver.start()
            self.assertIsNone(receiver.log)

    def test_local_assets_reject_known_remote_references(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in demo.FILES:
                (root / (name + ".html")).write_text("<html>synthetic</html>", encoding="utf-8")
            self.assertEqual(set(demo.local_assets(root)), set(demo.FILES))
            for remote in ('<img src="https://example.invalid/image">', '<style>body{background:url(//example.invalid/x)}</style>',
                           '<script src="https&#58;//example.invalid/x"></script>'):
                (root / "chart.html").write_text(remote, encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    demo.local_assets(root)

    def test_cutout_selects_separate_local_host_and_invalid_options_fail_before_obs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (*demo.FILES, "host-cutout"):
                (root / (name + ".html")).write_text("<html>synthetic</html>", encoding="utf-8")
            self.assertEqual(demo.local_assets(root)["host"].name, "host.html")
            self.assertEqual(demo.local_assets(root, presenter="cutout")["host"].name, "host-cutout.html")
        for theme, presenter in (("unknown", "framed"), ("light", "unknown"), (None, "cutout")):
            args = SimpleNamespace(theme=theme, presenter=presenter, assets_directory=ROOT, execute=True)
            with patch.object(demo, "local_assets") as assets, patch.object(demo, "StreamClient") as obs:
                with self.assertRaisesRegex(RuntimeError, "Unsupported demo"):
                    demo.run(args).send(None)
                with self.assertRaisesRegex(RuntimeError, "Unsupported demo"):
                    demo.build_and_stream(None, None, None, ROOT, {}, [], "p", "c", theme, presenter).send(None)
                assets.assert_not_called()
                obs.assert_not_called()

    def test_pnl_demo_snapshot_routes_to_actual_formatter(self):
        positive, negative = demo.pnl_display(), demo.pnl_display(negative=True)
        self.assertEqual((positive["net_minor"], negative["net_minor"]), (24500, -8500))
        self.assertTrue(positive["text"].startswith("DEMO P&L  USD +245.00"))
        self.assertTrue(negative["text"].startswith("DEMO P&L  USD -85.00"))
        self.assertEqual((positive["state"], negative["state"]), ("gain", "loss"))
        self.assertNotEqual(positive["color"], negative["color"])

    def test_cut_recipes_bind_only_existing_hero_host_banner_and_keep_artwork(self):
        from obs_director.layouts import validate_layout_recipe
        source = json.loads((ROOT / "src/obs_director/template_data/funded-desk/template.json").read_text())
        source["assets"] = {name: str((ROOT / (name + ".png")).resolve()) for name in source["assets"]}
        original = deepcopy(source)
        variants = demo.cut_recipes(source)
        self.assertEqual(source, original)
        for label, variant in zip(("Chart", "Replay"), variants):
            validate_layout_recipe(variant)
            self.assertEqual(variant["scene_name"], "Funded Desk - " + label)
            existing = [layer for layer in variant["layers"] if layer["type"] == "existing"]
            self.assertEqual([layer["source_name"] for layer in existing], ["Demo " + label, "Demo Host", "Demo Banner"])
            self.assertEqual(existing[0]["rect"], dict(x=40, y=130, width=1600, height=900))

    def test_dark_theme_routes_through_copy_and_replay_browser_gets_native_resolution(self):
        import asyncio
        calls = []
        sessions, inputs = [], {}
        class MCP:
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            async def initialize(self): pass
            async def call_tool(self, name, arguments):
                calls.append((name, deepcopy(arguments)))
                value = {"verified": True}
                if name == "obs_add_source":
                    inputs[arguments["source_name"]] = {"sourceName": arguments["source_name"], "sceneItemId": len(inputs) + 1}
                if name == "obs_apply_layout": value.update(change_count=0, receipts=[])
                if name == "obs_scene_sources": value["sources"] = list(inputs.values())
                if name == "obs_inputs": value["input_kinds"] = ["text_gdiplus_v3", "color_source_v3"]
                return SimpleNamespace(isError=False, structuredContent=value)
        @asynccontextmanager
        async def stdio(params, _log):
            sessions.append(deepcopy(params.env))
            if len(sessions) == 2:
                routes = json.loads(Path(params.env["OBS_MCP_EVENT_ROUTES"]).read_text())
                self.assertEqual(routes["schema"], "obs.event-routes.v1")
                self.assertEqual(len(routes["routes"]), 8)
            yield (None, None)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            recipe = json.loads((ROOT / "src/obs_director/template_data/funded-desk/template.json").read_text())
            recipe["scene_name"] = "Funded Desk Dark"
            copied = output / "template.json"
            copied.write_text(json.dumps(recipe), encoding="utf-8")
            files = {name: output / (name + ".html") for name in demo.FILES}
            files["host"] = output / "host-cutout.html"
            with patch.object(demo, "add_template", return_value={"recipe_path": str(copied)}) as copy, \
                    patch.object(demo, "hidden_stdio", stdio), patch.object(demo, "ClientSession", return_value=MCP()), \
                    patch.object(demo.rehearse, "require_owned_selection"), patch.object(demo.asyncio, "sleep", new_callable=AsyncMock), \
                    patch.object(demo, "stream_phases", new_callable=AsyncMock), \
                    patch.object(demo, "pnl_display", return_value={"text": "DEMO P&L\n+$245.00", "color": 0xFF00AA00}):
                asyncio.run(demo.build_and_stream(self.client, self.receiver, self.lease, output, files, [], "p", "c", theme="dark", presenter="cutout"))
            self.assertEqual(copy.call_args.kwargs, {"theme": "dark"})
            self.assertEqual(len(sessions), 2)
            self.assertNotIn("OBS_MCP_EVENT_ROUTES", sessions[0])
            self.assertEqual(sessions[1]["OBS_MCP_EVENT_ROUTES"], str(output / "audience-routes.json"))
        sources = {args["source_name"]: args for name, args in calls if name == "obs_add_source"}
        replay = sources["Demo Replay"]["settings"]
        self.assertEqual((replay["width"], replay["height"], replay["css"]), (1856, 1044, "html { zoom: 4; }"))
        self.assertTrue(sources["Demo Host"]["settings"]["local_file"].endswith("host-cutout.html"))
        for name in ("chart", "host", "status"):
            settings = sources["Demo " + name.title()]["settings"]
            self.assertEqual((settings["width"], settings["height"]), demo.FILES[name])
            self.assertEqual(settings["css"], "")
        applied = [args["recipe"] for name, args in calls if name == "obs_apply_layout"]
        self.assertEqual([value["scene_name"] for value in applied],
                         ["Funded Desk Dark", "Funded Desk Dark", "Funded Desk Dark - Chart", "Funded Desk Dark - Replay"])
        self.assertEqual(next(layer for layer in applied[0]["layers"] if layer["id"] == "host-b")["rect"],
                         dict(x=1416, y=668, width=464, height=261))
        for layout in applied:
            host = next(layer for layer in layout["layers"] if layer["id"] in ("host", "host-inset"))
            self.assertNotIn("border", host)
        overlays = [args for name, args in calls if name == "obs_add_source" and args["source_name"].startswith(("Demo Pnl", "Demo Audience"))]
        self.assertEqual(len(overlays), 4)
        self.assertTrue(all(args["scene_name"] == "Funded Desk Dark" for args in overlays))
        hidden = [args for name, args in calls if name == "obs_source_visibility"]
        self.assertEqual(len(hidden), 2)
        self.assertTrue(all(args["enabled"] is False for args in hidden))

    def test_overlay_phases_update_pnl_dispatch_dedupe_and_refuse_wrong_scene(self):
        import asyncio
        selected = ["Funded Desk"]
        visible = [False, False]
        text = ["initial"]
        ids, completed, screenshots, updates, wrong = set(), [], [], [], []
        original_sleep = asyncio.sleep
        alert_release = None
        async def call(name, **arguments):
            nonlocal alert_release
            if name == "obs_select_scene":
                selected[0] = arguments["scene_name"]
                return {"verified": True}
            if name == "obs_source_settings":
                updates.append(arguments)
                return {"verified": True}
            self.assertEqual(name, "obs_dispatch_event")
            event = arguments["event"]
            if arguments.get("expect_error"):
                self.assertNotEqual(selected[0], "Funded Desk")
                wrong.append(event)
                return {"expected_error": True}
            self.assertEqual(selected[0], "Funded Desk")
            if event["event_id"] in ids:
                return {"duplicate": True, "applied": False}
            ids.add(event["event_id"])
            visible[:] = [True, True]
            text[0] = event["payload"]["title"]
            alert_release = asyncio.Event()
            await alert_release.wait()
            self.advance(3.5)
            visible[:] = [False, False]
            completed.append(event["event_type"])
            return {"state": "completed"}
        async def sleep(seconds):
            self.advance(seconds)
            await original_sleep(0)
        def screenshot(client, actual, path, *_args):
            screenshots.append((actual, list(visible)))
            if alert_release and not alert_release.is_set():
                alert_release.set()
        def pnl(negative=False):
            return {"text": "DEMO\n-$85.00" if negative else "DEMO\n+$245.00", "color": 123,
                    "net_minor": -8500 if negative else 24500, "state": "negative" if negative else "positive"}
        lease = Mock()
        count = [0]
        def observe():
            count[0] += 1
            return {"output_bytes": count[0] * 100}
        lease.observe.side_effect = observe
        trace = []
        with patch.object(demo.asyncio, "sleep", sleep), patch.object(demo, "program_screenshot", screenshot), \
                patch.object(demo, "audience_state", side_effect=lambda *_args: (list(visible), text[0])), \
                patch.object(demo, "pnl_display", side_effect=pnl):
            asyncio.run(demo.stream_phases(Mock(), lease, call, "Funded Desk", ROOT, trace, "p", "c", overlays={"text_kind": "text_gdiplus_v3"}))
        self.assertEqual(completed, ["audience.twitch.subscription", "audience.youtube.membership"])
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(wrong), 1)
        self.assertEqual([flags for _, flags in screenshots], [[True, True], [False, False], [False, False], [True, True]])
        self.assertEqual([item["net_minor"] for item in trace if item["step"] == "demo_pnl"], [24500, -8500])
        self.assertTrue(all(item["allow_live"] is True and item["dry_run"] is False for item in updates))
        self.assertEqual(visible, [False, False])
        self.assertEqual(self.clock, 25)

    def test_alert_capture_failure_waits_for_dispatched_cue_before_propagating(self):
        import asyncio
        original_sleep = asyncio.sleep
        released = None
        completed = []
        async def call(name, **arguments):
            nonlocal released
            if name == "obs_source_settings": return {"verified": True}
            self.assertEqual(name, "obs_dispatch_event")
            released = asyncio.Event()
            await released.wait()
            await original_sleep(0)
            completed.append("cue finished")
            return {"state": "completed"}
        async def sleep(seconds):
            await original_sleep(0)
        def failed_capture(*_args):
            released.set()
            raise RuntimeError("synthetic screenshot failure")
        with patch.object(demo.asyncio, "sleep", sleep), patch.object(demo, "program_screenshot", failed_capture), \
                patch.object(demo, "audience_state", return_value=([True, True], "synthetic")):
            with self.assertRaisesRegex(RuntimeError, "synthetic screenshot failure"):
                asyncio.run(demo.stream_phases(Mock(), Mock(), call, "Funded Desk", ROOT, [], "p", "c", overlays={"text_kind": "text_gdiplus_v3"}))
        self.assertEqual(completed, ["cue finished"])

    def test_real_cut_schedule_requires_lease_and_screenshots_actual_selected_scene(self):
        import base64
        import asyncio
        selected = ["Funded Desk"]
        async def call(name, **arguments):
            self.assertEqual(name, "obs_select_scene")
            self.assertTrue(arguments["allow_live"])
            self.assertFalse(arguments["dry_run"])
            selected[0] = arguments["scene_name"]
            return {"verified": True}
        async def sleep(seconds):
            self.advance(seconds)
        count = [0]
        def observe():
            count[0] += 1
            return {"output_bytes": count[0] * 100}
        client = Mock()
        screenshots = []
        def request(name, data=None):
            if name == "GetProfileList": return {"currentProfileName": "owned"}
            if name == "GetSceneCollectionList": return {"currentSceneCollectionName": "owned"}
            if name == "GetCurrentProgramScene": return {"currentProgramSceneName": selected[0]}
            if name == "GetSourceScreenshot":
                screenshots.append(data["sourceName"])
                return {"imageData": "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()}
            raise AssertionError(name)
        client.request.side_effect = request
        lease = Mock()
        lease.observe.side_effect = observe
        trace = []
        with tempfile.TemporaryDirectory() as temp, patch.object(demo.asyncio, "sleep", sleep):
            asyncio.run(demo.stream_phases(client, lease, call, "Funded Desk", Path(temp), trace, "owned", "owned"))
            self.assertEqual(len(list(Path(temp).glob("stream-phase-*.png"))), 4)
        self.assertEqual(screenshots, ["Funded Desk", "Funded Desk - Chart", "Funded Desk - Replay", "Funded Desk"])
        self.assertEqual([item["elapsed_seconds"] for item in trace if item["step"] == "stream_phase"], [0, 8, 15, 21])
        self.assertEqual(self.clock, 25)


if __name__ == "__main__":
    unittest.main()
