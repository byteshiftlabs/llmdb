"""
Tests for llmdb.session.DebugSession.

All GDB subprocess I/O is mocked via pygdbmi.gdbcontroller.
No actual GDB binary is needed to run this suite.
"""

import pytest
from unittest.mock import MagicMock, patch, call

from llmdb.models import Frame, Breakpoint, Variable, StopEvent
from llmdb.session import DebugSession, DebugError, SandboxConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_controller(responses):
    """Return a mock GdbController whose get_gdb_response yields from responses."""
    ctrl = MagicMock()
    ctrl.get_gdb_response.side_effect = responses
    return ctrl


def stopped_response(reason="end-stepping-range", file="main.c", line=5, func="main"):
    return [{
        "type": "notify",
        "message": "stopped",
        "payload": {
            "reason": reason,
            "frame": {
                "func": func,
                "file": file,
                "fullname": file,
                "line": str(line),
                "level": "0",
            },
        },
    }]


def done_response(extra=None):
    payload = {} if extra is None else extra
    return [{"type": "result", "message": "done", "payload": payload}]


def error_response(msg="some gdb error"):
    return [{"type": "result", "message": "error", "payload": {"msg": msg}}]


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def session(tmp_path):
    """DebugSession with a mock GdbController; no real GDB spawned."""
    exe = tmp_path / "prog"
    exe.write_bytes(b"\x7fELF")  # must exist and be executable
    exe.chmod(0o755)

    with patch("llmdb.session.GdbController") as MockCtrl:
        mock_ctrl = MagicMock()
        MockCtrl.return_value = mock_ctrl
        # write_mi_response: consumed during __init__ (file-exec-and-symbols)
        mock_ctrl.get_gdb_response.return_value = done_response()
        s = DebugSession(str(exe), sandbox=SandboxConfig(enabled=False))
        s._gdb = mock_ctrl  # expose for test manipulation
        yield s


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestDebugSessionInit:
    def test_raises_if_executable_missing(self):
        with pytest.raises(FileNotFoundError):
            DebugSession("/nonexistent/prog")

    def test_session_id_is_nonempty_string(self, session):
        assert isinstance(session.session_id, str)
        assert len(session.session_id) > 0

    def test_sandboxed_launch_uses_bubblewrap_and_resource_wrapper(self, tmp_path):
        exe = tmp_path / "prog"
        exe.write_bytes(b"\x7fELF")
        exe.chmod(0o755)

        with patch("llmdb.session.GdbController") as MockCtrl, patch("llmdb.session.shutil.which", return_value="/usr/bin/bwrap"):
            mock_ctrl = MagicMock()
            mock_ctrl.get_gdb_response.return_value = done_response()
            MockCtrl.return_value = mock_ctrl

            DebugSession(
                str(exe),
                sandbox=SandboxConfig(enabled=True, workspace_root=tmp_path),
            )

        command = MockCtrl.call_args.kwargs["command"]
        assert "/usr/bin/bwrap" in command
        assert "--unshare-net" in command
        assert str(tmp_path.resolve()) in command

    def test_set_breakpoint_rejects_absolute_paths_outside_allowlist(self, session):
        with pytest.raises(PermissionError):
            session.set_breakpoint("/etc/passwd", 1)


# ---------------------------------------------------------------------------
# Execution control
# ---------------------------------------------------------------------------

class TestRun:
    def test_run_returns_stop_event(self, session):
        session._gdb.get_gdb_response.side_effect = [
            [],                          # *running (empty, ignored)
            stopped_response("breakpoint-hit", file="main.c", line=7, func="main"),
        ]
        ev = session.run()
        assert isinstance(ev, StopEvent)
        assert ev.reason == "breakpoint-hit"
        assert ev.frame.line == 7

    def test_run_raises_on_gdb_error(self, session):
        session._gdb.get_gdb_response.side_effect = [error_response("bad exec")]
        with pytest.raises(DebugError, match="bad exec"):
            session.run()


class TestNext:
    def test_next_returns_stop_event_with_new_line(self, session):
        session._gdb.get_gdb_response.side_effect = [
            [],
            stopped_response("end-stepping-range", file="main.c", line=9),
        ]
        ev = session.next()
        assert ev.reason == "end-stepping-range"
        assert ev.frame.line == 9

    def test_next_raises_on_gdb_error(self, session):
        session._gdb.get_gdb_response.side_effect = [error_response("no frame")]
        with pytest.raises(DebugError):
            session.next()


class TestStep:
    def test_step_into_function(self, session):
        session._gdb.get_gdb_response.side_effect = [
            [],
            stopped_response("end-stepping-range", file="helper.c", line=2, func="add"),
        ]
        ev = session.step()
        assert ev.frame.function == "add"


