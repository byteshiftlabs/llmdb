"""
Domain types for llmdb.

Pure dataclasses — no I/O, no GDB knowledge.
All MCP tools return instances of these types, serialised to dicts.
"""

from dataclasses import dataclass
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
    frame: Optional[Frame]  # None when the reason is an exit — the process has no frame left
    return_val: Optional[str] = None  # set when reason == "exited"
