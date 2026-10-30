"""Chess, for teaching it: the rules, and lessons played move by move.

A lesson is steps the model writes in one go: Flash plays a move and
says why, or the user is asked to find the move, with a hint, and the
board checks the answer. Every move is checked here against the rules
before the lesson is shown, so a model that misremembers an opening
hears which move is illegal, and where, and the page can mark a
student's move right or wrong without asking the model again.

The rules are all here, in plain Python: moves for every piece,
castling, en passant, promotion, check, mate and stalemate, and moves
written as people write them (e4, Nf3, exd5, O-O, e8=Q) or as squares
(e2e4).
"""

import re
from dataclasses import dataclass, field
from typing import Any, Optional

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
FILES = "abcdefgh"
MAX_STEPS = 60
MAX_SAY = 600

KNIGHT = ((1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1),
          (-1, 2))
KING = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1),
        (1, -1))
BISHOP = ((1, 1), (1, -1), (-1, 1), (-1, -1))
ROOK = ((1, 0), (-1, 0), (0, 1), (0, -1))


class ChessError(ValueError):
    """An illegal move, a bad position, or a lesson that does not work."""


def square(name: str) -> int:
    name = str(name).strip().lower()
    if len(name) != 2 or name[0] not in FILES or name[1] not in "12345678":
        raise ChessError(f"{name!r} is not a square")
    return (int(name[1]) - 1) * 8 + FILES.index(name[0])


