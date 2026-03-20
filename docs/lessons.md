# Lessons — Ambient Scribe

Behavioral lessons learned from agent performance and user corrections.

## Patterns

- **READ before acting:** Always read both ends of a contract (e.g., PHP <-> Python, Browser <-> WebSocket) before assuming a data format.
- **CLASSIFY accurately:** Distinguish between Inquiries (questions about the codebase) and Directives (instructions to change it). Questions get explanations, not edits.
- **VERIFY renames:** Always run `rg` after renaming a symbol or route to ensure no stale references remain.

## Entries

- **2026-03-21 (Audio Format):** AudioBuffer in `nemo_session.py` assumed PCM while the browser sent WebM/Opus. Lesson: Check `docs/footguns.md` (entry #3) and read both browser (`templates/scribe/index.html.twig`) and server (`nemo_session.py`) code.
- **2026-03-21 (Question vs Directive):** Questions like "How does session cleanup work?" are Inquiries. Do NOT migrate to Implement mode unless a Directive is issued.
- **2026-03-21 (Renames):** After renaming `mercure_topic_raw` to `mercure_topic_segments`, stale references might remain. Lesson: Always `rg` for the old symbol.
