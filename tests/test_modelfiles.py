# pylint: disable=C0114,C0115,C0116

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
MODELFILES = sorted(MODELS.glob("*.Modelfile"))
FLAGSHIP = MODELS / "flash-onyx-2.5.Modelfile"

# The recap at the foot of the prompt repeats the rules above it by
# design, so the duplication check stops where it starts.
RECAP = "IF YOU REMEMBER NOTHING ELSE"

# Every way an em-dash reaches a file: the character itself, the two
# HTML entities, and the escape people paste out of a JSON string. The
# last one is four literal characters, not the dash.
DASHES = ("—", "&mdash;", "&#8212;", "\\u2014")

CODE_SPAN = re.compile(r"`[^`]*`")
PARAMETER = re.compile(r'^PARAMETER \w+ (-?[\d.]+|".*")$')
SECTION = re.compile(r"^[A-Z][A-Z0-9 ,/&:-]*$")


def _build():
    """Import models/build.py, which sits outside any package."""

    spec = importlib.util.spec_from_file_location(
        "flash_models_build", MODELS / "build.py"
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {MODELS / 'build.py'}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


build = _build()


def _prose(line):
    """LINE with its backticked spans removed.

    The prompt quotes the things it bans (`&mdash;`, `**`) in order to
    ban them, so the rules only apply to what is not in backticks.
    """

    return CODE_SPAN.sub("", line)


def _body(path):
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", MODELFILES, ids=lambda p: p.name)
def test_every_modelfile_is_plain_ascii(path):
    # Catches the em-dash, the curly quote, and the one-character
    # ellipsis in one check: the prompt bans all three, and a model
    # cannot follow a rule its own instructions break.
    stray = {
        (index, character)
        for index, character in enumerate(_body(path))
        if ord(character) > 126
    }

    assert not stray  # nosec B101


@pytest.mark.parametrize("path", MODELFILES, ids=lambda p: p.name)
def test_no_em_dash_in_any_spelling(path):
    for number, line in enumerate(_body(path).splitlines(), 1):
        for dash in DASHES:
            assert dash not in _prose(line), f"{path.name}:{number}"  # nosec


@pytest.mark.parametrize("path", MODELFILES, ids=lambda p: p.name)
def test_no_bold_markers(path):
    for number, line in enumerate(_body(path).splitlines(), 1):
        assert "**" not in _prose(line), f"{path.name}:{number}"  # nosec


@pytest.mark.parametrize("path", MODELFILES, ids=lambda p: p.name)
def test_backticks_close_on_the_line_that_opened_them(path):
    # An unclosed backtick swallows the rest of the rule when the model
    # renders it, and it is invisible in a 700 line file.
    for number, line in enumerate(_body(path).splitlines(), 1):
        assert line.count("`") % 2 == 0, f"{path.name}:{number}"  # nosec


@pytest.mark.parametrize("path", MODELFILES, ids=lambda p: p.name)
def test_whitespace_is_clean(path):
    raw = path.read_bytes()
    body = _body(path)

    assert b"\r\n" not in raw  # nosec B101
    assert "\t" not in body  # nosec B101
    assert body.endswith("\n")  # nosec B101
    assert not [  # nosec B101
        number
        for number, line in enumerate(body.splitlines(), 1)
        if line != line.rstrip()
    ]


@pytest.mark.parametrize("path", MODELFILES, ids=lambda p: p.name)
def test_header_names_the_file_it_sits_in(path):
    name, sizes, _cloud = build.header(_body(path), path)

    assert name == path.stem  # nosec B101
    assert all(size == size.lower() for size in sizes)  # nosec B101


@pytest.mark.parametrize("path", MODELFILES, ids=lambda p: p.name)
def test_one_from_line_and_one_system_block(path):
    body = _body(path)

    assert len(build.FROM_PATTERN.findall(body)) == 1  # nosec B101
    assert body.count('SYSTEM """') == 1  # nosec B101
    assert body.count('"""') == 2  # nosec B101


@pytest.mark.parametrize("path", MODELFILES, ids=lambda p: p.name)
def test_no_license_block_is_written_by_hand(path):
    # build.py appends the repository license and the base model's
    # terms; one already in the file makes it skip both.
    assert not build.LICENSE_PATTERN.search(_body(path))  # nosec B101


@pytest.mark.parametrize("path", MODELFILES, ids=lambda p: p.name)
def test_every_parameter_line_parses(path):
    for number, line in enumerate(_body(path).splitlines(), 1):
        if line.startswith("PARAMETER"):
            assert PARAMETER.match(line), f"{path.name}:{number}"  # nosec


@pytest.mark.parametrize("path", MODELFILES, ids=lambda p: p.name)
def test_the_name_placeholder_is_used_and_renders_away(path):
    body = _body(path)
    name, _sizes, _cloud = build.header(body, path)

    assert build.NAME_PLACEHOLDER in body  # nosec B101

    rendered = build.render(body, name, "12b", "")

    assert build.NAME_PLACEHOLDER not in rendered  # nosec B101
    assert build.spoken(name) in rendered  # nosec B101


def test_the_flagship_never_says_the_same_thing_twice():
    # Eight words in a row repeated outside the recap is a rule that got
    # written twice, which is the one thing the prompt tells the model
    # not to do.
    lines = _body(FLAGSHIP).splitlines()
    seen = {}
    repeats = []

    for number, line in enumerate(lines, 1):
        if line.strip() == RECAP:
            break

        words = re.findall(r"[a-z']+", line.lower())

        for start in range(len(words) - 7):
            shingle = tuple(words[start:start + 8])

            if shingle in seen and seen[shingle] != number:
                repeats.append((seen[shingle], number, " ".join(shingle)))

            seen.setdefault(shingle, number)

    assert not repeats  # nosec B101


def test_the_flagship_section_headers_are_unique():
    headers = [
        line
        for line in _body(FLAGSHIP).splitlines()
        if SECTION.match(line) and line == line.strip()
    ]

    assert len(headers) == len(set(headers))  # nosec B101


def test_the_flagship_never_licenses_thinking_into_the_reply():
    # The one line that let the model print its whole plan instead of
    # acting on it. It cost a real session a turn; it stays out.
    body = _body(FLAGSHIP)

    assert "Think in the reply" not in body  # nosec B101


def test_header_reads_the_comment_block():
    source = "# name: demo\n# sizes: 4b, 12b\n# cloud-base: true\n\nFROM x\n"

    assert build.header(source, Path("demo")) == (  # nosec B101
        "demo",
        ["4b", "12b"],
        True,
    )


def test_header_stops_at_the_first_line_that_is_not_a_comment():
    source = "# name: demo\n\n# sizes: 4b\nFROM x\n"

    assert build.header(source, Path("demo")) == (  # nosec B101
        "demo",
        [],
        False,
    )


def test_header_needs_a_name():
    with pytest.raises(SystemExit):
        build.header("# sizes: 4b\n", Path("demo"))


def test_spoken_says_the_tag_out_loud():
    assert build.spoken("flash-onyx-2.5") == "Flash Onyx 2.5"  # nosec B101


def test_origin_reads_the_repo_off_the_from_line():
    assert (  # nosec B101
        build.origin("FROM gemma4:12b\n", Path("demo")) == "gemma4"
    )


def test_origin_needs_a_from_line():
    with pytest.raises(SystemExit):
        build.origin("# name: demo\n", Path("demo"))


def test_render_pins_the_size_and_leaves_the_rest_alone():
    source = "# name: demo\nFROM gemma4:12b\nSYSTEM \"\"\"{{name}}\"\"\"\n"

    rendered = build.render(source, "demo", "31b", "")

    assert "FROM gemma4:31b" in rendered  # nosec B101
    assert "gemma4:12b" not in rendered  # nosec B101
    assert "Demo" in rendered  # nosec B101


def test_render_without_a_size_keeps_the_from_line():
    source = "# name: demo\nFROM gemma4:12b\n"

    assert "FROM gemma4:12b" in build.render(  # nosec B101
        source, "demo", "", ""
    )


def test_notice_adds_the_repository_and_base_terms():
    rendered = build.notice("FROM gemma4:12b\n", "MIT-ISH TERMS")

    assert "LICENSE \"\"\"" in rendered  # nosec B101
    assert "MIT-ISH TERMS" in rendered  # nosec B101
    assert "Built on gemma4." in rendered  # nosec B101


def test_notice_leaves_a_license_the_file_already_has():
    body = 'FROM gemma4:12b\nLICENSE """mine"""\n'

    assert build.notice(body, "TERMS") == body  # nosec B101


def test_notice_says_nothing_without_terms():
    assert build.notice("FROM x\n", "") == "FROM x\n"  # nosec B101


def test_hosted_and_wrapper_name_the_cloud_tags():
    assert build.hosted("31b") == "31b-cloud"  # nosec B101
    assert build.hosted("") == "cloud"  # nosec B101
    assert build.wrapper("31b") == "31b-cloudbase"  # nosec B101
    assert build.wrapper("") == "cloudbase"  # nosec B101


def test_wanted_defaults_to_every_declared_size():
    assert build.wanted(  # nosec B101
        ["12b", "31b"], None, Path("demo"), False
    ) == ["12b", "31b"]


def test_wanted_rejects_a_size_the_header_never_declared():
    with pytest.raises(SystemExit):
        build.wanted(["12b"], ["31b"], Path("demo"), False)


def test_wanted_takes_a_cloudbase_tag_only_with_a_cloud_base():
    assert build.wanted(  # nosec B101
        ["31b"], ["31b-cloudbase"], Path("demo"), True
    ) == ["31b-cloudbase"]

    with pytest.raises(SystemExit):
        build.wanted(["31b"], ["31b-cloudbase"], Path("demo"), False)


def test_targets_builds_one_tag_per_size_without_a_cloud_base():
    assert build.targets(  # nosec B101
        ["12b", "31b"], "", False, Path("demo")
    ) == [("12b", "12b"), ("31b", "31b")]


def test_targets_adds_a_cloudbase_build_where_the_base_has_one(
    monkeypatch,
):
    monkeypatch.setattr(build, "published", lambda _repo, _tag: True)

    assert build.targets(  # nosec B101
        ["31b"], "gemma4", True, Path("demo")
    ) == [("31b", "31b"), ("31b-cloud", "31b-cloudbase")]


def test_targets_skips_a_cloud_tag_the_base_does_not_publish(monkeypatch):
    monkeypatch.setattr(build, "published", lambda _repo, _tag: False)

    assert build.targets(  # nosec B101
        ["12b"], "gemma4", True, Path("demo")
    ) == [("12b", "12b")]


def test_targets_refuses_a_cloudbase_asked_for_by_name(monkeypatch):
    monkeypatch.setattr(build, "published", lambda _repo, _tag: False)

    with pytest.raises(SystemExit):
        build.targets(["12b-cloudbase"], "gemma4", True, Path("demo"))


def test_our_tag_never_ends_in_the_suffix_ollama_resolves_remotely():
    # A name ending in -cloud sends Ollama to ollama.com looking for a
    # model that is not published there.
    for size in ("", "12b", "31b"):
        assert not build.wrapper(size).endswith(  # nosec B101
            build.CLOUD_SUFFIX
        )
