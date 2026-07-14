# M08 - Note-Fidelity Corpus Audit

Status: complete (2026-07-14; evaluation executed, release quality gate FAIL). The audit found
21/209 unsupported claims and one of six notes violates the input-length contract.
Complexity: System (reclassified 2026-07-14 after the detached six-note replay and sentence-level
audit exceeded the Standard interaction budget; boundary unchanged). No Ask First boundary
(evaluation only; no product edits), but the six-note run remains behind an explicit Bedrock-call
cap gate.

## Context for a fresh agent

The transcript lanes now have a full-corpus baseline; the NOTE lane does not. Fidelity
evidence so far comes from 3-5 fixture replay campaigns (M07/M10/M11) plus single
acceptance runs. The corpus artifacts make a broader audit cheap: stored sessions can be
summarized directly (the M08-c03 acceptance tool pattern) and every generated note audited
against its own transcript for the known fabrication families and flag precision. This is
the missing dimension the user's "measure the quality" ask implies for notes, and the
validation bed for 0.4.1-M02/M03 once they land.

## Evidence

- Corpus baseline "Recommendations": note-fidelity pass named as the natural add-on.
- Existing tooling: `.goat-flow/plans/0.4.0/tools/m08-c03-summary-acceptance.py` (same-session
  summary POST pattern), fidelity flag rendering in payloads, PHI-safe fidelity diagnostics
  in structured logs.
- Caveat from tonight: summaries must be generated while sessions/corrected artifacts are
  retrievable - session-store TTL means the audit needs FRESH replays or immediate
  summarization after each replay, not post-hoc summarization of the 20260710T2328Z run.

## Tasks

### Phase 0 - design the sample and the rubric

- [x] Stratified sample (proposal, adjust with user): 6 fixtures - best WER (d5c08), median
      (d5c09 or d2c02), worst (d1c07), plus the three historical note-bug fixtures (c03
      headache, day3-c01 lips, day5-c09 tired). Full length, 1x, generate note immediately
      after correction per fixture.
- [x] Rubric per note: fabrication families (uncertainty-resolved, invented denial,
      screening-as-exam, patient-state-without-evidence, non-verbatim quote), flag precision
      (each flag audited true/false), citation resolution rate, source (must be
      corrected_segments), truncation absent.

Phase 0 decision (2026-07-14):

- Live-tree discrepancy: M06 landed a newer accepted corpus than the proposal's 2026-07-10
  reference. `var/quality/full-corpus-20260713T084709Z/` changes corrected non-overlap WER
  best/median/worst to day5-c03 **16.6%**, day1-c04/day1-c06 **21.5/21.9%**, and day1-c07
  **32.6%**. The audit therefore uses day5-c03 as best, day1-c04 as the nearest median, and
  day1-c07 as worst, plus historical day1-c03, day3-c01, and day5-c09. All six are unique.
- Exact fixture stems are `primock57-day5-consultation03-im-feeling-very-anxious`,
  `primock57-day1-consultation04-i-dont-feel-well-i-have-a-cough-and-runny-nose`,
  `primock57-day1-consultation07-i-have-a-cough-and-cold`,
  `primock57-day1-consultation03-i-have-terrible-headache`,
  `primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich`, and
  `primock57-day5-consultation09-tired-all-the-time`. Their source audio totals **3,883.4s
  (64.7 minutes)** before correction and summary time.
- Every final section sentence and key point is checked against that session's corrected rows.
  Unsupported claims are classified into the five named families; novel unsupported shapes are
  recorded separately rather than forced into a known family. Fidelity `unverified` flags and
  M03 `low_confidence` flags receive separate true/false classifications. M02 split-question
  composite-denial false positives remain a separate fidelity subtype.
- Citation resolution is `resolved corrected segment IDs / all returned citation IDs`; every
  missing ID is a defect. `transcript_source` must equal `corrected_segments`, truncation must be
  false, and original/kept character counts must match. No prompt/checker change is allowed
  between fixtures.
- Cost contract: six final note requests; the shipped checker can make one fidelity-guided redo,
  so the paid range is **6-12 Bedrock generations**, hard-capped at 12. Preparation is approved;
  the executable run waits for explicit confirmation of that cap.

