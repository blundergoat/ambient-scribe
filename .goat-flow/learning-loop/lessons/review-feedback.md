---
category: review-feedback
last_reviewed: 2026-07-07
---

# Review Feedback Lessons

## Lesson: Harness line-count failures after instruction edits (2026-07-04)

**Created:** 2026-07-04

Adding required hot-path headings to `CLAUDE.md` fixed structural checks but pushed the file over the harness hard limit reported by `instruction-line-count`.

**Lesson:** When editing audited instruction files, include `wc -l <file>` in the verification gate before the first audit rerun and keep required section additions under the harness hard limit.
