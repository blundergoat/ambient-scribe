---
category: summary
last_reviewed: 2026-07-09
---

# Summary / Note-Generation Footguns

## Footgun: Summary context is head-truncated at 8000 chars - long consults silently lose Assessment and Plan

**Status:** active | **Created:** 2026-07-09 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/summary_request.py` (search: "max_chars=8000")
- **Files:** `strands_agents/api/summary_request.py` (search: "full_transcript[:max_chars]")
- **What breaks:** All three summary context paths (corrected_segments, browser_visible_segments, session_store) cap the transcript at 8000 chars and keep the HEAD. Consultation dialogue crosses 8000 chars at roughly 7-8 minutes, and Assessment + Plan are always spoken LAST - so the cap deletes exactly the clinically decisive content, while the note generator honestly reports "no assessment documented" / "discussion not completed" about its amputated input. Nothing logs when truncation fires; the only tell is `summary.requested ... transcript_chars=8000` (exactly at the cap). The code comment above the browser-visible path states the opposite intent (search: "covers the whole stored consultation").
- **Evidence:** 2026-07-08 (UTC) manual full-length run of day5-consultation09 (9:39, session `0a40e243-c813-48a5-b87f-e069da4def40`): `summary.requested source=browser_visible_segments segments=306 transcript_chars=8000`. The doctor's stated suspected Lyme disease, the support-line booking instruction, and the ~1-week phone follow-up - all in the final ~90s and ground-truth verified against `primock57-day5-consultation09-*.doctor.TextGrid` (search: "lyme disease") - are absent from the note, which instead claims "Further discussion of specific testing was not completed in this transcript". The two ~3-4 minute sessions the same evening came in at 3798/3926 chars and were unaffected.
- **Prevention:** 0.4.0 M08 owns the fix (tail-preserving budget + truncation warning). Until then, treat any `transcript_chars` equal to the cap as a truncated-input note: do not draw model-quality conclusions from it, and do not acceptance-test stated-Assessment capture on consults longer than ~7 minutes without checking this first.

## Footgun: Fidelity denial-verification is row-shape sensitive - live vs corrected rows flip verdicts

**Status:** active | **Created:** 2026-07-09 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/summary_fidelity.py` (search: "_did_patient_deny_topic")
- **Files:** `strands_agents/api/summary_fidelity.py` (search: "<= 40 and any(")
- **Files:** `strands_agents/api/summary_fidelity.py` (search: "_does_row_mention_any_stem")
- **Files:** `strands_agents/api/summary_fidelity.py` (search: "_EXAM_ABSENCE_PATTERN")
- **What breaks:** Three heuristics make the deterministic checker's verdict depend on transcript row SHAPE rather than content:
  1. A claimed denial verifies when ANY topic stem co-occurs with ANY negation token in a single patient row. A long monologue row about the chief complaint ("...my lips start feeling... I don't know what to do") therefore launders ANY denial claim about the chief-complaint topic - the most clinically dangerous denials to fabricate are the ones the checker is structurally weakest on.
  2. A bare "No." answer only verifies via the recent-rows path when the denial row is <= 40 chars. The corrected lane adds punctuation and re-merges rows, so a genuine denial can fail by ONE character ("No, I haven't noticed anything like that." = 41 chars with the corrected pass's trailing period) and flag a TRUE sentence.
  3. The honest-absence exemption requires a literal negation token (no/not/none/without/absent), so factually-true scribe phrasings like "The consultation concludes before clinical examination is performed" flag as fabricated exam findings.
- **Evidence:** 2026-07-08 (UTC) manual round. Session `203d1d35-2467-4bd0-944b-7002825004d6` (day3-consultation01, cut mid-question): the note's fabricated "She denies prior history of lip swelling..." - the patient never answered - passed with ZERO logged violations via mechanism (1). Session `d97a9bde-07c9-4bc1-b430-a5d19813f48d` (c03 @3:02): `summary.fidelity_flagged flagged=3`, and re-running `find_fidelity_violations` against the PERSISTED corrected rows reproduces exactly those 3 violations - all false positives via mechanisms (2) and (3), and the regeneration went 1 violation (attempt 0) to 3 (attempt 1). Field record for the day: 3 false positives shipped as visible flags, 1 false negative shipped clean, 0 confirmed true positives. Repros: `.goat-flow/plans/0.4.0/tools/m10-repro-fabricated-denial.py` and `m10-repro-c03-false-positives.py`.
- **Prevention:** 0.4.0 M10 owns the fixes. Until then: a CLEAN fidelity log on a chief-complaint denial proves nothing, and flags on scribe-true meta sentences ("concludes before examination...") or on denials answered with a terse punctuated "No..." row are presumed false positives - check against the persisted rows (patterns/verification.md, search: "persisted rows fetched from the agent API") before acting on either.
