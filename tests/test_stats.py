# pylint: disable=C0114,C0115,C0116

from flash import ai, sysprompt
from flash.stats import Turn, summary
from flash.sysprompt import get_context_ceiling, get_context_limit

NS = 1_000_000_000


class _Response:
    def __init__(self, eval_count=0, eval_duration=0, total_duration=0,
                 prompt_eval_count=0):
        self.eval_count = eval_count
        self.eval_duration = eval_duration
        self.total_duration = total_duration
        self.prompt_eval_count = prompt_eval_count


def _plain(turn, limit=None):
    line = summary(turn, limit)
    return None if line is None else line.plain


def test_turn_sums_generation_across_calls():
    turn = Turn()
    turn.add(_Response(eval_count=100, eval_duration=10 * NS,
                       total_duration=12 * NS, prompt_eval_count=900))
    turn.add(_Response(eval_count=50, eval_duration=5 * NS,
                       total_duration=6 * NS, prompt_eval_count=1500))

    assert turn.generated == 150  # nosec B101
    assert turn.seconds == 18  # nosec B101
    assert turn.rate == 10  # nosec B101


def test_turn_counts_the_prompt_once_at_its_high_water_mark():
    # A tool round re-sends the prompt; counting it per round would tell
    # the user they spent tokens they never spent.
    turn = Turn()
    turn.add(_Response(eval_count=10, prompt_eval_count=8000))
    turn.add(_Response(eval_count=10, prompt_eval_count=8200))
    turn.add(_Response(eval_count=10, prompt_eval_count=8100))

    assert turn.prompt_tokens == 8200  # nosec B101
    assert turn.tokens == 8230  # nosec B101


def test_turn_reads_a_plain_dict_response():
    turn = Turn()
    turn.add({"eval_count": 20, "eval_duration": 2 * NS,
              "prompt_eval_count": 40})

    assert turn.tokens == 60  # nosec B101
    assert turn.rate == 10  # nosec B101


def test_turn_survives_missing_counters():
    turn = Turn()
    turn.add(_Response())
    turn.add({})

    assert turn.tokens == 0  # nosec B101
    assert turn.seconds == 0  # nosec B101
    assert turn.rate is None  # nosec B101


def test_summary_leads_with_tokens_and_time():
    turn = Turn()
    turn.add(_Response(eval_count=412, eval_duration=33 * NS,
                       total_duration=35 * NS, prompt_eval_count=2140))

    assert _plain(turn, 65536) == (  # nosec B101
        "  2,552 tokens in 35s   12.5 tok/s   context 3% of 64K"
    )


def test_summary_says_nothing_when_nothing_was_generated():
    assert summary(Turn(), 65536) is None  # nosec B101


def test_summary_drops_each_part_it_has_no_number_for():
    turn = Turn()
    turn.add(_Response(eval_count=88))

    assert _plain(turn) == "  88 tokens"  # nosec B101


