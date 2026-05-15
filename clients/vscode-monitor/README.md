# llmdb VS Code Monitor Scaffold

This scaffold renders the JSON snapshot file written by `llmdb-monitor --json-out`.

## Usage

1. Run the terminal monitor and write snapshots to a file:

   ```bash
   llmdb-monitor /path/to/program \
     --remote-target :1234 \
     --gdb-executable riscv64-unknown-elf-gdb \
     --json-out /tmp/llmdb-monitor.json
   ```

2. Open this folder as a VS Code extension project.
3. Run the `llmdb: Open Monitor` command.
4. Point the panel at `/tmp/llmdb-monitor.json`.

The current scaffold is file-driven. It is meant to prove the layout and data model before a direct MCP-integrated webview is added.