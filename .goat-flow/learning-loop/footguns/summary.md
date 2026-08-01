---
category: summary
last_reviewed: 2026-08-01
---

# Summary / Note-Generation Footguns

## Footgun: The summariser silently repairs garbled drug names and can land on a different drug

**Status:** active | **Created:** 2026-08-01 | **Evidence:** OBSERVED

- **Files:** `strands_agents/api/summary_fidelity.py` (search: `_NO_MEDICATIONS_CLAIM_PATTERN`)
- **Files:** `strands_agents/api/summary_fidelity.py` (search: `_medication_scope_reasons`)
- **Files:** `strands_agents/data/medical_lexicon.txt`
- **What breaks:** The note generator normalises ASR garbles into plausible clinical
  vocabulary as a side effect of writing fluent prose. That silently succeeds often enough to
  look like a feature, and there is **no check that a medication named in the note was ever
  spoken**. The only medication rule in the fidelity checker is a *scope* rule — it flags
  "no current medications" when the patient described taking some. Nothing requires a drug
  *name* to be grounded in the source rows, so an invented one ships unflagged.
- **Evidence:** 2026-08-01 manual round, consult-1.2 (`primock57-day1-consultation02`),
  replay complete, 310 segments, roles 98%. Four garbles reached the note; the summariser
  rewrote all four and got one badly wrong:

  | Spoken | Live lane | Corrected lane (what the summariser read) | Note produced | Correct |
  | --- | --- | --- | --- | --- |
  | emollients | `amoleans` / `amolliums` | — | emollients | ✅ |
  | fexofenadine | `fsapenedine` | — | `fexafenadine` | ❌ misspelt |
  | loratadine | `Lauratidine` | — | `loratidine` | ❌ misspelt |
  | Piriton | `Puritan` | **`pyritin`** (`corrected-0271`) | **`pyrithamine`** | ❌ **different drug class** |

  **The corrected row is the sharp part.** `pyritin` is a *registered lexicon variant* of
  canonical `Piriton` — the lexicon holds the correct mapping and ADR-011's bypass is the only
  reason it never applied. The summariser was then handed a garble the system already knew how
  to fix, and guessed a different drug.

  `pyrithamine` appears **nowhere in either transcript lane** — it is generated, not transcribed. The
  Plan section states it is "available over the counter" as an antihistamine alternative.
  Piriton is chlorphenamine, a sedating antihistamine genuinely sold OTC in the UK.
  Pyrimethamine is a prescription-only antiparasitic used in toxoplasmosis and malaria, with
  bone-marrow toxicity. The generated token sits phonetically between the two and reads as a
  real drug name, which makes it far more dangerous than the raw ASR garble `Puritan` — a
  clinician skim-signing the note would catch "Puritan" and might not catch "pyrithamine".

  The two fidelity flags this note *did* raise were both correct (fabricated denials of
  "purulent discharge" and "temperature changes"). The checker is working; this class is
  simply outside what it inspects.
- **Why the lexicon did not save it:** all three drugs are registered canonicals, and all
  three produced variants missed:

  | Lexicon entry | ASR produced | Matched? |
  | --- | --- | --- |
  | `fexofenadine\|Fexaphenidine` | `fsapenedine` | no |
  | `loratadine\|Luratidine` | `Lauratidine` | no — one letter |
  | `Piriton\|Pyritin` | `Puritan` | no |

  Exact-variant replacement cannot generalise: `Luratidine` and `Lauratidine` differ by one
  character and the lexicon treats them as unrelated. But note the two failures are *different*:
  the live lane missed on coverage, while the corrected lane produced a **registered** variant
  (`pyritin`) that ADR-011's bypass prevented from being mapped. Widening the variant list would
  not have saved the note; only reaching the corrected lane would.
- **Prevention:** Do not read a fluent, correctly-spelled drug name in a note as evidence the
  drug was said. Ground every medication name against the source rows before trusting it. When
  scoring a consult, diff the note's clinical nouns against the transcript's — matching
  *counts* hides substitutions, because a wrong drug and a right drug both count as one drug.
  Treat a garble the summariser "fixed" as unverified until the audio says otherwise.
- **Open:** no medication-name grounding rule exists. Adding one is a summary-fidelity change
  and is Ask First; it is recorded as a finding, not a fix.

## Footgun: A resumed visit's stale corrected artifact deadlocks the note as stale lineage

**Status:** active | **Created:** 2026-07-17 | **Evidence:** OBSERVED

