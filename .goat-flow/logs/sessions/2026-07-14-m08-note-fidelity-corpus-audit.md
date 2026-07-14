# Session: M08 Note-Fidelity Corpus Audit

Date: 2026-07-14
Status: complete; audit verdict FAIL recorded and exact M08 staging prepared for handoff

## Intake and scope

- M07 was committed at `4ec5e15`; the worktree was clean before M08 opened. Agent health reports
  `models_loaded:true` and in-container CUDA reports `True`.
- Scope is evaluation-only: M08 plan/tooling, `var/quality` artifacts, corpus companion results,
  ISSUE/session records, and evidence-driven learning updates. Product, prompt, checker,
  provider/model, GPU, API, UI, `.env`, and mid-audit tuning are excluded.
- Relevant prior learning requires a real runtime Bedrock call rather than a config echo and
  warns that the installed Strands request shape owns provider options.

## Phase 0

- The most recent accepted corpus supersedes the drafted sample. Corrected non-overlap WER now
  selects day5-c03 best (16.6%), day1-c04 median proxy (21.5%), and day1-c07 worst (32.6%), plus
  historical day1-c03, day3-c01, and day5-c09.
- Six unique fixtures total 3,883.4 seconds of audio. Each will run full length at 1x, correct,
  and generate its note immediately while the corrected session remains retrievable.
- The rubric audits five fabrication families, fidelity-flag precision, low-confidence-flag
  precision, corrected citation resolution, corrected source, and absent truncation. Novel
  unsupported shapes remain separately visible.
- Expected output is six final notes. One automatic fidelity redo per note makes the paid range
  6-12 Bedrock generations; 12 is the hard cap explicitly approved by the user. No paid call has
  run in this session yet.

## Runner preparation

- Added `.goat-flow/plans/0.4.1/tools/m08-note-fidelity-audit.py`. Preparation mode verified all
  six WAV/TextGrid triplets, `models_loaded:true`, CUDA `True`, and 3,883.44 seconds of audio.
- Paid mode requires JSON logs, runs at 1x/5,000ms, captures logs/health/GPU evidence during the
  run, summarizes each fresh corrected session immediately, and enforces six requests/twelve
  generations with a resume-safe paid-work ledger.
- Ruff check, Ruff format check, Python compile, and preparation mode passed. The provenance path
  resolved 54/54 citations in the historical corrected c03 summary artifact. A live schema check
  caught and corrected the runner's initial `id` assumption: corrected rows use `segment_id`.
- `gruff-py` was not present in the documented binary locations; `npx` returned package 404 and
  `uv run` found no command. The gruff hook intentionally excludes `.goat-flow/` targets.

## Paid run

- The user approved six final notes / 6-12 Bedrock generations / hard cap 12. The runtime was
  recreated with command-scoped `LOG_FORMAT=json` (no `.env` edit), then health returned
  `models_loaded:true` and CUDA returned `True`.
- Evidence root: `var/quality/note-fidelity-audit-20260713T234904Z/`. The first plain `nohup`
  launch was terminated by the execution shell before replay, correction, session creation, or
  provider use. A verified `setsid --fork` launch resumed the same empty root; this launch
  mechanism finding requires a learning-loop note before M08 closes.
- Ten-second GPU evidence: 7,652/16,303 MiB and 99% utilization. Continuous structured logs and
  30-second health samples are active; no failed health sample or fatal runtime pattern so far.
- Note 1, day5-c03 anxiety, session `a32af89e-dec4-4922-bacc-495159491dd9`: corrected source,
  30,963/30,963 characters, 24/24 citations, two generations. Manual audit found one unflagged
  demographic sentence (`30-year-old woman`) and false-positive fidelity/low-confidence flags.
- Note 2, day1-c04 cough/runny nose, session `3c8ffde4-60b1-41f1-8d1b-3eacffb59134`:
  corrected source, 24,276/24,276 characters, 52/52 citations, two generations. Manual audit
  found unflagged `increased thirst` and overbroad `no current medications`; both fidelity flags
  and both low-confidence flags are false positives.
- Current state: 2/6 final notes, 4/12 generations observed; worst-WER day1-c07 replay is active.

## Mandatory stop - day1-c07 truncation

- The worst-WER fixture completed replay/correction and generated its one immutable final note for
  session `005eb45b-2db4-4d43-be55-656b65ab3a19`. The route used corrected source but returned
  `transcript_truncated:true`: **33,922 original characters / 32,698 kept**, **467 original rows /
  448 kept**. This violates M08's explicit no-truncation input contract, so the runner stopped
  before day1-c03 and did not regenerate or replace the returned note.
- Paid ledger at stop: 3 summary requests, 6 observed generations, two completed-contract notes,
  and the truncated third note preserved. `exit-sentinel.json` records `failed` at
  `2026-07-14T00:32:31Z`; no runner process remains.
- Structured logs captured `summary.transcript_truncated`, then one first-draft denial finding and
  the allowed retry. The retry had more violations, so the shipped selector kept attempt 0 with
  one visible flag. Continuous evidence reports zero health failures and zero fatal runtime
  patterns; this is a summary input-contract failure, not GPU/runtime instability.
- Required next decision: whether M08 remains an honest partial/failed audit with a follow-up debt
  item, or whether the user authorizes a separately scoped summary-context product fix before a
  fresh audit. The existing c07 paid note must not be silently regenerated in this evidence set.
