"""Tests for how tool output is cut short on screen, and expanded."""

import io

import pytest
from rich.console import Console

from flash import ai, theme


@pytest.fixture(autouse=True)
def screen(monkeypatch):
    out = Console(file=io.StringIO(), width=120, force_terminal=False)
    monkeypatch.setattr(theme, "console", out)
    monkeypatch.setattr(theme, "_collapsed", [])
    return out


def shown(screen):
    return screen.file.getvalue()


def numbered(count):
    return "\n".join(f"line {n}" for n in range(1, count + 1))


class TestCollapsing:
    def test_short_output_is_shown_whole(self, screen):
        theme.tool_result(numbered(theme.COLLAPSE_AFTER))

        assert f"line {theme.COLLAPSE_AFTER}" in shown(screen)
        assert theme.EXPAND_HINT not in shown(screen)
        assert theme.collapsed() == []

    def test_long_output_keeps_its_head(self, screen):
        theme.tool_line("Bash(ls)")
        theme.tool_result(numbered(40))

        text = shown(screen)
        hidden = 40 - theme.COLLAPSED_LINES

        assert f"line {theme.COLLAPSED_LINES}\n" in text
        assert f"line {theme.COLLAPSED_LINES + 1}\n" not in text
        assert f"+{hidden} lines (ctrl+o to expand)" in text
        assert theme.collapsed() == [("Bash(ls)", numbered(40))]

    def test_a_failure_keeps_its_tail(self, screen):
        theme.tool_result(numbered(40), style=theme.ERROR)

        text = shown(screen)

        assert "line 40" in text
        assert "line 1\n" not in text
        assert "lines above (ctrl+o to expand)" in text

    def test_expand_prints_everything(self, screen):
        theme.tool_line("Bash(ls)")
        theme.tool_result(numbered(40))
        screen.file.truncate(0)
        screen.file.seek(0)

        theme.expand_collapsed()

        text = shown(screen)
        assert "Bash(ls)" in text
        assert all(f"line {n}\n" in text for n in range(1, 41))

    def test_expand_with_nothing_cut(self, screen):
        theme.expand_collapsed()

        assert "No tool output was cut short" in shown(screen)

    def test_a_sub_agents_output_is_never_cut(self):
        seen = []

        with theme.capture_tool_output(lambda *event: seen.append(event)):
            theme.tool_result(numbered(40))

        assert seen == [("result", numbered(40), theme.DIM)]
        assert theme.collapsed() == []


class TestHardBreaks:
    def test_single_breaks_are_kept(self):
        assert ai._hard_breaks("one\ntwo\nthree") == "one  \ntwo  \nthree"

    def test_code_fences_are_left_alone(self):
        text = "look:\n```\na\nb\n```\nthanks"

        assert ai._hard_breaks(text) == "look:  \n```\na\nb\n```\nthanks"

    def test_a_multi_line_message_echoes_on_separate_lines(self, monkeypatch):
        out = Console(file=io.StringIO(), width=80, force_terminal=False)
        monkeypatch.setattr(out, "draw", lambda paint: paint(), raising=False)

        ai._render_sent_message(out, "> ", "first line\nsecond line")

        rows = [r.strip() for r in out.file.getvalue().splitlines()]
        assert "> first line" in rows
        assert "second line" in rows


class TestKeysInTheLoop:
    def run(self, monkeypatch, lines):
        """Drive the main loop through LINES; return what was toggled."""

        from flash import agent as subagents
        from flash.cli import parse_args

        feed = iter(lines)
        saved = {}
        expanded = []

        def fake_read_line(*args, **kwargs):
            try:
                return next(feed)
            except StopIteration:
                raise EOFError from None

        monkeypatch.setattr(ai, "parse_args", lambda: parse_args([]))
        monkeypatch.setattr(ai, "check_for_update", lambda: None)
        monkeypatch.setattr(ai, "open_screen", lambda c, u: ["row"])
        monkeypatch.setattr(ai, "read_line", fake_read_line)

        def save(name, value):
            saved[name] = value
            if name == "NO_COMMAND_CONFIRMATION":
                ai.Config.no_command_confirmation = value == "1"

        monkeypatch.setattr(ai, "set_config_var", save)
        monkeypatch.setattr(
            ai, "expand_collapsed", lambda: expanded.append(True)
        )
        monkeypatch.setattr(ai.Config, "no_command_confirmation", False)
        monkeypatch.setattr(subagents, "_agents", {})

        ai.main()
        return saved, expanded

    def test_shift_tab_toggles_autonomous_mode(self, monkeypatch):
        from flash.repl_input import TOGGLE_AUTO

        saved, _ = self.run(monkeypatch, [TOGGLE_AUTO])

        assert saved == {"NO_COMMAND_CONFIRMATION": "1"}

    def test_shift_tab_redraws_the_banner_with_the_new_mode(
        self, monkeypatch
    ):
        from flash.repl_input import TOGGLE_AUTO

        printed = []
        drawn = []
        monkeypatch.setattr(
            ai.console, "print", lambda *a, **k: printed.append(a)
        )
        monkeypatch.setattr(
            "builtins.print", lambda *a, **k: printed.append(a)
        )
        monkeypatch.setattr(
            ai, "repaint",
            lambda c, u: drawn.append(ai.Config.no_command_confirmation)
            or ["row"],
        )

        self.run(monkeypatch, [TOGGLE_AUTO, TOGGLE_AUTO])

        # Drawn again after each press, with the mode already flipped,
        # so the banner's autonomous notice comes and goes.
        assert drawn == [True, False]
        # EOF prints one bare newline, and nothing else: no
        # "Autonomous mode enabled." line.
        assert printed == [()]

    def test_shift_tab_after_the_banner_only_toggles(self, monkeypatch):
        from flash.repl_input import TOGGLE_AUTO

        drawn = []
        monkeypatch.setattr(ai, "repaint", lambda c, u: drawn.append(1))
        monkeypatch.setattr(
            ai, "_chat_retry_until_response",
            lambda *a, **k: ("reply", "", [], None),
        )
        monkeypatch.setattr(
            ai, "_session_system_prompt", lambda heard=False: ""
        )
        monkeypatch.setattr(ai, "notify_reply_ready", lambda: None)
        monkeypatch.setattr(ai.Config, "show_stats", False)
        monkeypatch.setattr(ai.Config, "model", "m")
        monkeypatch.setattr(ai, "_history_budget", lambda: 1000)

        saved, _ = self.run(monkeypatch, ["hello", TOGGLE_AUTO])

        assert saved == {"NO_COMMAND_CONFIRMATION": "1"}
        assert drawn == []

    def test_ctrl_o_expands(self, monkeypatch):
        from flash.repl_input import EXPAND

        _, expanded = self.run(monkeypatch, [EXPAND])

        assert expanded == [True]

    def test_neither_is_echoed_as_a_message(self, monkeypatch):
        from flash.repl_input import EXPAND, TOGGLE_AUTO

        sent = []
        monkeypatch.setattr(
            ai, "_render_sent_message", lambda *a: sent.append(a)
        )

        self.run(monkeypatch, [TOGGLE_AUTO, EXPAND])

        assert sent == []