### Phase 1 - run and audit

- [x] Build a small runner (plans tools dir) that replays each sampled fixture, waits for
      healthy correction, POSTs the summary, saves note+metadata beside the replay artifacts.
- [x] Audit each note against its own corrected transcript per the rubric - adversarial
      style (as M07/M11 audits), findings quoted with row evidence.
- [x] Aggregate: fabrications (target: zero unflagged), flag false-positive rate (the M02
      denial-composite class counted separately), citation resolution, per-family counts.

Runner evidence (2026-07-14):

- `.goat-flow/plans/0.4.1/tools/m08-note-fidelity-audit.py` validates the exact six fixture
  triplets, agent health, and CUDA without replay/token use in `--prepare-only` mode. The dry run
  reported **6 fixtures / 3,883.44s / 64.7 minutes / 6-12 generations, cap 12**.
- Paid mode is fixed at 1x/5,000ms, requires structured logs, replays and summarizes each session
  immediately, and stops on unavailable correction, unhealthy GPU/service, fatal agent logs,
  non-corrected/truncated input, missing character identity, empty note, or budget breach. It
  records paid requests before validation so resume cannot duplicate a returned note.
- Ruff check, Ruff format check, Python compile, and the no-token preparation run passed. A
  historical corrected c03 note resolved **54/54 citations** through the runner's provenance
  reader. `gruff-py` is unavailable on all playbook search/fallback paths; the repository hook
  excludes `.goat-flow/`, so Ruff plus the executable dry run are the runner's static/runtime
  gates.
- The user explicitly approved the six-note paid run with **6-12 Bedrock generations hard-capped
  at 12** on 2026-07-14. No provider request occurred during runner preparation.

Paid-run stop evidence (2026-07-14):

- Evidence root `var/quality/note-fidelity-audit-20260713T234904Z/` produced two complete-contract
  notes and one preserved contract-failing note using **3 summary requests / 6 generations**.
- Worst-WER day1-c07 session `005eb45b-2db4-4d43-be55-656b65ab3a19` returned corrected source but
  `transcript_truncated:true`: **33,922 original / 32,698 kept characters** and **467 original /
  448 kept rows**. `summary.transcript_truncated` is captured in `summary-events.jsonl`.
- The runner stopped before day1-c03, day3-c01, or day5-c09 and wrote a failed exit sentinel. There
  were zero failed health samples and zero fatal agent-log matches. Per the milestone contract and
  mission stop rules, the truncated note is not regenerated and the audit does not resume without
  an explicit routing decision.

Resume decision (2026-07-14):

- The user approved retaining day1-c07 as the audit's terminal no-truncation failure, making no
  replacement request for it, and completing the three unrequested historical fixtures within the
  original 12-generation cap.
- Resume-ledger validation identified exactly day1-c07 as requested, incomplete, and terminal;
  retained two completed notes, three paid requests, and six observed generations; and accounted
  for three of six selected fixtures before restart. Ruff check/format, Python compile, and the
  zero-spend preparation run passed after the resume guard was added.

Final run and adversarial-audit evidence (2026-07-14):

| fixture | session | claims | unsupported | fidelity TP/FP | wording TP/FP | citations | input |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| day5-c03 anxiety | `a32af89e-dec4-4922-bacc-495159491dd9` | 52 | 1 | 0/1 | 0/1 | 24/24 | full |
| day1-c04 cough | `3c8ffde4-60b1-41f1-8d1b-3eacffb59134` | 37 | 2 | 0/2 | 0/2 | 52/52 | full |
| day1-c07 cough | `005eb45b-2db4-4d43-be55-656b65ab3a19` | 23 | 7 | 0/1 | 0/0 | 35/35 | truncated |
| day1-c03 headache | `f3ff466a-f1cb-40d7-8ae2-97dd1d77e4d5` | 37 | 0 | 0/0 | 0/3 | 39/39 | full |
| day3-c01 lips | `3ffc631c-4b61-4291-9960-f6a2194b159b` | 28 | 5 | 0/2 | 1/2 | 19/19 | full |
| day5-c09 fatigue | `c34c46b6-3539-45fe-aeb9-dced4852dd5e` | 32 | 6 | 1/1 | 2/4 | 34/34 | full |
| **total** | **6 final notes** | **209** | **21** | **1/7** | **3/12** | **203/203** | **5 full / 1 truncated** |

