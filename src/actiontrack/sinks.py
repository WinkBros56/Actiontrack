"""Draft sinks (SPEC 7). Nothing in this module can send email.

* ``JsonSink``  - collects drafts as ``create_draft`` payloads for a Claude session (or a human) to create.
* ``McpSink``   - minimal MCP stdio client that calls one create-draft tool. Any tool whose name contains
                  "send" is refused, at construction and again at call time.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Protocol

from .drafts import Draft


class SinkError(Exception):
    pass


FORBIDDEN_TOOL_WORD = "send"
DEFAULT_ARGUMENT_MAP = {"to": "to", "subject": "subject", "body": "body", "includeSignature": "includeSignature"}


def assert_draft_only(tool: str) -> None:
    if not tool or FORBIDDEN_TOOL_WORD in tool.lower():
        raise SinkError(f"refusing tool {tool!r}: actiontrack only calls a create-draft tool, never a send tool")


class DraftSink(Protocol):
    name: str

    def create(self, draft: Draft) -> str | None: ...

    def close(self) -> None: ...


class JsonSink:
    name = "json"

    def __init__(self) -> None:
        self.drafts: list[Draft] = []

    def create(self, draft: Draft) -> str | None:
        self.drafts.append(draft)
        return None

    def payload(self) -> dict:
        return {"drafts": [{"owner": d.owner, "item_ids": d.item_ids, "warnings": d.warnings,
                            "create_draft": d.create_draft_args()} for d in self.drafts]}

    def close(self) -> None:
        pass


class McpSink:
    name = "mcp"

    def __init__(self, command: str, args: list[str] | None = None, env: dict | None = None,
                 tool: str = "create_draft", arguments: dict | None = None, timeout: float = 30.0) -> None:
        assert_draft_only(tool)
        self.tool = tool
        self.arg_map = dict(DEFAULT_ARGUMENT_MAP if arguments is None else arguments)
        self.timeout = timeout
        self._id = 0
        try:
            self._proc = subprocess.Popen(
                [command, *(args or [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", env={**os.environ, **(env or {})})
        except OSError as exc:
            raise SinkError(f"cannot start MCP server {command!r}: {exc}") from exc
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": "actiontrack", "version": "0.1.0"}})
        self._notify("notifications/initialized", {})

    @classmethod
    def from_config(cls, path: str | Path) -> "McpSink":
        try:
            cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))["gmail"]
            return cls(cfg["command"], cfg.get("args"), cfg.get("env"), cfg.get("tool", "create_draft"),
                       cfg.get("arguments"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SinkError(f"cannot read MCP config {path}: {exc}") from exc

    # -- transport ---------------------------------------------------------------------
    def _pump(self) -> None:
        for line in self._proc.stdout:  # type: ignore[union-attr]
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, msg: dict) -> None:
        try:
            self._proc.stdin.write(json.dumps(msg) + "\n")  # type: ignore[union-attr]
            self._proc.stdin.flush()  # type: ignore[union-attr]
        except OSError as exc:
            raise SinkError(f"MCP server closed the connection: {exc}") from exc

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict) -> dict:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        while True:
            try:
                line = self._lines.get(timeout=self.timeout)
            except queue.Empty:
                raise SinkError(f"timed out waiting for MCP response to {method}") from None
            if line is None:
                raise SinkError("MCP server exited unexpectedly")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") != self._id:
                continue
            if "error" in msg:
                raise SinkError(f"MCP error on {method}: {msg['error']}")
            return msg.get("result", {})

    # -- sink API ----------------------------------------------------------------------
    def create(self, draft: Draft) -> str | None:
        assert_draft_only(self.tool)
        source = {"to": draft.to, "subject": draft.subject, "body": draft.body,
                  "includeSignature": draft.include_signature}
        arguments = {target: source[field] for field, target in self.arg_map.items() if target and field in source}
        result = self._request("tools/call", {"name": self.tool, "arguments": arguments})
        text = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)).strip()
        if result.get("isError"):
            raise SinkError(f"{self.tool} failed: {text or result}")
        try:
            return str(json.loads(text).get("id", text))[:200]
        except (ValueError, AttributeError):
            return text[:200] or None

    def close(self) -> None:
        try:
            self._proc.stdin.close()  # type: ignore[union-attr]
            self._proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self._proc.kill()
        finally:
            for stream in (self._proc.stdin, self._proc.stdout):
                try:
                    stream.close()  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    pass

