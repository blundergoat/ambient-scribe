---
category: review-feedback
last_reviewed: 2026-07-07
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