**Incident evidence:** PR #5 Codex P2 finding, confirmed in-repo; regression tests in
`tests/python/test_terminal_source_integrity.py` (search: "stale_corrected_artifact_cannot_block")
**Mitigation shipped same day:** resume clears the corrected artifact with the watermark
(`strands_agents/api/streaming_session.py`, search: "replace_corrected_segments(session_id, [])"),
and corrected rows win summary source selection only when the current watermark attests them
(`strands_agents/api/summary_request.py`, search: "_corrected_rows_are_current").

Stop → Start on the same page is a resume: `startRecording` reuses `CONFIG.sessionId`, so
`_resume_or_create_session` discards the terminal watermark - but the corrected artifact from
the first finalize survived in storage. After the next Stop, any bounded correction failure
(`audio_expired`, `gpu_transient`, timeout) authorized the live fallback, while
`build_summary_context` still preferred the stale corrected rows;
`_resolve_summary_source_state` then returned `stale_lineage` on every Generate click.
Deadlock: the correction lane says "use the live transcript", the summary lane refuses it, and
after the row-correction UI removal no reachable code path cleared the artifact. Rule: state
derived from a terminal watermark must be invalidated everywhere the watermark is invalidated.

## Footgun: 30 s post-finalize grace destroys visit audio under the on-demand summary button

**Status:** active | **Created:** 2026-07-16 | **Evidence:** ACTUAL_MEASURED

**Incident evidence:** session `61747213` (real-time 5:38 consult-5.3 replay)
**Mitigation shipped same day:** finalized sessions keep audio for
`SESSION_POST_VISIT_AUDIO_RETENTION_SECONDS` (default 900) via
`_stream_cleanup_grace_seconds`, and the browser warms the free correction on `finalized`.
Pending the user's live re-proof before this entry moves to Resolved.

