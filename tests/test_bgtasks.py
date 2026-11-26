"""Background tasks: commands set aside to run on, read, stopped, and
told to their owner when they end."""

import sys
import time

import pytest

from flash import bgtasks, tools

PY = sys.executable


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(bgtasks, "log_dir", lambda: tmp_path / "tasks")
    bgtasks._tasks.clear()
    yield
    bgtasks.stop_all()


def wait_for(cond, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def start(code, owner="chat-1", **kw):
    return bgtasks.start([PY, "-u", "-c", code], shell=False, command=f"python -c {code!r}", owner=owner, **kw)


def test_output_is_read_as_it_comes_and_only_what_is_new():
    t = start("import time\nprint('one')\ntime.sleep(0.4)\nprint('two')", description="two prints")
    assert wait_for(lambda: "one" in open(t.log).read())
    first = bgtasks.output(t.id)
    assert "one" in first and "is running" in first
    assert wait_for(lambda: bgtasks.get(t.id).status == bgtasks.DONE)
    second = bgtasks.output(t.id)
    assert "two" in second and "one" not in second and "finished" in second
    assert "one" in bgtasks.output(t.id, everything=True)


def test_an_ending_is_told_once_to_its_owner_only():
    t = start("import sys; print('boom'); sys.exit(3)")
    assert wait_for(lambda: bgtasks.get(t.id).status == bgtasks.FAILED)
    assert bgtasks.unseen("other-chat") == []
    text, ids = bgtasks.notices("chat-1")
    assert ids == [t.id] and "failed (exit 3)" in text and "boom" in text
    bgtasks.mark_delivered(ids)
    assert bgtasks.notices("chat-1") == ("", [])


def test_a_task_stopped_by_its_owner_is_not_announced():
    t = start("import time; time.sleep(30)", description="dev server")
    assert bgtasks.get(t.id).status == bgtasks.RUNNING
    said = bgtasks.stop(t.id)
    assert "Stopped task" in said and "dev server" in said
    assert wait_for(lambda: bgtasks.get(t.id).ended is not None)
    assert bgtasks.get(t.id).status == bgtasks.STOPPED
    assert bgtasks.unseen("chat-1") == []


def test_too_many_at_once_is_refused(monkeypatch):
    monkeypatch.setattr(bgtasks, "MAX_RUNNING", 1)
    start("import time; time.sleep(30)")
    with pytest.raises(RuntimeError):
        start("import time; time.sleep(30)")


def test_the_shell_tool_sets_a_command_aside(monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    with tools.running_as("chat-9"):
        said = tools.shell_tool(f"{PY} -c \"print('hello')\"", background=True, description="say hello")
        task_id = said.split("background task ")[1].split(".")[0]
        assert wait_for(lambda: bgtasks.get(task_id).status == bgtasks.DONE)
        out = tools.task_output_tool(task_id)
        assert "hello" in out and "say hello" in out
        assert task_id in tools.task_output_tool("")
    # another chat cannot read or stop it
    with tools.running_as("chat-2"):
        assert "No background task" in tools.task_output_tool(task_id)
        assert "No background task" in tools.task_stop_tool(task_id)


def test_the_background_panel_lists_and_stops_them():
    t = start("import time; time.sleep(30)")
    rows = [r for r in tools.running_shells() if r.get("task") == t.id]
    assert rows and rows[0]["background"] is True
    assert tools.stop_shell(t.pid)
    assert wait_for(lambda: bgtasks.get(t.id).ended is not None)
