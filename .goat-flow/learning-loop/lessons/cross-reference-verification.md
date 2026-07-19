---
category: cross-reference-verification
last_reviewed: 2026-07-20
---

# Cross-Reference Verification Lessons

## Lesson: Existence-check every path a doc cites; combined shell output misattributes easily

**Created:** 2026-07-20
**What happened:** While refreshing the six README files against the current code, one Bash
call combined `ls .goat-flow/plans/_done/0.3.0/ | head` with
`ls .goat-flow/plans/_done/0.3.0/done/ | rg "M11|M12"`; the interleaved output made the
M11/M12 plan files look like direct children of `_done/0.3.0/`, and that wrong path was
written into `README_CLINICAL_INTELLIGENCE.md`. The final VERIFY sweep (`[ -e "$p" ]` over
every path the edited READMEs reference) caught both dead links before completion.
**Prevention:** When several listing commands share one shell call, print a delimiter naming
each directory before its output, and never transcribe a path from memory of combined output.
Before presenting doc changes, run a per-path existence check over every file, script, and
directory the docs newly cite - it is cheap and it caught the session's only error.

## Lesson: A truncated sweep grep ships an incomplete removal

**Added:** 2026-07-17 · **Trigger:** VERIFY (full pytest) failed on a test the removal sweep never listed

Removing row-level role corrections (PR #5 cleanup), the work-list came from
`grep -rn "user_row|compute_row_role_exceptions" ... | head -20`; the truncation hid
`test_clinician_corrected_orphan_rows_stay_untouched`, a second lane pinning the removed
guard, so the first pytest run failed on a file the sweep had already "cleared". A feature's
own test suite is never its whole surface: markers and side effects (`role_source ==
"user_row"`) are pinned by other lanes' tests too.
Prevention: never `head`-truncate the grep that builds a removal work-list - count the hits
first (`grep -c`) or write them all to a file, and treat the full suite as part of the sweep,
not a formality after it.

## Lesson: Stale references after rename (2026-03-21)

After renaming `mercure_topic_raw` to `mercure_topic_segments`, stale references remained in
config and docs.

**Lesson:** Always run `rg <old_symbol>` after renames and confirm zero remaining refs (DoD gate
#6).
