"""
End-to-end smoke test: llmdb debugs the buggy scores binary.

The MCP SDK (mcp 1.x) uses newline-delimited JSON over stdio,
not Content-Length framing.
"""
import subprocess, json, threading, sys
from pathlib import Path

_HERE  = Path(__file__).parent.resolve()
EXE    = str(_HERE / "scores")
SERVER = str(Path(sys.executable).parent / "llmdb")

proc = subprocess.Popen([SERVER],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# Silently drain stderr to avoid blocking
threading.Thread(target=lambda: [None for _ in proc.stderr], daemon=True).start()

def send(obj):
    proc.stdin.write((json.dumps(obj) + "\n").encode())
    proc.stdin.flush()

def recv():
    """Return the next JSON-RPC message that has an 'id' (skip notifications)."""
    while True:
        line = proc.stdout.readline()
        if not line.strip():
            continue
        msg = json.loads(line)
        if "id" in msg:
            return msg

def text_of(r):
    """Extract the text from a tools/call response; JSON-decode if it is a quoted string."""
    t = next(
        (c["text"] for c in r.get("result", {}).get("content", []) if c.get("type") == "text"),
        None,
    )
    if t is None:
        return str(r)
    return json.loads(t) if t.lstrip().startswith('"') else t

def call(i, name, **kw):
    send({"jsonrpc": "2.0", "id": i, "method": "tools/call",
          "params": {"name": name, "arguments": kw}})
    return recv()

# ── handshake ────────────────────────────────────────────────────────
send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05", "capabilities": {},
    "clientInfo": {"name": "smoke-test", "version": "1"}}})
recv()
send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

# ── start_session ─────────────────────────────────────────────────────
r   = call(1, "start_session", executable=EXE)
sid = text_of(r)
print(f"[start]  session_id = {sid}", flush=True)
if not sid or r.get("error"):
    print("FAILED:", r); proc.terminate(); sys.exit(1)

# ── run → crash ───────────────────────────────────────────────────────
r = call(2, "run", session_id=sid)
print(f"[run]    {text_of(r)}", flush=True)

# ── backtrace ─────────────────────────────────────────────────────────
r = call(3, "backtrace", session_id=sid)
print(f"[bt]     {text_of(r)}", flush=True)

# ── frame info ────────────────────────────────────────────────────────
r = call(4, "frame_info", session_id=sid)
print(f"[frame]  {text_of(r)}", flush=True)

# ── source context around the crash line ──────────────────────────────
r = call(5, "list_source_context", session_id=sid, radius=4)
print(f"[src]\n{text_of(r)}", flush=True)

# ── inspect the NULL pointer ──────────────────────────────────────────
r = call(6, "evaluate", session_id=sid, expression="e")
print(f"[e]      {text_of(r)}", flush=True)

# ── see which key caused the NULL ─────────────────────────────────────
r = call(7, "evaluate", session_id=sid, expression="names[i]")
print(f"[name]   {text_of(r)}", flush=True)

# ── stop ──────────────────────────────────────────────────────────────
call(8, "stop_session", session_id=sid)
proc.terminate()
print("=== DONE ===", flush=True)
