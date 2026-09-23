"""Tests for the banner and the status bar under the prompt."""

import io
import os

import pytest
from rich.console import Console

from flash import ai, repl_input
from flash.ai import Config


def render(call, width=100):
    console = Console(file=io.StringIO(), width=width, force_terminal=False)
    call(console)
    return console.file.getvalue()


@pytest.fixture(autouse=True)
def local_defaults(monkeypatch):
    monkeypatch.setattr(Config, "model", "flash-onyx-2.5:31b")
    monkeypatch.setattr(Config, "host", ai.OLLAMA_HOST_DEFAULT)
    monkeypatch.setattr(Config, "no_command_confirmation", False)
    monkeypatch.setattr(Config, "voice", False)
    monkeypatch.setattr(ai, "_history_budget", lambda: 1000)


class TestBanner:
    def test_names_the_model_and_the_directory(self):
        out = render(lambda c: ai.banner(c, None))

        assert "flash-onyx-2.5:31b" in out
        assert "cwd:" in out

    def test_the_host_loses_its_scheme(self, monkeypatch):
        monkeypatch.setattr(Config, "host", "http://192.168.1.50:11434")

        out = render(lambda c: ai.banner(c, None))

        assert "192.168.1.50:11434" in out
        assert "http://" not in out

    def test_each_field_sits_on_its_own_line(self):
        out = render(lambda c: ai.banner(c, None), width=60)
        lines = [line for line in out.splitlines() if ":" in line]

        # The old layout ran model and host together, which wrapped the
        # URL mid-string on a narrow terminal.
        for field in ("model:", "host:", "cwd:"):
            assert sum(field in line for line in lines) == 1

    def test_an_update_is_called_out(self):
        out = render(lambda c: ai.banner(c, "9.9.9"))

        assert "9.9.9" in out
        assert "/update" in out

    def test_a_quiet_session_shows_no_notices(self):
        out = render(lambda c: ai.banner(c, None))

        assert "autonomous" not in out
        assert "voice" not in out

    def test_both_modes_are_announced(self, monkeypatch):
        monkeypatch.setattr(Config, "no_command_confirmation", True)
        monkeypatch.setattr(Config, "voice", True)

        out = render(lambda c: ai.banner(c, None))

        assert "autonomous" in out
        assert "voice" in out

    def test_an_unset_model_still_renders(self, monkeypatch):
        monkeypatch.setattr(Config, "model", None)

        assert "(unset)" in render(lambda c: ai.banner(c, None))


class TestStatusText:
    def test_the_model_always_appears(self):
        assert "flash-onyx-2.5:31b" in ai._status_text([])

    def test_a_local_host_is_not_worth_saying(self):
        assert "11434" not in ai._status_text([])

    def test_a_remote_host_is(self, monkeypatch):
        monkeypatch.setattr(Config, "host", "http://192.168.1.50:11434")

        assert "192.168.1.50:11434" in ai._status_text([])

    def test_context_is_reported_once_there_is_any(self):
        status = ai._status_text([{"role": "user", "content": "x" * 350}])

        assert "context" in status

    def test_an_empty_conversation_reports_no_context(self):
        assert "context" not in ai._status_text([])

    def test_context_never_reads_above_full(self):
        status = ai._status_text(
            [{"role": "user", "content": "x" * 100000}]
        )

        assert "context 100%" in status

    def test_the_modes_that_change_what_happens_are_shown(
        self, monkeypatch
    ):
        monkeypatch.setattr(Config, "no_command_confirmation", True)
        monkeypatch.setattr(Config, "voice", True)

        status = ai._status_text([])

        assert "auto" in status
        assert "voice" in status

    def test_an_unset_model_says_so(self, monkeypatch):
        monkeypatch.setattr(Config, "model", None)

        assert "no model" in ai._status_text([])


