# llmdb — Architecture

## Purpose

llmdb is a GDB/MI MCP server. It exposes interactive software debugging capabilities
to LLMs as MCP tools, allowing a model to launch a program under GDB, control execution,
inspect variables, read stack frames, and set breakpoints — all through structured
JSON-RPC calls, with no screen-scraping of GDB output.

The name is a pun on `lldb` (LLVM debugger): one letter changes "LLVM" to "LLM".

---

## Scope

Software debugging only. The server starts a GDB subprocess, drives it via the
GDB/MI protocol, and returns structured results. No hardware, FPGA, or `/dev/mem`
access is in scope.

---

## Layers

```
┌──────────────────────────────────────────────────────┐
│              LLM (Claude, GPT, etc.)                 │
└─────────────────────┬────────────────────────────────┘
                      │  MCP JSON-RPC (stdio)
┌──────────────────────────────────────────────────────┐
│  server.py  — MCP tool registration and dispatch     │
│  (interface layer)                                   │
├──────────────────────────────────────────────────────┤
│  monitor.py — terminal dashboard and snapshot export │
│  (client layer)                                      │
├──────────────────────────────────────────────────────┤
│  session.py — DebugSession, GDB lifecycle            │
│  (business logic layer)                              │
├──────────────────────────────────────────────────────┤
│  models.py  — dataclasses: Frame, Breakpoint,        │
│               StopEvent, SessionStatus, Monitor...   │
│  (domain layer)                                      │
├──────────────────────────────────────────────────────┤
│  pygdbmi    — GDB/MI protocol parser                 │
│  GDB subprocess                                      │
│  (external layer)                                    │
└──────────────────────────────────────────────────────┘
```

Dependency direction: server → session → models, and monitor → session → models.
The domain layer (models) has no imports from any other project layer.

---

## Module Responsibilities

### `llmdb/models.py`

Pure dataclasses. No I/O, no GDB knowledge. Defines the types used as return
values by all MCP tools:

- `Frame` — file, line, function, level
- `Breakpoint` — id, file, line, function, enabled
- `Variable` — name, type, value
- `StopEvent` — reason (breakpoint-hit / end-stepping / signal / exited),
  frame, return value
- `SessionStatus`, `TargetInfo`, `ThreadInfo`, `RegisterValue` — monitor-facing state
- `StopRecord`, `MonitorSnapshot` — timeline and combined dashboard payloads

### `llmdb/session.py`

`DebugSession` wraps one GDB subprocess via `pygdbmi.GdbController`. Owns the
full lifecycle: spawn → configure → run → inspect → quit. One session per
debugging target. Sessions are keyed by a UUID string and held in a dict in
`server.py`.

Key responsibilities:
- Spawn GDB with the target executable
- Send MI commands and parse responses into domain types
- Block (with timeout) on async `*stopped` events after execution commands
- Track monitor-friendly state such as recent stop events and current frame
- Expose snapshot-oriented inspection helpers for threads, registers, and target metadata
- Raise `DebugError` with a clear message on any GDB error response

### `llmdb/server.py`

Registers MCP tools. Each tool is a thin function that:
1. Looks up the session by `session_id`
2. Delegates to `DebugSession`
3. Returns a JSON-serialisable dict

No logic here beyond input validation and session lookup.

### `llmdb/monitor.py`

Provides a lightweight terminal dashboard and snapshot export path for human-facing
monitoring workflows.

Key responsibilities:
- Build a combined `MonitorSnapshot` from a live `DebugSession`
- Tail a QEMU serial log file into that snapshot
- Render a terminal dashboard without depending on a heavyweight UI framework
- Write a JSON snapshot file that a richer frontend can watch

---

## MCP Tool Surface

| Tool | Parameters | Returns |
|------|-----------|---------|
| `start_session` | `executable, workspace_root=None, tool_policy='debug', allow_network=False, disable_sandbox=False, cpu_seconds=30, memory_mb=1024, process_limit=64, gdb_executable='gdb'` | `session_id: str` |
| `connect_remote_target` | `session_id, target, transport='remote'` | `{target, transport, connected}` |
| `disconnect_remote_target` | `session_id` | `{target, connected}` |
| `stop_session` | `session_id` | `null` |
| `run` | `session_id` | `StopEvent` |
| `next` | `session_id` | `StopEvent` |
| `step` | `session_id` | `StopEvent` |
| `continue_execution` | `session_id` | `StopEvent` |
| `set_breakpoint` | `session_id, file, line` | `Breakpoint` |
| `set_function_breakpoint` | `session_id, function` | `Breakpoint` |
| `remove_breakpoint` | `session_id, bp_id` | `null` |
| `list_breakpoints` | `session_id` | `list[Breakpoint]` |
| `session_status` | `session_id` | `SessionStatus` |
| `target_info` | `session_id` | `TargetInfo` |
| `stop_event_history` | `session_id, limit=20` | `list[StopRecord]` |
| `list_threads` | `session_id` | `list[ThreadInfo]` |
| `list_registers` | `session_id` | `list[RegisterValue]` |
| `read_variable` | `session_id, name` | `Variable` |
| `evaluate` | `session_id, expression` | `str` |
| `backtrace` | `session_id` | `list[Frame]` |
| `frame_info` | `session_id` | `Frame` |
| `list_locals` | `session_id` | `list[Variable]` |
| `list_source_context` | `session_id, radius=5` | `list[str]` |

