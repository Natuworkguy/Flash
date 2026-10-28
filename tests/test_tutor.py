"""The tutor's graph of propositions and knowledge tracing."""

import pytest

from flash import tools, tutor

DAY = tutor.DAY
PROPS = [
    {"id": "slope", "text": "Slope is rise over run."},
    {"id": "limit", "text": "A limit is the value a function nears."},
    {"id": "derivative", "text": "A derivative is the slope at a point.",
     "needs": ["slope", "limit"]},
]


def test_plan_teaches_groundwork_first():
    tutor.add("Calculus", PROPS)
    ids = [s["id"] for s in tutor.plan("calculus", now=0)]
    assert set(ids) == {"slope", "limit"}
    assert all(s["do"] == "teach" for s in tutor.plan("Calculus", now=0))


def test_measure_moves_knowledge_and_unlocks():
    tutor.add("Calculus", PROPS)
    for _ in range(4):
        tutor.measure("Calculus", "slope", True, now=0)
        tutor.measure("Calculus", "limit", True, now=0)
    plan = tutor.plan("Calculus", now=0)
    assert [s["id"] for s in plan] == ["derivative"]
    said = tutor.measure("Calculus", "derivative", False, now=0)
    assert said["known"] < tutor.KNOWN and said["review_in_days"] == 1
    assert tutor.plan("Calculus", now=0)[0]["do"] == "practice"


def test_trace():
    assert tutor.trace(0.5, True) > 0.5 > tutor.trace(0.5, False)


def test_reviews_come_due_with_widening_gaps():
    tutor.add("S", [{"id": "a", "text": "A."}])
    days = [tutor.measure("S", "a", True, now=0)["review_in_days"]
            for _ in range(5)]
    assert days == [1, 2, 4, 8, 16]
    assert tutor.plan("S", now=0) == []
    due = tutor.plan("S", now=17 * DAY)
    assert due and due[0]["do"] == "review"


def test_bad_graphs_are_refused():
    with pytest.raises(tutor.TutorError, match="not given"):
        tutor.add("S", [{"id": "a", "text": "A.", "needs": ["b"]}])
    with pytest.raises(tutor.TutorError, match="loop"):
        tutor.add("S", [{"id": "a", "text": "A.", "needs": ["b"]},
                        {"id": "b", "text": "B.", "needs": ["a"]}])
    with pytest.raises(tutor.TutorError, match="no subject"):
        tutor.plan("Nothing")


def test_describe_orders_groundwork_first():
    tutor.add("Calculus", PROPS)
    held = tutor.describe("Calculus")
    order = [p["id"] for p in held["propositions"]]
    assert order.index("derivative") > order.index("slope")
    assert (held["known"], held["of"]) == (0, 3)
    tutor.forget("Calculus")
    assert tutor.subjects() == []


def test_tool():
    run = tools.FUNCTIONS["tutor"]
    assert run(action="add", subject="Calc", propositions=PROPS).startswith(
        "Added 3")
    assert "teach slope" in run(action="plan", subject="Calc")
    assert "known" in run(action="measure", subject="Calc", id="slope",
                          right=True)
    assert "0 of 3 known" in run(action="graph", subject="Calc")
    assert run(action="plan", subject="Nope").startswith("Error")
    assert "tutor" in [t["function"]["name"] for t in tools.tools]


# Subjects learned by doing -------------------------------------------------

import shutil  # noqa: E402

HELLO_C = {"id": "printf", "text": "printf prints formatted text.",
           "exercise": {"task": "Print hello, 42.", "language": "C",
                        "expect": "hello, 42\n"}}
LINUX = {"id": "mkdir-mv", "text": "mkdir makes a folder; mv moves files.",
         "exercise": {"task": "Put notes.txt in a new folder, archive.",
                      "language": "linux",
                      "files": {"notes.txt": "keep me"},
                      "expect": "notes.txt\ngone",
                      "verify": ("ls archive; "
                                 "test ! -e notes.txt && echo gone")}}


def test_exercises_are_checked_when_added():
    with pytest.raises(tutor.TutorError, match="task and a language"):
        tutor.add("C", [{"id": "a", "text": "A.",
                         "exercise": {"task": "x", "language": "cobol"}}])
    with pytest.raises(tutor.TutorError, match="inside"):
        tutor.add("C", [{"id": "a", "text": "A.", "exercise": {
            "task": "x", "language": "c", "files": {"../x": ""}}}])
    with pytest.raises(tutor.TutorError, match="regex"):
        tutor.add("C", [{"id": "a", "text": "A.", "exercise": {
            "task": "x", "language": "c", "expect": "(", "match": "regex"}}])
    tutor.add("C", [HELLO_C])
    assert tutor.exercise("C", "printf")["language"] == "c"
    assert tutor.plan("C", now=0)[0]["exercise"]["task"] == "Print hello, 42."


