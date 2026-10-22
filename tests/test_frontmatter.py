# pylint: disable=C0114,C0116

from flash import frontmatter

POST = """---
title: "Shipping a tiny search engine"
date: 2026-10-22
description: How I built it in a weekend,
  and what broke.
tags: [search, rust, weekend]
categories:
  - engineering
  - notes
---

# Intro
"""


def test_fields_lists_and_wrapped_text():
    fields, rest = frontmatter.split(POST)
    assert fields == [  # nosec B101
        ("title", ["Shipping a tiny search engine"]),
        ("date", ["2026-10-22"]),
        ("description", ["How I built it in a weekend, and what broke."]),
        ("tags", ["search", "rust", "weekend"]),
        ("categories", ["engineering", "notes"]),
    ]
    assert rest == "\n# Intro\n"  # nosec B101


def test_only_at_the_top_and_only_fields():
    assert frontmatter.split("Text\n---\ntitle: x\n---\n") is None  # nosec
    assert frontmatter.split("---\nJust a rule\n---\n") is None  # nosec B101
    assert frontmatter.split("---\ntitle: x\nwords\n---") is None  # nosec
    assert frontmatter.split("---\n---\n") is None  # nosec B101


def test_terminal_table_with_the_first_field_as_its_head():
    text = frontmatter.for_terminal(
        "---\ntitle: A | B\ntags: [x, y]\n---\nBody"
    )
    assert text == (  # nosec B101
        "| title | A \\| B |\n| --- | --- |\n| tags | x · y |\n\nBody"
    )


def test_text_without_front_matter_is_left_alone():
    assert frontmatter.for_terminal("--- \nhello") == "--- \nhello"  # nosec
