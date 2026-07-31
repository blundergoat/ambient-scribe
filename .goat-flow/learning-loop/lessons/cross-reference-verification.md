---
category: cross-reference-verification
last_reviewed: 2026-07-31
---

# Cross-Reference Verification Lessons

## Lesson: Existence-check every path a doc cites; combined shell output misattributes easily

**Created:** 2026-07-20
**What happened:** While refreshing the six README files against the current code, one Bash
call combined `ls .goat-flow/plans/_done/0.3.0/ | head` with
`ls .goat-flow/plans/_done/0.3.0/done/ | rg "<milestone-ids>"`; the interleaved output made
those plan files look like direct children of `_done/0.3.0/`, and that wrong path was
written into `README_CLINICAL_INTELLIGENCE.md`. The final VERIFY sweep (`[ -e "$p" ]` over
every path the edited READMEs reference) caught both dead links before completion.
**Prevention:** When several listing commands share one shell call, print a delimiter naming
each directory before its output, and never transcribe a path from memory of combined output.
Before presenting doc changes, run a per-path existence check over every file, script, and
directory the docs newly cite - it is cheap and it caught the session's only error.

## Lesson: A repo-wide sweep is only as good as its blind spots — check the tool, the pattern, and the lanes

**Created:** 2026-07-31
**Decision changed:** Before trusting a sweep count, prove three things: the tool searched
everywhere, the pattern matched only what you meant, and the per-lane breakdown sums to the
total.
**Trigger phase:** READ

A repo-wide cleanup counted references three separate ways and got three wrong answers before
getting a right one:

1. **The tool skipped a whole tree.** `rg` does not search hidden directories without
   `--hidden`, so the entire `.goat-flow/` tree was silently excluded and the first count
   under-reported by about 40%.
2. **The pattern matched non-references.** A milestone-identifier regex also matched SVG path
   geometry (`<path d="M12 20h9M16.5 3.5a2.12">`) and the mangled variable names in a minified
   vendor bundle. Sampling the first hits per file had "validated" the pattern; the false
   positives were further down the same files.
3. **The lane breakdown was not exhaustive.** Work was scoped as a table of eight directories.
   The total was right, but root-level files, `docker/`, and config outside those directories
   were never enumerated, so roughly thirty references — including a README citing three
   deleted files — survived every phase until a final full sweep.

**Prevention:** Pass `--hidden --glob '!.git'` on any repo-wide `rg` audit. Scan *every* hit in
a file before declaring the pattern clean, not the first few. Make the per-lane numbers add up
to the repo-wide number before starting work — an unexplained gap is unscoped work, not
rounding. Finish with the same sweep you opened with, and treat surviving hits as either fixed
or explicitly exempt, never as noise.

## Lesson: A `(search: ...)` anchor must sit on one physical line

**Created:** 2026-07-31
**Decision changed:** After writing or editing any doc that a `(search: ...)` anchor points
into, grep the literal anchor string instead of assuming the file's existence is enough.
**Trigger phase:** VERIFY
**What happened:** A milestone's Read-first block cited a sibling coordination document with
a long `(search: ...)` anchor. That document was a declared deliverable of two milestones and
had its "update it" task ticked in both, yet had never been created - a dead anchor that
survived two closed milestones. Creating it did not fix the anchor either: prose wrapping at
~88 columns split the anchor phrase across two lines, and because `rg` matches per line the
grep still returned nothing. Rewrapping so the whole phrase sat on one line resolved it.
**Prevention:** Anchors are line-scoped. When authoring a target, keep the anchored phrase
unbroken on a single line even if that line runs long, or choose a short anchor - a heading
or a symbol name - that cannot wrap. Existence-checking the cited path proves only half the
reference; the retrieval that the anchor promises is the other half, so verify both with
`rg -n '<anchor>' <path>` before claiming a Read-first block is valid. A ticked "update
`HANDOFF.md`" task is not evidence the file exists.

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
