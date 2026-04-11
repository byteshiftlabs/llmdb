"""
DebugSession — wraps a single GDB subprocess via pygdbmi.

One session per target executable. Drives GDB via the MI protocol,
blocks on async events, and returns typed domain objects.
"""

import os
import resource
import shutil
import sys
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pygdbmi.gdbcontroller import GdbController

from llmdb.models import Breakpoint, Frame, StopEvent, Variable


class DebugError(Exception):
    """Raised when GDB returns an error record."""


class SandboxError(DebugError):
    """Raised when sandbox policy or availability prevents a session from starting."""


# Characters that GDB/MI uses as command separators.
# Allowing them in user-supplied strings would let an LLM inject
# arbitrary GDB commands into the MI stream.
_MI_FORBIDDEN = frozenset("\n\r\x00")
_REMOTE_TARGET_FORBIDDEN = frozenset(" \t\n\r\x00")

_MAX_RADIUS = 50  # source-context sanity cap

_DEFAULT_GDB_EXECUTABLE = "gdb"
_SYSTEM_SANDBOX_DIRS = ("/usr", "/bin", "/lib", "/lib64", "/sbin", "/etc")
_RESOURCE_WRAPPER = textwrap.dedent(
    """
    import os
    import resource
    import sys

    cpu_seconds = int(sys.argv[1])
    memory_bytes = int(sys.argv[2])
    process_limit = int(sys.argv[3])
    command = sys.argv[4:]

    if cpu_seconds > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    if memory_bytes > 0:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    if process_limit > 0 and hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (process_limit, process_limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    os.execvp(command[0], command)
    """
).strip()


@dataclass(frozen=True)
class SandboxConfig:
    enabled: bool = True
    workspace_root: Optional[Path] = None
    allow_network: bool = False
    cpu_seconds: int = 30
    memory_bytes: int = 1_073_741_824
    process_limit: int = 64
    extra_allowed_roots: tuple[Path, ...] = ()

    def allowed_roots(self, executable: Path) -> tuple[Path, ...]:
        roots: list[Path] = []
        if self.workspace_root is not None:
            roots.append(self.workspace_root.resolve())
        roots.append(executable.parent.resolve())
        roots.extend(root.resolve() for root in self.extra_allowed_roots)

        deduped: list[Path] = []
        for root in roots:
            if root not in deduped:
                deduped.append(root)
        return tuple(deduped)


def _check_mi_safe(value: str, label: str) -> None:
    """Raise ValueError if value contains GDB MI command-separator characters."""
    if any(ch in _MI_FORBIDDEN for ch in value):
        raise ValueError(
            f"{label!r} contains a character that is not allowed in a GDB MI "
            f"command (newlines and null bytes are forbidden)."
        )


def _check_remote_target_safe(value: str, label: str) -> None:
    if any(ch in _REMOTE_TARGET_FORBIDDEN for ch in value):
        raise ValueError(
            f"{label!r} contains whitespace or control characters that are not allowed "
            f"in a remote target specification."
        )


