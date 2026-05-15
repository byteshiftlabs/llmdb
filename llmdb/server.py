"""
llmdb MCP server.

Registers GDB debugging as MCP tools. Each tool is a thin dispatch
function: validate session_id, delegate to DebugSession, return a
JSON-serialisable dict.

Entry point: llmdb.server:main  (see pyproject.toml)
"""

import os
import sys
from dataclasses import asdict
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
import mcp.types as types

from llmdb.session import DebugSession, DebugError, SandboxConfig

# Session registry — keyed by session_id UUID string
_sessions: dict[str, DebugSession] = {}
_session_policies: dict[str, str] = {}

_POLICY_ORDER = {"inspect": 0, "debug": 1, "full": 2}
_TOOL_POLICIES = {
    "connect_remote_target": "debug",
    "disconnect_remote_target": "debug",
    "run": "debug",
    "next": "debug",
    "step": "debug",
    "continue_execution": "debug",
    "set_breakpoint": "debug",
    "set_function_breakpoint": "debug",
    "remove_breakpoint": "debug",
    "evaluate": "full",
}

app = Server("llmdb")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lookup(session_id: str) -> DebugSession:
    if session_id not in _sessions:
        raise KeyError(f"No session '{session_id}'. Call start_session first.")
    return _sessions[session_id]


def _check_tool_policy(session_id: str, tool_name: str) -> DebugSession:
    session = _lookup(session_id)
    current = _session_policies.get(session_id, "full")
    required = _TOOL_POLICIES.get(tool_name, "inspect")
    if _POLICY_ORDER[current] < _POLICY_ORDER[required]:
        raise PermissionError(
            f"Tool '{tool_name}' requires tool_policy='{required}', current policy is '{current}'."
        )
    return session


def _serialise(obj) -> dict:
    return asdict(obj)


# ---------------------------------------------------------------------------
# Session lifecycle (also called directly by tests)
# ---------------------------------------------------------------------------

def _start_session(
    executable: str,
    workspace_root: str | None = None,
    tool_policy: str = "debug",
    allow_network: bool = False,
    disable_sandbox: bool = False,
    cpu_seconds: int = 30,
    memory_mb: int = 1024,
    process_limit: int = 64,
    gdb_executable: str = "gdb",
) -> str:
    """Launch GDB on executable; return session_id."""
    if not Path(executable).is_file():
        raise FileNotFoundError(f"Executable not found: {executable}")
    if tool_policy not in _POLICY_ORDER:
        raise ValueError(f"Unsupported tool_policy: {tool_policy}")

    extra_roots = tuple(
        Path(root).resolve() for root in os.environ.get("LLMDB_ALLOWED_ROOTS", "").split(os.pathsep) if root
    )
    sandbox = SandboxConfig(
        enabled=not disable_sandbox,
        workspace_root=Path(workspace_root).resolve() if workspace_root else None,
        allow_network=allow_network,
        cpu_seconds=cpu_seconds,
        memory_bytes=memory_mb * 1024 * 1024,
        process_limit=process_limit,
        extra_allowed_roots=extra_roots,
    )
    session = DebugSession(executable, sandbox=sandbox, gdb_executable=gdb_executable)
    _sessions[session.session_id] = session
    _session_policies[session.session_id] = tool_policy
    return session.session_id


def _stop_session(session_id: str) -> None:
    session = _lookup(session_id)
    session.quit()
    del _sessions[session_id]
    _session_policies.pop(session_id, None)


# ---------------------------------------------------------------------------
# Execution tools
# ---------------------------------------------------------------------------

def _run(session_id: str) -> dict:
    return _serialise(_check_tool_policy(session_id, "run").run())


def _connect_remote_target(session_id: str, target: str, transport: str = "remote") -> dict:
    return _check_tool_policy(session_id, "connect_remote_target").connect_remote_target(target, transport)


def _disconnect_remote_target(session_id: str) -> dict:
    return _check_tool_policy(session_id, "disconnect_remote_target").disconnect_remote_target()


def _next(session_id: str) -> dict:
    return _serialise(_check_tool_policy(session_id, "next").next())


def _step(session_id: str) -> dict:
    return _serialise(_check_tool_policy(session_id, "step").step())


def _continue_execution(session_id: str) -> dict:
    return _serialise(_check_tool_policy(session_id, "continue_execution").continue_execution())


# ---------------------------------------------------------------------------
# Breakpoint tools
# ---------------------------------------------------------------------------

def _set_breakpoint(session_id: str, file: str, line: int) -> dict:
    return _serialise(_check_tool_policy(session_id, "set_breakpoint").set_breakpoint(file, line))


