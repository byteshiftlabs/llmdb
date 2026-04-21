import json
from copy import deepcopy

from llmdb.models import Breakpoint, Frame, MonitorSnapshot, RegisterValue, SessionStatus, StopEvent, StopRecord, TargetInfo, ThreadInfo, Variable
from llmdb.monitor import build_monitor_snapshot, read_serial_tail, render_monitor, write_snapshot_json


class FakeSession:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.radius = None

    def monitor_snapshot(self, radius=5):
        self.radius = radius
        return deepcopy(self._snapshot)


def sample_snapshot():
    return MonitorSnapshot(
        status=SessionStatus(
            session_id="test-session",
            state="stopped",
            current_frame=Frame(level=0, function="kernel_main", file="kernel/main.c", line=373),
            last_stop_event=StopRecord(
                sequence=1,
                event=StopEvent(
                    reason="breakpoint-hit",
                    frame=Frame(level=0, function="kernel_main", file="kernel/main.c", line=373),
                ),
            ),
            stop_event_count=1,
            breakpoint_count=2,
            thread_count=1,
        ),
        target=TargetInfo(
            executable="/tmp/thunderos.elf",
            gdb_executable="riscv64-unknown-elf-gdb",
            remote_target=":1234",
            remote_transport="remote",
            connected=True,
            sandbox_enabled=False,
            network_allowed=True,
            workspace_root="/tmp",
        ),
        breakpoints=[
            Breakpoint(bp_id=1, file="kernel/main.c", line=373, function="kernel_main", enabled=True)
        ],
        threads=[
            ThreadInfo(
                thread_id="1",
                target_id="Thread 1",
                state="stopped",
                current=True,
                frame=Frame(level=0, function="kernel_main", file="kernel/main.c", line=373),
            )
        ],
        registers=[RegisterValue(number=1, name="ra", value="0x1000")],
        locals=[Variable(name="boot_cpu", type="int", value="0")],
        source_context=[" 372: before", " 373: kernel_main();", " 374: after"],
        stop_event_history=[
            StopRecord(
                sequence=1,
                event=StopEvent(
                    reason="breakpoint-hit",
                    frame=Frame(level=0, function="kernel_main", file="kernel/main.c", line=373),
                ),
            )
        ],
    )


def test_read_serial_tail_reads_last_lines(tmp_path):
    log_file = tmp_path / "serial.log"
    log_file.write_text("a\nb\nc\n")
    assert read_serial_tail(str(log_file), max_lines=2) == ["b", "c"]


def test_build_monitor_snapshot_attaches_serial_tail(tmp_path):
    log_file = tmp_path / "serial.log"
    log_file.write_text("boot\ntrap\n")
    fake_session = FakeSession(sample_snapshot())
    snapshot = build_monitor_snapshot(fake_session, radius=7, serial_log=str(log_file), serial_tail=1)
    assert fake_session.radius == 7
    assert snapshot.serial_output == ["trap"]


def test_write_snapshot_json_writes_monitor_payload(tmp_path):
    output = tmp_path / "snapshot.json"
    write_snapshot_json(sample_snapshot(), str(output))
    payload = json.loads(output.read_text())
    assert payload["status"]["state"] == "stopped"
    assert payload["target"]["remote_target"] == ":1234"


def test_render_monitor_includes_key_sections():
    snapshot = sample_snapshot()
    snapshot.serial_output = ["serial line"]
    dashboard = render_monitor(snapshot)
    assert "llmdb monitor" in dashboard
    assert "Recent Stops" in dashboard
    assert "Threads" in dashboard
    assert "Registers" in dashboard
    assert "Serial" in dashboard
    assert "kernel_main" in dashboard