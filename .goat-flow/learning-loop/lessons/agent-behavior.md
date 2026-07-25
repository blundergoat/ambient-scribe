---
category: agent-behavior
last_reviewed: 2026-07-20
---

# Agent Behavior Lessons

## Lesson: Question misclassified as directive (2026-03-21)

**Created:** 2026-03-21

"How does session cleanup work?" was treated as a directive to implement changes to session cleanup. The SCOPE step should have identified this as a question and kept the agent in Explain mode.

**Lesson:** Questions get explanations, not edits - do NOT migrate to Implement mode unless a Directive is issued.

## Lesson: Gitignored plan intake needs directory listing (2026-07-05)

**Created:** 2026-07-05
**Incident count:** 2 | **Latest occurrence:** 2026-07-26
**Evidence:** `.goat-flow/plans/README.md` (search: "This directory is gitignored by design"), `.agents/skills/goat-plan/SKILL.md` (search: "Inspect existing plan state only after retrieval").

During a Copilot harness repair, `rg --files .goat-flow/plans` only showed tracked files and missed existing gitignored milestone directories. A later `ls -la .goat-flow/plans` and `find .goat-flow/plans -maxdepth 2 -type f -name 'M*.md' -print` corrected the intake before code edits.

**Lesson:** When goat-plan checks existing milestones, use `ls` or `find` for `.goat-flow/plans/` instead of `rg --files`, because plan artifacts are intentionally gitignored local state.

**2026-07-26 recurrence, content auditing rather than intake:** after writing milestone files, a `rg` search over `.goat-flow/plans/0.5.2/` for stale symbol references returned zero hits and was briefly read as "every anchor resolves". Ripgrep had skipped the whole directory because it honours `.gitignore`, and `.goat-flow/plans/.gitignore` ignores `*`. Re-running with `--no-ignore` exposed five stale references to a function name that never existed. The same blindness applies to any `rg` over `.goat-flow/plans/`, `.goat-flow/logs/`, or another ignored tree — including the audit scripts written to check those trees.

**Lesson:** Pass `--no-ignore` to `rg` whenever the target is a gitignored directory, and treat an empty audit result as unproven until the audit demonstrably matches a case known to be present. An audit that cannot see its target reports clean, which is indistinguishable from success at a glance.

## Lesson: Appended tests inherit whatever class ends the file

**Created:** 2026-07-07
**Evidence:** `tests/python/test_summary.py` (search: "test_unclosed_and_truncated_brackets_pass_through").

A hardening test appended to the end of `test_summary.py` via a shell heredoc landed inside
the LAST class in the file, which lacked the `_strip` helper the test called - it failed with
`AttributeError`, not a real regression. The fix relocated the block into the class that owns
the helper.

**Lesson:** Never append test methods to a file blind. Anchor the insertion to the class
that owns the helpers being used (Edit on a class-boundary anchor), and treat an
AttributeError on a self-helper as a placement bug before suspecting the product code.
