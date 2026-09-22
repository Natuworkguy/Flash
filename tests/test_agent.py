# pylint: disable=C0114,C0115,C0116

import io
import threading
import time

from rich.console import Console

from flash import agent, theme, tools
from flash.agent import Step, SubAgent


class FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, name, arguments):
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeResponse:
    def __init__(self, message):
        self.message = message


class FakeClient:
    """Replays a fixed sequence of responses, one per chat() call."""

    def __init__(self, responses, *, delay=0.0):
        self.responses = list(responses)
        self.delay = delay
        self.calls = []

    def __call__(self, host=None):  # stands in for ollama.Client(host=...)
        return self

    def chat(self, model, messages, tools=None, options=None):
        if self.delay:
            time.sleep(self.delay)
        self.calls.append({
            "model": model,
            "messages": list(messages),
            "tools": tools,
            "options": options,
        })
        return self.responses.pop(0)


def _reply(content="", *calls):
    return FakeResponse(FakeMessage(
        content=content,
        tool_calls=[FakeToolCall(name, args) for name, args in calls],
    ))


def _wire(monkeypatch, responses, *, model="test-model", delay=0.0,
          autonomous=False):
    fake_client = FakeClient(responses, delay=delay)
    monkeypatch.setattr(agent.ollama, "Client", fake_client)
    monkeypatch.setattr(agent, "get_model_system_prompt", lambda h, m: "")
    monkeypatch.setattr(agent, "chat_options", dict)
    monkeypatch.setattr(tools, "MODEL_NAME", model)
    monkeypatch.setattr(tools, "OLLAMA_HOST", "http://localhost:11434")
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", autonomous)
    return fake_client


def _finish(agent_id):
    entry = agent.follow(agent_id, timeout=2)
    assert entry is not None  # nosec B101
    return entry


def _draw(renderable) -> str:
    out = Console(file=io.StringIO(), width=120, color_system=None)
    out.print(renderable)
    return out.file.getvalue()


# --- tool set --------------------------------------------------------------


