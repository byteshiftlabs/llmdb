# llmdb quickstart

## Install

```bash
git clone https://github.com/byteshiftlabs/llmdb
cd llmdb
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Start the server

```bash
llmdb
```

## Example client configuration

Many MCP clients can launch llmdb as a local process. A minimal example is:

```json
{
  "mcpServers": {
    "llmdb": {
      "command": "/path/to/your/venv/bin/llmdb"
    }
  }
}
```

## Troubleshooting

- `gdb` is not found: install GDB and ensure it is on your `PATH`.
- `start_session` fails: confirm that the target executable exists, is executable, and was built with debug symbols where possible.
- The server is intended for Linux/macOS hosts with a working POSIX environment.
