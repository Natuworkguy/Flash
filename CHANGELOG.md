# Changelog

## 0.2.0

### Added

- **Streaming replies.** Model responses now render progressively, word by
  word, with a trailing cursor dot (`●`) while the text is still coming in
  -- no more waiting for the whole reply to print at once.
- **`/image` path suggestions.** Typing `/image ` now suggests files and
  folders from disk as you type, filtered to supported image types
  (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`). Paths with spaces are
  automatically quoted so they parse correctly.
- **Image-aware thinking states.** Sending an image now cycles through a
  dedicated set of status phrases ("Examining the image", "Studying the
  image", ...) instead of the generic "Thinking" list.
- **Rotating hint suggestions.** An idle prompt now shows a rotating tip
  (drawn from `flash/suggestions.json`) that sweeps into view letter by
  letter and sweeps back out, cycling through example commands and
  prompts.

### Changed

- Thinking-state phrases (generic and image-specific) now live together in
  `flash/thinking_states.json` under `states` and `image_states`.

## 0.1.0

Initial tracked release: Ollama-backed chat, shell command execution
(`shell` tool and `!` prefix), `flash://` URL scheme support, image
recognition via `/image`, file search tools (`glob`, `grep`), saved
memory, and self-update via `/update`.
