---
name: play-chess
description: Play a game of chess against the user on a board in the chat, no lesson
---
- "Play chess with me", "let's play a game of chess", "a game as black": use play_chess, not teach_chess and not moves written out in a reply.
- Start with play_chess and no game: side is the side the user plays (white unless they ask), start a FEN only when they want a set position. When they play black, give your first move as white in move.
- The board is in the chat. The user moves a piece there; the move is checked against the rules and comes to you as a message like "In our chess game (game 1a2b3c4d5e6f), I played Nf3. Position: <FEN>. Your move."
- Answer each with play_chess {"game": "<the id>", "move": "<your move>"}. The result gives the moves so far, the board and the FEN, and says whose turn it is. An illegal move comes back as an error listing the legal ones: pick one and call again.
- Play to win, at the strength they ask for (go easier for a beginner if they say so). Keep your reply to a line or nothing: the board shows the move. Do not comment on every move unless they ask.
- When the game ends (checkmate or stalemate) the board says so and offers "New game", which asks you for another.
- "Ask Flash" on the board sends the game id and FEN: answer from that position.
- "Change the chess pieces", "a different piece style": the user picks it in Settings, Chess pieces (Carved, Gold and silver, Classic). It is per browser; you cannot change it for them.
- In the terminal there is no board: offer to play in text instead, moves written as usual.

## Examples

```
play_chess {"side": "white"}
```

```
play_chess {"side": "black", "move": "e4"}
```

```
play_chess {"game": "1a2b3c4d5e6f", "move": "Nc6"}
```
