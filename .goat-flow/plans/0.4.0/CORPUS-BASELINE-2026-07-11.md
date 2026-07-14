# Full-Corpus Baseline - all 20 fixtures, full length, 1x (2026-07-11)

**Status: COMPLETE.** First quality baseline covering the full current corpus at full length
(M01's canonical table was 7 fixtures @60s). User-commissioned: "manually test all 20 of the
fixture demo audio and measure the accurate and quality of the results." Every fixture
exceeds the 240s one-shot envelope, so this run also exercised the M09 chunked correction
lane 20-for-20 - and found and fixed one real defect along the way.

## Method

- `CORRECTED_FIXTURE_RUN_DIR=var/quality/full-corpus-20260710T2328Z EVAL_PACE=1x
  scripts/eval-corrected-fixtures.sh --all` (browser-faithful 1x pacing, 5000ms chunks,
  real WebSocket -> NeMo -> correction -> storage path), scored per fixture against
  doctor+patient TextGrids. 186.8 minutes of audio total.
- Clean agent restart at sweep start (CUDA True); 10s GPU sampling throughout; per-fixture
  log greps for NeMo internal errors (zero all night).
- Metrics below are the non-overlap lane (overlapped speech is scored separately by the
  reports; see per-fixture `*-transcript-quality.txt`). Str = strict speaker attribution;
  Bad = incorrect-confident rate.
- Full artifacts + run manifest (binary boundaries, anomalies, correction ledger):
  `var/quality/full-corpus-20260710T2328Z/` (`run-manifest.md`).

## The table (non-overlap; live lane vs corrected lane)

| fixture | liveStr | corrStr | liveWER | corrWER | liveBad | corrBad |
|---|---:|---:|---:|---:|---:|---:|
| day1-c02 sore-red-skin | 86.5 | 86.5 | 27.4 | 22.7 | 13.1 | 13.1 |
| day1-c03 terrible-headache | 87.1 | 86.7 | 25.9 | 21.3 | 12.9 | 13.3 |
| day1-c04 cough-runny-nose | 89.6 | 90.3 | 27.7 | 21.5 | 10.4 | 9.7 |
| day1-c05 lower-abdominal-pain | 90.4 | 90.4 | 24.4 | 19.9 | 9.6 | 9.6 |
| day1-c06 hard-to-breathe | 84.6 | 83.9 | 29.5 | 21.8 | 15.1 | 15.8 |
| day1-c07 cough-and-cold (14.3m) | 88.2 | 87.3 | 41.4 | 32.2 | 11.8 | 12.7 |
| day1-c08 dry-itchy-skin | 86.1 | 86.6 | 32.2 | 25.4 | 13.9 | 13.4 |
| day2-c01 dont-hear-as-well | 97.6 | 96.4 | 22.8 | 16.7 | 2.4 | 3.6 |
| day2-c02 elbow-swelling | 91.5 | 91.5 | 26.9 | 20.4 | 8.1 | 8.1 |
| day2-c03 cant-hear-face-numb | 93.6 | 92.5 | 32.0 | 28.2 | 6.4 | 7.5 |
| day2-c07 chest-discomfort | 86.8 | 87.7 | 29.0 | 23.6 | 13.2 | 12.3 |
| day2-c08 hot-and-sweaty | 90.3 | 88.6 | 24.6 | 18.0 | 9.7 | 11.4 |
| day2-c09 cant-move-left-arm | 96.0 | 95.5 | 24.8 | 19.3 | 4.0 | 4.5 |
| day3-c01 lips-swelling | 90.0 | 89.5 | 31.2 | 24.2 | 10.0 | 10.5 |
| day3-c03 no-appetite-energy | 85.5 | 86.9 | 27.8 | 25.0 | 14.5 | 13.1 |
| day3-c05 feeling-dizzy | 84.7 | 84.2 | 22.5 | 17.7 | 14.8 | 15.8 |
| day5-c03 very-anxious | 85.7 | 87.5 | 21.1 | 16.5 | 5.5 | 5.2 |
| day5-c04 lower-stomach-pain | 91.4 | 90.9 | 32.5 | 25.7 | 8.6 | 9.1 |
| day5-c08 wheezy | 90.3 | 89.1 | 18.8 | 15.6 | 9.7 | 10.9 |
| day5-c09 tired-all-the-time | 87.7 | 87.7 | 27.4 | 20.8 | 12.3 | 12.3 |

## Corpus aggregates

- **Corrected WER improves on 20/20 fixtures**: live mean 27.5% -> corrected mean 21.8%
  (mean +5.7pp, worst +2.8, best +9.2). The second pass earns its keep universally.
- **Strict attribution passes through correction unchanged**: live mean 89.2 vs corrected
  mean 89.0 (-0.2pp, spread -1.7..+1.8) - by design, roles ride the live scaffold.
- Live strict range 84.6 (day1-c06) to 97.6 (day2-c01); median 88.9.
- Incorrect-confident rate ~10% both lanes (correction fixes words, not attribution).
- WER outlier: day1-c07 (41.4 live / 32.2 corrected) - the corpus's longest, most
  overlap-heavy clip; largest absolute WER but also a large correction gain (+9.2pp).
- Source chips: 62 errors over 6,073 corrected rows (~1.0%), range 0 (day5-c09) to 8
  (day2-c02), dominated by `patient_statement_in_doctor_row` - the known cross-talk
  bleed family (streaming-engine territory, ISSUE.md follow-up), not an M09/correction
  defect. Full breakdown: `source-chip-score.txt` (its artifacts=21 includes the preserved
  pre-fix c03 duplicate; canonical count is the 20 rows above).

## Correction health (M09 chunked lane, 20-for-20)

All 20 canonical corrections completed healthy: 12.2-21.2s each, chunk counts 2-5,
attempts=1, zero retries, zero NeMo internal errors, GPU bounded (~11.5/16.3 GiB peak
during the dirtiest correction). Two superseded pre-fold attempts recorded in the manifest:
c03's 4-chunk pre-fix pass and day2-c02's `empty_result` failure (below). Includes the
first-ever 5-chunk corrections (day1-c07 14.3m; day5-c03 12.9m) and the fold-confirmation
on day5-c04 (3.6s tail folded to 3 chunks).

## Findings

1. **Tail-sliver correction veto - FOUND, FIXED, REVALIDATED.** day2-c02 (541.56s) chunked
   to 180/180/180/1.56s; the sliver decoded empty and the empty-chunk guard vetoed the
   whole correction (`empty_result`, honest live fallback + notice - M09 behavior all
   correct; the gap was chunk construction). Fix: remainders under
   `_MIN_FINAL_CHUNK_SECONDS` (10s) ride inside the final chunk; a full-size chunk decoding
   empty still fails loudly. Red-test-first (51 tests in the correction file, full suite
   572); live re-run of the failing fixture then corrected healthy (13.5s, 3 chunks), and
   day5-c04's 3.6s tail confirmed the fold. Staged with the M09 commit set. Failure
   evidence: `day2-consultation02-FIELD-FAILURE-empty-result-partial/` in the run dir.
2. **Eval-runner corpus fragility - FOUND, not fixed (out of scope).**
   `scripts/eval-corrected-fixtures.sh:428` SystemExits on any unavailable correction;
   under `set -e` in `--all` mode one failed fixture forfeits the rest (killed the original
   runner at fixture 9/20). Proposed follow-up: corpus mode records the failure and
   continues.
3. **Comparison with M01 (methodology differs - NOT a regression).** M01's canonical
   corrected strict @60s: c02 100.0 / c03 96.8 / c08 93.8. Full length: 86.5 / 86.7 / 86.6.
   The @60s window covers the clean consultation opening; full length includes the whole
   visit's overlap spans, role churn, and long-turn dynamics. Tonight's M09 GPU trio re-run
   @60s reproduced M01 byte-identically, so the @60s gate itself is unchanged. Treat this
   table as the FIRST full-length reference, not a drift signal against M01.

## Recommendations (in priority order)

1. Streaming-engine cluster milestone (Ask First): cross-talk bleed / dual-identity /
   emission-hold / c08 bifurcation - the strict-attribution and chip-error ceiling lives
   here. This corpus run now provides per-fixture bleed evidence (row diagnostics + chip
   findings) to seed it.
2. Reopened M10 denial precision (split clinician questions) - small, repro'd, checker-scoped.
3. UX-M7 confidence styling - per-row confidence is persisted corpus-wide; styling unblocks
   the garble-hedging mitigation.
4. Eval-runner corpus-mode failure tolerance (finding 2).
5. WER work is 0.5.0 territory (lexicon plateau -> conditional Parakeet fine-tune); this
   table is the before-picture it will be judged against.

## M08 note-lane companion (2026-07-14)

M08 generated one final note immediately after each fresh full-length corrected replay. The
sample used best, nearest-median, and worst corrected WER from the accepted M06 corpus plus the
three historical note-defect visits. Every section sentence and key point was then checked
against that visit's corrected rows and, where needed, its Doctor/Patient TextGrid.

**Verdict: FAIL.** Only 1/6 notes is content-clean. Across 209 substantive claims, 21 are
unsupported and unflagged (10.0%); the worst-WER note also exceeds the summary context cap.

| fixture | sample role | claims | unsupported | fidelity TP/FP | wording TP/FP | citations | input |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| day5-c03 anxiety | best WER | 52 | 1 | 0/1 | 0/1 | 24/24 | full |
| day1-c04 cough | median WER | 37 | 2 | 0/2 | 0/2 | 52/52 | full |
| day1-c07 cough | worst WER | 23 | 7 | 0/1 | 0/0 | 35/35 | truncated |
| day1-c03 headache | historical | 37 | 0 | 0/0 | 0/3 | 39/39 | full |
| day3-c01 lip swelling | historical | 28 | 5 | 0/2 | 1/2 | 19/19 | full |
| day5-c09 fatigue | historical | 32 | 6 | 1/1 | 2/4 | 34/34 | full |
| **total** | **6 notes** | **209** | **21** | **1/7** | **3/12** | **203/203** | **5 full / 1 truncated** |

- Citation resolution is 203/203 (100%). Fidelity warnings are 1 true / 7 false (12.5%
  precision); four false positives directly reproduce split-question/composite M02 precision
  debt. Low-confidence warnings are 3 true / 12 false (20.0% precision), and two true warnings
  point to unrelated rows.
- Known unsupported families total 14: uncertainty resolved 6, invented denial 1,
  screening-as-exam 1, patient state/action without evidence 6, non-verbatim quote 0. Seven
  further shapes cover demographics, symptom inference, medication status/availability,
  treatment rationale, future-plan elaboration, and completed test ordering.
- day1-c07 retained 32,698/33,922 corrected-source characters. That honest terminal failure proves
  the 32,768-character selection contract is not corpus-safe; it was not regenerated.
- Routing: reopen M02 coverage/precision and M03 warning precision/linkage; record the input-cap
  debt and seven novel shapes in the 0.4.1 ISSUE. No prompt, checker, confidence, API, UI, or
  runtime behavior changed during this audit.
- Artifacts: `var/quality/note-fidelity-audit-20260713T234904Z/` (`aggregate.md`,
  `aggregate.json`, `run-state.json`, `exit-sentinel.json`, health/GPU/log evidence, and six
  per-fixture `audit.md` files). Spend was exactly 6 note requests / 12 Bedrock generations.

## Pointers

- Artifacts: `var/quality/full-corpus-20260710T2328Z/` (per-fixture transcripts, quality
  reports, diagnostics, role timelines, correction request/response, `runner.log`,
  `gpu-samples-10s.log`, `source-chip-score.*`, `run-manifest.md`).
- Preserved anomaly evidence: `day2-consultation02-FIELD-FAILURE-empty-result-partial/`,
  `day1-consultation03-PREFIX-SHAPE-prefold-4chunk/`.
- Related: M09 plan (chunked-correction design + acceptance), runtime.md footgun entry
  (correction lane behavior), M01 plan (the @60s canonical baseline this complements).
