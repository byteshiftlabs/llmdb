# llmdb

A GDB/MI MCP server that gives LLMs interactive software debugging capabilities.

## What it does

llmdb runs GDB as a subprocess, drives it via the GDB/MI protocol, and exposes the
results as [Model Context Protocol](https://modelcontextprotocol.io/) tools.
An LLM connected to this server can:

- Launch a compiled program under GDB
- Connect that GDB session to a remote target such as QEMU's GDB stub or `gdbserver`
- Step through code line by line or into function calls
- Set and remove breakpoints by file/line or function name
- Read local variables and evaluate arbitrary expressions
- Inspect the call stack and the current frame
- Inspect monitoring-oriented state such as session status, target metadata, threads, registers, and recent stop history
- View source context around the stopped line

All responses are structured JSON — no GDB screen-scraping required.

## Requirements

- Python 3.10
- GDB installed and on `$PATH`
- Bubblewrap (`bwrap`) installed for the default sandboxed mode on Linux
- A compiled binary to debug (unstripped, debug symbols recommended)
- Tested with C binaries only so far

## Installation

```bash
git clone https://github.com/byteshiftlabs/llmdb
cd llmdb
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e .
```

The editable install path above was validated on Linux with Python 3.10 and GDB
on `$PATH`.

## Security model

GDB runs inside a Bubblewrap user-namespace sandbox by default (Linux):

- Only the target executable, optional `workspace_root`, and allowlisted roots are mounted read-only.
- Networking, privilege escalation, and shell expansion are disabled.
- CPU, memory, and process-count limits are applied before launch.
- Tool access is policy-gated: `inspect` (read-only), `debug` (stepping/breakpoints), `full` (all tools including `evaluate`).

If Bubblewrap is unavailable, `start_session` fails unless `disable_sandbox=true` is set.

## Running the server

```bash
llmdb
```

Runs over stdio. Tested with VS Code; works with any MCP-capable client.

## MCP tools

| Tool | Description |
|------|-------------|
| `start_session` | Launch a program under GDB; returns a session ID |
| `connect_remote_target` | Connect the session to a remote GDB target such as `:1234` |
| `disconnect_remote_target` | Disconnect the session from its remote target |
| `stop_session` | Terminate GDB and clean up the session |
| `run` | Start execution (`-exec-run`) |
| `next` | Step over one source line |
| `step` | Step into a function call |
| `continue_execution` | Resume until the next breakpoint or program exit |
| `set_breakpoint` | Break at `file:line` |
| `set_function_breakpoint` | Break at a named function |
| `remove_breakpoint` | Delete a breakpoint by ID |
| `list_breakpoints` | List all active breakpoints |
| `session_status` | Summarise monitor-friendly session state such as last stop event and current frame |
| `target_info` | Report executable, GDB executable, remote target, and sandbox/network status |
| `stop_event_history` | Return recent stop events for timeline views |
| `list_threads` | Return threads reported by GDB |
| `list_registers` | Return register names and values |
| `read_variable` | Read a variable's value and type |
| `evaluate` | Evaluate any GDB expression |
| `backtrace` | Return the full call stack |
| `frame_info` | Return the current frame (file, line, function) |
| `list_locals` | List all local variables in the current frame |
| `list_source_context` | Show source lines around the current position |

`start_session` also accepts optional `workspace_root`, `tool_policy`, `allow_network`, `disable_sandbox`, `cpu_seconds`, `memory_mb`, and `process_limit` arguments.

When debugging non-host architectures, `start_session` can also take `gdb_executable` to select a matching debugger such as `riscv64-unknown-elf-gdb`.

For remote debugging workflows such as ThunderOS on QEMU, start the session with `allow_network=true`, then call `connect_remote_target` with the published GDB target such as `:1234`.

## Monitoring

`llmdb` ships a terminal monitoring client:

```bash
llmdb-monitor /path/to/program \
	--remote-target :1234 \
	--gdb-executable riscv64-unknown-elf-gdb \
	--function-breakpoint kernel_main \
	--auto-continue \
	--serial-log /tmp/thunderos-serial.log \
	--json-out /tmp/llmdb-monitor.json
```

Connects to a remote GDB target, renders a live terminal dashboard (stop history, threads, registers, locals, source, serial output), and writes a JSON snapshot for the VS Code monitor scaffold.

The repository also includes a VS Code webview scaffold at `clients/vscode-monitor/`.
It currently watches the snapshot file written by `llmdb-monitor --json-out` and renders a richer dashboard inside VS Code.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run the terminal monitor
llmdb-monitor --help
```

All tests mock the GDB subprocess — no actual GDB process is started during
`pytest`. Tests cover every MCP tool and every session method.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full design document.

Three layers:

```
server.py   ← MCP tool registration and dispatch
session.py  ← DebugSession wrapping pygdbmi / GDB
models.py   ← pure dataclasses (Frame, Breakpoint, Variable, StopEvent)
```

## License

MIT (see `LICENSE`)
