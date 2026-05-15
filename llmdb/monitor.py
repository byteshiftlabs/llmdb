"""Monitoring helpers and terminal dashboard for llmdb."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional, TextIO

from llmdb.models import MonitorSnapshot
from llmdb.session import DebugSession, SandboxConfig


def read_serial_tail(serial_log: Optional[str], max_lines: int = 20) -> list[str]:
    if not serial_log:
        return []

    path = Path(serial_log)
    if not path.is_file():
        return [f"(serial log unavailable: {path})"]

    lines = path.read_text(errors="replace").splitlines()
    return lines[-max_lines:] if max_lines > 0 else lines


def build_monitor_snapshot(
    session: DebugSession,
    radius: int = 5,
    serial_log: Optional[str] = None,
    serial_tail: int = 20,
) -> MonitorSnapshot:
    snapshot = session.monitor_snapshot(radius=radius)
    snapshot.serial_output = read_serial_tail(serial_log, max_lines=serial_tail)
    return snapshot


def write_snapshot_json(snapshot: MonitorSnapshot, output_path: str) -> None:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(target.suffix + ".tmp")
    temp_path.write_text(json.dumps(asdict(snapshot), indent=2), encoding="utf-8")
    temp_path.replace(target)


def render_monitor(snapshot: MonitorSnapshot, width: int = 100) -> str:
    divider = "=" * min(width, 100)
    status = snapshot.status
    target = snapshot.target

    lines = [
        divider,
        "llmdb monitor",
        divider,
        (
            f"state={status.state} session={status.session_id} "
            f"stops={status.stop_event_count} breakpoints={status.breakpoint_count}"
        ),
        (
            f"target={target.executable} gdb={target.gdb_executable} "
            f"remote={target.remote_target or '-'} transport={target.remote_transport or '-'}"
        ),
    ]

    if status.current_frame is not None:
        lines.append(
            "frame="
            f"{status.current_frame.function} "
            f"{status.current_frame.file}:{status.current_frame.line}"
        )
    else:
        lines.append("frame=<unavailable>")

    lines.append("")
    lines.append("Recent Stops")
    if snapshot.stop_event_history:
        for record in snapshot.stop_event_history[-5:]:
            event = record.event
            lines.append(
                f"  [{record.sequence}] {event.reason} "
                f"{event.frame.function} {event.frame.file}:{event.frame.line}"
            )
    else:
        lines.append("  <none>")

    lines.append("")
    lines.append("Threads")
    if snapshot.threads:
        for thread in snapshot.threads[:6]:
            marker = "*" if thread.current else " "
            frame = ""
            if thread.frame is not None:
                frame = f" {thread.frame.function} {thread.frame.file}:{thread.frame.line}"
            lines.append(f"  {marker} tid={thread.thread_id} {thread.state}{frame}")
    else:
        lines.append("  <none>")

    lines.append("")
    lines.append("Registers")
    if snapshot.registers:
        register_parts = [f"{register.name}={register.value}" for register in snapshot.registers[:8]]
        lines.append("  " + "  ".join(register_parts))
    else:
        lines.append("  <none>")

    lines.append("")
    lines.append("Locals")
    if snapshot.locals:
        for variable in snapshot.locals[:8]:
            lines.append(f"  {variable.type} {variable.name} = {variable.value}")
    else:
        lines.append("  <none>")

    lines.append("")
    lines.append("Source")
    if snapshot.source_context:
        lines.extend(f"  {line}" for line in snapshot.source_context)
    else:
        lines.append("  <none>")

    lines.append("")
    lines.append("Serial")
    if snapshot.serial_output:
        lines.extend(f"  {line}" for line in snapshot.serial_output[-12:])
    else:
        lines.append("  <none>")

    return "\n".join(lines)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render an llmdb monitoring dashboard in the terminal.")
    parser.add_argument("executable", help="Path to the executable with debug symbols")
    parser.add_argument("--workspace-root", help="Optional workspace root allowlist for source reads")
    parser.add_argument("--gdb-executable", default="gdb", help="Debugger executable to launch")
    parser.add_argument("--remote-target", help="Remote target such as :1234 or host:port")
    parser.add_argument("--transport", default="remote", choices=["remote", "extended-remote"])
    parser.add_argument(
        "--function-breakpoint",
        action="append",
        default=[],
        help="Function breakpoint to install before monitoring; can be repeated",
    )
    parser.add_argument("--auto-continue", action="store_true", help="Continue or run once after setup")
    parser.add_argument("--serial-log", help="Path to a QEMU serial log file to tail into the monitor")
    parser.add_argument("--serial-tail", type=int, default=20, help="Maximum serial lines to show")
    parser.add_argument("--radius", type=int, default=5, help="Source context radius")
    parser.add_argument("--cycles", type=int, default=1, help="Number of monitor refresh cycles to render")
    parser.add_argument("--refresh-seconds", type=float, default=1.0, help="Pause between refresh cycles")
    parser.add_argument("--json-out", help="Write each monitor snapshot to this JSON file")
    parser.add_argument("--no-clear", action="store_true", help="Do not clear the screen between refreshes")
    parser.add_argument("--disable-sandbox", action="store_true", help="Disable bubblewrap sandboxing")
    parser.add_argument("--allow-network", action="store_true", help="Allow networking inside the sandbox")
    parser.add_argument("--cpu-seconds", type=int, default=30)
    parser.add_argument("--memory-mb", type=int, default=1024)
    parser.add_argument("--process-limit", type=int, default=64)
    return parser.parse_args(argv)


def _build_session(args: argparse.Namespace) -> DebugSession:
    sandbox = SandboxConfig(
        enabled=not args.disable_sandbox,
        workspace_root=Path(args.workspace_root).resolve() if args.workspace_root else None,
        allow_network=args.allow_network or bool(args.remote_target),
        cpu_seconds=args.cpu_seconds,
        memory_bytes=args.memory_mb * 1024 * 1024,
        process_limit=args.process_limit,
    )
    return DebugSession(args.executable, sandbox=sandbox, gdb_executable=args.gdb_executable)


def _configure_session(session: DebugSession, args: argparse.Namespace) -> None:
    if args.remote_target:
        session.connect_remote_target(args.remote_target, args.transport)
    for function_name in args.function_breakpoint:
        session.set_function_breakpoint(function_name)
    if args.auto_continue:
        if args.remote_target:
            session.continue_execution()
        else:
            session.run()


def run_monitor(argv: Optional[list[str]] = None, output: TextIO = sys.stdout) -> int:
    args = _parse_args(argv)
    session = _build_session(args)
    try:
        _configure_session(session, args)
        for cycle in range(args.cycles):
            snapshot = build_monitor_snapshot(
                session,
                radius=args.radius,
                serial_log=args.serial_log,
                serial_tail=args.serial_tail,
            )
            if args.json_out:
                write_snapshot_json(snapshot, args.json_out)
            if not args.no_clear:
                output.write("\x1b[2J\x1b[H")
            output.write(render_monitor(snapshot) + "\n")
            output.flush()
            if cycle + 1 < args.cycles:
                time.sleep(args.refresh_seconds)
    finally:
        session.quit()
    return 0


def main(argv: Optional[list[str]] = None) -> None:
    raise SystemExit(run_monitor(argv))