`list_source_context` returns the lines of source code surrounding the current
execution point. This is the most important tool for LLM reasoning: without it
the model must infer what code looks like from variable names and frame info
alone.

---

## Data Flow: a single `next` call

```
LLM calls next(session_id)
  → server.py: looks up DebugSession by session_id
  → session.py: sends -exec-next to GDB subprocess via pygdbmi
  ← GDB responds *running (acknowledged)
  → session.py: polls pygdbmi until *stopped message arrives
  → session.py: parses *stopped payload into StopEvent dataclass
  ← server.py: serialises StopEvent to dict
← LLM receives: { reason, frame: { file, line, function }, return_val }
```

---

## Error Handling

- `KeyError` — raised by `server.py` when `session_id` is not in the session registry
- `PermissionError` — raised when tool policy blocks an operation or an accessed path is outside allowlisted roots
- `FileNotFoundError` — raised when executable/source files are missing
- `ValueError` — raised for invalid arguments (for example unsupported `tool_policy`, invalid remote target transport, unsafe MI input)
- `SandboxError` — raised when sandboxed launch is requested but Bubblewrap is unavailable
- `DebugError` — raised by `session.py` on GDB error responses (`^error`)

All of these propagate as MCP tool errors, which the MCP framework returns to the
LLM as structured error content.

---

## Configuration

No config file is required. Runtime behavior is controlled through
`start_session` arguments:

- launch target and debugger: `executable`, `gdb_executable`
- sandbox and resource controls: `disable_sandbox`, `allow_network`,
  `cpu_seconds`, `memory_mb`, `process_limit`
- path and tool boundaries: `workspace_root`, `tool_policy`

Environment variable `LLMDB_ALLOWED_ROOTS` adds extra allowlisted source/executable
roots (path-separated).

The terminal monitor client accepts matching sandbox/debugger options plus
monitor-specific output controls (for example `--json-out`).

---

## Security

### Implemented controls

**GDB MI injection prevention** (`session.py` — `_check_mi_safe`)

GDB/MI uses newline as a command separator; an embedded newline in a user-supplied string would inject a second command. `_check_mi_safe()` raises `ValueError` before any `write()` call if a string contains `\n`, `\r`, or `\x00`.

**Executable validation** (`session.py` — `DebugSession.__init__`)

- `Path.is_file()` — rejects directories, symlinks to non-files, and missing paths.
- `os.access(executable, os.X_OK)` — rejects non-executable files (e.g. source files passed by mistake).

**Session isolation**

Session IDs are UUID4 — non-sequential, non-guessable, and not reused after `stop_session`.

**No privilege escalation** — GDB inherits the server's user and group. No `setuid`, `sudo`, or capability-raising code is present.

**No shell expansion** — paths and breakpoint targets go directly to GDB/MI commands, never through `sh -c` or `subprocess.run(shell=True)`.

---

## Design Decisions

### 1. Three-layer architecture instead of a monolith

The split (server → session → models) makes each layer independently testable: models have no dependencies, session mocks GDB, server mocks the session.

### 2. pygdbmi as the GDB/MI transport

pygdbmi parses GDB/MI text into Python dicts, avoiding a hand-written tokeniser for a protocol that varies across GDB versions. It's the same library used by gdbgui and several DAP implementations.

### 3. `get_gdb_response` after every `write`

GDB/MI responses are asynchronous: `write()` sends a command, `get_gdb_response()` reads the reply. Keeping this two-call pattern consistent makes every `_send` exactly two mock interactions in tests.

### 4. Blocking `_wait_for_stop` with a 30-second polling interval

Execution commands block until GDB emits `*stopped`, polling in 30-second slices with no overall timeout. Long-running commands remain pending until the target stops or GDB errors.

### 5. stdio transport, not HTTP/SSE

stdio simplifies deployment (the client spawns the server; no ports, no TLS) and maps naturally to a single-tenant model. HTTP/SSE can be added later without changing the tool layer.

### 6. All tool dispatch functions are module-level, not class methods

Tool handlers are plain functions, not class methods — tests call them directly without constructing a server object. The `_sessions` dict is reset by the `clear_sessions` autouse fixture in `test_server.py`.

---

## Known Challenges

### 1. LLM-controlled executable path

`start_session` accepts any executable on the filesystem. The file must exist and be executable, but no path allowlist is enforced yet. For stricter control, run llmdb inside a container or under a seccomp profile.

### 2. GDB `shell` command via `evaluate`

`evaluate` passes expressions to GDB's `-data-evaluate-expression`, which can invoke target-process functions such as `system()`. Newlines are blocked to prevent MI injection; execution is isolated to the target process, not the host shell. Expression allowlisting is not yet implemented.

### 3. `_wait_for_stop` has no overall timeout control

Execution commands block indefinitely until the target stops. No caller-configurable overall timeout exists.

### 4. Session leak on server crash

If the server process is killed, active GDB subprocesses are orphaned until they detect closed stdin. No `SIGTERM` handler is implemented.

### 5. No multi-thread support

Single-threaded per session by design. Concurrent tool calls on the same session would serialize — correct for one-LLM-one-session use, but would need a redesign for parallel workloads.

