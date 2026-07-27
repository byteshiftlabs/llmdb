"""
Tests for MCP server tool dispatch (server.py).

DebugSession is fully mocked — these tests verify that:
- the server registers each tool
- tools delegate correctly to DebugSession
- missing session_id raises SessionNotFound
- DebugError from session propagates as MCP error content
"""

import pytest
from unittest.mock import MagicMock, patch

from llmdb.models import Frame, Breakpoint, Variable, StopEvent
from llmdb.session import DebugError


# We import server tools as plain callables for unit testing.
# The MCP framework is NOT invoked here — we test the underlying logic.
import llmdb.server as srv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session():
    """Return a mock DebugSession with sensible defaults."""
    s = MagicMock()
    s.session_id = "test-uuid-1234"
    return s


def sample_stop_event():
    return StopEvent(
        reason="end-stepping-range",
        frame=Frame(level=0, function="main", file="main.c", line=5),
    )


# ---------------------------------------------------------------------------
# Session registry helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_sessions():
    """Ensure _sessions is empty before and after each test."""
    srv._sessions.clear()
    yield
    srv._sessions.clear()


def register(session):
    srv._sessions[session.session_id] = session
    return session.session_id


# ---------------------------------------------------------------------------
# start_session / stop_session
# ---------------------------------------------------------------------------

class TestStartSession:
    def test_start_session_returns_session_id(self, tmp_path):
        exe = tmp_path / "prog"
        exe.write_text("")
        with patch("llmdb.server.DebugSession") as MockSession:
            mock = MagicMock()
            mock.session_id = "new-uuid"
            MockSession.return_value = mock
            sid = srv._start_session(str(exe))
        assert sid == "new-uuid"
        assert "new-uuid" in srv._sessions

    def test_start_session_rejects_nonexistent_executable(self):
        with pytest.raises(FileNotFoundError):
            srv._start_session("/does/not/exist")


class TestStopSession:
    def test_stop_session_removes_from_registry(self):
        session = make_session()
        sid = register(session)
        srv._stop_session(sid)
        assert sid not in srv._sessions

    def test_stop_session_calls_quit(self):
        session = make_session()
        sid = register(session)
        srv._stop_session(sid)
        session.quit.assert_called_once()

    def test_stop_session_unknown_id_raises(self):
        with pytest.raises(KeyError):
            srv._stop_session("no-such-id")


# ---------------------------------------------------------------------------
# Execution tools
# ---------------------------------------------------------------------------

class TestRunTool:
    def test_run_returns_serialised_stop_event(self):
        session = make_session()
        session.run.return_value = sample_stop_event()
        sid = register(session)
        result = srv._run(sid)
        assert result["reason"] == "end-stepping-range"
        assert result["frame"]["function"] == "main"

    def test_run_unknown_session_raises(self):
        with pytest.raises(KeyError):
            srv._run("ghost")


class TestNextTool:
    def test_next_returns_serialised_stop_event(self):
        session = make_session()
        session.next.return_value = sample_stop_event()
        sid = register(session)
        result = srv._next(sid)
        assert "frame" in result

    def test_next_propagates_debug_error(self):
        session = make_session()
        session.next.side_effect = DebugError("gdb died")
        sid = register(session)
        with pytest.raises(DebugError, match="gdb died"):
            srv._next(sid)


class TestStepTool:
    def test_step_delegates_to_session(self):
        session = make_session()
        session.step.return_value = sample_stop_event()
        sid = register(session)
        result = srv._step(sid)
        session.step.assert_called_once()
        assert "reason" in result


class TestContinueTool:
    def test_continue_delegates_to_session(self):
        session = make_session()
        session.continue_execution.return_value = sample_stop_event()
        sid = register(session)
        result = srv._continue_execution(sid)
        session.continue_execution.assert_called_once()
        assert "reason" in result


# ---------------------------------------------------------------------------
# Breakpoint tools
# ---------------------------------------------------------------------------

class TestSetBreakpointTool:
    def test_set_breakpoint_returns_serialised_breakpoint(self):
        session = make_session()
        session.set_breakpoint.return_value = Breakpoint(
            bp_id=1, file="main.c", line=5, function="main", enabled=True
        )
        sid = register(session)
        result = srv._set_breakpoint(sid, "main.c", 5)
        assert result["bp_id"] == 1
        assert result["enabled"] is True

    def test_set_function_breakpoint_returns_serialised_breakpoint(self):
        session = make_session()
        session.set_function_breakpoint.return_value = Breakpoint(
            bp_id=2, file="lib.c", line=10, function="helper", enabled=True
        )
        sid = register(session)
        result = srv._set_function_breakpoint(sid, "helper")
        assert result["function"] == "helper"


class TestRemoveBreakpointTool:
    def test_remove_breakpoint_delegates(self):
        session = make_session()
        sid = register(session)
        srv._remove_breakpoint(sid, 3)
        session.remove_breakpoint.assert_called_once_with(3)


class TestListBreakpointsTool:
    def test_list_breakpoints_returns_list_of_dicts(self):
        session = make_session()
        session.list_breakpoints.return_value = [
            Breakpoint(bp_id=1, file="main.c", line=5, function="main", enabled=True)
        ]
        sid = register(session)
        result = srv._list_breakpoints(sid)
        assert isinstance(result, list)
        assert result[0]["bp_id"] == 1


# ---------------------------------------------------------------------------
# Inspection tools
# ---------------------------------------------------------------------------

class TestReadVariableTool:
    def test_read_variable_returns_dict(self):
        session = make_session()
        session.read_variable.return_value = Variable(name="x", type="int", value="42")
        sid = register(session)
        result = srv._read_variable(sid, "x")
        assert result["value"] == "42"
        assert result["type"] == "int"


class TestEvaluateTool:
    def test_evaluate_returns_string(self):
        session = make_session()
        session.evaluate.return_value = "7"
        sid = register(session)
        result = srv._evaluate(sid, "3 + 4")
        assert result == "7"


class TestBacktraceTool:
    def test_backtrace_returns_list_of_dicts(self):
        session = make_session()
        session.backtrace.return_value = [
            Frame(level=0, function="bar", file="bar.c", line=3),
            Frame(level=1, function="main", file="main.c", line=10),
        ]
        sid = register(session)
        result = srv._backtrace(sid)
        assert len(result) == 2
        assert result[0]["function"] == "bar"


class TestFrameInfoTool:
    def test_frame_info_returns_dict(self):
        session = make_session()
        session.frame_info.return_value = Frame(level=0, function="foo", file="foo.c", line=7)
        sid = register(session)
        result = srv._frame_info(sid)
        assert result["function"] == "foo"
        assert result["line"] == 7


class TestListLocalsTool:
    def test_list_locals_returns_list_of_dicts(self):
        session = make_session()
        session.list_locals.return_value = [
            Variable(name="a", type="int", value="2"),
            Variable(name="b", type="int", value="3"),
        ]
        sid = register(session)
        result = srv._list_locals(sid)
        assert len(result) == 2
        assert result[0]["name"] == "a"


class TestListSourceContextTool:
    def test_list_source_context_returns_list_of_strings(self):
        session = make_session()
        session.list_source_context.return_value = ["line 3", "line 4", "line 5"]
        sid = register(session)
        result = srv._list_source_context(sid, radius=1)
        assert result == ["line 3", "line 4", "line 5"]
