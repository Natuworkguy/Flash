"""Every number in a brief or a report says where it came from."""

import pytest

from flash import loop, sources, sparks


@pytest.mark.parametrize("line", [
    "Sales were $12,400 last week (Q3 sheet, row 14).",
    "Revenue up 12% (https://example.com/revenue)",
    "Open rate was 41.5% ([dashboard](https://example.io))",
    "You have 14 unread emails (Gmail).",
    "The build took 340 seconds (from the CI log).",
    "Meeting at 9:30 on Nov 12, 2026.",
    "Version 2.4.1 is out.",
    "1. Reply to Sam",
    "3 new issues today.",
])
def test_cited_or_no_figure_passes(line):
    assert sources.uncited(line) == []


@pytest.mark.parametrize("line", [
    "Sales were $12,400 last week.",
    "You have 14 unread emails.",
    "Churn fell to 2.5%.",
])
def test_a_bare_figure_is_caught(line):
    assert sources.uncited(line) == [line]


def test_a_sources_section_and_a_source_column_count():
    text = (
        "| Metric | Value | Source |\n|---|---|---|\n"
        "| Sales | $12,400 | Q3 sheet |\n\n"
        "Signups were 140 this week [1].\n\n"
        "## Sources\n[1] https://example.com/signups 2026"
    )
    assert sources.uncited(text) == []


def test_the_nudge_names_the_lines_and_the_rule():
    said = sources.nudge("Sales were $12,400.\nAll good (from Sam's email).")
    assert "Sales were $12,400." in said
    assert "Sam's email" not in said
    assert sources.RULE in said
    assert sources.nudge("Nothing to count here.") == ""


def test_a_report_is_sent_back_once_to_cite():
    asked = []
    replies = iter([
        loop.Reply(content="Sales were $12,400 last week."),
        loop.Reply(content="Sales were $12,400 last week (Q3 sheet)."),
        loop.Reply(content="never asked for"),
    ])

    def ask(messages, with_tools):
        asked.append(list(messages))
        return next(replies)

    done = loop.run([{"role": "user", "content": "go"}], ask=ask,
                    call=lambda n, a: "", check_code=False,
                    review=sparks._cited)

    assert done.reply.content == "Sales were $12,400 last week (Q3 sheet)."
    assert len(asked) == 2
    assert "where they came from" in asked[1][-1]["content"]


def test_a_second_uncited_answer_stands():
    replies = iter([loop.Reply(content="Up 12%."), loop.Reply(content="Up 13%.")])
    done = loop.run([{"role": "user", "content": "go"}],
                    ask=lambda m, t: next(replies), call=lambda n, a: "",
                    check_code=False, review=sources.nudge)
    assert done.reply.content == "Up 13%."


def test_nothing_new_is_left_be():
    assert sparks._cited(sparks.NOTHING_NEW) == ""
