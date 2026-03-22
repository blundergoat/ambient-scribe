# Lessons — Ambient Scribe

Behavioral lessons learned from agent performance and user corrections.

## Patterns

- **READ before acting:** Always read both ends of a contract (e.g., PHP <-> Python, Browser <-> WebSocket) before assuming a data format.
- **CLASSIFY accurately:** Distinguish between Inquiries (questions about the codebase) and Directives (instructions to change it). Questions get explanations, not edits.
- **VERIFY renames:** Always run `rg` after renaming a symbol or route to ensure no stale references remain.

## Entries

### Entry: Audio format mismatch — read both pipeline ends (2026-03-21)

AudioBuffer in `nemo_session.py` assumed 16kHz 16-bit PCM while the browser MediaRecorder sent WebM/Opus. NeMo received garbage audio and produced nonsensical transcriptions with no errors in logs. Root cause found by reading both `templates/scribe/index.html.twig` (producer) and `nemo_session.py` (consumer). Lesson: always read both ends of a data pipeline before diagnosing silent failures. Related: `docs/footguns.md` entry #3, commit f7ba6b3.

### Entry: Question misclassified as directive (2026-03-21)

"How does session cleanup work?" was treated as a directive to implement changes to session cleanup. The CLASSIFY step should have identified this as an Inquiry. Lesson: questions get explanations, not edits — do NOT migrate to Implement mode unless a Directive is issued.

### Entry: Stale references after rename (2026-03-21)

After renaming `mercure_topic_raw` to `mercure_topic_segments`, stale references remained in config and docs. Lesson: always run `rg <old_symbol>` after renames and confirm zero remaining refs (DoD gate #6).
