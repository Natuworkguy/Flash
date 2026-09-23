"""Tests for the banner and the status bar under the prompt."""

import io
import os
import re
from types import SimpleNamespace

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


class TestWhenTheBarAppears:
    """The bar is off for the opening prompt and on from then on, so
    the banner has the screen to itself while it is being read."""

    def statuses(self, monkeypatch, lines):
        """The `status` each prompt was given, in order."""

        from flash import agent as subagents
        from flash.cli import parse_args

        seen = []
        feed = iter(lines)

        def fake_read_line(prompt, wake=None, status=None, health=None,
                           backdrop=None):
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


class TestPadToBottom:
    """The opening prompt is pushed to the foot of the screen, so the
    blank rows land above the frame rather than inside it."""

    def console(self, rows, terminal=True):
        return SimpleNamespace(
            is_terminal=terminal,
            size=SimpleNamespace(width=80, height=rows),
        )

    def test_it_fills_the_room_under_the_banner(self, capsys):
        written = ai.pad_to_bottom(self.console(24), used=11)

        assert written == 24 - 11 - ai.FIRST_PROMPT_ROWS
        assert capsys.readouterr().out == "\n" * written

    def test_a_full_screen_gets_no_padding(self, capsys):
        # A banner that already reaches the prompt leaves no room.
        used = 14 - ai.FIRST_PROMPT_ROWS

        assert ai.pad_to_bottom(self.console(14), used=used) == 0
        assert capsys.readouterr().out == ""

    def test_an_overflowing_banner_gets_no_padding(self, capsys):
        # Already taller than the terminal: padding would only make it
        # worse by scrolling the banner away.
        assert ai.pad_to_bottom(self.console(10), used=20) == 0
        assert capsys.readouterr().out == ""

    def test_a_pipe_is_left_alone(self, capsys):
        assert ai.pad_to_bottom(self.console(40, terminal=False), 11) == 0
        assert capsys.readouterr().out == ""

    def test_a_taller_terminal_gets_more_padding(self):
        short = ai.pad_to_bottom(self.console(20), used=11)
        tall = ai.pad_to_bottom(self.console(50), used=11)

        assert tall > short


class TestBannerHeight:
    def test_it_reports_the_rows_it_used(self):
        console = Console(file=io.StringIO(), width=100, force_terminal=False)

        used = ai.banner(console, None)
        drawn = len(console.file.getvalue().splitlines())

        assert used == drawn

    def test_a_longer_banner_reports_more(self, monkeypatch):
        console = Console(file=io.StringIO(), width=100, force_terminal=False)
        plain = ai.banner(console, None)

        monkeypatch.setattr(Config, "no_command_confirmation", True)
        monkeypatch.setattr(Config, "voice", True)
        loud = ai.banner(
            Console(file=io.StringIO(), width=100, force_terminal=False),
            "9.9.9",
        )

        assert loud > plain


def strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestStatusLine:
    """The text half of the bar: state on the left, hints right."""

    def wide(self, monkeypatch, columns):
        monkeypatch.setattr(
            repl_input.shutil,
            "get_terminal_size",
            lambda: os.terminal_size((columns, 24)),
        )

    def test_the_hints_sit_at_the_right_margin(self, monkeypatch):
        self.wide(monkeypatch, 100)

        line = repl_input.status_line("model")

        assert line.startswith(" model")
        assert line.rstrip().endswith(repl_input.HINTS)

    def test_a_narrow_terminal_drops_the_hints(self, monkeypatch):
        self.wide(monkeypatch, 40)

        line = repl_input.status_line("model")

        assert repl_input.HINTS not in line
        assert "model" in line

    def test_a_long_status_drops_the_hints_rather_than_wrapping(
        self, monkeypatch
    ):
        self.wide(monkeypatch, 80)

        assert repl_input.HINTS not in repl_input.status_line("m" * 70)

    def test_a_prefix_is_taken_off_the_right_margin(self, monkeypatch):
        self.wide(monkeypatch, 100)

        # The health dot is drawn before it, so the line has two fewer
        # columns to work with.
        assert len(repl_input.status_line("model", prefix=2)) <= 98

    def test_it_never_overruns_the_terminal(self, monkeypatch):
        for columns in (20, 40, 60, 61, 80, 120, 200):
            self.wide(monkeypatch, columns)

            assert len(repl_input.status_line("model  auto")) <= columns


