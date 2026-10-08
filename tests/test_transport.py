"""Wire-contract tests: authenticate, correlate responses, fail closed on disconnect."""
import base64
import hashlib
import json
import unittest
from unittest.mock import patch

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from obs_director.transport import ObsClient, ObsError, ObsSettings, _config_path, data_directory


class Socket:
    def __init__(self, *, reject=False, disconnect=False):
        self.incoming = [{"op": 0, "d": {"rpcVersion": 1, "authentication": {
            "salt": "salt", "challenge": "challenge"}}}]
        self.sent = []
        self.closed = False
        self.reject = reject
        self.disconnect = disconnect

    def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        if message["op"] == 1:
            self.incoming.append({"op": 2, "d": {"negotiatedRpcVersion": 1}})
        elif message["op"] == 6:
            payload = message["d"]
            self.incoming.extend([
                {"op": 5, "d": {"eventType": "RecordStateChanged", "eventData": {
                    "outputActive": True, "outputState": "OBS_WEBSOCKET_OUTPUT_STARTED"}}},
                {"op": 7, "d": {"requestId": "unrelated", "requestType": payload["requestType"],
                    "requestStatus": {"result": True}, "responseData": {"wrong": True}}},
                {"op": 7, "d": {"requestId": payload["requestId"], "requestType": payload["requestType"],
                    "requestStatus": {"result": not self.reject, "code": 601 if self.reject else 100,
                                      "comment": "not-a-real-secret"},
                    "responseData": {"obsVersion": "32.1.0"}}},
            ])

    def recv(self, timeout):
        if self.disconnect and self.sent and self.sent[-1]["op"] == 6:
            raise ConnectionError("not-a-real-secret")
        if not self.incoming:
            raise TimeoutError("not-a-real-secret")
        return json.dumps(self.incoming.pop(0))

    def close(self):
        self.closed = True


class ProtocolTests(unittest.TestCase):
    def test_authentication_correlation_and_event_capture(self):
        socket = Socket()
        with patch("obs_director.transport.connect", return_value=socket) as connect:
            with ObsClient(ObsSettings(password="not-a-real-secret")) as client:
                result = client.request("GetVersion")
                self.assertEqual(result, {"obsVersion": "32.1.0"})
                self.assertEqual(len(client.record_events), 1)
        secret = base64.b64encode(hashlib.sha256(b"not-a-real-secretsalt").digest()).decode()
        auth = base64.b64encode(hashlib.sha256((secret + "challenge").encode()).digest()).decode()
        self.assertEqual(socket.sent[0]["d"]["authentication"], auth)
        self.assertEqual(socket.sent[0]["d"]["eventSubscriptions"] & 64, 64)
        self.assertEqual(connect.call_args.args, ("ws://127.0.0.1:4455",))
        self.assertIsNone(connect.call_args.kwargs["proxy"])
        self.assertTrue(socket.closed)

    def test_rejection_omits_server_comment_and_secret(self):
        socket = Socket(reject=True)
        with patch("obs_director.transport.connect", return_value=socket):
            with ObsClient(ObsSettings(password="not-a-real-secret")) as client:
                with self.assertRaises(ObsError) as error:
                    client.request("GetVersion")
        self.assertIn("601", str(error.exception))
        self.assertNotIn("not-a-real-secret", str(error.exception))

    def test_disconnect_invalidates_connection_without_secret(self):
        socket = Socket(disconnect=True)
        with patch("obs_director.transport.connect", return_value=socket):
            with ObsClient(ObsSettings(password="not-a-real-secret")) as client:
                with self.assertRaises(ObsError) as error:
                    client.request("GetVersion")
                self.assertTrue(client.closed)
        self.assertNotIn("not-a-real-secret", str(error.exception))

    def test_truthy_non_boolean_success_is_rejected(self):
        socket = Socket()
        original_send = socket.send

        def send(raw):
            original_send(raw)
            if json.loads(raw)["op"] == 6:
                socket.incoming[-1]["d"]["requestStatus"]["result"] = "false"

        socket.send = send
        with patch("obs_director.transport.connect", return_value=socket):
            with ObsClient(ObsSettings(password="not-a-real-secret")) as client:
                with self.assertRaises(ObsError):
                    client.request("GetVersion")

    def test_invalid_handshake_does_not_send_requests(self):
        socket = Socket()
        socket.incoming = [{"op": 5, "d": {"eventType": "SomethingElse"}}]
        with patch("obs_director.transport.connect", return_value=socket):
            with self.assertRaises(ObsError):
                with ObsClient(ObsSettings(password="not-a-real-secret")):
                    self.fail("malformed Hello was accepted")
        self.assertEqual(socket.sent, [])
        self.assertTrue(socket.closed)

    def test_settings_repr_redacts_password(self):
        self.assertNotIn("not-a-real-secret", repr(ObsSettings(password="not-a-real-secret")))

    def test_capabilities_are_cached_and_missing_request_never_sent(self):
        socket = Socket()
        original_send = socket.send

        def send(raw):
            original_send(raw)
            message = json.loads(raw)
            if message["op"] == 6 and message["d"]["requestType"] == "GetVersion":
                socket.incoming[-1]["d"]["responseData"]["availableRequests"] = ["GetVersion", "GetRecordStatus"]

        socket.send = send
        with patch("obs_director.transport.connect", return_value=socket):
            with ObsClient(ObsSettings(password="not-a-real-secret")) as client:
                client.require_capabilities(["GetRecordStatus"])
                client.require_capabilities(["GetRecordStatus"])
                client.request("GetRecordStatus")
                with self.assertRaises(ObsError):
                    client.request("StartRecord")
        requests = [item["d"]["requestType"] for item in socket.sent if item["op"] == 6]
        self.assertEqual(["GetVersion", "GetRecordStatus"], requests)

    def test_unknown_capabilities_fail_closed(self):
        socket = Socket()
        with patch("obs_director.transport.connect", return_value=socket):
            with ObsClient(ObsSettings(password="not-a-real-secret")) as client:
                with self.assertRaises(ObsError):
                    client.request("StartRecord")
        self.assertEqual(["GetVersion"], [item["d"]["requestType"] for item in socket.sent if item["op"] == 6])

    def test_portable_config_and_state_override(self):
        root = Path(__file__).resolve().parent
        with patch.dict("os.environ", {"OBS_MCP_CONFIG": str(root / "fixture.json"),
                                      "OBS_MCP_DATA_DIR": str(root / "state")}, clear=True):
            self.assertEqual(root / "fixture.json", _config_path())
            self.assertEqual(root / "state", data_directory())
        with patch("obs_director.transport.sys.platform", "linux"), patch.dict("os.environ", {"XDG_CONFIG_HOME": str(root)}, clear=True):
            self.assertEqual(root / "obs-studio/plugin_config/obs-websocket/config.json", _config_path())


if __name__ == "__main__":
    unittest.main()
