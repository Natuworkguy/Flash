# pylint: disable=C0114,C0116

import json

import pytest

from flash import tools, visuals, voice


def block(*specs):
    data = specs[0] if len(specs) == 1 else list(specs)
    return f"```ui\n{json.dumps(data)}\n```"


CHART = {
    "type": "chart",
    "kind": "bar",
    "title": "Sales",
    "labels": ["Jan", "Feb"],
    "series": [{"name": "2026", "values": [10, 20]}],
    "unit": "k",
}
CALC = {
    "type": "calc",
    "title": "Tip",
    "inputs": [
        {"id": "bill", "label": "Bill", "value": 80, "prefix": "$"},
        {"id": "pct", "label": "Tip", "value": 15, "suffix": "%"},
    ],
    "outputs": [
        {
            "label": "Tip",
            "formula": "bill * pct / 100",
            "prefix": "$",
            "decimals": 2,
        }
    ],
}
CHOICES = {
    "type": "choices",
    "prompt": "Which one?",
    "options": ["Faster", "Cheaper", "Both"],
}


def test_more_by_default_and_less_with_the_setting(monkeypatch):
    monkeypatch.delenv(visuals.SETTING, raising=False)
    assert visuals.level() == "more"  # nosec B101
    assert "```ui" in tools.build_system_prompt()  # nosec B101
    assert visuals.LESS not in tools.build_system_prompt()  # nosec B101

    for value in ("less", "0", "off"):
        monkeypatch.setenv(visuals.SETTING, value)
        assert visuals.level() == "less"  # nosec B101
        prompt = tools.build_system_prompt()
        assert "```ui" in prompt and visuals.LESS in prompt  # nosec B101


def test_the_prompt_stays_inside_the_system_prompt(monkeypatch):
    monkeypatch.delenv(visuals.SETTING, raising=False)
    prompt = tools.build_system_prompt()

    assert prompt.index("== Rich answers ==") < prompt.index(  # nosec B101
        "=== END OF SYSTEM PROMPT ==="
    )


@pytest.mark.parametrize(
    "formula, expected",
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("2 ^ 3 ^ 2", 512),
        ("1 + r ^ 2", 10),
        ("-2 ^ 2", -4),
        ("r ^ -1", 1 / 3),
        ("max(r, 7) + round(2.6)", 10),
        ("sqrt(16) + abs(-1) + floor(1.9) + ceil(0.1)", 7),
        ("10 % 4", 2),
    ],
)
def test_formulas(formula, expected):
    worked = visuals.evaluate(formula, {"r": 3})
    assert worked == pytest.approx(expected)  # nosec B101


@pytest.mark.parametrize(
    "formula",
    [
        "1 / 0",
        "sqrt(-1)",
        "nope + 1",
        "__import__('os')",
        "r.real",
        "[1, 2]",
        "1 if r else 2",
        "open('x')",
        "1 +",
    ],
)
def test_formulas_that_are_not_sums_give_nothing(formula):
    assert visuals.evaluate(formula, {"r": 3}) is None  # nosec B101


def test_terminal_draws_a_bar_chart_in_text():
    shown = visuals.for_terminal(f"Look:\n\n{block(CHART)}\n\nUp.")

    assert "```ui" not in shown and "**Sales**" in shown  # nosec B101
    assert "Feb  " + "█" * visuals.BAR_WIDTH + " 20k" in shown  # nosec B101
    assert shown.endswith("Up.")  # nosec B101


def test_terminal_works_out_a_calculator_at_its_starting_values():
    shown = visuals.for_terminal(block(CALC))

    assert "- Bill: $80" in shown and "- Tip: 15%" in shown  # nosec B101
    assert "**Tip: $12**" in shown  # nosec B101


def test_terminal_numbers_the_choices():
    shown = visuals.for_terminal(block(CHOICES))

    assert (
        "Which one?" in shown and "1. Faster" in shown and "3. Both" in shown
    )  # nosec B101


def test_a_list_of_pieces_and_every_kind_renders():
    kinds = [
        CHART,
        CALC,
        CHOICES,
        {
            "type": "chart",
            "kind": "line",
            "labels": ["a", "b"],
            "series": [{"name": "x", "values": [1, 2]}],
        },
        {
            "type": "chart",
            "kind": "donut",
            "labels": ["a", "b"],
            "series": [{"values": [1, 3]}],
        },
        {
            "type": "compare",
            "items": [
                {"name": "A", "points": ["p"], "pick": True},
                {"name": "B"},
            ],
        },
        {
            "type": "stats",
            "items": [{"label": "Users", "value": "12k", "note": "+3%"}],
        },
        {"type": "steps", "items": [{"title": "One", "text": "first"}]},
        {
            "type": "flow",
            "nodes": [{"label": "In", "detail": "start"}, {"label": "Out"}],
        },
        {"type": "form", "fields": [{"id": "x", "label": "Name"}]},
        {"type": "checklist", "items": ["Pack"]},
    ]
    shown = visuals.for_terminal(block(*kinds))

    assert "```ui" not in shown  # nosec B101
    for word in (
        "(recommended)",
        "**12k** Users",
        "1. **One**: first",
        "**In** → **Out**",
        "- Name:",
        "- [ ] Pack",
        "(75%)",
    ):
        assert word in shown  # nosec B101


def test_half_written_or_foreign_blocks_stay_as_code():
    streaming = '```ui\n{"type": "chart", "labels": ['
    assert visuals.for_terminal(streaming) == streaming  # nosec B101
    unknown = block({"type": "hologram"})
    assert visuals.for_terminal(unknown) == unknown  # nosec B101


def test_speech_says_the_choices_and_skips_the_numbers():
    text = voice.for_speech(
        f"Here you go.\n\n{block(CHART)}\n\n{block(CHOICES)}"
    )

    assert "Which one? Faster, Cheaper or Both." in text  # nosec B101
    assert "Sales, on screen." in text and "20" not in text  # nosec B101