class TestContinue:
    def test_continue_runs_to_next_breakpoint(self, session):
        session._gdb.get_gdb_response.side_effect = [
            [],
            stopped_response("breakpoint-hit", line=20),
        ]
        ev = session.continue_execution()
        assert ev.reason == "breakpoint-hit"
        assert ev.frame.line == 20


# ---------------------------------------------------------------------------
# Breakpoints
# ---------------------------------------------------------------------------

class TestSetBreakpoint:
    def test_set_breakpoint_returns_breakpoint(self, session):
        session._gdb.get_gdb_response.return_value = done_response({
            "bkpt": {
                "number": "1",
                "file": "main.c",
                "line": "5",
                "func": "main",
                "enabled": "y",
            }
        })
        bp = session.set_breakpoint("main.c", 5)
        assert isinstance(bp, Breakpoint)
        assert bp.bp_id == 1
        assert bp.file == "main.c"
        assert bp.line == 5
        assert bp.enabled is True

    def test_set_breakpoint_raises_on_error(self, session):
        session._gdb.get_gdb_response.return_value = error_response("no such file")
        with pytest.raises(DebugError, match="no such file"):
            session.set_breakpoint("missing.c", 1)


class TestSetFunctionBreakpoint:
    def test_set_function_breakpoint_returns_breakpoint(self, session):
        session._gdb.get_gdb_response.return_value = done_response({
            "bkpt": {
                "number": "2",
                "file": "lib.c",
                "line": "10",
                "func": "helper",
                "enabled": "y",
            }
        })
        bp = session.set_function_breakpoint("helper")
        assert bp.function == "helper"
        assert bp.bp_id == 2


class TestRemoveBreakpoint:
    def test_remove_breakpoint_sends_correct_command(self, session):
        session._gdb.get_gdb_response.return_value = done_response()
        session.remove_breakpoint(3)
        written = session._gdb.write.call_args[0][0]
        assert "3" in written

    def test_remove_breakpoint_raises_on_error(self, session):
        session._gdb.get_gdb_response.return_value = error_response("no bp 99")
        with pytest.raises(DebugError):
            session.remove_breakpoint(99)


class TestListBreakpoints:
    def test_list_breakpoints_returns_list(self, session):
        session._gdb.get_gdb_response.return_value = done_response({
            "BreakpointTable": {
                "body": [
                    {"number": "1", "file": "main.c", "line": "5",
                     "func": "main", "enabled": "y"},
                ]
            }
        })
        bps = session.list_breakpoints()
        assert len(bps) == 1
        assert bps[0].bp_id == 1

    def test_list_breakpoints_empty_when_none(self, session):
        session._gdb.get_gdb_response.return_value = done_response({
            "BreakpointTable": {"body": []}
        })
        assert session.list_breakpoints() == []


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

class TestReadVariable:
    def test_read_variable_returns_variable(self, session):
        session._gdb.get_gdb_response.return_value = done_response({
            "value": "42", "type": "int"
        })
        v = session.read_variable("x")
        assert isinstance(v, Variable)
        assert v.name == "x"
        assert v.value == "42"
        assert v.type == "int"

    def test_read_variable_raises_on_error(self, session):
        session._gdb.get_gdb_response.return_value = error_response("no symbol x")
        with pytest.raises(DebugError, match="no symbol x"):
            session.read_variable("x")


class TestEvaluate:
    def test_evaluate_returns_string(self, session):
        session._gdb.get_gdb_response.return_value = done_response({"value": "5"})
        result = session.evaluate("2 + 3")
        assert result == "5"

    def test_evaluate_raises_on_error(self, session):
        session._gdb.get_gdb_response.return_value = error_response("invalid expr")
        with pytest.raises(DebugError):
            session.evaluate("??? bad")


class TestBacktrace:
    def test_backtrace_returns_frames(self, session):
        session._gdb.get_gdb_response.return_value = done_response({
            "stack": [
                {"frame": {"level": "0", "func": "bar", "file": "bar.c", "line": "3"}},
                {"frame": {"level": "1", "func": "main", "file": "main.c", "line": "10"}},
            ]
        })
        frames = session.backtrace()
        assert len(frames) == 2
        assert frames[0].function == "bar"
        assert frames[1].function == "main"

    def test_backtrace_accepts_real_gdb_flat_frame_shape(self, session):
        session._gdb.get_gdb_response.return_value = done_response({
            "stack": [
                {"level": "0", "func": "bar", "file": "bar.c", "line": "3"},
                {"level": "1", "func": "main", "file": "main.c", "line": "10"},
            ]
        })
        frames = session.backtrace()
        assert len(frames) == 2
        assert frames[0].function == "bar"
        assert frames[1].function == "main"


