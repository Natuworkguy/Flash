# pylint: disable=C0114,C0115,C0116

from flash.latex import render_latex, render_math


def test_renders_the_canonical_dirichlet_integral():
    source = (
        r"\int_{0}^{\infty} \frac{\sin(tx)}{t} dt "
        r"= \frac{\pi}{2} \operatorname{sgn}(x)"
    )
    assert render_math(source) == "∫₀^∞ sin(tx)/t dt = " \
        "π/2 sgn(x)"


def test_subscripts_and_superscripts_use_unicode():
    assert render_math(r"x_1^2") == "x₁²"
    assert render_math(r"\sum_{n=1}^{10} n") == "∑ₙ₌₁" \
        "¹⁰ n"


def test_scripts_fall_back_when_unicode_has_no_glyph():
    assert render_math(r"e^{i\pi}") == "e^(iπ)"
    assert render_math(r"x^\infty") == "x^∞"


def test_fractions_bracket_only_where_precedence_needs_it():
    assert render_math(r"\frac{\pi}{2}") == "π/2"
    assert render_math(r"\frac{a+b}{c}") == "(a + b)/c"
    assert render_math(r"\frac{1}{n+1}") == "1/(n + 1)"


def test_roots_greek_and_blackboard_letters():
    assert render_math(r"\sqrt{2}") == "√2"
    assert render_math(r"\sqrt[3]{x}") == "∛x"
    assert render_math(r"\sqrt{b^2 - 4ac}") == "√(b² − 4ac)"
    assert render_math(r"\alpha \in \mathbb{R}") == "α ∈ ℝ"


def test_leading_sign_is_unary():
    assert render_math(r"x = -b") == "x = −b"
    assert render_math(r"a - b") == "a − b"


def test_row_separators_become_line_breaks():
    source = r"\begin{aligned} a &= b \\ c &= d \end{aligned}"
    assert render_math(source) == "a = b\nc = d"


def test_unknown_macros_degrade_to_their_name():
    assert render_math(r"\wobble{x}") == "wobblex"


def test_display_math_becomes_its_own_paragraph():
    out = render_latex("Result:\n\n$$\\frac{\\pi}{2}$$\n\nDone.")
    assert out == "Result:\n\nπ/2\n\nDone."


def test_inline_delimiters_are_replaced_in_place():
    assert render_latex(r"holds for $x \neq 0$ only") == \
        "holds for x ≠ 0 only"
    assert render_latex(r"and \(y^2\) too") == "and y² too"


def test_currency_is_left_alone():
    text = "It costs $5 and $7, or $12 total."
    assert render_latex(text) == text


def test_code_spans_and_fences_are_untouched():
    text = "Use `$\\pi$` here.\n\n```\n$$\\frac{1}{2}$$\n```\n"
    assert render_latex(text) == text


def test_plain_prose_passes_through_unchanged():
    text = "No math here at all."
    assert render_latex(text) == text


def test_markdown_specials_in_output_are_escaped():
    assert render_latex(r"$a \ast b$") == r"a \* b"
