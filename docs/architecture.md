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
┌─────────────────────▼────────────────────────────────┐
│  server.py  — MCP tool registration and dispatch     │
│  (interface layer)                                   │
├──────────────────────────────────────────────────────┤
│  session.py — DebugSession, GDB lifecycle            │
│  (business logic layer)                              │
├──────────────────────────────────────────────────────┤
│  models.py  — dataclasses: Frame, Breakpoint,        │
│               StopEvent, Variable                    │
│  (domain layer)                                      │
├──────────────────────────────────────────────────────┤
│  pygdbmi    — GDB/MI protocol parser                 │
│  GDB subprocess                                      │
│  (external layer)                                    │
└──────────────────────────────────────────────────────┘
```

Dependency direction: server → session → models. The domain layer (models) has
no imports from any other project layer.

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

### `llmdb/session.py`

`DebugSession` wraps one GDB subprocess via `pygdbmi.GdbController`. Owns the
full lifecycle: spawn → configure → run → inspect → quit. One session per
debugging target. Sessions are keyed by a UUID string and held in a dict in
`server.py`.

Key responsibilities:
- Spawn GDB with the target executable
- Send MI commands and parse responses into domain types
- Block (with timeout) on async `*stopped` events after execution commands
- Raise `DebugError` with a clear message on any GDB error response

### `llmdb/server.py`

Registers MCP tools. Each tool is a thin function that:
1. Looks up the session by `session_id`
2. Delegates to `DebugSession`
3. Returns a JSON-serialisable dict

No logic here beyond input validation and session lookup.

---

## MCP Tool Surface

| Tool | Parameters | Returns |
|------|-----------|---------|
| `start_session` | `executable: str` | `session_id: str` |
| `stop_session` | `session_id` | `null` |
| `run` | `session_id` | `StopEvent` |
| `next` | `session_id` | `StopEvent` |
| `step` | `session_id` | `StopEvent` |
| `continue_execution` | `session_id` | `StopEvent` |
| `set_breakpoint` | `session_id, file, line` | `Breakpoint` |
| `set_function_breakpoint` | `session_id, function` | `Breakpoint` |
| `remove_breakpoint` | `session_id, bp_id` | `null` |
| `list_breakpoints` | `session_id` | `list[Breakpoint]` |
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

- `KeyError` — raised by `server.py` when `session_id` is not in the sessions dict
- `DebugError` — raised by `session.py` on any GDB error record (`^error`), and also
  wraps a GDB timeout (no response within the configured timeout) as its message

Both propagate as MCP tool errors, which the MCP framework returns to the
LLM as structured error content.

---

## Configuration

No config file. The only external parameter is the executable path passed to
`start_session`. GDB binary is resolved from `$PATH` (must be `gdb`).

---

## Security

### Implemented controls

**GDB MI injection prevention** (`session.py` — `_check_mi_safe`)

All user-supplied strings (`file`, `function`, `name`, `expression`) are passed
directly into GDB/MI command strings as f-string arguments. The GDB/MI protocol
uses newline as a command separator: a newline embedded in a user string would
let an LLM (or a compromised caller) inject an arbitrary second GDB command.

`_check_mi_safe()` raises `ValueError` before any `write()` call if the string
contains `\n`, `\r`, or `\x00`. This is the primary injection-prevention
boundary for the server's attack surface.

**Executable validation** (`session.py` — `DebugSession.__init__`)

- `Path.is_file()` — rejects directories, symlinks to non-files, and missing paths.
- `os.access(executable, os.X_OK)` — rejects files that are not executable by
  the current process, catching common mistakes (e.g. passing a source file
  instead of a compiled binary) and reducing the risk of accidentally running
  non-program files.

**Session isolation**

Sessions are identified by UUID4. IDs are not sequential, not guessable, and
not reused after `stop_session` removes them from the registry.

**No privilege escalation**

The GDB subprocess inherits the server's user and group. No `setuid`, `sudo`,
or capability-raising code is present.

**No shell expansion**

Executable paths and breakpoint targets are passed directly to GDB/MI commands,
never through `sh -c` or `subprocess.run(shell=True)`.

---

## Design Decisions

### 1. Three-layer architecture instead of a monolith

A single-file design would have been simpler to write but would couple GDB
protocol details, domain objects, and MCP wiring in one place. Tests would
require starting a real GDB process. The three-layer split (server → session →
models) makes each layer independently testable: models have no dependencies,
session mocks GDB, server mocks the session.

### 2. pygdbmi as the GDB/MI transport

pygdbmi turns raw GDB/MI text into Python dicts. The alternative — parsing GDB's
text output directly — would require writing and maintaining a tokeniser for a
protocol that varies across GDB versions. pygdbmi is the established choice for
this; it is the same library used by gdbgui and several DAP implementations.

### 3. `get_gdb_response` after every `write`

`session._send()` calls `write()` then reads the response with a separate
`get_gdb_response()` call rather than using the return value of `write()`.
This matches the way GDB/MI actually works: `write()` sends a command but the
timing of the response is asynchronous. Keeping the pattern consistent also
makes mocking in tests straightforward — every `_send` call makes exactly two
mock interactions.

### 4. Blocking `_wait_for_stop` with a 30-second timeout

After execution commands (`run`, `next`, `step`, `continue`), the server blocks
until GDB emits a `*stopped` notification. A 30-second hard timeout prevents
the server from hanging forever on a program that loops. The trade-off: programs
that take longer than 30 seconds to hit a breakpoint will time out. This is an
acceptable limitation for an interactive debugging tool driven by an LLM; a
human-paced debugging session will not run for 30 seconds between GDB responses.

### 5. stdio transport, not HTTP/SSE

MCP supports both stdio and HTTP+SSE transports. stdio was chosen because:
- Simpler deployment: the client spawns the server as a child process; no port
  binding, no firewall rules, no TLS setup.
- Natural single-tenant model: one Claude Desktop client ↔ one llmdb process.
- HTTP/SSE can be added later without changing the tool layer.

### 6. All tool dispatch functions are module-level, not class methods

`server.py` defines `_start_session`, `_next`, `_evaluate`, etc. as plain
functions rather than methods on a class. This means tests can call them
directly without constructing a server object or using the MCP framework. The
`_sessions` registry is a module-level dict, which is reset by the
`clear_sessions` autouse fixture in `test_server.py`.

---

## Known Challenges

### 1. LLM-controlled executable path

`start_session` accepts an absolute path to an executable. An LLM prompted
adversarially could pass `/bin/rm`, `/usr/bin/python3`, or any other executable
on the filesystem. Once started, `run` would execute it under GDB.

**Current mitigation**: the server requires the file to exist and be executable,
but does not restrict which executables are allowed. Callers who need stricter
isolation should run llmdb inside a container or under a seccomp profile.

**Not yet implemented**: an optional allowlist of permitted executable prefixes
(e.g. only paths under `/home/user/projects/`).

### 2. GDB `shell` command via `evaluate`

The `evaluate` tool maps to GDB's `-data-evaluate-expression`. An LLM could
call `evaluate("(void)system(\"rm -rf /\")")` — a C expression that GDB
evaluates by calling `system()` in the target process. This is not an llmdb
bug; it is a fundamental property of a debugger that can call functions inside
a live process.

**Current mitigation**: newlines are blocked (preventing multi-command injection);
the expression is evaluated in the context of the target process, not the host
shell. The target process already runs with the same privileges as the server,
so this does not introduce new privilege escalation.

**Not yet implemented**: expression allowlisting or a read-only mode that
disables `evaluate` entirely.

### 3. `_wait_for_stop` timeout is not configurable

The 30-second timeout in `_wait_for_stop` is a module-level constant. There is
no way for callers to pass a custom timeout. Long-running programs (benchmarks,
servers) will always time out before they can be debugged interactively.

### 4. Session leak on server crash

If the MCP server process is killed while a session is active, the GDB
subprocess is orphaned. The GDB process will eventually notice its stdin is
closed and exit, but until then it holds file descriptors and process resources.
No cleanup mechanism (e.g. a `SIGTERM` handler) is currently implemented.

### 5. No multi-thread support

`_wait_for_stop` polls `get_gdb_response` in a tight loop on the calling thread.
If the server were extended to handle multiple concurrent MCP tool calls over
one session, callers would block each other. The current design is strictly
single-threaded per session, which is correct for the intended use case (one LLM
driving one debugging session step by step) but would need a redesign for
concurrent use.

