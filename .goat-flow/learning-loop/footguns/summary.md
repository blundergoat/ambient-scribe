---
category: summary
last_reviewed: 2026-07-10
---

# Summary / Note-Generation Footguns

## Footgun: 30 s post-finalize grace destroys visit audio under the on-demand summary button

**Added:** 2026-07-16 · **Evidence:** session `61747213` (real-time 5:38 consult-5.3 replay)
**Mitigation shipped same day (M12):** finalized sessions keep audio for
`SESSION_POST_VISIT_AUDIO_RETENTION_SECONDS` (default 900) via
`_stream_cleanup_grace_seconds`, and the browser warms the free correction on `finalized`.
Pending the user's live re-proof before this entry moves to Resolved.

`session_lifecycle.py:143` schedules session destruction 30 s after `finalized`
(`destroy_scheduled` → `grace_period_expired`), taking the audio buffer with it. The
correction endpoint's first availability check (`api/server.py:599`) then returns
`audio_expired` → the note silently downgrades to the live lane. This was invisible while
the summary AUTO-fired within milliseconds of finalize; M11's Generate-summary button made
click delay a user variable, so any user who reads the transcript first (>30 s) loses the
corrected pass. Proof: finalized 06:31:12.2, grace expired 06:31:42.2, user click 06:33:04
→ `correction.segments_merged` then 200 in 1 ms with no GPU pass and no
`correction.completed`. Yesterday's passes only worked because clicks/scripts came ≤7 s
after finalize. The M11 pause investigation checked mid-visit gaps (WS idle, TTL 7200 s,
audio-time windows) but never re-checked this post-finalize timer against the new
user-controlled delay. Compounding trap: the uncited fallback lane then mislabels every
claim (see the absence-mislabel entry in this file's backlog reference,
`summary_request.py:268` + `summary_generation.py:973-981`).

## Footgun: Fidelity denial-verification remains row-shape sensitive across split questions

