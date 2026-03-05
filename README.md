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

- Python 3.11 or newer
- GDB installed and on `$PATH`
- A compiled binary to debug (unstripped, debug symbols recommended)

## Installation

```bash
git clone https://github.com/yourname/llmdb
cd llmdb
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Or with [uv](https://github.com/astral-sh/uv) (much faster):

```bash
uv venv && uv pip install -e .
```

## Running the server

```bash
llmdb
```

The server speaks MCP over stdio. Connect it from any MCP-capable client
(Claude Desktop, a custom agent, `mcp` CLI, etc.).

### Claude Desktop example

Add this block to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "llmdb": {
      "command": "/path/to/llmdb/.venv/bin/llmdb"
    }
  }
}
```

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
| `backtrace` | Return the full call stack |
| `frame_info` | Return the current frame (file, line, function) |
| `list_locals` | List all local variables in the current frame |
| `list_source_context` | Show source lines around the current position |

## Development

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

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