def _is_within_allowed_roots(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve()
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _check_allowed_path(path: Path, label: str, allowed_roots: tuple[Path, ...]) -> None:
    if not _is_within_allowed_roots(path, allowed_roots):
        roots = ", ".join(str(root) for root in allowed_roots)
        raise PermissionError(f"{label} must stay inside allowed roots: {roots}")


def _build_sandbox_command(
    executable: Path,
    config: SandboxConfig,
    allowed_roots: tuple[Path, ...],
    gdb_executable: str,
) -> list[str]:
    gdb_command = [gdb_executable, "--nx", "--quiet", "--interpreter=mi3"]
    wrapped_command = _wrap_with_resource_limits(gdb_command, config)
    if not config.enabled:
        return wrapped_command

    bwrap_path = shutil.which("bwrap")
    if bwrap_path is None:
        raise SandboxError(
            "Sandboxed mode requires bubblewrap ('bwrap') on PATH. "
            "Install bubblewrap or start the session with disable_sandbox=true."
        )

    sandbox_command = [
        bwrap_path,
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--uid",
        "65534",
        "--gid",
        "65534",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/var/tmp",
        "--setenv",
        "HOME",
        "/tmp/llmdb-home",
        "--chdir",
        str(config.workspace_root.resolve() if config.workspace_root else executable.parent.resolve()),
    ]
    if not config.allow_network:
        sandbox_command.append("--unshare-net")

    for system_dir in _SYSTEM_SANDBOX_DIRS:
        if Path(system_dir).exists():
            sandbox_command.extend(["--ro-bind", system_dir, system_dir])
    for root in allowed_roots:
        sandbox_command.extend(["--ro-bind", str(root), str(root)])

    sandbox_command.extend(wrapped_command)
    return sandbox_command


def _wrap_with_resource_limits(command: list[str], config: SandboxConfig) -> list[str]:
    return [
        sys.executable,
        "-c",
        _RESOURCE_WRAPPER,
        str(config.cpu_seconds),
        str(config.memory_bytes),
        str(config.process_limit),
        *command,
    ]


class DebugSession:
    def __init__(
        self,
        executable: str,
        sandbox: Optional[SandboxConfig] = None,
        gdb_executable: str = _DEFAULT_GDB_EXECUTABLE,
    ) -> None:
        p = Path(executable).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Executable not found: {executable}")
        if not os.access(executable, os.X_OK):
            raise PermissionError(f"File is not executable: {executable}")
        self._sandbox = sandbox or SandboxConfig()
        self._allowed_roots = self._sandbox.allowed_roots(p)
        _check_allowed_path(p, "executable", self._allowed_roots)
        self.session_id: str = str(uuid.uuid4())
        self._executable: str = str(p)
        self._gdb_executable = gdb_executable
        self._remote_target: Optional[str] = None
        self._gdb = GdbController(
            command=_build_sandbox_command(p, self._sandbox, self._allowed_roots, self._gdb_executable)
        )
        self._send(f"-file-exec-and-symbols {self._executable}", timeout_sec=30)

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

    def connect_remote_target(self, target: str, transport: str = "remote") -> dict:
        _check_remote_target_safe(target, "target")
        if transport not in {"remote", "extended-remote"}:
            raise ValueError("transport must be 'remote' or 'extended-remote'")
        self._send(f"-target-select {transport} {target}")
        self._remote_target = target
        return {"target": target, "transport": transport, "connected": True}

    def disconnect_remote_target(self) -> dict:
        self._send("-target-disconnect")
        target = self._remote_target
        self._remote_target = None
        return {"target": target, "connected": False}

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
        file_path = Path(file)
        if file_path.is_absolute():
            _check_allowed_path(file_path, "breakpoint file", self._allowed_roots)
        payload = self._send(f"-break-insert {file}:{line}")
        return self._parse_breakpoint(payload["bkpt"])

    def set_function_breakpoint(self, function: str) -> Breakpoint:
        _check_mi_safe(function, "function")
        payload = self._send(f"-break-insert {function}")
        return self._parse_breakpoint(payload["bkpt"])

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
        return Variable(name=name, type=payload.get("type", "unknown"), value=payload["value"])

    def evaluate(self, expression: str) -> str:
        _check_mi_safe(expression, "expression")
        payload = self._send(f"-data-evaluate-expression {expression}")
        return payload["value"]

    def backtrace(self) -> list[Frame]:
        payload = self._send("-stack-list-frames")
        stack = payload.get("stack", [])
        return [self._parse_frame(entry.get("frame", entry)) for entry in stack]

    def frame_info(self) -> Frame:
        payload = self._send("-stack-info-frame")
        return self._parse_frame(payload["frame"])

    def list_locals(self) -> list[Variable]:
        payload = self._send("-stack-list-locals --all-values")
        return [
            Variable(name=v["name"], type=v.get("type", "unknown"), value=v.get("value", "?"))
            for v in payload.get("locals", [])
        ]

    def list_source_context(self, radius: int = 5) -> list[str]:
        radius = min(radius, _MAX_RADIUS)
        frame = self.frame_info()
        path = Path(frame.file)
        if not path.is_file():
            raise FileNotFoundError(f"Source file not found: {frame.file}")
        _check_allowed_path(path, "source file", self._allowed_roots)
        lines = path.read_text().splitlines()
        center = frame.line - 1  # 0-based
        start = max(0, center - radius)
        end = min(len(lines), center + radius + 1)
        return [f"{start + i + 1:4}: {lines[start + i]}" for i in range(end - start)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send(self, command: str, timeout_sec: int = 10) -> dict:
        """Send an MI command; return its payload or raise DebugError."""
        self._gdb.write(command, read_response=False)
        responses = self._gdb.get_gdb_response(timeout_sec=timeout_sec) or []
        return self._extract_payload(responses)

    def _extract_payload(self, responses: list) -> dict:
        for r in responses:
            if r.get("message") == "error":
                raise DebugError(r.get("payload", {}).get("msg", "GDB error"))
            if r.get("message") in {"done", "connected"}:
                return r.get("payload") or {}
        return {}

    def _wait_for_stop(self) -> StopEvent:
        """Poll GDB until a *stopped notify arrives or an error is seen."""
        while True:
            responses = self._gdb.get_gdb_response(timeout_sec=30)
            if responses is None:
                responses = []
            for r in responses:
                if r.get("message") == "error":
                    raise DebugError(r.get("payload", {}).get("msg", "GDB error"))
                if r.get("message") == "stopped":
                    return self._parse_stop_event(r["payload"])

    def _parse_stop_event(self, payload: dict) -> StopEvent:
        return StopEvent(
            reason=payload.get("reason", "unknown"),
            frame=self._parse_frame(payload["frame"]),
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
            bp_id=int(bkpt["number"]),
            file=bkpt.get("file", "??"),
            line=int(bkpt.get("line", 0)),
            function=bkpt.get("func", "??"),
            enabled=bkpt.get("enabled", "y") == "y",
        )