**Status:** active | **Created:** 2026-07-09 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/summary_fidelity.py` (search: "_did_patient_deny_topic")
- **Files:** `strands_agents/api/summary_fidelity.py` (search: "_is_short_denial_answer")
- **Files:** `strands_agents/api/summary_fidelity.py` (search: "_matched_topic_words")
- **Files:** `strands_agents/api/summary_fidelity.py` (search: "_EXAM_ABSENCE_PATTERN")
- **What breaks:** Three heuristics make the deterministic checker's verdict depend on transcript row SHAPE rather than content:
  1. A claimed denial verifies when ANY topic stem co-occurs with ANY negation token in a single patient row. A long monologue row about the chief complaint ("...my lips start feeling... I don't know what to do") therefore launders ANY denial claim about the chief-complaint topic - the most clinically dangerous denials to fabricate are the ones the checker is structurally weakest on.
  2. A bare "No." answer only verifies via the recent-rows path when the denial row is <= 40 chars. The corrected lane adds punctuation and re-merges rows, so a genuine denial can fail by ONE character ("No, I haven't noticed anything like that." = 41 chars with the corrected pass's trailing period) and flag a TRUE sentence.
  3. The honest-absence exemption requires a literal negation token (no/not/none/without/absent), so factually-true scribe phrasings like "The consultation concludes before clinical examination is performed" flag as fabricated exam findings.
- **Evidence:** 2026-07-08 (UTC) manual round. Session `203d1d35-2467-4bd0-944b-7002825004d6` (day3-consultation01, cut mid-question): the note's fabricated "She denies prior history of lip swelling..." - the patient never answered - passed with ZERO logged violations via mechanism (1). Session `d97a9bde-07c9-4bc1-b430-a5d19813f48d` (c03 @3:02): `summary.fidelity_flagged flagged=3`, and re-running `find_fidelity_violations` against the PERSISTED corrected rows reproduces exactly those 3 violations - all false positives via mechanisms (2) and (3), and the regeneration went 1 violation (attempt 0) to 3 (attempt 1). Field record for the day: 3 false positives shipped as visible flags, 1 false negative shipped clean, 0 confirmed true positives. Repros: `.goat-flow/plans/0.4.0-slice-1/tools/m10-repro-fabricated-denial.py` and `m10-repro-c03-false-positives.py`.
- **Prevention:** Treat denial flags as candidates until they are checked against the exact persisted rows. In particular, inspect whether a clinician list was split across several rows before a short patient `no`; `_did_patient_deny_topic` checks each prior clinician row independently, so a multi-word topic distributed across rows can flag a true denial. Persisted-row verification remains the release gate (patterns/verification.md, search: "persisted rows fetched from the agent API").
- **Partial resolution (2026-07-10, M10):** the original three mechanisms are fixed and pinned. (1) Denial evidence is clause-scoped with epistemic phrases masked first (`summary_fidelity.py`, search: "_denial_evidence_clauses" and "_mask_epistemic_phrases"); multi-word topics need two matched words in ONE clause, and a note sentence admitting its question went unanswered fails outright (search: "_UNANSWERED_ADMISSION_PATTERN"). The day3 fabricated denial now flags with the monologue present. (2) The 40-character gate is replaced by an eight-lexical-word answer that must OPEN with a denial word after a DOCTOR question naming the topic (search: "_is_short_denial_answer") - the 41-character corrected rash denial verifies, and "...but no no" mid-row negations no longer do. (3) Honest absence/intent frames ("concludes before examination", "prior to examination", "not yet performed", "proposed ... examination") are exempt while performed-exam claims still flag (search: "_EXAM_INTENT_PATTERN"). Worse-retry selection ships the fewer-violations draft (`summary_generation.py`, search: "fidelity_draft_selected").
- **Reopened (2026-07-10, M11 replay audit):** the five-generation day5 audit found false denial flags for explicit answers whose clinician topics were fragmented across rows (cold/respiratory, GI/urinary, and joint swelling), plus an accurate `no recall of infections` phrase classified as a denial. Evidence is recorded in `.goat-flow/plans/0.4.0-slice-1/M11-note-phrasing-fidelity.md` (search: "unrelated `negative-without-denial`"). M11 intentionally did not change M10 denial logic.
- **Partial resolution (2026-07-11, 0.4.0-slice-2 M02):** split-question aggregation is resolved.
  The checker joins at most six clinician fragments per bounded answer
  (`strands_agents/api/summary_fidelity.py`, search:
  "_recent_denial_question_context") only when the immediate follow-up already overlaps the
  same topic (search: "_immediate_denial_question_context"), narrowly recovers a patient
  `No` folded onto the next clinician row (search: "_folded_patient_denial_answer"), and
  stops denial scope at a contrasting reported fact (search:
  "_DENIAL_TOPIC_SCOPE_BOUNDARY_PATTERN"). The exact two
  c03 warnings clear; all 21 retained day5 split-question occurrences are covered by three
  pinned families; a clean five-generation c03 audit found 16 supported denials, zero false
  denial flags, and zero unflagged fabricated denials. Evidence:
  `var/quality/m02-fidelity-denial-precision-20260711T083957Z/` (search:
  "c03-campaign-clean-audit.md"). The separate accurate `reports no recall of infections`
  classification remains open, so this footgun stays active.

## Resolved Entries

## Footgun: Summary context was head-truncated at 8000 chars - long consults silently lost Assessment and Plan

**Status:** resolved | **Created:** 2026-07-09 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/summary_request.py` (search: "SUMMARY_TRANSCRIPT_MAX_CHARS")
- **Files:** `strands_agents/api/summary_request.py` (search: "def select_summary_segments")
- **Files:** `strands_agents/api/server.py` (search: "transcript_truncated")
- **Files:** `public/js/scribe-output.js` (search: "function setSummaryTruncationNotice")
- **What broke:** The three summary context paths truncated INCONSISTENTLY (the original "all three keep the first 8000 characters" wording was wrong): `browser_visible_segments` kept only the first 8000 characters (the field-proven head slice), `session_store` kept a 500-character head plus the tail (losing the middle), and `corrected_segments` built an 8000-character head-plus-tail string that fed retrieval while the normal cited prompt bypassed the cap entirely through the uncapped citation source index. The clinically decisive Assessment and Plan are normally spoken last, so a long consultation on the browser-visible lane could produce a fluent note that falsely said those sections were not documented. No warning or browser-visible metadata exposed the omitted input.
- **Evidence:** The 2026-07-08 full day5-consultation09 run lost suspected Lyme disease, support-line booking, and the one-week follow-up after logging exactly 8000 characters. M08 replayed the same 306 captured browser rows in fresh session `b4d40534-818b-4672-8d3e-5ffea855e09b`; all 9,705 characters reached generation and the note retained Lyme assessment, blood/Lyme testing, support-line booking, and one-week phone follow-up (`.goat-flow/plans/0.4.0-slice-1/M08-summary-context-tail-loss.md`, search: "Phase 5 evidence").
- **Resolution:** All corrected, browser-visible, and stored lanes now use one measured 32,768-character whole-row selector. Every measured consultation fits in full; larger future inputs keep complete opening and closing rows with an explicit non-row elision marker. Prompt text, citations, retrieval input, and fidelity evidence use the same selected rows. Real elision emits a structured warning and additive HTTP/Mercure metadata, and the browser keeps a persistent accessible notice beside the note status.