def _set_function_breakpoint(session_id: str, function: str) -> dict:
    return _serialise(_check_tool_policy(session_id, "set_function_breakpoint").set_function_breakpoint(function))


def _remove_breakpoint(session_id: str, bp_id: int) -> None:
    _check_tool_policy(session_id, "remove_breakpoint").remove_breakpoint(bp_id)


def _list_breakpoints(session_id: str) -> list[dict]:
    return [_serialise(bp) for bp in _lookup(session_id).list_breakpoints()]


def _session_status(session_id: str) -> dict:
    return _serialise(_lookup(session_id).session_status())


def _target_info(session_id: str) -> dict:
    return _serialise(_lookup(session_id).target_info())


def _stop_event_history(session_id: str, limit: int = 20) -> list[dict]:
    return [_serialise(record) for record in _lookup(session_id).stop_event_history(limit)]


def _list_threads(session_id: str) -> list[dict]:
    return [_serialise(thread) for thread in _lookup(session_id).list_threads()]


def _list_registers(session_id: str) -> list[dict]:
    return [_serialise(register) for register in _lookup(session_id).list_registers()]


# ---------------------------------------------------------------------------
# Inspection tools
# ---------------------------------------------------------------------------

def _read_variable(session_id: str, name: str) -> dict:
    return _serialise(_lookup(session_id).read_variable(name))


def _evaluate(session_id: str, expression: str) -> str:
    return _check_tool_policy(session_id, "evaluate").evaluate(expression)


def _backtrace(session_id: str) -> list[dict]:
    return [_serialise(f) for f in _lookup(session_id).backtrace()]


def _frame_info(session_id: str) -> dict:
    return _serialise(_lookup(session_id).frame_info())


def _list_locals(session_id: str) -> list[dict]:
    return [_serialise(v) for v in _lookup(session_id).list_locals()]


def _list_source_context(session_id: str, radius: int = 5) -> list[str]:
    return _lookup(session_id).list_source_context(radius)


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------

