---
category: verification
last_reviewed: 2026-07-04
---

# READ / SCOPE / VERIFY Lessons

## Lesson: Audio format mismatch — read both pipeline ends (2026-03-21)

AudioBuffer in `strands_agents/nemo_session.py` assumed 16 kHz 16-bit PCM while the browser MediaRecorder sent WebM/Opus. NeMo received garbage audio and produced nonsensical transcriptions with no errors in logs. Root cause was found only after reading both `templates/scribe/index.html.twig` (producer) and `strands_agents/nemo_session.py` (consumer).

**Lesson:** Always read both ends of a data pipeline before diagnosing silent failures. Related footgun: `.goat-flow/learning-loop/footguns/audio.md`.

## Lesson: Question misclassified as directive (2026-03-21)

"How does session cleanup work?" was treated as a directive to implement changes to session cleanup. The SCOPE step should have identified this as a question and kept the agent in Explain mode.

**Lesson:** Questions get explanations, not edits — do NOT migrate to Implement mode unless a Directive is issued.

## Lesson: Stale references after rename (2026-03-21)

After renaming `mercure_topic_raw` to `mercure_topic_segments`, stale references remained in config and docs.

**Lesson:** Always run `rg <old_symbol>` after renames and confirm zero remaining refs (DoD gate #6).

## Lesson: Harness line-count failures after instruction edits (2026-07-04)

Adding required hot-path headings to `CLAUDE.md` fixed structural checks but pushed the file over the harness hard limit reported by `instruction-line-count`.

**Lesson:** When editing audited instruction files, include `wc -l <file>` in the verification gate before the first audit rerun and keep required section additions under the harness hard limit.