`strands_agents/session_lifecycle.py` (search: "session_lifecycle.destroy_scheduled") schedules session destruction 30 s after `finalized`
(`destroy_scheduled` → `grace_period_expired`), taking the audio buffer with it. The
correction endpoint's first availability check (`strands_agents/api/server.py`, search:
"Session audio is no longer available for correction.") then returns
`audio_expired` → the note silently downgrades to the live lane. This was invisible while
the summary AUTO-fired within milliseconds of finalize; the on-demand Generate-summary button made
click delay a user variable, so any user who reads the transcript first (>30 s) loses the
corrected pass. Proof: finalized 06:31:12.2, grace expired 06:31:42.2, user click 06:33:04
→ `correction.segments_merged` then 200 in 1 ms with no GPU pass and no
`correction.completed`. Yesterday's passes only worked because clicks/scripts came ≤7 s
after finalize. The pause investigation checked mid-visit gaps (WS idle, TTL 7200 s,
audio-time windows) but never re-checked this post-finalize timer against the new
user-controlled delay. Compounding trap: the uncited fallback lane then mislabels every
claim (see the absence-mislabel entry in this file's backlog reference,
`strands_agents/api/summary_request.py` (search: "browser_visible_segments") and
`strands_agents/api/summary_generation.py` (search: "transcript_absence")).

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
- **Evidence:** 2026-07-08 (UTC) manual round. Session `203d1d35-2467-4bd0-944b-7002825004d6` (day3-consultation01, cut mid-question): the note's fabricated "She denies prior history of lip swelling..." - the patient never answered - passed with ZERO logged violations via mechanism (1). Session `d97a9bde-07c9-4bc1-b430-a5d19813f48d` (c03 @3:02): `summary.fidelity_flagged flagged=3`, and re-running `find_fidelity_violations` against the PERSISTED corrected rows reproduces exactly those 3 violations - all false positives via mechanisms (2) and (3), and the regeneration went 1 violation (attempt 0) to 3 (attempt 1). Field record for the day: 3 false positives shipped as visible flags, 1 false negative shipped clean, 0 confirmed true positives. The retained regression specimens are in `tests/python/test_summary_fidelity.py` (search: "test_fabricated_lip_denial_is_caught_despite_the_monologue") and the nearby c03 denial tests.
- **Prevention:** Treat denial flags as candidates until they are checked against the exact persisted rows. In particular, inspect whether a clinician list was split across several rows before a short patient `no`; `_did_patient_deny_topic` checks each prior clinician row independently, so a multi-word topic distributed across rows can flag a true denial. Persisted-row verification remains the release gate (patterns/verification.md, search: "persisted rows fetched from the agent API").
- **Partial resolution (2026-07-10):** the original three mechanisms are fixed and pinned. (1) Denial evidence is clause-scoped with epistemic phrases masked first (`summary_fidelity.py`, search: "_denial_evidence_clauses" and "_mask_epistemic_phrases"); multi-word topics need two matched words in ONE clause, and a note sentence admitting its question went unanswered fails outright (search: "_UNANSWERED_ADMISSION_PATTERN"). The day3 fabricated denial now flags with the monologue present. (2) The 40-character gate is replaced by an eight-lexical-word answer that must OPEN with a denial word after a DOCTOR question naming the topic (search: "_is_short_denial_answer") - the 41-character corrected rash denial verifies, and "...but no no" mid-row negations no longer do. (3) Honest absence/intent frames ("concludes before examination", "prior to examination", "not yet performed", "proposed ... examination") are exempt while performed-exam claims still flag (search: "_EXAM_INTENT_PATTERN"). Worse-retry selection ships the fewer-violations draft (`summary_generation.py`, search: "fidelity_draft_selected").
- **Reopened (2026-07-10, replay audit):** the five-generation day5 audit found false denial flags for explicit answers whose clinician topics were fragmented across rows (cold/respiratory, GI/urinary, and joint swelling), plus an accurate `no recall of infections` phrase classified as a denial. The retained split-question specimens are in `tests/python/test_summary_fidelity.py` (search: "test_day5_split_respiratory_question_supports_the_composite_denial" and "test_day5_joint_swelling_denial_ignores_the_reported_pain_clause"). That change intentionally did not touch the earlier denial logic.
- **Partial resolution (2026-07-11, 0.4.0-slice-2):** split-question aggregation is resolved.
  The checker joins at most six clinician fragments per bounded answer
  (`strands_agents/api/summary_fidelity.py`, search:
  "_recent_denial_question_context") only when the immediate follow-up already overlaps the
  same topic (search: "_immediate_denial_question_context"), narrowly recovers a patient
  `No` folded onto the next clinician row (search: "_folded_patient_denial_answer"), and
  stops denial scope at a contrasting reported fact (search:
  "_DENIAL_TOPIC_SCOPE_BOUNDARY_PATTERN"). The exact two
  c03 warnings clear; all 21 retained day5 split-question occurrences are covered by three
  pinned families; a clean five-generation c03 audit found 16 supported denials, zero false
  denial flags, and zero unflagged fabricated denials. That clean-audit campaign was a
  local-only artifact; the pinned families are enforced by `tests/python/test_summary_fidelity.py`.
  The separate accurate `reports no recall of infections`
  classification remains open, so this footgun stays active.

## Footgun: One action proposition can launder another action's completion state