def test_summary_omits_a_sub_second_turn():
    turn = Turn()
    turn.add(_Response(eval_count=5, eval_duration=NS // 2,
                       total_duration=NS // 2))

    assert "in 0s" not in _plain(turn)  # nosec B101


def test_summary_spells_out_a_tiny_context_share():
    turn = Turn()
    turn.add(_Response(eval_count=10, prompt_eval_count=100))

    assert "context under 1% of 64K" in _plain(turn, 65536)  # nosec B101


def test_summary_counts_minutes_past_sixty_seconds():
    turn = Turn()
    turn.add(_Response(eval_count=1000, eval_duration=100 * NS,
                       total_duration=93 * NS))

    assert "in 1m 33s" in _plain(turn)  # nosec B101


def test_context_limit_prefers_the_pinned_num_ctx(monkeypatch):
    monkeypatch.setattr(
        sysprompt,
        "_show",
        lambda _host, _model: {
            "parameters": "temperature 0.6\nnum_ctx 65536\ntop_k 64",
            "model_info": {"gemma4.context_length": 262144},
        },
    )

    assert get_context_limit("h", "m") == 65536  # nosec B101


def test_context_limit_ignores_the_architecture_ceiling(monkeypatch):
    monkeypatch.setattr(
        sysprompt,
        "_show",
        lambda _host, _model: {
            "parameters": "temperature 0.6",
            "model_info": {"gemma4.context_length": 262144},
        },
    )

    assert get_context_limit("h", "m") is None  # nosec B101


def test_context_limit_is_none_when_ollama_says_nothing(monkeypatch):
    monkeypatch.setattr(sysprompt, "_show", lambda _host, _model: {})

    assert get_context_limit("h", "m") is None  # nosec B101


def test_context_ceiling_reports_what_the_architecture_supports(monkeypatch):
    monkeypatch.setattr(
        sysprompt,
        "_show",
        lambda _host, _model: {
            "parameters": "num_ctx 65536",
            "model_info": {"gemma4.context_length": 262144},
        },
    )

    # The pinned window is what it runs in; the ceiling is only what it
    # could be told to run in.
    assert get_context_limit("h", "m") == 65536  # nosec B101
    assert get_context_ceiling("h", "m") == 262144  # nosec B101


def test_context_ceiling_is_none_when_unreported(monkeypatch):
    monkeypatch.setattr(sysprompt, "_show", lambda _host, _model: {})

    assert get_context_ceiling("h", "m") is None  # nosec B101


def _config(monkeypatch, num_ctx="", model="m"):
    monkeypatch.setattr(ai.Config, "num_ctx", num_ctx, raising=False)
    monkeypatch.setattr(ai.Config, "model", model, raising=False)
    monkeypatch.setattr(ai.Config, "host", "h", raising=False)
    ai._context_limits.clear()
    ai._context_ceilings.clear()
    ai._context_notices.clear()
    ai._num_ctx_notices.clear()


def test_num_ctx_is_left_alone_when_unset(monkeypatch):
    _config(monkeypatch)
    monkeypatch.setattr(ai, "get_context_limit", lambda _h, _m: None)

    assert ai._num_ctx() == 0  # nosec B101
    assert "num_ctx" not in ai._chat_options()  # nosec B101


def test_num_ctx_takes_a_token_count(monkeypatch):
    _config(monkeypatch, num_ctx="32768")

    assert ai._num_ctx() == 32768  # nosec B101
    assert ai._chat_options()["num_ctx"] == 32768  # nosec B101


def test_num_ctx_max_resolves_to_the_model_ceiling(monkeypatch):
    _config(monkeypatch, num_ctx="max")
    monkeypatch.setattr(ai, "get_context_ceiling", lambda _h, _m: 262144)

    assert ai._num_ctx() == 262144  # nosec B101


def test_num_ctx_ignores_a_value_it_cannot_read(monkeypatch):
    _config(monkeypatch, num_ctx="lots")

    assert ai._num_ctx() == 0  # nosec B101


def test_what_flash_asks_for_beats_what_the_model_pins(monkeypatch):
    _config(monkeypatch, num_ctx="8192")
    monkeypatch.setattr(ai, "get_context_limit", lambda _h, _m: 65536)

    assert ai._context_limit() == 8192  # nosec B101


def test_the_unpinned_notice_is_printed_once_per_model(monkeypatch):
    _config(monkeypatch)
    monkeypatch.setattr(ai, "get_context_ceiling", lambda _h, _m: 262144)
    printed = []
    monkeypatch.setattr(ai.console, "print", lambda text: printed.append(text))

    ai._note_unpinned_context()
    ai._note_unpinned_context()

    assert len(printed) == 1  # nosec B101
    assert "256K" in printed[0].plain  # nosec B101
    assert "NUM_CTX" in printed[0].plain  # nosec B101


def test_num_ctx_max_says_so_when_it_cannot_resolve(monkeypatch):
    # A setting that is quietly ignored is worse than one never set.
    _config(monkeypatch, num_ctx="max")
    monkeypatch.setattr(ai, "get_context_ceiling", lambda _h, _m: None)
    monkeypatch.setattr(ai, "is_remote", lambda _h, _m: True)
    warned = []
    monkeypatch.setattr(ai, "warn", warned.append)

    assert ai._num_ctx() == 0  # nosec B101
    assert ai._num_ctx() == 0  # nosec B101

    assert len(warned) == 1  # nosec B101
    assert "changed nothing" in warned[0]  # nosec B101
    assert "cloud" in warned[0]  # nosec B101


def test_num_ctx_says_so_when_the_value_is_junk(monkeypatch):
    _config(monkeypatch, num_ctx="lots")
    warned = []
    monkeypatch.setattr(ai, "warn", warned.append)

    assert ai._num_ctx() == 0  # nosec B101
    assert "'lots'" in warned[0]  # nosec B101


def test_num_ctx_max_is_quiet_when_it_works(monkeypatch):
    _config(monkeypatch, num_ctx="max")
    monkeypatch.setattr(ai, "get_context_ceiling", lambda _h, _m: 262144)
    warned = []
    monkeypatch.setattr(ai, "warn", warned.append)

    assert ai._num_ctx() == 262144  # nosec B101
    assert warned == []  # nosec B101