def test_matches():
    assert tutor.matches("a\nb\n", "a  \r\nb")
    assert not tutor.matches("a", "a b")
    assert tutor.matches("42", "the answer is 42", "contains")
    assert tutor.matches(r"^\d+$", "x\n17\n", "regex")


@pytest.mark.skipif(not shutil.which("cc"), reason="needs a C compiler")
def test_c_answers_are_compiled_run_and_measured():
    tutor.add("C", [HELLO_C])
    good = tutor.check("C", "printf", '#include <stdio.h>\n'
                       'int main(void){printf("hello, %d\\n", 42);}\n', now=0)
    assert good["right"] and good["measured"]["known"] > tutor.START
    broken = tutor.check("C", "printf", "int main(void){ return x; }", now=0)
    assert not broken["right"] and not broken["built"]
    assert "x" in broken["stderr"]
    wrong = tutor.check("C", "printf", '#include <stdio.h>\n'
                        'int main(void){puts("hello");}\n', now=0)
    assert wrong["built"] and not wrong["right"]
    assert wrong["measured"]["review_in_days"] == 1


@pytest.mark.skipif(not shutil.which("cc"), reason="needs a C compiler")
def test_a_loop_that_never_ends_is_stopped(monkeypatch):
    monkeypatch.setattr(tutor, "RUN_SECONDS", 1)
    tutor.add("C", [HELLO_C])
    said = tutor.check("C", "printf", "int main(void){for(;;);}", now=0)
    assert said["timed_out"] and not said["right"]


def test_shell_answers_are_verified_by_what_they_changed():
    tutor.add("Linux", [LINUX])
    good = tutor.check("Linux", "mkdir-mv",
                       "mkdir archive\nmv notes.txt archive/\n", now=0)
    assert good["right"], good
    assert good["stdout"] == "notes.txt\ngone\n"
    wrong = tutor.check("Linux", "mkdir-mv", "mkdir archive\n", now=0)
    assert not wrong["right"]


def test_missing_toolchains_measure_nothing(monkeypatch):
    tutor.add("C", [HELLO_C])
    monkeypatch.setattr(tutor.shutil, "which", lambda _: None)
    with pytest.raises(tutor.TutorError, match="not installed"):
        tutor.check("C", "printf", "int main(){}")
    assert tutor.describe("C")["propositions"][0]["known"] == tutor.START
    with pytest.raises(tutor.TutorError, match="no exercise"):
        tutor.add("S", [{"id": "a", "text": "A."}])
        tutor.check("S", "a", "x")


@pytest.mark.parametrize("lang,code,out", [
    ("python", "print(6 * 7)", "42"),
    ("cpp", "#include <iostream>\nint main(){std::cout << 42;}", "42"),
    ("javascript", "console.log(6 * 7)", "42"),
    ("rust", "fn main(){ println!(\"{}\", 6 * 7); }", "42"),
    ("go", "package main\nimport \"fmt\"\nfunc main(){ fmt.Println(42) }",
     "42"),
    ("java", "class Main { public static void main(String[] a) {"
     " System.out.println(42); } }", "42"),
])
def test_every_language_runs(lang, code, out):
    if any(not shutil.which(n) for n in tutor.LANGUAGES[lang]["needs"]):
        pytest.skip(f"{lang} is not installed")
    ran = tutor.run(lang, code)
    assert ran["built"] and ran["stdout"].strip() == out, ran


def test_check_tool(monkeypatch):
    monkeypatch.setattr(tools, "_user_allows", lambda *a, **_: True)
    run = tools.FUNCTIONS["tutor"]
    run(action="add", subject="Linux", propositions=[LINUX])
    assert "exercise (shell)" in run(action="plan", subject="Linux")
    out = run(action="check", subject="Linux", id="mkdir-mv",
              answer="mkdir archive && mv notes.txt archive")
    assert out.startswith("Right.") and "known" in out
    out = run(action="check", subject="Linux", id="mkdir-mv", answer="ls")
    assert out.startswith("Not yet.") and "A right answer prints" in out
    monkeypatch.setattr(tools, "_user_allows", lambda *a, **_: False)
    assert "said no" in run(action="check", subject="Linux", id="mkdir-mv",
                            answer="ls")