- The run finished at `2026-07-14T01:15:40Z` with **6 requests / 12 generations**, exactly the
  approved cap. It recorded 150 health samples with zero failures, two ten-second GPU samples,
  and zero fatal agent-log matches.
- Only day1-c03 is content-clean. Known unsupported families total 14 occurrences:
  uncertainty resolved 6, invented denial 1, screening-as-exam 1, patient state/action without
  evidence 6, and non-verbatim quote 0. Seven novel shapes are recorded separately.
- Fidelity precision is **1/8 (12.5%)**, including four direct split-question/composite M02
  false positives. Low-confidence precision is **3/15 (20.0%)**; two true warnings link to
  unrelated rows, so warning presence does not prove correct provenance.
- Each fixture's `audit.md` pins the visible claim and corrected-row/TextGrid evidence. Aggregate
  arithmetic and routing live in `aggregate.json` and `aggregate.md` under the evidence root.

### Phase 2 - route the findings

- [x] Every confirmed defect becomes: a regression test against the shipped M02/M03 behavior
      (this milestone runs AFTER they land, so their families should be clean - a hit there
      is a reopened-debt record, not a new M02 task), a new recorded ISSUE item (if novel),
      or a footgun update (if architectural).
- [x] Results table + verdicts appended to CORPUS-BASELINE doc as the note-lane companion;
      artifacts under `var/quality/note-fidelity-audit-<ts>/`.

Routing verdict: reopen M02 fidelity coverage and composite precision, reopen M03 warning
precision/linkage, record the 32,768-character summary-input cap as corpus-unsafe, and retain the
seven novel unsupported shapes as ISSUE regression specimens. M08 adds no product behavior or
mid-sample tuning; all 21 quoted occurrences remain pinned in per-note audits for the owning fix.

## Boundaries and non-goals

- NO product edits in this milestone - findings route to M02/M03 or new records. No prompt
  tuning mid-audit (that would invalidate the sample). Cost-bounded: 6 notes, one
  regeneration max each (existing policy).

## Gate / exit criteria

- [x] 6/6 sampled notes generated from corrected source and audited with quoted evidence.
      One corrected-source note is deliberately retained as the terminal truncation failure.
- [x] Zero unflagged fabrications, or each one converted into a test case + record.
      The 21 failures are evidence-pinned adversarial regression specimens and ISSUE records;
      executable product tests belong to the reopened owning work because M08 forbids product edits.
- [x] Flag false-positive rate measured and recorded (baseline for M02's gate).
- [x] Results appended to the corpus baseline doc; ISSUE updated.

## Closure verification (2026-07-14)

- Focused runner gates pass: Ruff lint, focused Ruff format, Python compile, 42/42 definitions
  documented, and no-token preparation with six fixtures / 3,883.44 seconds / healthy models and
  CUDA. Aggregate arithmetic validates and all six per-fixture audits exist.
- Broad suites pass: Python **624**, PHPUnit **37 tests / 159 assertions**, Playwright **49**,
  JavaScript **15**, PHPStan, PHP-CS-Fixer, canonical Ruff lint, all **12 enabled** preflight
  checks, and context validation.
- The optional repository-wide Ruff format probe reports 34 untouched legacy files. Those files
  are outside M08 and remain unchanged; the canonical lint gate and M08 runner format gate pass.
  The distinction is recorded in `.goat-flow/learning-loop/lessons/audit-runners.md`.
- Final runtime checks pass with `models_loaded:true`, CUDA `True`, 150/150 healthy monitor
  samples, and zero fatal agent-log matches. Learning index/statistics are clean.
- Final context validation and staged-diff checks pass; only the declared M08 records, runner,
  CHANGELOG entry, and generated learning index are included in the handoff.

## References

- `../0.4.0/CORPUS-BASELINE-2026-07-11.md`; M07/M10/M11 audit protocols;
  `m08-c03-summary-acceptance.py` runner pattern.
