"""Skills passed to someone as a code, and added back from one."""

import pytest

from flash import skills, tools


@pytest.fixture(autouse=True)
def _own_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "skills_dir", lambda: tmp_path / "skills")


def _make(name="ship-it", body="1. Bump the version.\n2. Tag it: `git tag v1`.\n\nÜnïcödé survives."):
    skills.manage("create", name, description="Cut a release", content=body, by="you")


def test_a_skill_survives_the_trip_through_a_code():
    _make()
    code = skills.share_code("ship-it")
    assert code.startswith(skills.CODE_PREFIX) and " " not in code
    skills.remove("ship-it")
    assert skills.add_from_code(code) == "Added skill ship-it: Cut a release"
    back = skills.find("ship-it", guides=False)
    assert back.body.startswith("1. Bump") and "Ünïcödé" in back.body and back.by == "you" and not back.managed


def test_a_code_pasted_with_line_breaks_and_words_round_it_still_works():
    _make()
    code = skills.share_code("ship-it")
    wrapped = "here you go:\n" + "\n".join(code[i:i + 30] for i in range(0, len(code), 30)) + "\n"
    assert skills.add_from_code(wrapped, name="ship-it-too").startswith("Added skill ship-it-too")


def test_a_name_already_taken_is_kept_unless_replaced():
    _make()
    code = skills.share_code("ship-it")
    with pytest.raises(skills.SkillError, match="already have"):
        skills.add_from_code(code)
    assert skills.add_from_code(code, replace=True).startswith("Replaced")


@pytest.mark.parametrize("bad, said", [("hello", "not a skill code"), (skills.CODE_PREFIX + "abc", "damaged"),
                                       (skills.CODE_PREFIX, "damaged")])
def test_what_is_not_a_whole_code_says_so(bad, said):
    with pytest.raises(skills.SkillError, match=said):
        skills.add_from_code(bad)


def test_the_tool_shares_and_adds():
    _make()
    run = tools.FUNCTIONS["skill_manage"]
    said = run(action="share", name="ship-it")
    code = said.split("\n")[-1]
    assert code.startswith(skills.CODE_PREFIX)
    assert run(action="add", name="from-a-friend", content=code).startswith("Added skill from-a-friend")
    assert run(action="add", content="nonsense").startswith("Error")
