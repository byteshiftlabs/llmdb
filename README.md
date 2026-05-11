# llmdb

A GDB/MI MCP server that gives LLMs interactive software debugging capabilities.

## What it does

llmdb runs GDB as a subprocess, drives it via the GDB/MI protocol, and exposes the
results as [Model Context Protocol](https://modelcontextprotocol.io/) tools.
An LLM connected to this server can:

- Launch a compiled program under GDB
- Step through code line by line or into function calls
- Set and remove breakpoints by file/line or function name
- Read local variables and evaluate arbitrary expressions
- Inspect the call stack and the current frame
- View source context around the stopped line

All responses are structured JSON — no GDB screen-scraping required.

## Requirements

- Python 3.10
- GDB installed and on `$PATH`
- Bubblewrap (`bwrap`) installed for the default sandboxed mode on Linux
- A compiled binary to debug (unstripped, debug symbols recommended)

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

`llmdb` now supports a sandbox-first session model for Linux:

- GDB is launched inside a Bubblewrap user-namespace sandbox by default.
- Only the requested executable directory, the optional `workspace_root`, and explicit allowlisted roots are mounted read-only.
- Networking is disabled by default.
- The sandbox runs as an unprivileged uid/gid and gets a temporary home directory.
- CPU, address-space, and process-count limits are applied before GDB starts.
- Tool access is policy-gated:
	- `inspect`: read-only inspection tools only
	- `debug`: stepping and breakpoint management, but no arbitrary `evaluate`
	- `full`: all tools, including `evaluate`

If Bubblewrap is not installed, `start_session` fails closed unless the caller explicitly sets `disable_sandbox=true`.

## Running the server

```bash
llmdb
```

The server speaks MCP over stdio. It has been tested with VSCode.
You can integrate it as an MCP server in any capable client by configuring the command to run `llmdb`.

## MCP tools

| Tool | Description |
|------|-------------|
| `start_session` | Launch a program under GDB; returns a session ID |
| `stop_session` | Terminate GDB and clean up the session |
| `run` | Start execution (`-exec-run`) |
| `next` | Step over one source line |
| `step` | Step into a function call |
| `continue` | Resume until the next breakpoint or program exit |
| `set_breakpoint` | Break at `file:line` |
| `set_function_breakpoint` | Break at a named function |
| `remove_breakpoint` | Delete a breakpoint by ID |
| `list_breakpoints` | List all active breakpoints |
| `read_variable` | Read a variable's value and type |
| `evaluate` | Evaluate any GDB expression |

`start_session` also accepts optional `workspace_root`, `tool_policy`, `allow_network`, `disable_sandbox`, `cpu_seconds`, `memory_mb`, and `process_limit` arguments.
| `backtrace` | Return the full call stack |
| `frame_info` | Return the current frame (file, line, function) |
| `list_locals` | List all local variables in the current frame |
| `list_source_context` | Show source lines around the current position |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest
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

MIT
