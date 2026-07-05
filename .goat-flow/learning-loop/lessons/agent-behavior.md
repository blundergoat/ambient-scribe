---
category: agent-behavior
last_reviewed: 2026-07-05
---

# Agent Behavior Lessons

## Lesson: Question misclassified as directive (2026-03-21)

**Created:** 2026-03-21

"How does session cleanup work?" was treated as a directive to implement changes to session cleanup. The SCOPE step should have identified this as a question and kept the agent in Explain mode.

**Lesson:** Questions get explanations, not edits - do NOT migrate to Implement mode unless a Directive is issued.

## Lesson: Gitignored plan intake needs directory listing (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `.goat-flow/plans/README.md` (search: "This directory is gitignored by design"), `.agents/skills/goat-plan/SKILL.md` (search: "Check for existing milestones first").

During a Copilot harness repair, `rg --files .goat-flow/plans` only showed tracked files and missed existing gitignored milestone directories. A later `ls -la .goat-flow/plans` and `find .goat-flow/plans -maxdepth 2 -type f -name 'M*.md' -print` corrected the intake before code edits.

**Lesson:** When goat-plan checks existing milestones, use `ls` or `find` for `.goat-flow/plans/` instead of `rg --files`, because plan artifacts are intentionally gitignored local state.
