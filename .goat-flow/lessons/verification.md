---
category: verification
last_reviewed: 2026-04-22
---

# READ / SCOPE / VERIFY Lessons

## Lesson: Audio format mismatch — read both pipeline ends (2026-03-21)

AudioBuffer in `strands_agents/nemo_session.py` assumed 16 kHz 16-bit PCM while the browser MediaRecorder sent WebM/Opus. NeMo received garbage audio and produced nonsensical transcriptions with no errors in logs. Root cause was found only after reading both `templates/scribe/index.html.twig` (producer) and `strands_agents/nemo_session.py` (consumer).

**Lesson:** Always read both ends of a data pipeline before diagnosing silent failures. Related footgun: `.goat-flow/footguns/audio.md`.

## Lesson: Question misclassified as directive (2026-03-21)

"How does session cleanup work?" was treated as a directive to implement changes to session cleanup. The SCOPE step should have identified this as a question and kept the agent in Explain mode.

**Lesson:** Questions get explanations, not edits — do NOT migrate to Implement mode unless a Directive is issued.

## Lesson: Stale references after rename (2026-03-21)

After renaming `mercure_topic_raw` to `mercure_topic_segments`, stale references remained in config and docs.

**Lesson:** Always run `rg <old_symbol>` after renames and confirm zero remaining refs (DoD gate #6).
