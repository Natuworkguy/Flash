"""Tests for the token budget that decides what the model still sees."""

from flash import context


def user(text):
    return {"role": "user", "content": text}


def assistant(text, calls=None):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {"function": {"name": name, "arguments": {}}} for name in calls
        ]
    return message


def tool(name, text):
    return {"role": "tool", "content": text, "tool_name": name}


class TestEstimates:
    def test_empty_text_costs_nothing(self):
        assert context.estimate_tokens("") == 0

    def test_longer_text_costs_more(self):
        assert context.estimate_tokens("a" * 400) > \
            context.estimate_tokens("a" * 40)

    def test_a_message_costs_more_than_its_text(self):
        assert context.message_tokens(user("hello")) > \
            context.estimate_tokens("hello")

    def test_an_image_dominates_the_cost(self):
        plain = context.message_tokens(user("hi"))
        with_image = context.message_tokens(
            {"role": "user", "content": "hi", "images": [b"..."]}
        )

        assert with_image - plain >= context.TOKENS_PER_IMAGE

    def test_tool_call_arguments_are_counted(self):
        bare = context.message_tokens(assistant("", None))
        calling = context.message_tokens(assistant("", ["grep"]))

        assert calling > bare

    def test_total_adds_up_the_messages(self):
        messages = [user("a"), assistant("b")]

        assert context.total_tokens(messages) == sum(
            context.message_tokens(m) for m in messages
        )


class TestBudget:
    def test_a_bigger_window_buys_more_history(self):
        small = context.history_budget(4096)
        large = context.history_budget(128000)

        assert large > small

    def test_the_system_prompt_and_reply_come_off_the_top(self):
        plain = context.history_budget(32000)
        loaded = context.history_budget(
            32000, system_tokens=8000, output_tokens=4000
        )

        assert loaded < plain

    def test_an_unknown_window_still_gives_a_usable_budget(self):
        assert context.history_budget(None) >= context.MIN_HISTORY_TOKENS

    def test_a_tiny_window_never_goes_to_zero(self):
        budget = context.history_budget(
            512, system_tokens=4000, output_tokens=4000
        )

        assert budget == context.MIN_HISTORY_TOKENS


class TestBlocks:
    def test_each_user_message_starts_a_block(self):
        grouped = context.blocks([
            user("one"), assistant("a"),
            user("two"), assistant("b"),
        ])

        assert len(grouped) == 2

    def test_tool_traffic_stays_with_its_user_message(self):
        grouped = context.blocks([
            user("one"),
            assistant("", ["grep"]),
            tool("grep", "match"),
            assistant("done"),
        ])

        assert len(grouped) == 1
        assert len(grouped[0]) == 4

    def test_messages_before_the_first_user_message_lead(self):
        grouped = context.blocks([assistant("a"), user("one")])

        assert len(grouped) == 2
        assert grouped[0] == [assistant("a")]

    def test_no_messages_makes_no_blocks(self):
        assert context.blocks([]) == []


class TestTrim:
    def test_history_inside_the_budget_is_left_alone(self):
        messages = [user("one"), assistant("a")]
        result = context.trim(messages, 10000)

        assert result.kept == messages
        assert not result.lost

    def test_the_oldest_blocks_go_first(self):
        messages = []
        for index in range(10):
            messages.append(user(f"question {index} " + "x" * 400))
            messages.append(assistant(f"answer {index}"))

        result = context.trim(messages, 400)

        assert result.lost
        assert result.kept[-1] == messages[-1]
        assert "question 0" not in context.transcript(result.kept)

    def test_a_tool_result_is_never_orphaned_from_its_call(self):
        messages = []
        for index in range(8):
            messages.extend([
                user(f"q{index} " + "x" * 300),
                assistant("", ["grep"]),
                tool("grep", "m" * 300),
                assistant(f"a{index}"),
            ])

        result = context.trim(messages, 500)

        for position, message in enumerate(result.kept):
            if message.get("role") == "tool":
                before = result.kept[position - 1] if position else {}
                assert before.get("tool_calls")

    def test_the_newest_block_survives_even_when_oversized(self):
        messages = [user("q"), assistant("a"), user("x" * 100000)]
        result = context.trim(messages, 100)

        assert messages[-1] in result.kept

    def test_an_oversized_block_sheds_its_tool_output_first(self):
        messages = [
            user("please search"),
            assistant("", ["grep"]),
            tool("grep", "m" * 20000),
            assistant("here is the answer"),
        ]

        result = context.trim(messages, 200)

        assert not any(m.get("role") == "tool" for m in result.kept)
        assert messages[0] in result.kept

    def test_nothing_in_makes_nothing_out(self):
        result = context.trim([], 1000)

        assert result.kept == []
        assert not result.lost

    def test_the_dropped_messages_are_handed_back(self):
        messages = []
        for index in range(6):
            messages.append(user(f"q{index} " + "x" * 400))
            messages.append(assistant(f"a{index}"))

        result = context.trim(messages, 400)

        assert result.dropped
        assert len(result.dropped) + len(result.kept) == len(messages)

    def test_the_reported_token_count_matches_what_was_kept(self):
        messages = [user("one"), assistant("a")]
        result = context.trim(messages, 10000)

        assert result.tokens == context.total_tokens(result.kept)


class TestSummary:
    def test_a_transcript_names_each_speaker(self):
        text = context.transcript([user("hello"), assistant("hi")])

        assert "user: hello" in text
        assert "assistant: hi" in text

    def test_a_transcript_records_which_tools_were_called(self):
        text = context.transcript([assistant("", ["grep", "read"])])

        assert "grep, read" in text

    def test_a_transcript_labels_tool_results_by_tool(self):
        text = context.transcript([tool("grep", "three matches")])

        assert "tool:grep: three matches" in text

    def test_a_summary_request_carries_the_instruction(self):
        request = context.summary_request([user("hello")])

        assert request[0]["role"] == "system"
        assert "Summarize" in request[0]["content"]
        assert "hello" in request[1]["content"]

    def test_a_summary_is_recognisable_afterwards(self):
        message = context.summary_message("we fixed the parser")

        assert context.is_summary(message)
        assert not context.is_summary(user("we fixed the parser"))

    def test_merging_puts_the_summary_first(self):
        merged = context.merge_summary([user("latest")], "earlier work")

        assert context.is_summary(merged[0])
        assert merged[1] == user("latest")

    def test_a_second_summary_replaces_the_first(self):
        once = context.merge_summary([user("a")], "first summary")
        twice = context.merge_summary(once, "second summary")

        assert sum(1 for m in twice if context.is_summary(m)) == 1
        assert "second summary" in twice[0]["content"]

    def test_an_empty_summary_adds_nothing(self):
        merged = context.merge_summary([user("a")], "   ")

        assert merged == [user("a")]