def test_confirmed_tools_are_withheld_outside_autonomous_mode(monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    names = agent.allowed_tool_names()
    assert "shell" not in names  # nosec B101
    assert "write" not in names  # nosec B101
    assert "read" in names  # nosec B101


def test_autonomous_mode_gets_every_subagent_tool(monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    assert agent.allowed_tool_names() == tools.SUBAGENT_TOOL_NAMES  # nosec


def test_only_allowed_schemas_are_sent(monkeypatch):
    client = _wire(monkeypatch, [_reply("done")])

    _finish(agent.start("task"))

    sent = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert sent == set(agent.allowed_tool_names())  # nosec B101
    assert "agent" not in sent  # nosec B101


# --- the loop --------------------------------------------------------------


def test_runs_to_completion_without_tool_calls(monkeypatch):
    _wire(monkeypatch, [_reply("done answer")])

    entry = _finish(agent.start("do a thing"))

    assert entry.status == agent.DONE  # nosec B101
    assert entry.result == "done answer"  # nosec B101
    assert entry.rounds == 1  # nosec B101


def test_tool_output_becomes_steps_not_terminal_output(monkeypatch, capsys):
    _wire(monkeypatch, [
        _reply("", ("get_date", {})),
        _reply("final answer"),
    ])
    agent_id = agent.start("what day is it")
    while agent.status(agent_id).status == agent.RUNNING:
        time.sleep(0.01)

    entry = agent.status(agent_id)
    assert entry.result == "final answer"  # nosec B101
    assert [s.label for s in entry.steps] == ["GetDate()"]  # nosec B101
    assert entry.steps[0].detail  # nosec B101
    assert "GetDate" not in capsys.readouterr().out  # nosec B101


def test_reason_is_logged_rather_than_printed(monkeypatch, capsys):
    _wire(monkeypatch, [
        _reply("", ("reason", {"thought": "check the docs"})),
        _reply("ok"),
    ])
    agent_id = agent.start("task")
    while agent.status(agent_id).status == agent.RUNNING:
        time.sleep(0.01)

    step = agent.status(agent_id).steps[0]
    assert (step.label, step.detail) == ("Reason", "check the docs")  # nosec
    assert "check the docs" not in capsys.readouterr().out  # nosec B101


def test_withheld_tool_is_refused_and_never_prompts(monkeypatch):
    client = _wire(monkeypatch, [
        _reply("", ("shell", {"command": "rm -rf /"})),
        _reply("gave up"),
    ])

    entry = _finish(agent.start("task"))

    assert entry.steps[0].failed  # nosec B101
    tool_message = client.calls[1]["messages"][-1]
    assert tool_message["content"].startswith("Unknown tool: shell")  # nosec


def test_tool_output_is_trimmed(monkeypatch):
    client = _wire(monkeypatch, [
        _reply("", ("get_date", {})),
        _reply("ok"),
    ])
    monkeypatch.setitem(tools.FUNCTIONS, "get_date", lambda: "x" * 5000)
    monkeypatch.setattr(tools, "MAX_TOOL_OUTPUT_CHARS", 600)

    _finish(agent.start("task"))

    content = client.calls[1]["messages"][-1]["content"]
    assert "truncated" in content  # nosec B101
    assert len(content) < 1000  # nosec B101


def test_round_limit_forces_a_final_answer(monkeypatch):
    client = _wire(monkeypatch, [
        _reply("", ("get_date", {})),
        _reply("answer from what I have"),
    ])
    monkeypatch.setattr(agent, "MAX_SUBAGENT_ROUNDS", 1)

    entry = _finish(agent.start("task"))

    assert entry.result == "answer from what I have"  # nosec B101
    assert client.calls[1]["tools"] is None  # nosec B101
    last = client.calls[1]["messages"][-1]
    assert last["content"] == agent.ROUND_LIMIT_MESSAGE  # nosec B101


def test_sends_the_main_loops_options_and_prompts(monkeypatch):
    client = _wire(monkeypatch, [_reply("ok")])
    monkeypatch.setattr(agent, "chat_options", lambda: {"num_ctx": 8192})
    monkeypatch.setattr(
        agent, "get_model_system_prompt", lambda h, m: "I am Onyx."
    )

    _finish(agent.start("task"))

    call = client.calls[0]
    assert call["options"] == {"num_ctx": 8192}  # nosec B101
    system = call["messages"][0]["content"]
    assert system.startswith("I am Onyx.")  # nosec B101
    assert agent.SUBAGENT_SYSTEM_PROMPT in system  # nosec B101
    assert tools.CURRENT_DATE_PROMPT in system  # nosec B101


def test_marks_error_when_model_not_set(monkeypatch):
    _wire(monkeypatch, [], model="")

    entry = _finish(agent.start("task"))

    assert entry.status == agent.FAILED  # nosec B101
    assert "MODEL is not set" in entry.result  # nosec B101


def test_follow_times_out_while_running(monkeypatch):
    _wire(monkeypatch, [_reply("eventually")], delay=0.3)

    entry = agent.follow(agent.start("slow task"), timeout=0.05)

    assert entry.status == agent.RUNNING  # nosec B101


def test_follow_unknown_id_returns_none():
    assert agent.follow("no-such-id", timeout=0.1) is None  # nosec B101


def test_status_returns_a_copy(monkeypatch):
    _wire(monkeypatch, [_reply("ok")])
    agent_id = agent.start("task")
    _finish(agent_id)

    agent.status(agent_id).steps.append(Step(label="bogus"))

    assert agent.status(agent_id).steps == []  # nosec B101


# --- progress view ---------------------------------------------------------


def test_render_running_shows_activity_and_round():
    entry = SubAgent(id="a1b2c3", task="Research Apple's marketing",
                     rounds=2, activity="Running WebSearch(apple ads)",
                     steps=[Step("WebSearch(apple ads)")])

    text = _draw(agent.render(entry))

    assert "Agent a1b2c3" in text  # nosec B101
    assert "Research Apple's marketing" in text  # nosec B101
    assert "Running WebSearch(apple ads)" in text  # nosec B101
    assert "round 2" in text  # nosec B101


def test_render_done_and_failed():
    done = SubAgent(id="x", task="t", status=agent.DONE, rounds=3,
                    steps=[Step("Read(a.py)", "1 line")], finished=time.time())
    failed = SubAgent(id="y", task="t", status=agent.FAILED,
                      result="ResponseError: model not found",
                      finished=time.time())

    assert "Done · 1 step · 3 rounds" in _draw(agent.render(done))  # nosec
    assert "Failed: ResponseError" in _draw(agent.render(failed))  # nosec


def test_render_folds_older_steps():
    entry = SubAgent(id="x", task="t",
                     steps=[Step(f"Read({i})") for i in range(7)])

    text = _draw(agent.render(entry, recent=3))

    assert "4 earlier steps" in text  # nosec B101
    assert "Read(6)" in text  # nosec B101
    assert "Read(0)" not in text  # nosec B101


def test_render_result_only_once_finished():
    running = SubAgent(id="x", task="t", result="")
    done = SubAgent(id="x", task="t", status=agent.DONE, result="The answer")

    assert "The answer" in _draw(agent.render(done, result=True))  # nosec
    assert "The answer" not in _draw(agent.render(running, result=True))


def test_watch_with_no_agents(monkeypatch, capsys):
    monkeypatch.setattr(agent, "_agents", {})
    agent.watch()
    assert "No sub-agents yet" in capsys.readouterr().out  # nosec B101


def test_watch_unknown_id(capsys):
    agent.watch("nope")
    assert "No sub-agent with ID 'nope'" in capsys.readouterr().out  # nosec


# --- delivery to the next turn ---------------------------------------------


def _registry(monkeypatch, *entries):
    monkeypatch.setattr(agent, "_agents", {e.id: e for e in entries})


def test_notices_empty_with_nothing_to_say(monkeypatch):
    _registry(monkeypatch, SubAgent(id="old", task="t", status=agent.DONE,
                                    result="seen", delivered=True))
    assert agent.notices() == ("", [])  # nosec B101


def test_notices_carry_a_finished_answer_once(monkeypatch):
    _registry(monkeypatch, SubAgent(
        id="28a965", task="Research superconductors",
        status=agent.DONE, result="LK-99 failed.",
    ))

    text, ids = agent.notices()

    assert ids == ["28a965"]  # nosec B101
    assert "Sub-agent 28a965 finished" in text  # nosec B101
    assert "Research superconductors" in text  # nosec B101
    assert "LK-99 failed." in text  # nosec B101
    assert "not typed by the user" in text  # nosec B101

    agent.mark_delivered(ids)
    assert agent.notices() == ("", [])  # nosec B101


def test_notices_name_running_agents_without_consuming_them(monkeypatch):
    _registry(monkeypatch, SubAgent(id="d4e5f6", task="Apple marketing"))

    text, ids = agent.notices()

    assert "Sub-agent d4e5f6 is still running" in text  # nosec B101
    assert ids == []  # nosec B101
    assert agent.notices()[0] == text  # nosec B101


def test_notices_report_a_failure(monkeypatch):
    _registry(monkeypatch, SubAgent(id="x", task="t", status=agent.FAILED,
                                    result="ResponseError: boom"))
    text, ids = agent.notices()
    assert "Sub-agent x failed" in text  # nosec B101
    assert "ResponseError: boom" in text  # nosec B101
    assert ids == ["x"]  # nosec B101


def test_notices_trim_long_answers_and_tasks(monkeypatch):
    monkeypatch.setattr(tools, "MAX_TOOL_OUTPUT_CHARS", 600)
    _registry(monkeypatch, SubAgent(id="x", task="word " * 200,
                                    status=agent.DONE, result="y" * 5000))

    text, _ = agent.notices()

    assert "truncated" in text  # nosec B101
    assert len(text) < 1300  # nosec B101


def test_agent_result_marks_the_answer_delivered(monkeypatch):
    entry = SubAgent(id="abc123", task="x", status=agent.DONE, result="hi")
    _registry(monkeypatch, entry)
    monkeypatch.setattr(agent, "follow", lambda agent_id, timeout: entry)

    tools.agent_result("abc123")

    assert agent.notices() == ("", [])  # nosec B101


# --- capture ---------------------------------------------------------------


def test_capture_only_affects_its_own_thread(capsys):
    seen = []
    ready = threading.Event()
    release = threading.Event()

    def captured():
        with theme.capture_tool_output(lambda *a: seen.append(a)):
            ready.set()
            release.wait(1)
            theme.tool_line("Sub(1)")

    worker = threading.Thread(target=captured)
    worker.start()
    ready.wait(1)
    theme.tool_line("Main(1)")
    release.set()
    worker.join(1)

    assert seen == [("line", "Sub(1)", "")]  # nosec B101
    out = capsys.readouterr().out
    assert "Main(1)" in out  # nosec B101
    assert "Sub(1)" not in out  # nosec B101


# --- tool wrappers ---------------------------------------------------------


def test_agent_tool_starts_a_subagent(monkeypatch):
    monkeypatch.setattr(agent, "start", lambda task: "abc123")
    assert "abc123" in tools.agent_tool("research something")  # nosec B101


def test_agent_tool_rejects_empty_task():
    assert "must not be empty" in tools.agent_tool("   ")  # nosec B101


def test_agent_result_returns_the_answer(monkeypatch):
    entry = SubAgent(id="abc123", task="x", status=agent.DONE,
                     result="the answer")
    monkeypatch.setattr(agent, "follow", lambda agent_id, timeout: entry)
    assert tools.agent_result("abc123") == "the answer"  # nosec B101


def test_agent_result_reports_a_failure(monkeypatch):
    entry = SubAgent(id="abc123", task="x", status=agent.FAILED,
                     result="ResponseError: boom")
    monkeypatch.setattr(agent, "follow", lambda agent_id, timeout: entry)
    result = tools.agent_result("abc123")
    assert result == "Sub-agent abc123 failed: ResponseError: boom"  # nosec


def test_agent_result_reports_unknown_id(monkeypatch):
    monkeypatch.setattr(agent, "follow", lambda agent_id, timeout: None)
    assert "no sub-agent" in tools.agent_result("no-such-id")  # nosec B101


def test_agent_result_reports_still_running(monkeypatch):
    entry = SubAgent(id="abc123", task="x")
    monkeypatch.setattr(agent, "follow", lambda agent_id, timeout: entry)
    result = tools.agent_result("abc123", wait_seconds=1)
    assert "still running" in result  # nosec B101


def test_agent_result_clamps_the_wait(monkeypatch):
    waits = []

    def fake_follow(agent_id, timeout):
        waits.append(timeout)

    monkeypatch.setattr(agent, "follow", fake_follow)
    tools.agent_result("x", wait_seconds=99999)
    tools.agent_result("x", wait_seconds="soon")

    assert waits == [agent.MAX_WAIT_SECONDS, agent.DEFAULT_WAIT_SECONDS]
