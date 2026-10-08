# Derived from in-house prototype SHA256 04933b2733106d6f093aba821c48626e3bd21b90f8e1dae24eb869811aaeb861.
"""Portable local OBS websocket v5 transport.

Original implementation of the public obs-websocket protocol. Pixels and media
stay on this machine; callers receive metadata, never screenshots or passwords.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import uuid

from websockets.sync.client import connect


class ObsError(RuntimeError):
    """A sanitized configuration, protocol or ownership failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_directory() -> Path:
    """User-local state root; no machine-specific paths are built into the package."""
    override = os.environ.get("OBS_MCP_DATA_DIR")
    if override:
        return _local_path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return _local_path(base / "obs-mcp")


def _config_path() -> Path:
    override = os.environ.get("OBS_MCP_CONFIG")
    if override:
        return _local_path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "obs-studio/plugin_config/obs-websocket/config.json"


@dataclass(frozen=True)
class ObsSettings:
    port: int = 4455
    password: str = field(default="", repr=False)
    timeout: float = 5.0

    def __post_init__(self) -> None:
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("OBS port must be an integer from 1 to 65535")
        if not isinstance(self.password, str):
            raise ValueError("OBS password must be text")
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 30:
            raise ValueError("OBS timeout must be between 0 and 30 seconds")

    @classmethod
    def from_environment(cls) -> ObsSettings:
        config: dict[str, Any] = {}
        path = _config_path()
        if path.exists():
            try:
                config = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(config, dict):
                    raise ValueError()
            except (OSError, ValueError):
                raise ObsError("Cannot read local OBS websocket configuration") from None
        try:
            port = int(os.environ.get("OBS_MCP_PORT", config.get("server_port", 4455)))
            password = os.environ.get("OBS_MCP_PASSWORD", config.get("server_password", ""))
            return cls(port=port, password=password)
        except (TypeError, ValueError):
            raise ObsError("Invalid OBS websocket port or password configuration") from None


class ObsClient:
    """One authenticated loopback connection; requests have bounded deadlines.

    Recording lifecycle events are retained to invalidate ownership after an
    external stop/restart. A lost connection can never authorize StopRecord.
    """

    def __init__(self, settings: ObsSettings | None = None):
        self.settings = settings or ObsSettings.from_environment()
        self.socket: Any = None
        self.record_events: list[dict[str, Any]] = []
        self.closed = False
        self._version: dict[str, Any] | None = None

    def __enter__(self) -> ObsClient:
        try:
            self.socket = connect(
                f"ws://127.0.0.1:{self.settings.port}",
                open_timeout=self.settings.timeout, close_timeout=1,
                max_size=12 * 1024 * 1024, proxy=None,
            )
            hello = self._receive(time.monotonic() + self.settings.timeout)
            if hello.get("op") != 0:
                raise ObsError("OBS did not send a websocket v5 Hello")
            data: dict[str, Any] = {"rpcVersion": 1, "eventSubscriptions": 64}
            auth = hello.get("d", {}).get("authentication")
            if auth:
                if not self.settings.password:
                    raise ObsError("OBS requires a websocket password; configure it locally")
                secret = base64.b64encode(hashlib.sha256(
                    (self.settings.password + auth["salt"]).encode()).digest()).decode()
                data["authentication"] = base64.b64encode(hashlib.sha256(
                    (secret + auth["challenge"]).encode()).digest()).decode()
            self.socket.send(json.dumps({"op": 1, "d": data}))
            identified = self._receive(time.monotonic() + self.settings.timeout)
            if identified.get("op") != 2:
                raise ObsError("OBS websocket identification failed")
            return self
        except ObsError:
            self.close()
            raise
        except Exception:
            self.close()
            raise ObsError("Cannot connect or authenticate to local OBS websocket") from None

    def _receive(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self.close()
            raise ObsError("OBS request timed out; recording state may be uncertain")
        try:
            message = json.loads(self.socket.recv(timeout=remaining))
            if not isinstance(message, dict) or not isinstance(message.get("d"), dict):
                raise ValueError()
            return message
        except Exception:
            self.close()
            raise ObsError("OBS connection failed or timed out; recording state may be uncertain") from None

    def request(self, request_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.closed or self.socket is None:
            raise ObsError("OBS connection is closed; recording ownership is unproven")
        if request_type != "GetVersion":
            self.require_capabilities([request_type])
        request_id = uuid.uuid4().hex
        payload = {"requestType": request_type, "requestId": request_id}
        if data is not None:
            payload["requestData"] = data
        deadline = time.monotonic() + self.settings.timeout
        try:
            self.socket.send(json.dumps({"op": 6, "d": payload}))
            while True:
                message = self._receive(deadline)
                reply = message["d"]
                if message.get("op") == 5 and reply.get("eventType") == "RecordStateChanged":
                    if len(self.record_events) >= 256:
                        self.close()
                        raise ObsError("Recording event history exceeded its bound; ownership is unproven")
                    self.record_events.append(reply.get("eventData", {}))
                elif message.get("op") == 7 and reply.get("requestId") == request_id:
                    if reply.get("requestType") != request_type:
                        raise ObsError("OBS returned a mismatched response type")
                    status = reply.get("requestStatus", {})
                    if status.get("result") is not True:
                        code = status.get("code")
                        code = code if type(code) is int else "unknown"
                        raise ObsError(f"OBS rejected {request_type} (code {code})")
                    result = reply.get("responseData", {})
                    if not isinstance(result, dict):
                        raise ObsError("OBS returned malformed response data")
                    if request_type == "GetVersion":
                        self._version = {key: result.get(key) for key in (
                            "obsVersion", "obsWebSocketVersion", "rpcVersion", "availableRequests")}
                    return result
        except ObsError:
            raise
        except Exception:
            self.close()
            raise ObsError("OBS request failed; recording state may be uncertain") from None

    def version(self) -> dict[str, Any]:
        """Read and cache only version/capability metadata."""
        if self._version is None:
            self.request("GetVersion")
        return dict(self._version or {})

    def require_capabilities(self, names: list[str]) -> None:
        available = self.version().get("availableRequests")
        if not isinstance(available, list) or not all(isinstance(name, str) for name in available):
            raise ObsError("OBS did not provide a valid capability list")
        missing = sorted(set(names) - set(available))
        if missing:
            raise ObsError("OBS lacks required requests: " + ", ".join(missing))

    def close(self) -> None:
        self.closed = True
        if self.socket is not None:
            try:
                self.socket.close()
            except Exception:
                pass

    def __exit__(self, *_: Any) -> None:
        self.close()


def _text(value: str, label: str, max_length: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{label} must contain 1 to {max_length} characters")
    if any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} cannot contain control characters")
    return value.strip()


def _local_path(value: str | Path) -> Path:
    raw = str(value)
    if raw.startswith(("\\\\", "//")) or "://" in raw:
        raise ObsError("Only local absolute filesystem paths are supported")
    path = Path(value).resolve()
    if not Path(value).is_absolute():
        raise ObsError("An absolute local filesystem path is required")
    if str(path).startswith(("\\\\", "//")):
        raise ObsError("Resolved path must stay on a local filesystem")
    return path
