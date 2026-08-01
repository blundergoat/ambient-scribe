---
category: workflow
last_reviewed: 2026-07-31
---

## Lesson: When simplifying a process, separate ceremony from mechanism

**Created:** 2026-07-31
**Decision changed:** Before dropping a step from an inherited procedure, classify it as
process or mechanism. Process can be renegotiated; mechanism fails closed for a reason that
is usually invisible until it fires.
**Trigger phase:** SCOPE

A transcription-accuracy baseline campaign had accumulated three gated approval packets and
~96KB of plan text without scoring a single fixture, so a successor milestone replaced it
with a direct run. Three safeguards were questioned. Only one was actually ceremony.

- **Ceremony (correctly dropped):** hash-bound approval packets, attempt sentinels, and
  one-shot campaign budgets wrapped around ten local replays of public audio.
- **Mechanism (wrongly dropped):** the two NeMo log-mode recreations, dismissed because
  `EVAL_REQUIRE_STRUCTURED_LOGS` defaults to `0`. That variable only controls whether
  `scripts/eval-corrected-fixtures.sh` **pre-checks** the log mode; the evaluator still reads
  `session.quality` from JSONL. With `LOG_FORMAT=console` the run streamed a whole fixture and
  then died on `no session.quality JSONL row found`. Disabling the check had converted a fast,
  legible startup failure into a slow, misleading one.
- **Mechanism (correctly kept):** the development-corpus manifest validator, which then caught
  a real defect - all ten fixture WAVs had been overwritten by their stereo counterparts, so
  every score would have been computed against the wrong audio.

**Prevention:** A permissive default on a `*_REQUIRE_*` flag is a statement about *when you
find out*, not about whether the requirement exists - `rg` the value the guard protects, not
just the guard's own name, before concluding a prerequisite is optional. Ceremony is what
governs *permission to act*; mechanism is what governs *whether the act is valid*. Removing
the second to move faster produces results that look complete and are silently wrong.
Related: [[audit-runners]] on proving contracts before paid work.

## Lesson: Re-read the active plan pointer immediately before changing it

**Created:** 2026-07-14
**What happened:** A fresh-plan patch bundled new milestone files with an expected
`.goat-flow/plans/.active` value. Another workflow changed the pointer after intake, so
`apply_patch` rejected the whole batch before writing any plan content.
**Prevention:** Treat `.active` as an optimistic-concurrency write. Re-read it immediately before
the patch; if it changed, preserve the concurrent pointer unless the user explicitly asks to
activate the new plan, and write the user-requested plan artifacts in a separate patch.