class TestInputFrame:
    def wide(self, monkeypatch, columns=80):
        monkeypatch.setattr(
            repl_input.shutil,
            "get_terminal_size",
            lambda: os.terminal_size((columns, 24)),
        )

    def test_the_rule_spans_the_terminal(self, monkeypatch):
        self.wide(monkeypatch, 80)

        assert len(repl_input.input_rule()) == 80

    def test_it_follows_a_resize(self, monkeypatch):
        self.wide(monkeypatch, 120)

        assert len(repl_input.input_rule()) == 120

    def test_it_is_drawn_with_the_themes_glyph(self, monkeypatch):
        self.wide(monkeypatch)

        from flash.theme import BAR_EMPTY

        assert set(repl_input.input_rule()) == {BAR_EMPTY}


class TestStatusPrefix:
    """Everything drawn above the input, as one prompt string.

    It hangs off the prompt rather than a bottom toolbar, because a
    toolbar pins the layout to the foot of the screen and leaves the
    completion dropdown nowhere to open.
    """

    def wide(self, monkeypatch, columns=100):
        monkeypatch.setattr(
            repl_input.shutil,
            "get_terminal_size",
            lambda: os.terminal_size((columns, 24)),
        )

    def test_with_no_status_it_is_just_the_rule(self, monkeypatch):
        self.wide(monkeypatch)

        prefix = strip_ansi(repl_input.status_prefix())

        assert prefix == repl_input.input_rule() + "\n"

    def test_an_empty_status_is_treated_as_none(self, monkeypatch):
        self.wide(monkeypatch)

        assert repl_input.status_prefix("") == repl_input.status_prefix()

    def test_with_a_status_the_line_sits_above_the_rule(self, monkeypatch):
        self.wide(monkeypatch)

        rows = strip_ansi(repl_input.status_prefix("model")).splitlines()

        assert len(rows) == 2
        assert "model" in rows[0]
        assert rows[1] == repl_input.input_rule()

    def test_it_always_ends_ready_for_the_prompt(self, monkeypatch):
        self.wide(monkeypatch)

        for status in (None, "model"):
            assert repl_input.status_prefix(status).endswith("\n")

    def test_the_dot_leads_the_status_line(self, monkeypatch):
        self.wide(monkeypatch)

        first = strip_ansi(repl_input.status_prefix("model")).splitlines()[0]

        assert first.lstrip().startswith(repl_input.HEALTH_DOT)

    def test_each_state_is_coloured_differently(self, monkeypatch):
        self.wide(monkeypatch)

        seen = {
            repl_input.status_prefix("model", state)
            for state in (
                repl_input.HEALTH_OK,
                repl_input.HEALTH_DOWN,
                repl_input.HEALTH_UNKNOWN,
            )
        }

        assert len(seen) == 3

    def test_the_status_row_fits_the_terminal(self, monkeypatch):
        self.wide(monkeypatch, 100)

        first = strip_ansi(repl_input.status_prefix("model")).splitlines()[0]

        assert len(first) <= 100


class TestWhatSitsBelowTheInput:
    """Only the closing rule. The status line goes above the input
    instead, which is what leaves the completion menu room to open
    upward over the rows above it."""

    def wide(self, monkeypatch, columns=80):
        monkeypatch.setattr(
            repl_input.shutil,
            "get_terminal_size",
            lambda: os.terminal_size((columns, 24)),
        )

    def test_the_toolbar_carries_the_rule_and_nothing_else(
        self, monkeypatch
    ):
        self.wide(monkeypatch)

        bar = repl_input.closing_rule()

        assert len(bar) == 1
        assert bar[0][1] == repl_input.input_rule()

    def test_the_closing_rule_costs_one_row(self, monkeypatch):
        self.wide(monkeypatch)

        # A newline here would reserve a second row for nothing and
        # stretch the frame.
        assert "\n" not in repl_input.closing_rule()[0][1]

    def test_the_status_line_is_not_down_there(self, monkeypatch):
        self.wide(monkeypatch)

        drawn = repl_input.closing_rule()[0][1]

        assert repl_input.HEALTH_DOT not in drawn
        assert repl_input.HINTS not in drawn

    def test_the_prompt_asks_for_that_toolbar(self):
        import inspect

        source = inspect.getsource(repl_input.read_line)

        assert "bottom_toolbar=closing_rule" in source

    def test_nothing_is_reserved_for_the_menu(self):
        import inspect

        source = inspect.getsource(repl_input.read_line)

        # Pinned to zero, not left to prompt_toolkit's default of 8.
        # Those rows are drawn whether or not a menu is open, so they
        # sit under the prompt as dead space and overflow the screen by
        # their own height, which scrolls the banner away on launch.
        assert "reserve_space_for_menu=0" in source

    def test_the_opening_prompt_is_three_rows(self):
        # Rule, the line you type on, rule. pad_to_bottom counts on it.
        assert ai.FIRST_PROMPT_ROWS == 3