## Fidelity-retry regeneration can exceed max_tokens on long visits (v2 structured output)

- **Files:** `strands_agents/agents/summary_agent.py` (search: "SUMMARY_AGENT_MAX_TOKENS")
- **Files:** `strands_agents/api/summary_generation.py` (search: "regeneration_feedback")
- **What broke:** M07 certification request 2 (consult 5.3, 776 s of audio, 435 corrected rows,
  30,825 prompt chars): attempt 0 completed but carried 3 fidelity violations, and the bounded
  retry - base prompt PLUS per-violation feedback - pushed the v2 structured output past the
  frozen 4,096-token cap. Strands raised MaxTokensReachedException mid-JSON, the route returned
  502, and the frozen campaign rules stopped the whole certification. The M06 spike had flagged
  exactly this ceiling (tokens_out 3,923/4,096 on the same consult's biggest draft) as a watch
  item; the fresh replay's retry crossed it.
- **Evidence:** `var/quality/m07-campaign-20260715T222402Z/consult-5.3-request2-failure.log`;
  spike telemetry `var/quality/m06-schema-spike-*/spike-report.json` (gen 3: tokens_out 3923).
- **Resolution:** M10 (2026-07-16, post-campaign, user-approved): `SUMMARY_AGENT_MAX_TOKENS`
  default raised 4096→8192 (2x headroom over the largest observed clean draft; model ceiling
  64K), and `MaxTokensReachedException` now maps to an honest `note_output_limit` 502 instead
  of the generic "model unavailable" path. Live-proven on a fresh 5.3 replay: both generations
  incl. the previously-fatal retry completed (session b717aa5f, 45.5 s, 48 claims). The M07
  campaign artifacts and FAIL verdict are unchanged; re-certification needs a new campaign.

## Claim-level wording cue majority-votes across the unit and masks medication rows

- **Files:** `strands_agents/api/summary_generation.py` (search: "_sentence_needs_wording_review")
- **What broke:** M07 note 1 (consult 1.2) stated "Piriteze" where the official track says
  "Piriton" - a different antihistamine product. The source rows ("Luratidine" 0.656, "Pyritin"
  0.737) are BELOW the 0.78 corrected review threshold, but the claim cites a ~10-row unit whose
  other rows are confident, and the per-claim wording cue requires the cited wording to be
  PREDOMINANTLY low-confidence - so no cue rendered on an unmarked definite medication
  misidentification. Unit-level majority is the wrong denominator when the risk lives in one or
  two rows; medication names are exactly where that happens.
- **Evidence:** `var/quality/m07-campaign-20260715T222402Z/consult-1.2/worksheet-scored.json`
  (claim plan-04); corrected rows 0268/0269 in the same campaign's replay dir.
- **Resolution:** none inside M07 (detector changes are frozen). Candidate re-scope: row-scoped
  cue for medication-bearing tokens, or M04 lexicon-gated per-row threshold - needs its own
  precision gate per the M05 detector contract.
