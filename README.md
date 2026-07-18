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

- Python 3.10.x
- GDB installed and on `$PATH`
- A compiled binary to debug (unstripped, debug symbols recommended)

## Quick start

```bash
git clone https://github.com/byteshiftlabs/llmdb
cd llmdb
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
llmdb
```

The editable install path above was validated on Linux with Python 3.10 and GDB
on `$PATH`.

## Example client configuration

Many MCP clients can launch the server as a local process. A typical VS Code or Claude Desktop-style configuration looks like this:

```json
{
  "mcpServers": {
    "llmdb": {
      "command": "/path/to/your/venv/bin/llmdb"
    }
  }
}
```

See [docs/quickstart.md](docs/quickstart.md) for a fuller walkthrough and troubleshooting tips.

## MCP tools

| Tool | Description |
|------|-------------|
| `start_session` | Launch a program under GDB; returns a session ID |
| `stop_session` | Terminate GDB and clean up the session |
| `run` | Start execution (`-exec-run`) |
| `next` | Step over one source line |
| `step` | Step into a function call |
| `continue_execution` | Resume until the next breakpoint or program exit |
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
pip install -e ".[dev]"

# Run tests
pytest
```

All tests mock the GDB subprocess — no actual GDB process is started during
`pytest`. The suite covers the MCP tools and the session layer.

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