class TestClearingToTheBottom:
    """Clearing leaves the cursor at the top, which is what strands the
    prompt halfway up the screen with dead space under it. Parking it
    on the last row makes output fill upward and keeps the prompt at
    the foot of the terminal for the rest of the session."""

    def terminal(self, monkeypatch, rows=24, is_terminal=True):
        monkeypatch.setattr(
            ai,
            "console",
            SimpleNamespace(
                is_terminal=is_terminal,
                size=SimpleNamespace(width=80, height=rows),
            ),
        )

    def test_a_plain_clear_leaves_the_cursor_where_it_lands(
        self, monkeypatch, capsys
    ):
        self.terminal(monkeypatch)

        ai._clear_screen()

        assert capsys.readouterr().out == "\x1b[H\x1b[2J\x1b[3J"

    def test_clearing_to_the_bottom_parks_on_the_last_row(
        self, monkeypatch, capsys
    ):
        self.terminal(monkeypatch, rows=24)

        ai._clear_screen(bottom=True)

        assert capsys.readouterr().out.endswith("\x1b[24;1H")

    def test_the_row_follows_the_terminal_height(
        self, monkeypatch, capsys
    ):
        self.terminal(monkeypatch, rows=50)

        ai._clear_screen(bottom=True)

        assert capsys.readouterr().out.endswith("\x1b[50;1H")

    def test_the_scrollback_is_cleared_either_way(
        self, monkeypatch, capsys
    ):
        self.terminal(monkeypatch)

        ai._clear_screen(bottom=True)

        # 3J is the one that drops scrollback; without it the banner
        # is still up there to be scrolled back to.
        assert "\x1b[3J" in capsys.readouterr().out

    def test_a_pipe_is_left_alone(self, monkeypatch, capsys):
        self.terminal(monkeypatch, is_terminal=False)

        ai._clear_screen(bottom=True)

        assert capsys.readouterr().out == ""


class TestRepaintingOnResize:
    """The picture is ordinary output, so a resize reflows it at the
    width it was drawn for. The opening screen gets drawn again."""

    def drive(self, monkeypatch, lines):
        """Run main() over `lines`, returning what repaint saw."""

        from flash import agent as subagents
        from flash.cli import parse_args
        from flash.repl_input import RESIZE

        repaints = []
        feed = iter(lines)
        sent = []

        def fake_read_line(prompt, wake=None, status=None, health=None,
                           backdrop=None):
            try:
                return next(feed)
            except StopIteration:
                raise EOFError from None

        def fake_chat(console, client, messages, tools_arg=None, **kwargs):
            sent.append(messages[-1]["content"])
            return "reply", "", [], None

        monkeypatch.setattr(ai, "parse_args", lambda: parse_args([]))
        monkeypatch.setattr(ai, "check_for_update", lambda: None)
        monkeypatch.setattr(ai, "read_line", fake_read_line)
        monkeypatch.setattr(ai, "_chat_retry_until_response", fake_chat)
        monkeypatch.setattr(
            ai, "_session_system_prompt", lambda heard=False: ""
        )
        monkeypatch.setattr(ai, "notify_reply_ready", lambda: None)
        monkeypatch.setattr(
            ai, "repaint", lambda c, update=None: repaints.append(update)
        )
        monkeypatch.setattr(Config, "show_stats", False)
        monkeypatch.setattr(subagents, "_agents", {})

        ai.main()
        return repaints, sent, RESIZE

    def test_a_resize_repaints_the_opening_screen(self, monkeypatch):
        from flash.repl_input import RESIZE

        repaints, sent, _ = self.drive(monkeypatch, [RESIZE, "hello"])

        assert len(repaints) == 1

    def test_the_sentinel_never_reaches_the_model(self, monkeypatch):
        from flash.repl_input import RESIZE

        repaints, sent, _ = self.drive(monkeypatch, [RESIZE, "hello"])

        assert all(RESIZE not in message for message in sent)
        assert any("hello" in message for message in sent)

    def test_several_resizes_each_repaint(self, monkeypatch):
        from flash.repl_input import RESIZE

        repaints, _, _ = self.drive(
            monkeypatch, [RESIZE, RESIZE, RESIZE, "hi"]
        )

        assert len(repaints) == 3

    def test_no_repaint_once_the_banner_has_gone(self, monkeypatch):
        from flash.repl_input import RESIZE

        # After a turn the conversation above is the terminal's to
        # reflow, and prompt_toolkit has redrawn the prompt already.
        repaints, _, _ = self.drive(monkeypatch, ["hi", RESIZE])

        assert repaints == []