- Cleanup restored the normal command-scoped `LOG_FORMAT=console` runtime. Post-restore health is
  `models_loaded:true` and CUDA is `True`.

## Approved resume

- The user explicitly approved preserving the returned day1-c07 truncation failure, skipping any
  regeneration of that visit, and generating only the remaining three notes under the existing
  twelve-generation ceiling.
- The runner now migrates only the exact saved `summary transcript was truncated` state into a
  terminal-failure ledger. The terminal stem must have a recorded paid request and cannot also be
  complete. Resume validation resolved only day1-c07, with two completed visits, three requested
  visits, and six observed generations.
- Ruff check, Ruff format check, Python compile, and no-spend preparation all passed. A resumed GPU
  sample receives a distinct filename so the original 99% utilization evidence remains immutable.

## Resume progress

- day1-c07's preserved note is audited: 23 substantive claims, seven unflagged unsupported
  sentence occurrences, 35/35 citations resolved, one false-positive fidelity flag, and no
  low-confidence flags. The 33,922/32,698-character truncation remains the independent contract
  failure; no replacement request was made.
- day1-c03 session `f3ff466a-f1cb-40d7-8ae2-97dd1d77e4d5` passed content fidelity: all 37
  claims are supported, no M02 fidelity warnings recur, source is corrected and untruncated,
  22,195/22,195 characters are retained, and 39/39 citations resolve. Its three low-confidence
  warnings are false positives. The run has spent four requests / eight generations and moved to
  day3-c01.
- The runner's pre-spend schema and detachment mistakes are consolidated in
  `.goat-flow/learning-loop/lessons/audit-runners.md`. An initial oversized addition was moved out
  of the near-limit verification bucket; `goat-flow index` and `goat-flow stats --check` pass.
- day3-c01 session `3ffc631c-4b61-4291-9960-f6a2194b159b` retained all 18,369 characters and
  resolved 19/19 citations, but five of 28 claims overstate duration, antihistamine availability,
  ambulance-call completion, or injection follow-up. Both fidelity flags are false positives.
  Low-confidence precision is one true / two false: it correctly exposes `pumping blood` becoming
  `pumping a lot`. The run has spent five requests / ten generations and is replaying final
  day5-c09.

## Completed run and audit

- day5-c09 session `c34c46b6-3539-45fe-aeb9-dced4852dd5e` completed the sample. Six of 32
  claims are unsupported: the known equivocal-weight false negative, two wrong rash locations,
  resolved headache/concentration uncertainty, and a future blood-test recommendation reported as
  already ordered. Fidelity precision is one true / one false; wording precision is two true /
  four false, with both true warnings linked to unrelated rows.
- The detached run finished at `2026-07-14T01:15:40Z` with exactly six note requests and twelve
  observed Bedrock generations. Five notes satisfy full-character identity; c07 remains the
  approved 32,698/33,922-character terminal contract failure. There are 150 health samples with
  zero failures, two ten-second GPU samples, and zero fatal agent-log matches.
- Aggregate verdict is FAIL: 1/6 notes is content-clean; 21/209 substantive claims are unsupported
  and unflagged (10.0%); 203/203 citations resolve. Fidelity flags are 1 true / 7 false (12.5%
  precision). Low-confidence flags are 3 true / 12 false (20.0% precision).
- Known unsupported families total 14 and seven novel shapes remain separately recorded. Every
  occurrence has visible claim text plus corrected-row/TextGrid evidence in its fixture
  `audit.md`; `aggregate.json` and `aggregate.md` reproduce the totals and routing.

## Routing and closure preparation

- The corpus baseline now contains the M08 note-lane table and FAIL verdict. ISSUE records reopen
  fidelity coverage, M02 composite precision, and M03 warning precision/linkage; it also records
  the summary context cap and seven novel regression shapes. No prompt, checker, confidence, API,
  UI, provider, or runtime behavior changed.
- Final runner checks pass on the documentation-complete code state: Ruff check, Ruff format,
  Python compile, 42/42 definitions documented, and no-token `--prepare-only` with six fixtures,
  3,883.44 seconds, `models_loaded:true`, and CUDA `True`.
- Remaining work is the milestone-wide verification suite, final health/CUDA/fatal scan, learning
  index/stats, exact staging, and the manual commit handoff.

## Closure verification

- Python passed **624** tests; PHPUnit passed **37 tests / 159 assertions**; Playwright passed
  **49** browser tests; the JavaScript suite passed **15** tests. PHPStan, PHP-CS-Fixer, and
  canonical Ruff lint are clean.
- Preflight passed all **12 enabled** checks (mutation testing remained its documented opt-in),
  and context validation passed. The focused M08 runner remains Ruff-formatted and compiled.
- An extra repository-wide Ruff format probe reported 34 untouched Python files. This is outside
  the canonical lint gate and M08 boundary, so no unrelated reformat was made. The scope lesson is
  recorded in `.goat-flow/learning-loop/lessons/audit-runners.md`.
- Aggregate arithmetic validation returned `true`; all six `audit.md` files exist. Final health is
  `models_loaded:true`, CUDA is `True`, all 150 monitor samples are healthy, and the structured
  agent-log fatal scan is zero.
- `goat-flow index` and `goat-flow stats --check` pass after the learning update. Final context,
  staged-diff, and exact-file checks pass in the final handoff state.
