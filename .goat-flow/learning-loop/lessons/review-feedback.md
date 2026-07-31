---
category: review-feedback
last_reviewed: 2026-07-14
---

# Review Feedback Lessons

## Lesson: Harness line-count failures after instruction edits (2026-07-04)

**Created:** 2026-07-04

Adding required hot-path headings to `CLAUDE.md` fixed structural checks but pushed the file over the harness hard limit reported by `instruction-line-count`.

**Lesson:** When editing audited instruction files, include `wc -l <file>` in the verification gate before the first audit rerun and keep required section additions under the harness hard limit.

## Lesson: Bot "Addressed in <commit>" markers are not evidence

**Created:** 2026-07-07
**What happened:** Triaging the PR #3 bot reviews (Copilot, Codex, CodeRabbit), five of
CodeRabbit's "✅ Addressed in commits ..." markers pointed at commits that never touched the
flagged code. One hid a still-live bug: `src/Logging/JsonLineLogger.php` was marked addressed
in a commit that was actually an unrelated medical-lexicon change, while the uncaught
`JSON_THROW_ON_ERROR` throw path stayed in the request path until this session fixed it
(search: "JSON_INVALID_UTF8_SUBSTITUTE"). Only about half the resolution markers checked out.
**Lesson:** During review triage, never close a finding on a bot's resolution marker. Verify
with `git show <commit> --stat` that the cited commit modifies the flagged file, then re-read
the flagged code at HEAD. Treat the marker as a hint about WHERE a fix might be, not that one
exists.

## Lesson: Bot findings on long-lived PRs go stale, not wrong

**Created:** 2026-07-07
**What happened:** Two Codex cross-boundary findings on PR #3 ("live summaries overwrite the
finalized transcript via replace_segments"; "stop closes the EventSource before the finalize
tail") were INVALID at HEAD - but both accurately described earlier code: destructive
`replace_segments` was superseded by `merge_browser_segments` (`strands_agents/storage.py`,
search: "def merge_browser_segments") and stop paths became drain-wait (see footguns/runtime.md,
search: "Browser stop teardown can race server finalize publishes"). Dismissing them as
hallucinations would have been wrong twice over: they were correct when filed, and the drift
pattern they describe is the thing to watch for.
**Lesson:** Before accepting OR dismissing a bot finding on a long-lived branch, date it:
`git log -S "<quoted code>"` on the mechanism it describes. Verdicts belong to one of four
classes - VALID at HEAD, FIXED since filing, STALE (true then, refactored away), or WRONG -
and only the first class gets a code change.

## Lesson: Design against consumer-semantic contracts, not producer proxies

**Created:** 2026-07-14
**What happened:** During the 0.4.0-improve-claude plan cycle, an adversarial review invalidated three
designs that had each already survived one self-review. (1) A role-update dedup key was
wrong TWICE: first as byte-identical payload comparison (defeated by per-batch
`attributed_segments` and free-text `reasoning`), then - after self-correction - by
including the whole `role_stability` object, whose `windows` counter increments every
chunk while the browser consumes only `role_stability.level`
(`strands_agents/api/role_inference_queue.py`, search: "def _build_role_stability";
`public/js/scribe-transcript.js`, search: "roleStability = roleUpdateEvent.role_stability").
(2) A proposed "regenerate the note" recovery could not repair the incident it targeted:
three independent reuse layers (browser correction early-return, server
reuse-unless-`force`, corrected-artifact precedence over the POST body;
`strands_agents/api/summary_request.py`, search: "corrected_segments") meant retry
consumed the same stale 214-row source. (3) Acceptance gates were written on proxy
metrics (raw row counts, "≈ 0" of an unmeasured quantity) rather than identity-bearing
state.
**Lesson:** Before proposing a dedup key, a recovery/retry action, or an acceptance gate,
first trace (a) exactly what the CONSUMER reads (not what the producer emits) and (b) the
full reuse/caching path a retry actually takes, then define the contract in
identity-bearing terms (terminal version + unique IDs, consumer-visible state bands,
exact counters that exist). Self-review does not reliably catch second-order design
errors in one's own corrections - an independent adversarial pass over primary sources
does, and is worth commissioning before implementation for multi-milestone plans.