**Status:** active | **Created:** 2026-07-19 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/summary_fidelity.py` (search: "_action_completion_reasons")
- **Files:** `strands_agents/api/summary_fidelity.py` (search: "_rows_with_any_token")
- **Files:** `strands_agents/api/summary_fidelity.py` (search: "_ACTION_COMPLETION_EVIDENCE_PATTERN")
- **What breaks:** the action reviewer builds object tokens from a whole compound note sentence, admits a
  source row after any one-token overlap, and treats bare `arranging` as completion evidence. A prospective
  GP-follow-up row can therefore suppress the existing warning on a blood-test `arranged` claim. Missing
  `scheduled`, `booked`, and `completed` claim verbs create independent false negatives.
- **Evidence:** the exact persisted consult-5.3 Plan replay returned zero reasons. Removing its follow-up
  clause, removing the row containing `arranging`, or changing only that gerund to `planning` restored
  `action_not_confirmed_done`. The production pattern matched `arranged` but none of the three missing verbs.
- **Prevention:** never merge persisted rows when characterising fidelity logic. Freeze exact row boundaries,
  then require claim and source completion evidence to share the same proposition and clinical object.

## Resolved Entries

## Footgun: Summary context was head-truncated at 8000 chars - long consults silently lost Assessment and Plan

**Status:** resolved | **Created:** 2026-07-09 | **Evidence:** ACTUAL_MEASURED

- **Files:** `strands_agents/api/summary_request.py` (search: "SUMMARY_TRANSCRIPT_MAX_CHARS")
- **Files:** `strands_agents/api/summary_request.py` (search: "def select_summary_segments")
- **Files:** `strands_agents/api/server.py` (search: "transcript_truncated")
- **Files:** `public/js/scribe-output.js` (search: "function setSummaryTruncationNotice")
- **What broke:** The three summary context paths truncated INCONSISTENTLY (the original "all three keep the first 8000 characters" wording was wrong): `browser_visible_segments` kept only the first 8000 characters (the field-proven head slice), `session_store` kept a 500-character head plus the tail (losing the middle), and `corrected_segments` built an 8000-character head-plus-tail string that fed retrieval while the normal cited prompt bypassed the cap entirely through the uncapped citation source index. The clinically decisive Assessment and Plan are normally spoken last, so a long consultation on the browser-visible lane could produce a fluent note that falsely said those sections were not documented. No warning or browser-visible metadata exposed the omitted input.
- **Evidence:** The 2026-07-08 full day5-consultation09 run lost suspected Lyme disease, support-line booking, and the one-week follow-up after logging exactly 8000 characters. A later replay reran the same 306 captured browser rows in fresh session `b4d40534-818b-4672-8d3e-5ffea855e09b`; all 9,705 characters reached generation and the note retained Lyme assessment, blood/Lyme testing, support-line booking, and one-week phone follow-up. The aligned whole-row selection contract is pinned in `tests/python/test_summary.py` (search: "test_over_budget_corrected_rows_align_prompt_citations_and_fidelity").
- **Resolution:** All corrected, browser-visible, and stored lanes now use one measured 32,768-character whole-row selector. Every measured consultation fits in full; larger future inputs keep complete opening and closing rows with an explicit non-row elision marker. Prompt text, citations, retrieval input, and fidelity evidence use the same selected rows. Real elision emits a structured warning and additive HTTP/Mercure metadata, and the browser keeps a persistent accessible notice beside the note status.

## Fidelity-retry regeneration can exceed max_tokens on long visits (v2 structured output)

- **Files:** `strands_agents/agents/summary_agent.py` (search: "SUMMARY_AGENT_MAX_TOKENS")
- **Files:** `strands_agents/api/summary_generation.py` (search: "regeneration_feedback")
- **What broke:** A certification request (consult 5.3, 776 s of audio, 435 corrected rows,
  30,825 prompt chars): attempt 0 completed but carried 3 fidelity violations, and the bounded
  retry - base prompt PLUS per-violation feedback - pushed the v2 structured output past the
  frozen 4,096-token cap. Strands raised MaxTokensReachedException mid-JSON, the route returned
  502, and the frozen campaign rules stopped the whole certification. An earlier schema spike had flagged
  exactly this ceiling (tokens_out 3,923/4,096 on the same consult's biggest draft) as a watch
  item; the fresh replay's retry crossed it.
- **Evidence:** Campaign failure log and spike telemetry were local-only; the measured figures
  above (tokens_out 3,923 of a 4,096 cap) are the record.
- **Resolution:** 2026-07-16, post-campaign, user-approved: `SUMMARY_AGENT_MAX_TOKENS`
  default raised 4096→8192 (2x headroom over the largest observed clean draft; model ceiling
  64K), and `MaxTokensReachedException` now maps to an honest `note_output_limit` 502 instead
  of the generic "model unavailable" path. Live-proven on a fresh 5.3 replay: both generations
  incl. the previously-fatal retry completed (session b717aa5f, 45.5 s, 48 claims). That
  campaign's artifacts and FAIL verdict are unchanged; re-certification needs a new campaign.

## Claim-level wording cue majority-votes across the unit and masks medication rows

- **Files:** `strands_agents/api/summary_generation.py` (search: "_sentence_needs_wording_review")
- **What broke:** A certification note (consult 1.2) stated "Piriteze" where the official track says
  "Piriton" - a different antihistamine product. The source rows ("Luratidine" 0.656, "Pyritin"
  0.737) are BELOW the 0.78 corrected review threshold, but the claim cites a ~10-row unit whose
  other rows are confident, and the per-claim wording cue requires the cited wording to be
  PREDOMINANTLY low-confidence - so no cue rendered on an unmarked definite medication
  misidentification. Unit-level majority is the wrong denominator when the risk lives in one or
  two rows; medication names are exactly where that happens.
- **Evidence:** The scored worksheet and corrected rows 0268/0269 were local-only campaign
  artifacts; the confidence values above are the record.
- **Resolution:** none at the time (detector changes were frozen). Candidate re-scope: row-scoped
  cue for medication-bearing tokens, or a lexicon-gated per-row threshold - needs its own
  precision gate per the detector contract.
