"""The one agent loop (flash/loop.py) and the output limit the model can raise."""

import pytest

from flash import loop, tools


@pytest.fixture(autouse=True)
def fresh_limit():
    tools.reset_output_limit()
    yield
    tools.reset_output_limit()


def scripted(*replies):
    """ask() answering with REPLIES in turn, noting what it was offered."""

    asked = []

    def ask(messages, with_tools):
        asked.append((list(messages), with_tools))
        return replies[len(asked) - 1]
    return ask, asked


def test_runs_tools_until_the_answer():
    ask, asked = scripted(loop.Reply("", calls=[("get_date", {})]),
                          loop.Reply("It is Tuesday."))
    messages = [{"role": "user", "content": "day?"}]

    done = loop.run(messages, ask=ask, call=lambda n, a: "Tuesday",
                    check_code=False)

    assert done.reply.content == "It is Tuesday."
    assert done.rounds == 1 and done.tool_count == 1
    assert messages[-1] == {"role": "tool", "content": "Tuesday",
                            "tool_name": "get_date"}


def test_at_the_limit_the_asked_steps_still_run_then_it_answers():
    ran = []
    ask, asked = scripted(
        loop.Reply("Looking", calls=[("shell", {"command": "ls"})]),
        loop.Reply("Now let me run the tests:", calls=[("shell", {"command": "pytest"})]),
        loop.Reply("Listed the files and ran the tests: 3 passed."),
    )
    messages = []

    done = loop.run(messages, ask=ask, rounds=2, check_code=False,
                    call=lambda n, a: ran.append(a["command"]) or "ok")

    # the step asked for in the last round ran: nothing dropped
    assert ran == ["ls", "pytest"]
    assert done.hit_limit
    # the answer is the one asked for with no tools, not the half sentence
    assert done.reply.content.startswith("Listed the files")
    assert asked[-1][1] is False
    assert "tool rounds" in asked[-1][0][-1]["content"]


def test_an_empty_reply_is_asked_for_again():
    ask, asked = scripted(loop.Reply(""), loop.Reply(""), loop.Reply("Here."))

    done = loop.run([], ask=ask, call=lambda n, a: "", check_code=False)

    assert done.reply.content == "Here."
    assert loop.EMPTY_NUDGE in asked[1][0][-1]["content"]


def test_changed_code_is_sent_back_to_be_checked(tmp_path):
    code = tmp_path / "app.py"
    code.write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    ask, asked = scripted(
        loop.Reply("", calls=[("edit", {"path": str(code), "old_string": "1",
                                        "new_string": "2"})]),
        loop.Reply("Changed it."),
        loop.Reply("Checked: tests pass."),
    )

    done = loop.run([], ask=ask, call=lambda n, a: "Edited app.py")

    nudged = [m for m in asked[2][0] if m["role"] == "user"]
    assert nudged, "the model was not sent back to check its change"
    assert done.reply.content == "Checked: tests pass."


def test_the_model_can_raise_the_output_limit_and_see_what_was_cut():
    log = "\n".join(f"line {i}" for i in range(3000))
    cut = tools.trim_tool_output(log, "shell")
    assert "truncated" in cut and "more_output" in cut
    assert "line 1500" not in cut

    shown = tools.more_output(100_000)

    assert "line 1500" in shown and "truncated" not in shown
    assert tools.output_limit() == 100_000
    # for the rest of the task
    assert tools.trim_tool_output(log, "shell") == log.strip()


def test_more_output_is_capped():
    tools.more_output(10**9)
    assert tools.output_limit() == tools.MOST_OUTPUT_CHARS


def test_rounds_default_to_fifty(monkeypatch):
    monkeypatch.delenv("MAX_TOOL_ROUNDS", raising=False)
    assert loop.max_rounds() == 50
    monkeypatch.setenv("MAX_TOOL_ROUNDS", "7")
    assert loop.max_rounds() == 7