class TestStatusBar:
    def wide(self, monkeypatch, columns):
        monkeypatch.setattr(
            repl_input.shutil,
            "get_terminal_size",
            lambda: os.terminal_size((columns, 24)),
        )

    def text(self, status="model"):
        """Every segment of the bar joined, as it appears on screen."""

        return "".join(
            chunk for _, chunk in repl_input.status_bar(status)
        )

    def body(self, status="model"):
        """Just the text half, without the leading health dot."""

        return repl_input.status_bar(status)[-1][1]

    def test_the_hints_sit_at_the_right_margin(self, monkeypatch):
        self.wide(monkeypatch, 100)

        assert self.body().startswith(" model")
        assert self.text().rstrip().endswith(repl_input.HINTS)
        assert len(self.text()) <= 100

    def test_a_narrow_terminal_drops_the_hints(self, monkeypatch):
        self.wide(monkeypatch, 40)

        text = self.text()

        assert repl_input.HINTS not in text
        assert "model" in text

    def test_a_long_status_drops_the_hints_rather_than_wrapping(
        self, monkeypatch
    ):
        self.wide(monkeypatch, 80)

        text = self.text("m" * 70)

        assert repl_input.HINTS not in text
        assert len(text) <= 80

    def test_it_never_overruns_the_terminal(self, monkeypatch):
        for columns in (20, 40, 60, 61, 80, 120, 200):
            self.wide(monkeypatch, columns)

            assert len(self.text("model  auto")) <= columns

    def test_it_is_styled_as_a_footnote_not_a_widget(self, monkeypatch):
        self.wide(monkeypatch, 100)

        assert repl_input.status_bar("model")[-1][0] == (
            "class:bottom-toolbar"
        )


class TestHealthDot:
    """The one part of the bar that is not dim, because it is the one
    part worth looking at when something is wrong."""

    def wide(self, monkeypatch, columns=100):
        monkeypatch.setattr(
            repl_input.shutil,
            "get_terminal_size",
            lambda: os.terminal_size((columns, 24)),
        )

    def test_the_dot_leads_the_bar(self, monkeypatch):
        self.wide(monkeypatch)

        first = repl_input.status_bar("model")[0]

        assert repl_input.HEALTH_DOT in first[1]

    def test_each_state_gets_its_own_style(self, monkeypatch):
        self.wide(monkeypatch)

        seen = {
            repl_input.status_bar("model", state)[0][0]
            for state in (
                repl_input.HEALTH_OK,
                repl_input.HEALTH_DOWN,
                repl_input.HEALTH_UNKNOWN,
            )
        }

        assert len(seen) == 3

    def test_a_reachable_backend_is_green(self, monkeypatch):
        self.wide(monkeypatch)

        assert repl_input.HEALTH_HEX[repl_input.HEALTH_OK] != (
            repl_input.HEALTH_HEX[repl_input.HEALTH_DOWN]
        )

    def test_untested_is_not_the_same_as_broken(self):
        # Grey before anything has been sent, red once something has
        # failed: they are different things to look at.
        assert repl_input.HEALTH_HEX[repl_input.HEALTH_UNKNOWN] != (
            repl_input.HEALTH_HEX[repl_input.HEALTH_DOWN]
        )

    def test_the_dot_does_not_eat_the_right_margin(self, monkeypatch):
        self.wide(monkeypatch, 100)

        whole = "".join(
            chunk for _, chunk in repl_input.status_bar("model")
        )

        assert len(whole) <= 100
        assert whole.rstrip().endswith(repl_input.HINTS)

    def test_a_failed_request_turns_it_red(self, monkeypatch):
        monkeypatch.setattr(ai, "_backend_health", repl_input.HEALTH_OK)
        ai._note_backend(False)

        assert ai._backend_health == repl_input.HEALTH_DOWN

    def test_a_good_request_turns_it_green(self, monkeypatch):
        monkeypatch.setattr(ai, "_backend_health", repl_input.HEALTH_DOWN)
        ai._note_backend(True)

        assert ai._backend_health == repl_input.HEALTH_OK

    def test_the_waiting_bar_carries_it_too(self, monkeypatch):
        self.wide(monkeypatch)
        monkeypatch.setattr(ai, "_backend_health", repl_input.HEALTH_DOWN)

        line = ai._bar_text([])

        assert repl_input.HEALTH_DOT in line.plain