class TestFrameInfo:
    def test_frame_info_returns_current_frame(self, session):
        session._gdb.get_gdb_response.return_value = done_response({
            "frame": {"level": "0", "func": "foo", "file": "foo.c", "line": "7"}
        })
        f = session.frame_info()
        assert isinstance(f, Frame)
        assert f.function == "foo"
        assert f.line == 7


class TestListLocals:
    def test_list_locals_returns_variables(self, session):
        session._gdb.get_gdb_response.return_value = done_response({
            "locals": [
                {"name": "a", "type": "int", "value": "2"},
                {"name": "b", "type": "int", "value": "3"},
            ]
        })
        locals_ = session.list_locals()
        assert len(locals_) == 2
        assert locals_[0].name == "a"

    def test_list_locals_empty_frame(self, session):
        session._gdb.get_gdb_response.return_value = done_response({"locals": []})
        assert session.list_locals() == []


class TestListSourceContext:
    def test_list_source_context_returns_lines(self, tmp_path, session):
        src = tmp_path / "main.c"
        src.write_text("\n".join(f"line {i}" for i in range(1, 21)))

        # Patch frame_info to return line 10 of our temp file
        session.frame_info = MagicMock(return_value=Frame(
            level=0, function="main", file=str(src), line=10
        ))
        lines = session.list_source_context(radius=2)
        assert len(lines) == 5   # lines 8–12

    def test_list_source_context_rejects_source_outside_allowlist(self, session, tmp_path):
        src = tmp_path.parent / "outside.c"
        src.write_text("int main(void) { return 0; }\n")
        session.frame_info = MagicMock(return_value=Frame(
            level=0, function="main", file=str(src), line=1
        ))
        with pytest.raises(PermissionError):
            session.list_source_context(radius=1)

    def test_list_source_context_clamps_at_file_start(self, tmp_path, session):
        src = tmp_path / "short.c"
        src.write_text("int main(){}\n")
        session.frame_info = MagicMock(return_value=Frame(
            level=0, function="main", file=str(src), line=1
        ))
        lines = session.list_source_context(radius=5)
        assert len(lines) >= 1

    def test_list_source_context_raises_if_file_missing(self, session):
        session.frame_info = MagicMock(return_value=Frame(
            level=0, function="main", file="/nonexistent/file.c", line=1
        ))
        with pytest.raises(FileNotFoundError):
            session.list_source_context()


# ---------------------------------------------------------------------------
# Security — GDB MI injection prevention
# ---------------------------------------------------------------------------

class TestMiInjectionGuard:
    """User-supplied strings must not reach the GDB MI stream with newlines."""

    def test_set_breakpoint_rejects_newline_in_file(self, session):
        with pytest.raises(ValueError, match="file"):
            session.set_breakpoint("main.c\n-gdb-quit", 5)

    def test_set_function_breakpoint_rejects_newline(self, session):
        with pytest.raises(ValueError, match="function"):
            session.set_function_breakpoint("main\r-gdb-quit")

    def test_read_variable_rejects_newline(self, session):
        with pytest.raises(ValueError, match="name"):
            session.read_variable("x\n-exec-run")

    def test_evaluate_rejects_newline(self, session):
        with pytest.raises(ValueError, match="expression"):
            session.evaluate("2 + 3\n-gdb-quit")

    def test_evaluate_rejects_null_byte(self, session):
        with pytest.raises(ValueError, match="expression"):
            session.evaluate("x\x00y")

    def test_set_breakpoint_rejects_null_byte(self, session):
        with pytest.raises(ValueError, match="file"):
            session.set_breakpoint("main.c\x00evil", 1)


class TestDebugSessionPermission:
    def test_raises_if_not_executable(self, tmp_path):
        exe = tmp_path / "prog"
        exe.write_bytes(b"\x7fELF")
        exe.chmod(0o644)  # readable but not executable
        with pytest.raises(PermissionError):
            DebugSession(str(exe))


class TestListSourceContextRadiusCap:
    def test_radius_capped_at_max(self, tmp_path, session):
        src = tmp_path / "big.c"
        src.write_text("\n".join(f"line {i}" for i in range(1, 10)))
        session.frame_info = MagicMock(return_value=Frame(
            level=0, function="main", file=str(src), line=5
        ))
        # A huge radius should not cause an error — it's clamped internally
        lines = session.list_source_context(radius=999999)
        assert len(lines) <= 9  # file only has 9 lines
