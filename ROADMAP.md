# llmdb — Roadmap

This file tracks planned work, in rough priority order.
Items without a milestone are ideas under consideration, not committed work.

---

## v0.1 — Minimum viable product (current)

- [x] MCP server with 16 tools (exec control, breakpoints, inspection, source context)
- [x] `DebugSession` wrapping pygdbmi / GDB/MI
- [x] Pure dataclass domain model
- [x] Full unit test suite (57 tests, no real GDB needed)
- [x] Architecture documentation

---

## v0.2 — Reliability and error handling

- [ ] Smoke test against a real compiled binary (integration test, gated separately)
- [ ] Graceful handling of GDB crash or unexpected exit during `_wait_for_stop`
- [ ] Session timeout: auto-clean sessions idle longer than a configurable duration
- [ ] Structured MCP error codes (map `DebugError` subtypes to MCP error payloads)
- [ ] Pin the `uv`-resolved lockfile (`uv lock`) so CI uses reproducible deps

### Security and isolation

- [x] Linux sandboxed launch path using Bubblewrap namespaces for GDB
- [x] Explicit workspace / executable-path allowlist for session start and source reads
- [x] Unprivileged sandbox user with temporary home directory
- [x] Networking disabled by default inside the sandbox
- [x] CPU, memory, and process-count limits applied before launching GDB
- [x] Tool policy tiers: `inspect`, `debug`, `full`

---

## v0.3 — Richer inspection

- [ ] `watch_variable` — set a watchpoint on a variable
- [ ] `disassemble` — show assembly around the current program counter
- [ ] `memory_read` — read raw bytes at an address (useful for pointer debugging)
- [ ] `set_variable` — change a variable's value at runtime
- [ ] Type information via GDB variable objects (`-var-create`) rather than heuristics

---

## v0.4 — Multi-process and thread support

- [ ] `list_threads` — return all threads in the target process
- [ ] `switch_thread` — change the selected thread
- [ ] `follow_fork` — configure GDB's fork follow mode (`parent` / `child`)
- [ ] Multi-session: allow more than one debug session simultaneously (currently supported but untested under load)

---

## Future ideas (no commitment)

- Remote target support: connect to `gdbserver` running on a separate host or device
- Core file analysis: load a `.core` file and inspect a post-mortem state
- Structured pretty-printers: emit parsed C++ STL containers instead of raw addresses
- VS Code debug adapter bridge: translate MCP tool calls to DAP messages so llmdb can also be used as a VS Code debug adapter
- Language server integration: resolve symbol names to file/line using `clangd` or `rust-analyzer` before passing them to GDB

---

## Known limitations

- No hardware or embedded support (JTAG, OpenOCD, etc.) — out of scope by design
- `read_variable` returns GDB's text representation; complex types (structs, arrays) are returned as a single string
- `_wait_for_stop` has a hard 30-second timeout; long-running programs will timeout rather than run indefinitely
