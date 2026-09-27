"""Tests for bringing memory over from another assistant."""

from flash import memory

REPLY = """Sure! Here is everything I have saved about you:

```text
- Prefers type hints written with Union, never the pipe
- [2025-03-02] - Lives in Oregon
1. Works on the Flash CLI
Preferences:
## Tools
* Uses   Ollama on a Mac
- Lives in oregon
```

That's the complete set."""


class TestParsing:
    def test_only_the_code_block_counts(self):
        assert memory.parse_import(REPLY) == [
            "Prefers type hints written with Union, never the pipe",
            "Lives in Oregon",
            "Works on the Flash CLI",
            "Uses Ollama on a Mac",
        ]

    def test_a_reply_with_no_code_block_is_read_whole(self):
        assert memory.parse_import("- Likes tea\n- Hates meetings") == [
            "Likes tea", "Hates meetings",
        ]

    def test_an_unclosed_block_still_counts(self):
        assert memory.parse_import("```\n- Likes tea\n") == ["Likes tea"]

    def test_long_facts_and_long_lists_are_capped(self):
        facts = memory.parse_import(
            "\n".join(f"- fact {n} " + "x" * 900 for n in range(300))
        )

        assert len(facts) == memory.MAX_IMPORTED
        assert all(len(f) <= memory.MAX_ENTRY_CHARS for f in facts)

    def test_nothing_in_nothing_out(self):
        assert memory.parse_import("") == []
        assert memory.parse_import("```\n\n```") == []


class TestImporting:
    def test_new_facts_are_added_as_written(self):
        memory.add_memory("Lives in Oregon")

        added, skipped = memory.import_memory(REPLY)

        assert skipped == 1
        assert "Lives in Oregon" not in added
        assert memory.list_memory() == ["Lives in Oregon", *added]
        assert len(added) == 3

    def test_importing_twice_adds_nothing(self):
        memory.import_memory(REPLY)
        before = memory.list_memory()

        added, skipped = memory.import_memory(REPLY)

        assert added == [] and skipped == 4
        assert memory.list_memory() == before

    def test_the_prompt_asks_for_what_the_parser_reads(self):
        assert "code block" in memory.IMPORT_PROMPT
        assert '"- "' in memory.IMPORT_PROMPT


class TestEditing:
    def test_an_entry_is_changed_in_place(self):
        memory.add_memory("Uses npm")
        memory.add_memory("Lives in Oregon")

        memory.edit_memory(1, "  Uses pnpm,\n not npm ")

        assert memory.list_memory() == [
            "Uses pnpm, not npm", "Lives in Oregon",
        ]

    def test_a_missing_or_blank_entry_is_refused(self):
        import pytest

        memory.add_memory("Uses npm")

        with pytest.raises(IndexError, match="No memory at index 2"):
            memory.edit_memory(2, "x")
        with pytest.raises(ValueError, match="Forget it instead"):
            memory.edit_memory(1, "   ")
        assert memory.list_memory() == ["Uses npm"]