def name_of(sq: int) -> str:
    return FILES[sq % 8] + str(sq // 8 + 1)


def _white(piece: str) -> bool:
    return piece.isupper()


@dataclass
class Position:
    board: list = field(default_factory=lambda: [None] * 64)
    turn: str = "w"
    castling: str = "KQkq"
    ep: Optional[int] = None
    halfmove: int = 0
    fullmove: int = 1

    # ----- FEN -----
    @classmethod
    def from_fen(cls, fen: str = START) -> "Position":
        parts = str(fen or START).split()
        if len(parts) < 2:
            raise ChessError(f"not a position (FEN): {fen!r}")
        rows = parts[0].split("/")
        if len(rows) != 8:
            raise ChessError("a position needs eight ranks")
        board: list = [None] * 64
        for i, row in enumerate(rows):
            rank, file = 7 - i, 0
            for ch in row:
                if ch.isdigit():
                    file += int(ch)
                elif ch.lower() in "pnbrqk":
                    if file > 7:
                        raise ChessError(f"rank {rank + 1} is too long")
                    board[rank * 8 + file] = ch
                    file += 1
                else:
                    raise ChessError(f"{ch!r} is not a piece")
            if file != 8:
                raise ChessError(f"rank {rank + 1} has {file} squares")
        if board.count("K") != 1 or board.count("k") != 1:
            raise ChessError("each side needs exactly one king")
        turn = parts[1] if parts[1] in ("w", "b") else "w"
        castling = parts[2] if len(parts) > 2 and parts[2] != "-" else ""
        ep = square(parts[3]) if len(parts) > 3 and parts[3] != "-" else None
        half = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        full = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 1
        pos = cls(board, turn, "".join(c for c in "KQkq" if c in castling),
                  ep, half, full)
        if pos.attacked(pos.king(pos.turn == "b"), pos.turn == "w"):
            raise ChessError("the side not to move is in check")
        return pos

    def fen(self) -> str:
        rows = []
        for rank in range(7, -1, -1):
            row, empty = "", 0
            for file in range(8):
                piece = self.board[rank * 8 + file]
                if piece is None:
                    empty += 1
                    continue
                if empty:
                    row += str(empty)
                    empty = 0
                row += piece
            rows.append(row + (str(empty) if empty else ""))
        ep = name_of(self.ep) if self.ep is not None else "-"
        return (f"{'/'.join(rows)} {self.turn} {self.castling or '-'} {ep} "
                f"{self.halfmove} {self.fullmove}")

    # ----- attacks and check -----
    def king(self, white: bool) -> int:
        return self.board.index("K" if white else "k")

    def attacked(self, sq: int, by_white: bool) -> bool:
        """Whether SQ is attacked by the white pieces (or black ones)."""

        r, f = divmod(sq, 8)

        def at(dr, df):
            rr, ff = r + dr, f + df
            if 0 <= rr < 8 and 0 <= ff < 8:
                return self.board[rr * 8 + ff]
            return None

        pawn = "P" if by_white else "p"
        step = -1 if by_white else 1
        if at(step, -1) == pawn or at(step, 1) == pawn:
            return True
        for dr, df in KNIGHT:
            if at(dr, df) == ("N" if by_white else "n"):
                return True
        for dr, df in KING:
            if at(dr, df) == ("K" if by_white else "k"):
                return True
        for dirs, kinds in ((BISHOP, "BQ"), (ROOK, "RQ")):
            for dr, df in dirs:
                rr, ff = r + dr, f + df
                while 0 <= rr < 8 and 0 <= ff < 8:
                    piece = self.board[rr * 8 + ff]
                    if piece is not None:
                        if _white(piece) == by_white and \
                                piece.upper() in kinds:
                            return True
                        break
                    rr, ff = rr + dr, ff + df
        return False

    def in_check(self) -> bool:
        white = self.turn == "w"
        return self.attacked(self.king(white), not white)

    # ----- moves -----
    def _pseudo(self):
        white = self.turn == "w"

        def mine(p):
            return p is not None and _white(p) == white

        def theirs(p):
            return p is not None and _white(p) != white

        for sq, piece in enumerate(self.board):
            if not mine(piece):
                continue
            r, f = divmod(sq, 8)
            kind = piece.upper()
            if kind == "P":
                d = 1 if white else -1
                last = 7 if white else 0
                promos = "qrbn"
                one = sq + 8 * d
                if 0 <= one < 64 and self.board[one] is None:
                    for p in (promos if one // 8 == last else (None,)):
                        yield (sq, one, p)
                    start = 1 if white else 6
                    two = sq + 16 * d
                    if r == start and self.board[two] is None:
                        yield (sq, two, None)
                for df in (-1, 1):
                    if not 0 <= f + df < 8:
                        continue
                    to = sq + 8 * d + df
                    if not 0 <= to < 64:
                        continue
                    if theirs(self.board[to]) or to == self.ep:
                        for p in (promos if to // 8 == last else (None,)):
                            yield (sq, to, p)
                continue
            if kind in "NK":
                for dr, df in (KNIGHT if kind == "N" else KING):
                    rr, ff = r + dr, f + df
                    if 0 <= rr < 8 and 0 <= ff < 8 and not mine(
                            self.board[rr * 8 + ff]):
                        yield (sq, rr * 8 + ff, None)
                if kind == "K":
                    yield from self._castles(white)
                continue
            dirs = {"B": BISHOP, "R": ROOK, "Q": BISHOP + ROOK}[kind]
            for dr, df in dirs:
                rr, ff = r + dr, f + df
                while 0 <= rr < 8 and 0 <= ff < 8:
                    target = self.board[rr * 8 + ff]
                    if mine(target):
                        break
                    yield (sq, rr * 8 + ff, None)
                    if target is not None:
                        break
                    rr, ff = rr + dr, ff + df

    def _castles(self, white: bool):
        rank = 0 if white else 56
        king = rank + 4
        if self.board[king] != ("K" if white else "k"):
            return
        if self.attacked(king, not white):
            return
        rook = "R" if white else "r"
        if ("K" if white else "k") in self.castling and \
                self.board[rank + 7] == rook and \
                self.board[rank + 5] is None and self.board[rank + 6] is None \
                and not self.attacked(rank + 5, not white) \
                and not self.attacked(rank + 6, not white):
            yield (king, rank + 6, None)
        if ("Q" if white else "q") in self.castling and \
                self.board[rank] == rook and all(
                    self.board[rank + i] is None for i in (1, 2, 3)) \
                and not self.attacked(rank + 3, not white) \
                and not self.attacked(rank + 2, not white):
            yield (king, rank + 2, None)

    def play(self, move) -> "Position":
        """The position after MOVE (from, to, promotion), unchecked."""

        frm, to, promo = move
        board = list(self.board)
        piece = board[frm]
        white = _white(piece)
        captured = board[to]
        board[to], board[frm] = piece, None
        kind = piece.upper()
        if kind == "P" and to == self.ep:
            board[to - 8 if white else to + 8] = None
            captured = "p"
        if kind == "P" and promo:
            board[to] = promo.upper() if white else promo.lower()
        if kind == "K" and abs(to - frm) == 2:
            if to > frm:
                board[frm + 1], board[frm + 3] = board[frm + 3], None
            else:
                board[frm - 1], board[frm - 4] = board[frm - 4], None
        castling = self.castling
        for sq, rights in ((4, "KQ"), (60, "kq"), (0, "Q"), (7, "K"),
                           (56, "q"), (63, "k")):
            if frm == sq or to == sq:
                castling = "".join(c for c in castling if c not in rights)
        ep = (frm + to) // 2 if kind == "P" and abs(to - frm) == 16 else None
        return Position(
            board, "b" if self.turn == "w" else "w", castling, ep,
            0 if kind == "P" or captured else self.halfmove + 1,
            self.fullmove + (1 if self.turn == "b" else 0),
        )

    def legal(self) -> list:
        white = self.turn == "w"
        out = []
        for move in self._pseudo():
            after = self.play(move)
            if not after.attacked(after.king(white), not white):
                out.append(move)
        return out

    def outcome(self) -> str:
        """"" while the game goes on; else checkmate or stalemate."""

        if self.legal():
            return ""
        return "checkmate" if self.in_check() else "stalemate"

    # ----- moves as written -----
    def san(self, move) -> str:
        frm, to, promo = move
        piece = self.board[frm]
        kind = piece.upper()
        if kind == "K" and abs(to - frm) == 2:
            text = "O-O" if to > frm else "O-O-O"
        else:
            capture = self.board[to] is not None or (
                kind == "P" and to == self.ep)
            if kind == "P":
                text = (FILES[frm % 8] + "x" if capture else "") + name_of(to)
                if promo:
                    text += "=" + promo.upper()
            else:
                others = [m for m in self.legal() if m[1] == to and m[0] != frm
                          and self.board[m[0]] == piece]
                which = ""
                if others:
                    if all(o[0] % 8 != frm % 8 for o in others):
                        which = FILES[frm % 8]
                    elif all(o[0] // 8 != frm // 8 for o in others):
                        which = str(frm // 8 + 1)
                    else:
                        which = name_of(frm)
                text = kind + which + ("x" if capture else "") + name_of(to)
        after = self.play(move)
        if after.in_check():
            text += "#" if not after.legal() else "+"
        return text

    def uci(self, move) -> str:
        return name_of(move[0]) + name_of(move[1]) + (move[2] or "")

    def find(self, written: str):
        """The legal move WRITTEN means, in SAN (e4, Nxf3, O-O, e8=Q) or
        as squares (e2e4, e7e8q); a ChessError naming the legal moves
        when there is none."""

        text = str(written or "").strip()
        clean = re.sub(r"[!?+#]|e\.p\.", "", text).replace("0", "O").strip()
        clean = re.sub(r"^(\d+\.+\s*)", "", clean)
        moves = self.legal()
        if re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbnQRBN]?", clean):
            for move in moves:
                if self.uci(move) == clean.lower():
                    return move
        promo = re.fullmatch(r"([a-h](?:x[a-h])?[18])=?([QRBNqrbn])", clean)
        if promo:
            clean = f"{promo.group(1)}={promo.group(2).upper()}"
        plain = {re.sub(r"[+#]", "", self.san(m)): m for m in moves}
        if clean in plain:
            return plain[clean]
        loose = [m for s, m in plain.items() if s.replace("x", "") ==
                 clean.replace("x", "")]
        if len(loose) == 1:
            return loose[0]
        who = "White" if self.turn == "w" else "Black"
        listed = ", ".join(sorted(self.san(m) for m in moves)) or "none"
        raise ChessError(f"{text!r} is not a legal move for {who} here. "
                         f"Legal moves: {listed}")

    def diagram(self, flip: bool = False) -> str:
        """The board as text, white at the bottom unless FLIP."""

        ranks = range(8) if flip else range(7, -1, -1)
        files = range(7, -1, -1) if flip else range(8)
        lines = []
        for rank in ranks:
            row = " ".join(self.board[rank * 8 + f] or "." for f in files)
            lines.append(f"{rank + 1} {row}")
        lines.append("  " + " ".join(FILES[f] for f in files))
        return "\n".join(lines)


def _squares(value: Any, what: str) -> list:
    out = []
    for item in value or []:
        text = str(item).strip().lower()
        if re.fullmatch(r"[a-h][1-8][a-h][1-8]", text):
            square(text[:2])
            square(text[2:])
            out.append(text)
        elif re.fullmatch(r"[a-h][1-8]", text):
            out.append(text)
        else:
            raise ChessError(f"{what}: {item!r} is not a square or a pair "
                             "of squares like e2e4")
    return out


def build_lesson(title: str, steps: Any, start: str = "",
                 side: str = "white") -> dict:
    """A lesson the page can play, every move checked: each step with
    the position before and after it, its move as written and as
    squares, and for a step the user answers, every legal move there,
    so the page marks an answer at once."""

    if not isinstance(steps, list) or not steps:
        raise ChessError("a lesson needs steps")
    if len(steps) > MAX_STEPS:
        raise ChessError(f"a lesson has at most {MAX_STEPS} steps")
    pos = Position.from_fen(start or START)
    first = pos.fen()
    built = []
    for n, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ChessError(f"step {n} must be an object")
        where = f"step {n}"
        say = " ".join(str(step.get("say") or "").split())[:MAX_SAY]
        out: dict = {"say": say, "fen": pos.fen(),
                     "arrows": _squares(step.get("arrows"), f"{where} arrows"),
                     "marks": _squares(step.get("highlight"),
                                       f"{where} highlight")}
        try:
            answers = step.get("answer")
            if answers:
                if isinstance(answers, str):
                    answers = answers.split(",")
                found = []
                for given in answers:
                    move = pos.find(given)
                    if move not in found:
                        found.append(move)
                out["ask"] = True
                out["answers"] = [pos.uci(m) for m in found]
                out["answer_san"] = [pos.san(m) for m in found]
                out["hint"] = " ".join(
                    str(step.get("hint") or "").split())[:300]
                out["legal"] = {pos.uci(m): pos.san(m) for m in pos.legal()}
                out["turn"] = pos.turn
                pos = pos.play(found[0])
                out["mid"] = pos.fen()
                if step.get("reply"):
                    reply = pos.find(step["reply"])
                    out["reply"] = pos.uci(reply)
                    out["reply_san"] = pos.san(reply)
                    pos = pos.play(reply)
            elif step.get("move"):
                move = pos.find(step["move"])
                out["move"] = pos.uci(move)
                out["san"] = pos.san(move)
                pos = pos.play(move)
            elif not say:
                raise ChessError("it needs something to say, a move, or an "
                                 "answer for the user to find")
        except ChessError as exc:
            raise ChessError(f"{where}: {exc}") from None
        out["after"] = pos.fen()
        out["end"] = pos.outcome()
        built.append(out)
    side = "black" if str(side).lower().startswith("b") else "white"
    return {"title": " ".join(str(title or "Chess lesson").split())[:80],
            "side": side, "start": first, "steps": built}


def summary(lesson: dict) -> str:
    """The lesson in words, for the model: what it asks, and where it
    ends."""

    asks = sum(1 for s in lesson["steps"] if s.get("ask"))
    end = Position.from_fen(lesson["steps"][-1]["after"])
    board = end.diagram(lesson["side"] == "black")
    over = f" ({end.outcome()})" if end.outcome() else ""
    return (f"{lesson['title']}: {len(lesson['steps'])} steps, {asks} for "
            f"the user to play. It ends here:\n{board}\nFEN {end.fen()}"
            + over)