_TOOLS = [
    Tool(name="start_session",
         description="Launch GDB on an executable. Returns a session_id.",
         inputSchema={"type": "object",
                      "properties": {
                          "executable": {"type": "string"},
                          "workspace_root": {"type": "string"},
                          "tool_policy": {"type": "string", "enum": ["inspect", "debug", "full"], "default": "debug"},
                          "allow_network": {"type": "boolean", "default": False},
                          "disable_sandbox": {"type": "boolean", "default": False},
                          "cpu_seconds": {"type": "integer", "default": 30},
                          "memory_mb": {"type": "integer", "default": 1024},
                          "process_limit": {"type": "integer", "default": 64},
                          "gdb_executable": {"type": "string", "default": "gdb"}},
                      "required": ["executable"]}),
    Tool(name="stop_session",
         description="Quit GDB and remove the session.",
         inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
    Tool(name="run",
         description="Run the program to the first breakpoint or exit.",
         inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
        Tool(name="connect_remote_target",
            description="Connect the current GDB session to a remote target such as gdbserver or QEMU's debug stub.",
            inputSchema={"type": "object",
                      "properties": {
                         "session_id": {"type": "string"},
                         "target": {"type": "string"},
                         "transport": {"type": "string", "enum": ["remote", "extended-remote"], "default": "remote"}},
                      "required": ["session_id", "target"]}),
        Tool(name="disconnect_remote_target",
            description="Disconnect the current GDB session from its remote target.",
            inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
    Tool(name="next",
         description="Step over one source line.",
         inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
    Tool(name="step",
         description="Step into one source line (follows function calls).",
         inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
    Tool(name="continue_execution",
         description="Continue execution to the next breakpoint or exit.",
         inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
    Tool(name="set_breakpoint",
         description="Set a breakpoint at file:line.",
         inputSchema={"type": "object",
                      "properties": {
                          "session_id": {"type": "string"},
                          "file": {"type": "string"},
                          "line": {"type": "integer"}},
                      "required": ["session_id", "file", "line"]}),
    Tool(name="set_function_breakpoint",
         description="Set a breakpoint at the entry of a named function.",
         inputSchema={"type": "object",
                      "properties": {
                          "session_id": {"type": "string"},
                          "function": {"type": "string"}},
                      "required": ["session_id", "function"]}),
    Tool(name="remove_breakpoint",
         description="Remove a breakpoint by its numeric id.",
         inputSchema={"type": "object",
                      "properties": {
                          "session_id": {"type": "string"},
                          "bp_id": {"type": "integer"}},
                      "required": ["session_id", "bp_id"]}),
    Tool(name="list_breakpoints",
         description="List all current breakpoints.",
         inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
        Tool(name="session_status",
            description="Return high-level session state for monitoring views.",
            inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
        Tool(name="target_info",
            description="Return target connection details such as executable, remote target, and sandbox mode.",
            inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
        Tool(name="stop_event_history",
            description="Return recent stop events for timeline-style monitoring.",
            inputSchema={"type": "object",
                      "properties": {
                         "session_id": {"type": "string"},
                         "limit": {"type": "integer", "default": 20}},
                      "required": ["session_id"]}),
        Tool(name="list_threads",
            description="List threads reported by GDB for the current target.",
            inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
        Tool(name="list_registers",
            description="List register names and values for the current target.",
            inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
    Tool(name="read_variable",
         description="Read the value and type of a variable in the current frame.",
         inputSchema={"type": "object",
                      "properties": {
                          "session_id": {"type": "string"},
                          "name": {"type": "string"}},
                      "required": ["session_id", "name"]}),
    Tool(name="evaluate",
            description="Evaluate an arbitrary GDB expression; available only with tool_policy='full'.",
         inputSchema={"type": "object",
                      "properties": {
                          "session_id": {"type": "string"},
                          "expression": {"type": "string"}},
                      "required": ["session_id", "expression"]}),
    Tool(name="backtrace",
         description="Return the full call stack.",
         inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
    Tool(name="frame_info",
         description="Return the current frame (file, line, function).",
         inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
    Tool(name="list_locals",
         description="List all local variables in the current frame.",
         inputSchema={"type": "object",
                      "properties": {"session_id": {"type": "string"}},
                      "required": ["session_id"]}),
    Tool(name="list_source_context",
         description="Return source lines surrounding the current execution point.",
         inputSchema={"type": "object",
                      "properties": {
                          "session_id": {"type": "string"},
                          "radius": {"type": "integer", "default": 5}},
                      "required": ["session_id"]}),
]

_DISPATCH = {
    "start_session":           lambda a: _start_session(
                                   a["executable"],
                                   a.get("workspace_root"),
                                   a.get("tool_policy", "debug"),
                                   a.get("allow_network", False),
                                   a.get("disable_sandbox", False),
                                   a.get("cpu_seconds", 30),
                                   a.get("memory_mb", 1024),
                                   a.get("process_limit", 64),
                                   a.get("gdb_executable", "gdb"),
                               ),
    "connect_remote_target":   lambda a: _connect_remote_target(
                                   a["session_id"], a["target"], a.get("transport", "remote")
                               ),
    "disconnect_remote_target": lambda a: _disconnect_remote_target(a["session_id"]),
    "stop_session":            lambda a: _stop_session(a["session_id"]),
    "run":                     lambda a: _run(a["session_id"]),
    "next":                    lambda a: _next(a["session_id"]),
    "step":                    lambda a: _step(a["session_id"]),
    "continue_execution":      lambda a: _continue_execution(a["session_id"]),
    "set_breakpoint":          lambda a: _set_breakpoint(a["session_id"], a["file"], a["line"]),
    "set_function_breakpoint": lambda a: _set_function_breakpoint(a["session_id"], a["function"]),
    "remove_breakpoint":       lambda a: _remove_breakpoint(a["session_id"], a["bp_id"]),
    "list_breakpoints":        lambda a: _list_breakpoints(a["session_id"]),
    "session_status":          lambda a: _session_status(a["session_id"]),
    "target_info":             lambda a: _target_info(a["session_id"]),
    "stop_event_history":      lambda a: _stop_event_history(a["session_id"], a.get("limit", 20)),
    "list_threads":            lambda a: _list_threads(a["session_id"]),
    "list_registers":          lambda a: _list_registers(a["session_id"]),
    "read_variable":           lambda a: _read_variable(a["session_id"], a["name"]),
    "evaluate":                lambda a: _evaluate(a["session_id"], a["expression"]),
    "backtrace":               lambda a: _backtrace(a["session_id"]),
    "frame_info":              lambda a: _frame_info(a["session_id"]),
    "list_locals":             lambda a: _list_locals(a["session_id"]),
    "list_source_context":     lambda a: _list_source_context(
                                   a["session_id"], a.get("radius", 5)),
}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return _TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name not in _DISPATCH:
        raise ValueError(f"Unknown tool: {name}")
    result = _DISPATCH[name](arguments)
    import json
    return [TextContent(type="text", text=json.dumps(result, default=str))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import asyncio
    asyncio.run(_serve())


async def _serve() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    main()
