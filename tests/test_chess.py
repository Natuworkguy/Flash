"""Tests for chess lessons: the rules, and lessons checked move by move."""

import json

import pytest

from flash import chess, theme, tools, web

KIWIPETE = ("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R "
            "w KQkq - 0 1")


def perft(pos, depth):
    if depth == 0:
        return 1
    return sum(perft(pos.play(m), depth - 1) for m in pos.legal())


class TestRules:
    def test_move_counts_match_the_known_ones(self):
        assert perft(chess.Position.from_fen(), 3) == 8902
        # Castling, en passant, promotion and pins all at once.
        assert [perft(chess.Position.from_fen(KIWIPETE), d)
                for d in (1, 2)] == [48, 2039]
        assert perft(chess.Position.from_fen(
            "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1"), 3) == 2812

    def test_fen_goes_round(self):
        for fen in (chess.START, KIWIPETE):
            assert chess.Position.from_fen(fen).fen() == fen

    @pytest.mark.parametrize("fen, why", [
        ("8/8/8/8/8/8/8/8 w - - 0 1", "one king"),
        ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBN w - - 0 1", "squares"),
        ("4k3/8/8/8/8/8/4R3/4K3 w - - 0 1", "not to move is in check"),
    ])
    def test_bad_positions_are_refused(self, fen, why):
        with pytest.raises(chess.ChessError, match=why):
            chess.Position.from_fen(fen)

    def test_moves_as_written(self):
        pos = chess.Position.from_fen()
        for written in ("e4", "e2e4", "1. e4", "e4!"):
            assert pos.uci(pos.find(written)) == "e2e4"
        knights = chess.Position.from_fen("4k3/8/8/8/8/8/8/1N2KN2 w - - 0 1")
        assert knights.san(knights.find("b1d2")) == "Nbd2"
        assert knights.uci(knights.find("Nfd2")) == "f1d2"
        castle = chess.Position.from_fen("4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1")
        assert castle.san(castle.find("0-0")) == "O-O"
        assert castle.uci(castle.find("O-O-O")) == "e1c1"
        promote = chess.Position.from_fen("8/4P2k/8/8/8/8/8/4K3 w - - 0 1")
        assert promote.uci(promote.find("e8=N")) == "e7e8n"
        assert promote.san(promote.find("e7e8q")) == "e8=Q"

    def test_castling_through_check_and_en_passant(self):
        through = chess.Position.from_fen(
            "4k3/8/8/8/8/8/5r2/4K2R w K - 0 1")
        assert "O-O" not in [through.san(m) for m in through.legal()]
        passant = chess.Position.from_fen(
            "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
        move = passant.find("exd6")
        assert passant.play(move).board[chess.square("d5")] is None

    def test_mate_and_stalemate(self):
        mated = chess.Position.from_fen(
            "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")
        assert mated.san(mated.find("Rd8")) == "Rd8#"
        assert mated.play(mated.find("Rd8")).outcome() == "checkmate"
        assert chess.Position.from_fen(
            "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1").outcome() == "stalemate"

    def test_an_illegal_move_names_the_legal_ones(self):
        with pytest.raises(chess.ChessError, match=r"Legal moves: .*Nf3"):
            chess.Position.from_fen().find("e5")


class TestLessons:
    STEPS = [
        {"move": "e4", "say": "The centre."},
        {"move": "e5"},
        {"answer": "Nf3, Nc3", "hint": "Attack e5.", "reply": "Nc6",
         "arrows": ["f3e5"], "highlight": ["e5"]},
    ]

    def test_every_step_comes_with_its_positions(self):
        lesson = chess.build_lesson("Opening", self.STEPS)
        one, _, ask = lesson["steps"]
        assert one["move"] == "e2e4" and one["san"] == "e4"
        assert one["fen"] == chess.START
        assert ask["ask"] and ask["answers"] == ["g1f3", "b1c3"]
        assert ask["answer_san"] == ["Nf3", "Nc3"]
        assert ask["turn"] == "w" and ask["legal"]["d2d4"] == "d4"
        assert ask["mid"].split()[1] == "b"  # after Nf3, before Nc6
        assert ask["reply"] == "b8c6" and ask["reply_san"] == "Nc6"
        assert ask["arrows"] == ["f3e5"] and ask["marks"] == ["e5"]
        assert chess.summary(lesson).startswith(
            "Opening: 3 steps, 1 for the user to play.")

    @pytest.mark.parametrize("steps, why", [
        ([], "needs steps"),
        ([{"move": "e5"}], "step 1: 'e5' is not a legal move for White"),
        ([{"move": "e4"}, {"answer": "Nf3"}], "step 2: 'Nf3' is not a legal"),
        ([{"move": "e4", "arrows": ["z9"]}], "not a square"),
        ([{}], "needs something to say"),
    ])
    def test_a_lesson_that_does_not_work_says_where(self, steps, why):
        with pytest.raises(chess.ChessError, match=why):
            chess.build_lesson("x", steps)

    def test_from_a_position_and_seen_from_black(self):
        lesson = chess.build_lesson(
            "Back rank", [{"answer": "Rd8#"}],
            "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", "black")
        assert lesson["side"] == "black"
        assert lesson["steps"][0]["end"] == "checkmate"


class TestTheTool:
    def test_in_the_browser_it_is_a_board_in_the_chat(self):
        session = web.Session()
        chat = session.new_chat()
        with theme.capture_tool_output(web._sink(session, chat)):
            said = tools.teach_chess("Opening", TestLessons.STEPS)
        assert said.startswith("It is in the chat as a board")
        shown = [e for e in chat.log if e.get("type") == "chess"]
        assert len(shown) == 1 and shown[0]["title"] == "Opening"
        web.command(session, {"name": "chess-step", "arg": shown[0]["id"],
                              "chat": chat.id, "at": 2, "solved": [2]})
        assert shown[0]["at"] == 2 and shown[0]["solved"] == [2]
        with pytest.raises(ValueError, match="not in this chat"):
            web.command(session, {"name": "chess-step", "arg": "nope",
                                  "chat": chat.id, "at": 1})

    def test_steps_given_as_text_and_mistakes_sent_back(self):
        said = tools.teach_chess("t", json.dumps([{"move": "e5"}]))
        assert said.startswith("Error: step 1:")
        assert "call teach_chess again" in said

    def test_in_the_terminal_with_nobody_there_it_shows_it_all(self):
        said = tools.teach_chess("Opening", TestLessons.STEPS)
        assert said.startswith("Shown in the terminal")
        assert "FEN " in said
