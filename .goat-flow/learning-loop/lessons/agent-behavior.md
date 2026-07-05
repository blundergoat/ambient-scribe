---
category: agent-behavior
last_reviewed: 2026-07-05
---

# Agent Behavior Lessons

## Lesson: Question misclassified as directive (2026-03-21)

**Created:** 2026-03-21

"How does session cleanup work?" was treated as a directive to implement changes to session cleanup. The SCOPE step should have identified this as a question and kept the agent in Explain mode.

**Lesson:** Questions get explanations, not edits - do NOT migrate to Implement mode unless a Directive is issued.