class TestWhenTheBarAppears:
    """The bar is off for the opening prompt and on from then on.

    A bottom toolbar anchors prompt_toolkit's layout to the foot of the
    screen, so the rows it reserves for the completion menu render as
    blank lines and scroll the banner away before it has been read.
    """

    def statuses(self, monkeypatch, lines):
        """The `status` each prompt was given, in order."""

        from flash import agent as subagents
        from flash.cli import parse_args

        seen = []
        feed = iter(lines)

        def fake_read_line(prompt, wake=None, status=None, health=None):
            seen.append(status)
            try:
                return next(feed)
            except StopIteration:
                raise EOFError from None

        def fake_chat(console, client, messages, tools_arg=None, **kwargs):
            return "reply", "", [], None

        monkeypatch.setattr(ai, "parse_args", lambda: parse_args([]))
        monkeypatch.setattr(ai, "check_for_update", lambda: None)
        monkeypatch.setattr(ai, "read_line", fake_read_line)
        monkeypatch.setattr(ai, "_chat_retry_until_response", fake_chat)
        monkeypatch.setattr(
            ai, "_session_system_prompt", lambda heard=False: ""
        )
        monkeypatch.setattr(ai, "notify_reply_ready", lambda: None)
        monkeypatch.setattr(Config, "show_stats", False)
        monkeypatch.setattr(subagents, "_agents", {})

        ai.main()
        return seen

    def test_the_opening_prompt_has_no_bar(self, monkeypatch):
        seen = self.statuses(monkeypatch, ["hello"])

        assert seen[0] is None

    def test_it_appears_once_a_prompt_has_been_run(self, monkeypatch):
        seen = self.statuses(monkeypatch, ["hello", "again"])

        assert seen[0] is None
        assert seen[1] is not None
        assert "flash-onyx" in seen[1]

    def test_it_stays_on_for_every_prompt_after(self, monkeypatch):
        seen = self.statuses(monkeypatch, ["one", "two", "three"])

        assert seen[0] is None
        assert all(entry is not None for entry in seen[1:])

    def test_a_slash_command_still_counts_as_a_prompt(self, monkeypatch):
        seen = self.statuses(monkeypatch, ["/help", "then ask"])

        # The banner has already been scrolled past by the command's
        # own output, so there is nothing left to protect.
        assert seen[1] is not None


class TestMenuRows:
    """How much room the completion dropdown is allowed to reserve.

    prompt_toolkit keeps eight rows free by default and, once a bottom
    toolbar anchors the layout, draws them whether or not a menu is
    open. Ten rows of banner plus a prompt, those eight and the bar is
    twenty rows, which fits a terminal window and not a VS Code panel.
    """

    def at(self, monkeypatch, rows):
        monkeypatch.setattr(
            repl_input.shutil,
            "get_terminal_size",
            lambda: os.terminal_size((80, rows)),
        )
        return repl_input.menu_rows()

    def test_a_tall_terminal_keeps_the_full_dropdown(self, monkeypatch):
        assert self.at(monkeypatch, 60) == repl_input.MAX_MENU_ROWS

    def test_a_short_panel_reserves_less(self, monkeypatch):
        assert self.at(monkeypatch, 16) < repl_input.MAX_MENU_ROWS

    def test_it_never_reserves_nothing(self, monkeypatch):
        assert self.at(monkeypatch, 4) >= repl_input.MIN_MENU_ROWS

    def test_it_never_exceeds_the_cap(self, monkeypatch):
        for rows in (4, 10, 24, 40, 200):
            assert self.at(monkeypatch, rows) <= repl_input.MAX_MENU_ROWS

    def test_it_never_takes_more_than_a_quarter_of_the_screen(
        self, monkeypatch
    ):
        for rows in (12, 16, 20, 24, 32):
            reserved = self.at(monkeypatch, rows)

            assert reserved <= max(repl_input.MIN_MENU_ROWS, rows // 4)

    def test_it_grows_with_the_terminal(self, monkeypatch):
        short = self.at(monkeypatch, 16)
        tall = self.at(monkeypatch, 48)

        assert tall > short


class TestAgentsInTheBar:
    def running(self, monkeypatch, count):
        from flash import agent as subagents

        monkeypatch.setattr(subagents, "running_count", lambda: count)

    def test_no_agents_means_no_mention(self, monkeypatch):
        self.running(monkeypatch, 0)

        assert "agent" not in ai._status_text([])

    def test_one_agent_is_singular(self, monkeypatch):
        self.running(monkeypatch, 1)

        assert "1 agent" in ai._status_text([])
        assert "1 agents" not in ai._status_text([])

    def test_several_agents_are_plural(self, monkeypatch):
        self.running(monkeypatch, 3)

        assert "3 agents" in ai._status_text([])

    def test_it_sits_beside_the_rest_of_the_state(self, monkeypatch):
        self.running(monkeypatch, 2)
        monkeypatch.setattr(Config, "no_command_confirmation", True)

        status = ai._status_text([])

        assert "flash-onyx-2.5:31b" in status
        assert "2 agents" in status
        assert "auto" in status

    def test_the_waiting_bar_is_rebuilt_not_captured(self, monkeypatch):
        # A count read once at the start of the turn would be wrong by
        # the time an agent finishes, which is while the model writes.
        counts = iter([2, 2, 1, 0])
        from flash import agent as subagents

        monkeypatch.setattr(
            subagents, "running_count", lambda: next(counts, 0)
        )

        seen = [ai._status_text([]) for _ in range(3)]

        assert "2 agents" in seen[0]
        assert "1 agent" in seen[2]
