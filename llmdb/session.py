"""
DebugSession — wraps a single GDB subprocess via pygdbmi.

One session per target executable. Drives GDB via the MI protocol,
blocks on async events, and returns typed domain objects.
"""

import os
import uuid
from pathlib import Path
from typing import Optional

from pygdbmi.constants import GdbTimeoutError
from pygdbmi.gdbcontroller import GdbController

from llmdb.models import Breakpoint, Frame, StopEvent, Variable


class DebugError(Exception):
    """Raised when GDB returns an error record."""


# Characters that GDB/MI uses as command separators.
# Allowing them in user-supplied strings would let an LLM inject
# arbitrary GDB commands into the MI stream.
_MI_FORBIDDEN = frozenset("\n\r\x00")

_MAX_RADIUS = 50  # source-context sanity cap


def _check_mi_safe(value: str, label: str) -> None:
    """Raise ValueError if value contains GDB MI command-separator characters."""
    if any(ch in _MI_FORBIDDEN for ch in value):
        raise ValueError(
            f"{label!r} contains a character that is not allowed in a GDB MI "
            f"command (newlines and null bytes are forbidden)."
        )


class DebugSession:
    def __init__(self, executable: str) -> None:
        p = Path(executable)
        if not p.is_file():
            raise FileNotFoundError(f"Executable not found: {executable}")
        if not os.access(executable, os.X_OK):
            raise PermissionError(f"File is not executable: {executable}")
        self.session_id: str = str(uuid.uuid4())
        self._executable: str = executable
        self._gdb = GdbController()
        self._send(f"-file-exec-and-symbols {executable}")

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    def run(self) -> StopEvent:
        self._gdb.write("-exec-run", read_response=False)
        return self._wait_for_stop()

    def next(self) -> StopEvent:
        self._gdb.write("-exec-next", read_response=False)
        return self._wait_for_stop()

    def step(self) -> StopEvent:
        self._gdb.write("-exec-step", read_response=False)
        return self._wait_for_stop()

    def continue_execution(self) -> StopEvent:
        self._gdb.write("-exec-continue", read_response=False)
        return self._wait_for_stop()

    def quit(self) -> None:
        try:
            self._gdb.write("-gdb-quit", timeout_sec=3)
        except Exception:
            pass
        self._gdb.exit()

    # ------------------------------------------------------------------
    # Breakpoints
    # ------------------------------------------------------------------

    def set_breakpoint(self, file: str, line: int) -> Breakpoint:
        _check_mi_safe(file, "file")
        payload = self._send(f"-break-insert {file}:{line}")
        return self._parse_breakpoint(self._require(payload, "bkpt", "set_breakpoint response"))

    def set_function_breakpoint(self, function: str) -> Breakpoint:
        _check_mi_safe(function, "function")
        payload = self._send(f"-break-insert {function}")
        return self._parse_breakpoint(self._require(payload, "bkpt", "set_function_breakpoint response"))

    def remove_breakpoint(self, bp_id: int) -> None:
        self._send(f"-break-delete {bp_id}")

    def list_breakpoints(self) -> list[Breakpoint]:
        payload = self._send("-break-list")
        body = payload.get("BreakpointTable", {}).get("body", [])
        return [self._parse_breakpoint(entry) for entry in body]

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def read_variable(self, name: str) -> Variable:
        _check_mi_safe(name, "name")
        payload = self._send(f"-data-evaluate-expression {name}")
        value = self._require(payload, "value", "read_variable response")
        return Variable(name=name, type=payload.get("type", "unknown"), value=value)

    def evaluate(self, expression: str) -> str:
        _check_mi_safe(expression, "expression")
        payload = self._send(f"-data-evaluate-expression {expression}")
        return self._require(payload, "value", "evaluate response")

    def backtrace(self) -> list[Frame]:
        payload = self._send("-stack-list-frames")
        stack = payload.get("stack", [])
        return [self._parse_frame(entry.get("frame", entry)) for entry in stack]

    def frame_info(self) -> Frame:
        payload = self._send("-stack-info-frame")
        return self._parse_frame(self._require(payload, "frame", "frame_info response"))

    def list_locals(self) -> list[Variable]:
        payload = self._send("-stack-list-locals --all-values")
        return [
            Variable(name=v["name"], type=v.get("type", "unknown"), value=v.get("value", "?"))
            for v in payload.get("locals", [])
        ]

    def list_source_context(self, radius: int = 5) -> list[str]:
        if radius < 0:
            raise ValueError(f"radius must be non-negative, got {radius}")
        radius = min(radius, _MAX_RADIUS)
        frame = self.frame_info()
        path = Path(frame.file)
        if not path.is_file():
            raise FileNotFoundError(f"Source file not found: {frame.file}")
        lines = path.read_text().splitlines()
        center = frame.line - 1  # 0-based
        start = max(0, center - radius)
        end = min(len(lines), center + radius + 1)
        return [f"{start + i + 1:4}: {lines[start + i]}" for i in range(end - start)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require(self, payload: dict, key: str, context: str) -> object:
        """Return payload[key], or raise DebugError if GDB's response is missing it."""
        try:
            return payload[key]
        except KeyError:
            raise DebugError(f"Unexpected GDB response: missing {key!r} in {context}")

    def _send(self, command: str) -> dict:
        """Send an MI command; return its payload or raise DebugError."""
        self._gdb.write(command, read_response=False)
        try:
            responses = self._gdb.get_gdb_response(timeout_sec=10)
        except GdbTimeoutError:
            raise DebugError(f"GDB did not respond to {command!r} within 10 seconds")
        return self._extract_payload(responses)

    def _extract_payload(self, responses: list) -> dict:
        for r in responses:
            if r.get("message") == "error":
                raise DebugError(r.get("payload", {}).get("msg", "GDB error"))
            if r.get("message") == "done":
                return r.get("payload") or {}
        return {}

    def _wait_for_stop(self) -> StopEvent:
        """Poll GDB until a *stopped notify arrives or an error is seen."""
        while True:
            try:
                responses = self._gdb.get_gdb_response(timeout_sec=30)
            except GdbTimeoutError:
                raise DebugError("GDB did not stop within 30 seconds")
            for r in responses:
                if r.get("message") == "error":
                    raise DebugError(r.get("payload", {}).get("msg", "GDB error"))
                if r.get("message") == "stopped":
                    return self._parse_stop_event(r["payload"])

    def _parse_stop_event(self, payload: dict) -> StopEvent:
        return StopEvent(
            reason=payload.get("reason", "unknown"),
            frame=self._parse_frame(self._require(payload, "frame", "stop event")),
            return_val=payload.get("return-value"),
        )

    def _parse_frame(self, f: dict) -> Frame:
        return Frame(
            level=int(f.get("level", 0)),
            function=f.get("func", "??"),
            file=f.get("file", f.get("fullname", "??")),
            line=int(f.get("line", 0)),
        )

    def _parse_breakpoint(self, bkpt: dict) -> Breakpoint:
        return Breakpoint(
            bp_id=int(self._require(bkpt, "number", "breakpoint record")),
            file=bkpt.get("file", "??"),
            line=int(bkpt.get("line", 0)),
            function=bkpt.get("func", "??"),
            enabled=bkpt.get("enabled", "y") == "y",
        )
