---
name: teach-chess
description: Teach chess move by move on a board the user plays through: openings, tactics, endgames, games
---
- "Teach me chess", "show me the Italian Game", "how do knight forks work", "where did I go wrong in this game": use teach_chess, not moves written out in a reply.
- A lesson is one board and a list of steps, each from where the last left off. A step plays a move ("move") and says why, or asks the user to find the move ("answer"), with a "hint" that does not give it away and the other side's "reply" after it. A step with only "say" talks about the position.
- Keep each step to one move and a sentence or two. Ask them to find the moves that matter (the idea of the lesson), and play the rest yourself. Several right answers go comma separated in answer; the first is the one the lesson goes on with.
- Start from a position with start (FEN) for tactics, puzzles and endgames; leave it out for openings. Set side to black when the user plays Black, so the board is seen from their side.
- arrows (like "g1f3") and highlight (like "f7") point things out: after the step's move, or while they think on an answer step, so never draw the answer itself.
- Every move is checked against the rules before anything is shown. An error names the step and lists the legal moves there: fix that step and send the whole lesson again.
- For a beginner, start with how the pieces move, one lesson per piece, each ending with a move for them to find. Then checkmate patterns, then openings by their ideas.
- After a lesson, keep it to a line about what it covered. When they ask about a position, the message gives its FEN: answer from that position.
- "Next lesson" means the next one in the same line: the same opening further on, or the next tactic, harder.

## Examples

```
teach_chess {"title": "The Italian Game", "steps": [{"move": "e4", "say": "Take the centre and open lines for the bishop and queen."}, {"move": "e5", "say": "Black does the same."}, {"answer": "Nf3", "hint": "Develop a piece and attack Black's pawn.", "reply": "Nc6", "say": "Your move. What attacks e5 and develops at the same time?"}, {"answer": "Bc4", "hint": "Aim a bishop at Black's weakest square.", "arrows": ["c4f7"], "say": "Now bring out the bishop. Where does it do the most?"}]}
```

```
teach_chess {"title": "Back rank mate", "start": "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", "steps": [{"say": "Black's king is shut in by its own pawns.", "highlight": ["f7", "g7", "h7"]}, {"answer": "Rd8#", "hint": "The back rank has no defender."}]}
```

```
teach_chess {"title": "Defending against the fork", "side": "black", "steps": [{"move": "e4"}, {"answer": "e5, c5", "say": "Answer in the centre."}]}
```
