---
category: auditor
last_reviewed: 2026-08-28
---

# Auditor Footguns

## Footgun: Instruction parity is audited across every agent file, not per agent

**Status:** active | **Created:** 2026-08-28 | **Evidence:** ACTUAL_MEASURED
**Decision changed:** Whether a `--agent claude` change may touch only `CLAUDE.md`.
**Trigger phase:** SCOPE

**Symptoms:** `goat-flow audit . --agent claude` fails on `drift` with "instruction parity: ... while present in CLAUDE.md", naming `AGENTS.md` and `.github/copilot-instructions.md` - files the claude agent config puts out of scope.

- **Files:** `CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md` (search: "Prose surfaces route the same way before writing")
- **Files:** `CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md` (search: "Before creating, changing, reviewing, consolidating, moving, or pruning tests")
- **What breaks:** The `--agent` flag scopes which surfaces are *checked*, not which are *compared*. The release manifest carries a parity_phrases list, and parity is evaluated over every instruction file present in the repo, so adding a required phrase to one file makes the other two fail. Adding the eight 1.16.0 parity phrases to `CLAUDE.md` alone produced 26 drift findings and blocked all three audit gates; the peer files had passed only because all three were equally out of date.
- **Evidence:** Measured on the 1.14.0 to 1.16.0 upgrade. Base and content audits went from `overall=pass` to `overall=fail` on a `CLAUDE.md`-only edit, and returned to pass once the same block was applied to both peers. Plan any instruction-file parity edit as a three-file change, or get scope widened before starting.

## Footgun: The hallucination red-flags marker must end its own line

**Status:** active | **Created:** 2026-08-28 | **Evidence:** ACTUAL_MEASURED
**Decision changed:** Whether the red-flags clauses may be folded onto the marker line to save hot-path lines.
**Trigger phase:** ACT

**Symptoms:** `evidence-before-claims` fails with "missing Hallucination red-flags" / "section missing" even though the phrase is plainly in the instruction file.

- **Files:** `node_modules/@blundergoat/goat-flow/dist/cli/audit/harness/check-verification.js` (search: "Hallucination red-flags:?(?:\\*\\*)?\\s*$")
- **Files:** `CLAUDE.md` (search: "Hallucination red-flags")
- **What breaks:** The checker locates the section with a line-anchored regex and then reads everything *after* the match for its four clauses. Putting `**Checks passed.**` on the same line as the marker leaves no match at all, so the whole section reads as absent and every clause is reported missing at once - which looks like content loss rather than a formatting fault.
- **Evidence:** Measured while compressing the block during the 1.16.0 upgrade. Keep `**Hallucination red-flags:**` alone on its line, with the clauses and the `.goat-flow/skill-docs/skill-preamble.md` pointer in paragraphs below it.

## Footgun: Harness provenance can lag behind consolidated learning-loop buckets

**Status:** active | **Created:** 2026-07-05 | **Evidence:** OBSERVED

**Symptoms:** A harness or dashboard quality finding can report stale footgun/lesson references even when the structural `feedback-loop-active` check passes.

- **Files:** `node_modules/@blundergoat/goat-flow/dist/cli/audit/harness/check-verification.js` (search: ".goat-flow/learning-loop/lessons/review-feedback.md")
- **Files:** `node_modules/@blundergoat/goat-flow/dist/cli/audit/check-agent-deny-mechanism.js` (search: ".goat-flow/learning-loop/footguns/auditor.md")
- **What breaks:** The audit runtime can emit provenance paths from older generic bucket names while the project has consolidated entries into different bucket files. A downstream harness or quality check that validates provenance paths can then flag stale local-path markup even though the live learning-loop entry counts are healthy.
- **Evidence:** The installed audit runtime still references legacy bucket paths for verification and guardrail provenance. Keep those buckets real with evidence-backed entries, or update the audit runtime provenance upstream.
