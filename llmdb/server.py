"""
llmdb MCP server.

Registers GDB debugging as MCP tools. Each tool is a thin dispatch
function: validate session_id, delegate to DebugSession, return a
JSON-serialisable dict.

Entry point: llmdb.server:main  (see pyproject.toml)
"""

import sys
from dataclasses import asdict
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
import mcp.types as types

from llmdb.session import DebugSession, DebugError

# Session registry — keyed by session_id UUID string
_sessions: dict[str, DebugSession] = {}

app = Server("llmdb")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lookup(session_id: str) -> DebugSession:
    if session_id not in _sessions:
        raise KeyError(f"No session '{session_id}'. Call start_session first.")
    return _sessions[session_id]


def _serialise(obj) -> dict:
    return asdict(obj)


# ---------------------------------------------------------------------------
# Session lifecycle (also called directly by tests)
# ---------------------------------------------------------------------------

def _start_session(executable: str) -> str:
    """Launch GDB on executable; return session_id."""
    if not Path(executable).is_file():
        raise FileNotFoundError(f"Executable not found: {executable}")
    session = DebugSession(executable)
    _sessions[session.session_id] = session
    return session.session_id


def _stop_session(session_id: str) -> None:
    session = _lookup(session_id)
    session.quit()
    del _sessions[session_id]


# ---------------------------------------------------------------------------
# Execution tools
# ---------------------------------------------------------------------------

def _run(session_id: str) -> dict:
    return _serialise(_lookup(session_id).run())


def _next(session_id: str) -> dict:
    return _serialise(_lookup(session_id).next())


def _step(session_id: str) -> dict:
    return _serialise(_lookup(session_id).step())


def _continue_execution(session_id: str) -> dict:
    return _serialise(_lookup(session_id).continue_execution())


# ---------------------------------------------------------------------------
# Breakpoint tools
# ---------------------------------------------------------------------------

def _set_breakpoint(session_id: str, file: str, line: int) -> dict:
    return _serialise(_lookup(session_id).set_breakpoint(file, line))


def _set_function_breakpoint(session_id: str, function: str) -> dict:
    return _serialise(_lookup(session_id).set_function_breakpoint(function))


def _remove_breakpoint(session_id: str, bp_id: int) -> None:
    _lookup(session_id).remove_breakpoint(bp_id)


def _list_breakpoints(session_id: str) -> list[dict]:
    return [_serialise(bp) for bp in _lookup(session_id).list_breakpoints()]


# ---------------------------------------------------------------------------
# Inspection tools
# ---------------------------------------------------------------------------

def _read_variable(session_id: str, name: str) -> dict:
    return _serialise(_lookup(session_id).read_variable(name))


def _evaluate(session_id: str, expression: str) -> str:
    return _lookup(session_id).evaluate(expression)


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
                      "properties": {"executable": {"type": "string"}},
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
    Tool(name="read_variable",
         description="Read the value and type of a variable in the current frame.",
         inputSchema={"type": "object",
                      "properties": {
                          "session_id": {"type": "string"},
                          "name": {"type": "string"}},
                      "required": ["session_id", "name"]}),
    Tool(name="evaluate",
         description="Evaluate an arbitrary GDB expression; returns the result as a string.",
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
    "start_session":           lambda a: _start_session(a["executable"]),
    "stop_session":            lambda a: _stop_session(a["session_id"]),
    "run":                     lambda a: _run(a["session_id"]),
    "next":                    lambda a: _next(a["session_id"]),
    "step":                    lambda a: _step(a["session_id"]),
    "continue_execution":      lambda a: _continue_execution(a["session_id"]),
    "set_breakpoint":          lambda a: _set_breakpoint(a["session_id"], a["file"], a["line"]),
    "set_function_breakpoint": lambda a: _set_function_breakpoint(a["session_id"], a["function"]),
    "remove_breakpoint":       lambda a: _remove_breakpoint(a["session_id"], a["bp_id"]),
    "list_breakpoints":        lambda a: _list_breakpoints(a["session_id"]),
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
