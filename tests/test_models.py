"""Tests for llmdb.models — domain dataclasses."""

from llmdb.models import Frame, Breakpoint, Variable, StopEvent


class TestFrame:
    def test_frame_stores_all_fields(self):
        f = Frame(level=0, function="main", file="main.c", line=10)
        assert f.level == 0
        assert f.function == "main"
        assert f.file == "main.c"
        assert f.line == 10

    def test_frame_equality(self):
        a = Frame(level=0, function="main", file="main.c", line=10)
        b = Frame(level=0, function="main", file="main.c", line=10)
        assert a == b

    def test_frame_different_levels_not_equal(self):
        a = Frame(level=0, function="foo", file="a.c", line=1)
        b = Frame(level=1, function="foo", file="a.c", line=1)
        assert a != b


class TestBreakpoint:
    def test_breakpoint_stores_all_fields(self):
        bp = Breakpoint(bp_id=1, file="main.c", line=5, function="main", enabled=True)
        assert bp.bp_id == 1
        assert bp.file == "main.c"
        assert bp.line == 5
        assert bp.function == "main"
        assert bp.enabled is True

    def test_breakpoint_disabled(self):
        bp = Breakpoint(bp_id=2, file="foo.c", line=20, function="foo", enabled=False)
        assert bp.enabled is False


class TestVariable:
    def test_variable_stores_all_fields(self):
        v = Variable(name="x", type="int", value="42")
        assert v.name == "x"
        assert v.type == "int"
        assert v.value == "42"

    def test_variable_accepts_struct_type(self):
        v = Variable(name="node", type="struct Node *", value="0x555555557260")
        assert "struct" in v.type


class TestStopEvent:
    def test_stop_event_breakpoint_hit(self):
        frame = Frame(level=0, function="main", file="main.c", line=7)
        ev = StopEvent(reason="breakpoint-hit", frame=frame)
        assert ev.reason == "breakpoint-hit"
        assert ev.frame is frame
        assert ev.return_val is None

    def test_stop_event_exited_with_return_val(self):
        frame = Frame(level=0, function="main", file="main.c", line=12)
        ev = StopEvent(reason="exited", frame=frame, return_val="0")
        assert ev.return_val == "0"

    def test_stop_event_end_stepping(self):
        frame = Frame(level=0, function="foo", file="foo.c", line=3)
        ev = StopEvent(reason="end-stepping-range", frame=frame)
        assert ev.reason == "end-stepping-range"
