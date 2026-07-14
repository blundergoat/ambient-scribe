---
category: verification
last_reviewed: 2026-07-14
---

# Audit Runner Lessons

## Lesson: Prove process and artifact contracts before paid work

**Created:** 2026-07-14
**What happened:** M08's one-shot shell reaped a bare `nohup` child before replay, while its first
provenance reader expected `id` instead of the retained rows' `segment_id`. No audio or provider
request was lost: a no-token artifact probe caught the schema error, and `setsid --fork` plus
process, first-log-line, and unchanged-ledger checks proved the replacement launch.
**Evidence:** `var/quality/note-fidelity-audit-20260713T234904Z/` and the M08 plan's runner evidence
(search: `54/54 citations`).
**Prevention:** Before paid or long evidence runs, assert one known non-zero result through every
new artifact reader. After launch, require live process state, first progress, and an owned exit
sentinel; a successful shell return proves neither schema compatibility nor detachment.

## Lesson: Keep repository lint separate from changed-file formatting

**Created:** 2026-07-14
**What happened:** M08's canonical Ruff lint passed, and its runner passed a focused format check.
An extra repository-wide format check found 34 untouched files outside the milestone's boundary.
**Evidence:** `var/quality/note-fidelity-audit-20260713T234904Z/verify-ruff-format-full.log`.
**Prevention:** Run the repository's canonical Ruff `check` gate, then format-check only edited
Python files unless a separately scoped milestone adopts a whole-repository formatter baseline.
