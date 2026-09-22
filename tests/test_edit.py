"""Tests for the exact-string edit engine."""

from flash.edit import (
    MAX_EDITS,
    Edit,
    apply_edits,
    apply_one,
    find_matches,
    nearest,
)

SOURCE = """def greet(name):
    if not name:
        return "hello"
    return f"hello {name}"
"""


def test_replaces_a_unique_block():
    result = apply_one(SOURCE, Edit('return "hello"', 'return "hi"'))

    assert result.ok
    assert 'return "hi"' in result.text
    assert result.replacements == 1
    assert result.notes == []


def test_keeps_the_rest_of_the_file_byte_for_byte():
    result = apply_one(SOURCE, Edit('"hello"', '"hi"'))

    assert result.text == SOURCE.replace('"hello"', '"hi"', 1)


def test_missing_text_is_an_error_not_a_silent_no_op():
    result = apply_one(SOURCE, Edit("return 'howdy'", "x"))

    assert not result.ok
    assert result.text == ""
    assert "not found" in result.error


def test_a_near_miss_quotes_the_real_text_back():
    result = apply_one(SOURCE, Edit('    return "helo"', "x"))

    assert not result.ok
    assert 'return "hello"' in result.error


def test_a_wild_miss_says_so_instead_of_guessing():
    result = apply_one(SOURCE, Edit("import antigravity", "x"))

    assert not result.ok
    assert "comes close" in result.error


def test_ambiguous_text_is_refused_with_the_count():
    text = "a = 1\nb = 1\n"
    result = apply_one(text, Edit("= 1", "= 2"))

    assert not result.ok
    assert "2 times" in result.error
    assert "replace_all" in result.error


def test_replace_all_changes_every_occurrence():
    text = "a = 1\nb = 1\n"
    result = apply_one(text, Edit("= 1", "= 2", replace_all=True))

    assert result.ok
    assert result.text == "a = 2\nb = 2\n"
    assert result.replacements == 2


def test_empty_old_string_points_at_write():
    result = apply_one(SOURCE, Edit("", "x"))

    assert not result.ok
    assert "write tool" in result.error


def test_identical_strings_are_refused():
    result = apply_one(SOURCE, Edit("hello", "hello"))

    assert not result.ok
    assert "identical" in result.error


def test_self_overlapping_text_counts_once_per_occurrence():
    result = apply_one("aaa\n", Edit("aa", "b"))

    assert result.ok
    assert result.text == "ba\n"


class TestIndentationTolerance:
    """A block copied out of a read result, minus its indentation."""

    def test_a_dedented_block_still_matches(self):
        result = apply_one(SOURCE, Edit(
            'if not name:\n    return "hello"',
            'if not name:\n    return "nobody"',
        ))

        assert result.ok
        assert '        return "nobody"' in result.text
        assert "indentation" in result.notes[0]

    def test_the_file_keeps_its_own_indentation(self):
        result = apply_one(SOURCE, Edit(
            'if not name:\n    return "hello"',
            'if not name:\n    return "nobody"',
        ))

        for line in result.text.splitlines():
            assert not line.startswith(" return")
        assert '    if not name:' in result.text

    def test_an_over_indented_block_still_matches(self):
        result = apply_one(SOURCE, Edit(
            "        def greet(name):", "        def hi(name):"
        ))

        assert result.ok
        assert result.text.startswith("def hi(name):")

    def test_trailing_whitespace_does_not_break_the_match(self):
        text = "x = 1   \ny = 2\n"
        result = apply_one(text, Edit("x = 1", "x = 9"))

        assert result.ok
        assert result.text.startswith("x = 9")

    def test_an_exact_match_wins_over_a_shifted_one(self):
        text = "    x = 1\nx = 1\n"
        result = apply_one(text, Edit("    x = 1", "    x = 2"))

        # Line 2 would match too once indentation may shift, which
        # would make this ambiguous. The exact hit on line 1 has to win
        # outright so the shifted candidate never gets a vote.
        assert result.ok
        assert result.text == "    x = 2\nx = 1\n"
        assert result.notes == []

    def test_an_edit_spanning_a_line_boundary_is_not_double_counted(self):
        # 'return "hello"' sits inside the indented line as a plain
        # substring as well as standing alone on the next one, so this
        # really is ambiguous and has to be refused rather than guessed.
        text = '    return "hello"\nreturn "hello"\n'
        result = apply_one(text, Edit('return "hello"', 'return "hi"'))

        assert not result.ok
        assert "2 times" in result.error

    def test_mixed_tabs_and_spaces_are_refused(self):
        text = "\tx = 1\n"
        result = apply_one(text, Edit("    x = 1", "    x = 2"))

        assert not result.ok

    def test_a_blank_line_inside_the_block_is_allowed(self):
        text = "def f():\n    a = 1\n\n    b = 2\n"
        result = apply_one(text, Edit(
            "a = 1\n\nb = 2", "a = 1\n\nb = 3"
        ))

        assert result.ok
        assert "    b = 3" in result.text

    def test_a_shifted_block_is_not_matched_twice(self):
        text = "  x = 1\n  x = 1\n"
        result = apply_one(text, Edit("x = 1", "x = 2"))

        assert not result.ok
        assert "2 times" in result.error


class TestMultiEdit:
    def test_applies_every_edit_in_order(self):
        result = apply_edits(SOURCE, [
            Edit("def greet", "def hello"),
            Edit('return "hello"', 'return "hi"'),
        ])

        assert result.ok
        assert "def hello(name):" in result.text
        assert 'return "hi"' in result.text
        assert result.replacements == 2

    def test_a_later_edit_sees_an_earlier_one(self):
        result = apply_edits("a\n", [
            Edit("a", "b"),
            Edit("b", "c"),
        ])

        assert result.ok
        assert result.text == "c\n"

    def test_one_failure_abandons_the_whole_batch(self):
        result = apply_edits(SOURCE, [
            Edit("def greet", "def hello"),
            Edit("nothing like this", "x"),
        ])

        assert not result.ok
        assert result.text == ""
        assert "edit 2 of 2" in result.error
        assert "unchanged" in result.error

    def test_a_single_edit_reports_its_own_error_plainly(self):
        result = apply_edits(SOURCE, [Edit("missing", "x")])

        assert not result.ok
        assert "edit 1 of 1" not in result.error

    def test_no_edits_is_an_error(self):
        assert not apply_edits(SOURCE, []).ok

    def test_too_many_edits_is_refused(self):
        edits = [Edit(f"x{i}", f"y{i}") for i in range(MAX_EDITS + 1)]
        result = apply_edits(SOURCE, edits)

        assert not result.ok
        assert str(MAX_EDITS) in result.error


class TestHelpers:
    def test_find_matches_on_empty_old_returns_nothing(self):
        assert find_matches("abc", "") == []

    def test_find_matches_reports_offsets(self):
        matches = find_matches("abcabc", "abc")

        assert [(m.start, m.end) for m in matches] == [(0, 3), (3, 6)]

    def test_nearest_returns_none_when_nothing_is_close(self):
        assert nearest("a\nb\nc\n", "zzzzzzzzzz") is None

    def test_nearest_finds_the_closest_line(self):
        found = nearest(SOURCE, '    return "helo"')

        assert found is not None
        assert "hello" in found

    def test_nearest_handles_a_block_longer_than_the_file(self):
        assert nearest("a\n", "a\nb\nc\nd\n") is None
