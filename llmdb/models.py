"""
Domain types for llmdb.

Pure dataclasses — no I/O, no GDB knowledge.
All MCP tools return instances of these types, serialised to dicts.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Frame:
    """One entry in the call stack."""
    level: int
    function: str
    file: str
    line: int


@dataclass
class Breakpoint:
    """A breakpoint registered with GDB."""
    bp_id: int
    file: str
    line: int
    function: str
    enabled: bool


@dataclass
class Variable:
    """A named value from the current frame."""
    name: str
    type: str
    value: str


@dataclass
class StopEvent:
    """Describes why and where GDB stopped."""
    reason: str          # "breakpoint-hit", "end-stepping-range", "exited", "signal-received"
    frame: Frame
    return_val: Optional[str] = None  # set when reason == "exited"


@dataclass
class StopRecord:
    """One recorded stop event in session history."""
    sequence: int
    event: StopEvent


@dataclass
class ThreadInfo:
    """A thread reported by GDB/MI."""
    thread_id: str
    target_id: str
    state: str
    current: bool
    name: Optional[str] = None
    core: Optional[str] = None
    frame: Optional[Frame] = None


@dataclass
class RegisterValue:
    """A register value reported by GDB/MI."""
    number: int
    name: str
    value: str


@dataclass
class TargetInfo:
    """Static and connection-oriented metadata about the debug target."""
    executable: str
    gdb_executable: str
    remote_target: Optional[str]
    remote_transport: Optional[str]
    connected: bool
    sandbox_enabled: bool
    network_allowed: bool
    workspace_root: Optional[str] = None


@dataclass
class SessionStatus:
    """High-level state for monitoring dashboards and clients."""
    session_id: str
    state: str
    current_frame: Optional[Frame]
    last_stop_event: Optional[StopRecord]
    stop_event_count: int
    breakpoint_count: int
    thread_count: Optional[int] = None


@dataclass
class MonitorSnapshot:
    """A combined monitoring view used by higher-level clients."""
    status: SessionStatus
    target: TargetInfo
    breakpoints: list[Breakpoint] = field(default_factory=list)
    threads: list[ThreadInfo] = field(default_factory=list)
    registers: list[RegisterValue] = field(default_factory=list)
    locals: list[Variable] = field(default_factory=list)
    source_context: list[str] = field(default_factory=list)
    stop_event_history: list[StopRecord] = field(default_factory=list)
    serial_output: list[str] = field(default_factory=list)
