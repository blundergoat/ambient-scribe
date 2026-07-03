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

## Lesson: Browser stream state ordering needs a focused regression check (2026-07-04)

**Source:** git history (auto-seeded)
**Evidence:** `templates/scribe/index.html.twig` + commit `0125a6b` fixed the StreamOrchestrator `_active` ordering bug.

**Lesson:** When changing EventSource subscription setup or stream lifecycle state, run a browser or contract regression that proves subscriptions can connect, reconnect, and shut down in that order.

## Lesson: Live transcription fixes need cross-layer verification, not one-service checks (2026-07-04)

**Source:** git history (auto-seeded)
**Evidence:** `docker-compose.yml`, `strands_agents/api/server.py`, `strands_agents/nemo_pipeline.py`, `strands_agents/nemo_session.py`, `templates/scribe/index.html.twig`, and `tests/python/test_api.py` + commit `35bceb4` restored live transcription and healthcheck contracts.

**Lesson:** For live transcription regressions, verify Docker wiring, FastAPI health/WebSocket behavior, NeMo session handling, and browser config together before declaring the fix complete.

## Lesson: Semantic anchors should prefer function names over escaped route strings (2026-07-04)

`./scripts/context-validate.sh` rejected a generated footgun citation that used an escaped decorator string for the WebSocket route in `strands_agents/api/server.py`. The route existed, but the checker did not accept the escaped quote form.

**Lesson:** For learning-loop citations, prefer stable function-name anchors such as `(search: "async def transcribe_stream")` over quoted decorator or route literals that require escaping.

## Lesson: Goat-flow installed skill edits can drift from package templates (2026-07-04)

Updating installed goat-plan skill copies under `.agents/skills/`, `.claude/skills/`, and `.github/skills/` removed stale project path text, but `goat-flow audit` still compares those files to the package template in `node_modules/@blundergoat/goat-flow/workflow/skills/goat-plan/SKILL.md`.

**Lesson:** When changing installed goat-flow skill text for project policy, run `goat-flow audit` and either accept/report template drift or make the change upstream in the package before claiming the audit is clean.
