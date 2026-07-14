# Changelog

All notable changes to Ambient Scribe are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.4.1] - unreleased

### Added

- **Corpus and evidence harness hardening (0.4.1 M01)** - corrected-fixture `--all` and
  multi-fixture runs now preserve an explicit unavailable correction as PHI-safe fixture/run
  failure artifacts, show `FAILED` corrected metrics plus the safe reason in the final table,
  continue through later fixtures, and end with a stable `fixtures=N ok=N failed=N` sentinel;
  named single-fixture gates remain fail-fast. Correction failures now report the one-based failed
  chunk and planned chunk count through exception metadata, the API response, and structured logs.
  The isolated browser runner uses parallel PHP workers plus a test-only static-asset router, with
  two consecutive 43-test Playwright passes replacing the asset-drop-prone single-server lane.
  Long eval guidance now requires capturing correction/instrumentation lines during the run, and
  the roadmap points at the completed 20-fixture baseline and active 0.4.1 order.

- **Composite denial checks follow the user's complete screening answer (0.4.1 M02)** -
  generated-note fidelity checks now join bounded clinician question fragments before judging a
  short patient denial, so one composite sentence can verify each independently answered topic
  across corrected-row splits. The checker also recognizes the retained `that's all fine`
  screening response, narrowly recovers a leading patient `No` folded onto the next clinician
  row, preserves the topic when a patient repeats a question and answers `No` in the same row,
  and stops denial scope before a contrasting reported symptom. The c03 neurological and mood
  false warnings plus retained respiratory, GI/urinary, and joint-swelling families now pass while
  unanswered questions, clinician-only negatives, long answers, and the equivocal weight-change
  claim remain protected. Older question fragments can complete a compound topic only when the
  immediate follow-up overlaps it, so a later `No` cannot overwrite an earlier affirmative
  answer. The fidelity suite now has 80 passing tests; a clean
  five-generation c03 campaign audited 16 denial claims with zero unsupported denials, zero false
  denial flags, and zero unflagged fabricated denials.

- **Low-confidence wording is visibly reviewable without rewriting the note (0.4.1 M03)** -
  corpus-derived strict thresholds now mark live rows below `0.76` and corrected rows below `0.78`
  with local dotted tint, keyboard focus, and an accessible explanation in both themes, without
  adding a persistent confidence label to live cards or corrected blocks; absent and exact-boundary
  confidence stay plain, role cards keep their labels and height, and corrected stitched utterances
  preserve row-local cues.
  Generated prose remains byte-identical: after citation validation, a deterministic additive
  `sections[].low_confidence` field marks only sentences whose conservatively linked, measured
  corrected rows are predominantly sub-threshold, with tooltip `Low-confidence transcription`.
  The retained day5 calf/carp sentence is now visibly flagged; no citation/overlap, unmeasured
  rows, and low/high ties remain unmarked. A zero-token A/B selected this deterministic flag over
  prompt-side text hedging. Product re-renders matched stored threshold counts across the
  worst-liveBad, typical, and day5 fixtures (10.3-24.1% marked; no warning wall), while focused
  fidelity/summary tests remained green.

- **Cross-talk fold mechanism confirmed; guarded policies rejected (0.4.1 M04)** -
  OFF-by-default, PHI-safe streaming evidence now records cache-slot frame counts, acoustic
  and stable-word shares, fold decisions, and timing/word-count spans without consultation
  wording. Two controlled 1x replays confirmed that marginal third slots can fold short
  Patient speech into the visible Doctor stream. A reusable TextGrid-grounded scorer now
  classifies those spans as correct, wrong, or unresolved and compares candidate attribution
  with the same Phase 0 timing.

  Neither guarded policy is accepted for user visits. Keeping every acoustically sustained
  origin visible improved the named fixtures but regressed corpus phantom identities 10 -> 18.
  A visit-long stable alias then passed the two targeted attribution-delta gates (9 improved,
  zero worsened, zero newly confident wrong) and reproduced all three flag-OFF hashes, but the
  final 20-fixture corpus regressed corrected strict 88.985% -> 87.695% and raised incorrect-
  confident 10.595% -> 12.240%. The stable-alias product/test delta was removed after that
  kill. The diagnostic guard remains `0`, release fold behavior is unchanged, and the retained
  scorer, ADR-008, footgun, and corpus artifacts preserve the evidence for future work.

- **Dual-identity duplicate prevalence is measurable without exposing transcript wording
  (0.4.1 M05 Phase 0)** - a deterministic offline scorer finds rows with identical normalized
  non-empty text, positive spoken-time overlap, and different visible speaker IDs. It compares
  wording only in memory and reports safe session/segment IDs, roles, timing, overlap, word count,
  and a metadata-derived pair ID; malformed and empty histories have explicit outcomes. The exact
  canonical 20-fixture baseline contains zero candidates across 6,072 live rows, while the retained
  M08 browser replacement reproduces one candidate across 306 rows and canonical day5-c09 remains
  zero across 296. A fresh instrumented 1x day5-c09 replay also found zero candidates across 296
  final server rows despite 10 phantom merges and 10 folded spans / 17 words. A subsequent
  132.8-second browser check reproduced one ground-truth Patient across overlapping speaker_0 and
  speaker_3 rows while the exact scorer still reported zero. A JSON/slot-evidence browser replay
  then located the mechanism before adapter folding: both raw slots clear the substantial-voice
  threshold, so the adapter preserves the engine's split identity and the browser receives both.
  A planning-only D3 pass selected a narrow default-OFF adapter candidate: withhold a later
  cross-slot row only when it shares four consecutive words within a 0.5-second start gap and at
  least 0.80 word-LCS similarity, with equal normalized vocabulary so no distinct clinical term is
  lost; a TextGrid-grounded scorer must reproduce target=1 and canonical corpus=2 first. M05 is
  locally implemented behind the off flag with 35 focused tests green, but the first retained-
  artifact score rejected the equal-vocabulary assumption: the target shares seven of eight ordered
  words (0.875 similarity) while differing by one connector. The approved refinement accepts equal
  word multisets or exactly one `and`/`but` substitution and no other differing word. Its red proof
  produced exactly four intended failures with 35 existing/anti-target contracts passing. The shared
  Counter-based scorer/runtime rule now passes all 39 focused contracts; Ruff, Compose, and both
  Gruff lanes are clean. Retained manual/instrumented artifacts now score 1/1, but the exact accepted
  canonical 20 scores 0 rather than the planned 2 because both planning candidates contain distinct
  non-connector words. The corrected baseline is approved: safe canonical repeats remain 0, both
  near-matches are anti-targets that stay visible, and the retained connector target must improve
  1 -> 0. The first flag-OFF c02 byte-gate attempt finalized streaming cleanly but its correction
  request timed out after 120 seconds before a corrected artifact or hash existed; c03/c08 were not
  started. One clean isolated c02 retry is approved; the candidate remains unaccepted/off and a
  repeated timeout stops the gate before c03/c08. The retry completed in 50.821 seconds and matched
  the retained c02 canonical hash exactly; c03/c08 then completed in 11.636/11.823 seconds. The
  flag-OFF trio passes `HASH_MATCHES=3/3`, strict 100.0/96.8/93.8, zero source-chip findings, and
  clean runtime health. The exact guard-ON target then scored zero pairs without a withheld-row
  event; it emitted 63 vs 67 baseline rows and changed phantom merges 3 -> 10 because the target
  pair was not reproduced. The runtime/config/guard-test candidate was therefore rejected before
  browser/corpus promotion and removed exactly; no duplicate guard or user default ships. The
  retained PHI-safe scorer and contracts reproduce manual/instrumented/canonical counts 1/1/0 and
  pass 7 focused tests, Ruff, and scorer Gruff A/100. Full closure passes Python 608, PHPUnit
  37/159, Playwright 48, PHPStan, PHP-CS, all 12 enabled preflight checks, learning index/stats,
  context validation, PHI/symbol scans, and `git diff --check`. M05 closes diagnostic/no-fix with
  its rejection evidence preserved and no runtime behavior change.

- **Long-turn transcript freezes now have a PHI-safe causal signal (0.4.1 M06 Phase 0)** -
  operator-only streaming evidence records why stable rows released or remained held using only
  clock/frontier times, row counts, and speaker-slot activity counts. Fresh 1x c01 and c03 replays
  prove the global stability frontier is the starvation mechanism: c01 held 8 -> 44 rows across a
  50-second zero-emission span before a 25-row browser burst; c03 reached 25 seconds without rows
  and a 30-second batch interval. Both had stable clock-ready rows waiting, which rejects decoder
  finality, speaker-turn boundaries, and the browser adapter as causes. Captured evidence selects a
  default-off 10-second bounded-release candidate. When explicitly enabled, it releases only
  already-stable rows that an obsolete frontier has hidden past the limit; mutable wording, future
  rows, honest timestamps, cadence, and speaker policy stay unchanged. Ordinary visits retain the
  prior release policy at the default `0`. The first flag-off c02 compatibility stream finalized
  cleanly, but post-visit correction timed out before a corrected hash existed; later byte, target,
  corpus, and browser gates remained pending rather than being misreported as regressions. One
  approved clean retry completed in 50.013 seconds and reproduced the exact retained c02 hash;
  c03/c08 then completed and the compatibility gate passed `HASH_MATCHES=3/3`. A c08 strict-score
  wobble is confined to post-ASR role labels; canonical transcript bytes remain exact. With the
  10-second bound enabled, c01 improves from 50 to 10 seconds without rows, 55 to 15 seconds
  between batches, and a 25- to 9-row largest burst; WER, strict attribution, and seams remain
  within the declared noise band. A real browser replay reproduces the 10/15-second delivery and
  six screenshots through the long Patient turn show the visible transcript growing from 14 to
  89 rows instead of freezing and appending a wall near 02:25. The first full-corpus promotion
  gate is retained as rejection evidence rather than enabling the policy: although all 20
  corrections completed and corpus quality means moved by at most 0.405 points on the declared
  WER/strict/incorrect-confident measures, four fixtures still had 20-25-second startup batch
  gaps. The candidate therefore remains default-OFF pending a separately approved refinement.
  That refinement is now approved and in progress: the release decision may account for the next
  browser-audio tick only when stable clock-ready rows are already waiting; startup silence with
  no stable wording remains untouched. Direct contracts pass and the first causal target now
  emits at 15 rather than 20 seconds, but its full-length post-stop correction timed out before a
  corrected artifact existed. A live-only alternate gate is approved: retained c03 plus fresh
  c06/c08/day3-c05 runs measure causal delivery and live quality without invoking correction.
  That gate passes: stable-ready/no-emission waits are 0/5/5/5 seconds, four-fixture live WER
  moves only +0.025 points, strict and incorrect-confident means are flat, and all quality,
  health, CUDA, and fatal-log checks are clean. The runtime is restored default-OFF; promotion
  remains pending the second full corpus. A final c08 browser replay visually confirms first rows
  at 20 seconds versus 25-26 seconds default-off, followed by continuous delivery; it finalizes
  106 rows / zero errors and correction completes without retry. The refined second full corpus
  passes 20/20 corrections with zero causal violations or runtime errors: stable-ready wait is at
  most 5 seconds, corrected WER/strict/incorrect-confident move only +0.345/-0.250/+0.195 points,
  and source-chip findings improve 62 -> 61. The user-assisted restore passes with effective
  streaming/max-hold/evidence/log values `streaming/0/0/console`, loaded models, CUDA, and clean
  fatal logs; the protected local environment file remains unread and unedited. Final verification
  passes Python 624, PHPUnit 37/159, Playwright 48, JavaScript 15, PHPStan, PHP-CS, Ruff, all 12
  enabled preflight checks, context validation, learning index/stats, and `git diff --check`.
  M06 closes with the bounded policy available for explicit use and ordinary visits still at `0`.

- **Generated-note quality now has a six-visit corpus baseline (0.4.1 M08)** - six fresh,
  full-length corrected replays produced the approved six final notes at the twelve-generation
  Bedrock cap, followed by sentence-level adversarial review against corrected rows and
  Doctor/Patient TextGrids. The audit records 203/203 resolved citations, but fails release
  quality: only 1/6 notes is content-clean and 21/209 substantive claims are unsupported and
  unflagged. Fidelity warnings are 1 true / 7 false (12.5% precision); low-confidence wording
  warnings are 3 true / 12 false (20.0% precision), with two true warnings linked to unrelated
  rows. One 33,922-character visit is honestly retained as a truncation failure at the 32,768-
  character selection cap. The baseline and ISSUE route reopened M02/M03 coverage and precision,
  the input-cap debt, and seven novel unsupported shapes; no prompt, checker, UI, API, model, or
  runtime behavior changed during this evaluation-only milestone. Closure passes Python 624,
  PHPUnit 37/159, Playwright 49, JavaScript 15, PHPStan, PHP-CS, canonical Ruff lint, all 12
  enabled preflight checks, context validation, learning index/statistics, and final health/CUDA.

- **Overlap speech now has a corpus decision baseline (0.4.1 M09)** - analysis of the accepted
  20-fixture corpus finds 672.2/11,174.9 seconds (6.0%) and 2,974/31,186 known reference words
  (9.5%) associated with simultaneous speech. Corrected known-word overlap WER remains 82.6%,
  while visible overlap-row attribution is 84.2% (433/514). A TextGrid audit of all 248 overlap
  spans in the three heaviest fixtures confirms privacy-confirmation, symptom-denial, medication,
  and safety-net wording or attribution defects, without finding a complete dosage-instruction
  loss. The user selected model-lane escalation: 0.5.0-M02 now requires a tag-clean, speaker-aware
  overlap benchmark and candidate non-regression gate. No runtime, model, prompt, UI, or API
  behavior changed. Closure passes Python 624, PHPUnit 37/159, Playwright 49, JavaScript 15,
  PHPStan, PHP-CS, Ruff, all 12 enabled preflight checks, context validation, learning statistics,
  and final health/CUDA.

### Changed

- **Effective model defaults now agree across local and production setup (0.4.1 M00)** - production
  Terraform emits canonical role and summary provider/model variables, using AU Haiku 4.5 in
  `ap-southeast-2` for both user flows. Bare Compose keeps CPU Ollama/Qwen local-first while its
  Bedrock fallback and optional summary inheritance resolve to the same Haiku profile; summary
  remains on Haiku deliberately for lower note-generation cost. The model checker validates every
  distinct role/summary pair once, and the Ollama installer no longer edits `.env` or exposes a
  GPU: it exact-matches model tags and requires zero VRAM after a one-token smoke. Fixture-only
  second-pass evaluation now defaults to the pinned-runtime TDT v3 model; Unified remains an
  explicit experiment after its recorded construction failure. No selected model was upgraded.

### Fixed

- **Delayed same-speaker wording stays in one transcript card** - when a stable row arrives after
  a later speaker turn, the browser now inserts it into the preceding card for the same raw speaker
  instead of showing a second adjacent Doctor or Patient entry. Spoken order, correctable row IDs,
  and the later speaker's separate turn remain intact for both review and summary input.

## [0.4.0] - unreleased

### Added

- **Long post-visit corrections stay inside the single-GPU capacity envelope (0.4.0 M09)** - short visits preserve the existing one-shot second-pass ASR call, while capacity-risk recordings are transcribed as ordered three-minute chunks through one restored NeMo model, with visit-relative timings/confidence recombined and scratch audio removed. The exact observed `device not ready` failure gets one same-model retry after CUDA reclamation and a bounded backoff; OOM, illegal-memory, model-load, malformed-audio, and empty-result failures remain immediate fallbacks. A red-test-first `gc.collect()` + `torch.cuda.empty_cache()` now runs before every correction model restore after a real day5 restore failure with stale cache resident. Correction requests are globally single-flight without occupying an executor worker while queued, duplicate Summarise clicks reuse the completed artifact, and PHI-safe API/log metadata reports attempts, retry, chunk count, and a sanitized reason category without returning raw CUDA details. The browser now retains that safe outcome per consultation and pairs it with the summary's actual source: corrected notes show no degradation notice, a known fallback persistently says the note was built from live transcript because correction was unavailable, and direct live/store summaries use neutral source wording without inventing a failed attempt; HTTP and Mercure share the same accessible notice. GPU acceptance: the M01 c02/c03/c08 trio stayed byte-identical; the full 579-second day5 browser replay completed correction in 17.06 s (chunk_count=4, no retry) and summarized from corrected source in 32.37 s with no degradation notice; the previously three-for-three-failing full-length c03 correction passed on a deliberately dirty GPU (pre-restore release reclaimed ~4.7 GiB, bounded peak 11.5/16.3 GiB vs the fatal 15.7 GiB one-shot spike) and the M08 escalated summary acceptance reported corrected source with resolved citations, the clinician's hedged migraine impression, and the diary/analgesia/follow-up plan. A first full-corpus sweep (all 20 fixtures, full length, 1x) then exposed one boundary case - a 541.56-second visit chunked to a 1.56-second tail sliver that decoded empty and vetoed the whole correction - so remainders under ten seconds now ride inside the final chunk (red-test-first; a full-size chunk decoding empty still falls back loudly to the live transcript).

- **Generated notes preserve who was uncertain and quote only transcript words (0.4.0 M11)** - summary instructions now describe missing or unintelligible material as a limitation of the record, never as patient uncertainty, failed recall, or refusal unless the patient's own words establish that state. The deterministic verifier adds `patient-state-without-evidence` with topic-local patient evidence and `non-verbatim-quote` with straight/curly single/double quote parsing, exact token-sequence matching, same-role row joins, and explicit patient/clinician attribution. Existing one-redo, fewer-violations draft selection, and visible flags apply unchanged. The fidelity suite grew from 32 to 64 tests and the full Python suite is 555 green. Five retained day3/day5 generations passed the M11 target-family audit after every live paraphrase shape was pinned; the same audit reopened the separate M10 row-shape footgun because split clinician questions still create false denial warnings.

- **The note fidelity checker now catches the fabricated denial it was built for and stops flagging true sentences (0.4.0 M10)** - denial evidence is clause-scoped: uncertainty phrases ("I don't know", "don't think") are masked before matching so they never impersonate a denial, a denial and its topic must share one local clause (multi-word topics need two matched words), and a note sentence that claims a denial while admitting the question went unanswered fails outright - the day3 field fabrication ("She denies prior history of lip swelling...") now flags with the patient's no-punctuation monologue present. The arbitrary 40-character bare-answer gate became an eight-word rule: the answer must open with a denial word after a clinician question naming the topic, so the real 41-character corrected rash denial verifies while "...but no no" mid-row negations no longer launder anything (a day5 replay proved that shape live: "denies weight change" flagged TRUE against the patient's "weight change just a bit" self-correction, which the old gate would have verified). Honest exam-absence and exam-intent phrasings ("concludes before examination is performed", "Doctor proposed ... examination") are exempt while performed-exam claims still flag. The one allowed regeneration can no longer ship a worse note: both drafts are kept and the fewer-violations draft ships (field case 1-vs-3 pinned), with selection and per-violation diagnostics (rule, subtype, location, ordinals, word count - never clinical prose) in structured logs. Fidelity suite grew 17 to 32 tests; replay audits: c03 3/3 generations zero false flags, day5 single flag audited true.

- **Long consultations now keep their closing Assessment and Plan in generated notes (0.4.0 M08)** - all corrected, browser-visible, and stored summary inputs now share one 32,768-character whole-row selection contract instead of silently taking the first 8,000 characters. Every measured consultation fits in full; if a larger future visit exceeds the cap, generation keeps complete opening and closing rows, uses that exact selected set for the prompt, citations, retrieval, and fidelity checks, emits a structured warning, and returns neutral source/elision metadata through both HTTP and Mercure. The note panel displays a persistent accessible notice when middle rows were omitted and remains unchanged for complete inputs.
- **Dev startup now catches a silently dropped WSL2 GPU (0.4.0)** - `scripts/start-dev.sh` previously showed `nvidia-smi ✔` even when WSL2 had lost the GPU adapter (nvidia-smi exits 0 with empty output in that state), letting startup continue until the nemo-agent container failed with a cryptic "no adapters were found" error. Detection (`scripts/env-detect.sh`) now requires a NAMED adapter, not just the binary, and both failure points print the actual remedy: quit Docker Desktop, `wsl --shutdown` from Windows, restart Docker Desktop, re-run. Companion footgun documents the deeper trap: a hot-reload in that state silently loads NeMo on CPU and makes baseline-gated evals produce false regressions (`.goat-flow/learning-loop/footguns/runtime.md`).
- **Every transcript row now carries how clearly it was heard (0.4.0 M06 phase 2)** - live streaming rows, windowed rows, and post-visit corrected rows all gain an additive `confidence` value (0-1, the row's weakest word under the probe-proven `confidence_cfg` decoding), persisted through memory and SQLite storage (in-place column upgrade, no DB reset), published over Mercure, stamped on each row as `data-confidence`, and echoed back through the summary round-trip so a server restore keeps it. Absent stays absent: unmeasured rows (legacy histories, live-fallback corrected rows) omit the key and render exactly as before, and nothing styles rows yet - this unblocks the summary UX low-confidence styling task (UX-M7). No-harm evidence: a paired control-vs-confidence probe through the real streaming engine emitted byte-identical rows on both fixtures (1514/1514 steps word-aligned), and the GPU trio gate re-run (2026-07-08, after the host GPU restore) PASSED: text and timing byte-identical to the M01 baseline on all six lanes (live + corrected for all three fixtures), corrected strict 100.0/96.8/93.8 reproduced, 100% live-row and 93% corrected-row coverage with non-degenerate values (row minimums 0.58-0.77), and the only movement anywhere confined to LLM role labels (the documented role-worker wobble; the role-agent prompt provably never sees confidence). Ships with 28 new Python contract tests and a browser e2e for the with/without-field render contract.
- **Every generated note is now verified against the transcript before the clinician sees it (0.4.0 M07)** - a deterministic fidelity checker compares each note sentence with the visit transcript and catches the three fabrication families prompt rules alone could not reliably prevent: patient uncertainty resolved to a definitive value ("onset was sudden" after "I don't know"), negative findings the patient never gave (including unanswered trailing questions, "denied all", and "screening (...) negative" shapes), and screening answers dressed up as examination findings. A failing draft gets one regeneration with the exact rejected sentences named; sentences that still fail ship visibly marked with an amber "Unverified against transcript" underline rather than silently stripped (the user-decided policy - nothing is ever removed). Across four 5-generation replay campaigns the checker caught first-draft fabrications in every flagged run, the redo repaired most outright, and an independent adversarial audit of the final campaign passed all five notes with zero unflagged fabrications. Ships with 17 unit tests pinned to real campaign specimens, a browser e2e for the visible marker (37 total), and `summary.fidelity_*` log events for observability.
- **Word-confidence GPU spike: GO (0.4.0 M06 phase 1)** - a new fixture-only probe (`scripts/probe-word-confidence.py`) proves NeMo word/token confidence is obtainable from BOTH pinned models (the live multitalker streamer and the post-visit second pass) by enabling `confidence_cfg` in the decoding config: real discriminative values on two fixtures (word-confidence minimums 0.61-0.73, far from all-1.0), with the runtime's CUDA-graph workaround mirrored and - unlike the known-crashy timestamp mode - zero CUDA instability. A follow-up eval run reproduced the canonical baseline byte-for-byte. This unblocks per-row confidence persistence (M06 phase 2) and, after it, the summary UX low-confidence styling (UX-M7).
- **Demo audio fixture expansion and day-qualified labels (0.4.0)** - the local fixture set now holds 20 full-length PriMock57 consultations (13 added to the original 7), each with doctor/patient TextGrid ground truth, broadening replay coverage across ENT, musculoskeletal, cardiac red flags, stroke-like symptoms, allergy/anaphylaxis, dizziness, anxiety, gynaecology/abdominal pain, wheeze, fatigue, rash, and systemic symptoms. The Demo Audio picker now labels each row with a compact day-qualified ID like `consult 1.2` (day 1, consultation 2) instead of the ambiguous `consultation-02` prefixes, and the fixture transcript downloader discovers TextGrid URLs from local PriMock filenames instead of a day1-only table.

## [0.3.0] - 2026-07-07

### Added

- **Post-visit role requests no longer resurrect role state (0.4.0 M04)** - the speaker-scope role override and the roles snapshot endpoint now peek at role state for finished visits instead of creating it, closing the remaining two callers behind the badge-wipe footgun. A finished visit's speaker relabel still persists everywhere it should: the clinician's explicit label is applied to stored rows AND the corrected artifact (so a retried summary cites the new label) and broadcast as a partial mapping with no fabricated confidence, while the auto-row re-judge is skipped rather than run against a one-speaker mapping. Live visits are liveness-gated, so a label clicked before the role worker's first update still pins against later agent proposals. Three new regressions plus the badge e2e suite (7/7) cover both paths.
- **Summary notes preserve patient uncertainty and stay scribe-true (0.4.0 M00, ADR-007)** - the note generator's shared rules now forbid asserting clinical facts the transcript does not support: explicit patient uncertainty ("I don't know") is documented as unclear rather than resolved to one side, clinical characteristics (onset, severity, laterality, timing) appear only as the speaker stated them, and negative findings require an explicit denial or examination. The Assessment section is restricted to clinician-stated diagnoses per ADR-007 (Option B: scribe, not assistant) - AI-inferred diagnoses and unstated rule-outs are barred, and an absent assessment is reported as not documented. The Objective section takes only clinician-performed examination content, keeping patient-reported symptoms in Subjective (closes the c08 acceptance-run mislabeling, M03 item a). Prompt-rule regressions pin all three rule families.
- **Chip scorer no longer flags the doctor's question preamble (0.4.0 M03b)** - the corrected source-chip QA scorer treats consultation-structure preambles ("I'm just gonna ask" / "I'm just going to ask") as doctor-owned wording instead of patient first-person symptom talk, removing the false positive the c03 acceptance run surfaced (corrected-0146). Reporting-layer change only: the shared runtime cue lexicon that drives real row decisions is untouched. A before/after sweep across all 59 saved corrected artifacts shows exactly one finding change - the false positive disappearing - with every historical true positive still firing.
- **Citation deep links into the Transcript tab (summary UX M6)** - "Open in transcript" in a provenance popover now lands the clinician on the evidence: the Transcript tab opens, the first cited utterance scrolls into view, and every cited block holds a temporary accent highlight that fades after about three seconds. Citations resolve against stitched blocks through their constituent segment IDs, so a citation still matches after its row was merged into a longer utterance. If none of the cited rows exist in the transcript view, a small self-hiding notice appears instead of a crash or a silent no-op. Two new browser e2e tests cover the highlight-and-fade and miss-notice paths (35 total, all green).
- **Per-section provenance popovers replace citation chips (summary UX M5)** - each cited section of the generated note now ends with a small superscript count (screen-reader label "View source, N utterances"). Clicking it opens a keyboard-accessible popover (Escape or an outside click dismisses; focus returns to the toggle) showing the cited utterances in the same stitched block form as the Transcript tab, ordered by spoken time, with an "Open in transcript" action that switches tabs (the M6 deep link will add scroll and highlight). Sections without citations render no affordance, so empty popovers cannot appear. The old always-visible chip rows and their live-preview jump were deleted outright, and the browser e2e citation suite was migrated to the popover behaviour (33 tests total, all green).
- **Note/Transcript tabs in the summary panel (summary UX M4)** - the post-visit summary panel now has two views: the Note tab keeps the generated note with the TL;DR key points strip moved above the SOAP sections, and a new Transcript tab shows the full consultation as stitched utterance blocks (M3 transform) built from the corrected transcript - the rows the note was actually generated from - fetched through a new same-origin proxy (`GET /session/{sessionId}/corrected-transcript`). When no corrected artifact exists the tab falls back to the visible live rows with an explanatory line and upgrades automatically once correction runs. Tabs follow the ARIA pattern (roving tabindex, arrow keys), work in light and dark themes, and change no existing element IDs, so all 31 browser e2e tests pass unchanged. Layout only: provenance popovers and transcript deep links arrive in later milestones.
- **Dropped summary citations are now logged PHI-safe (summary UX M2)** - citation validation (already strictly citation-driven; the browser renders chips only from validated per-section citations) no longer silently strips bad model citations. One WARNING per summary reports blank, duplicate, and unresolved counts plus a capped sample of unresolved IDs, gated by the synthetic `corrected-`/`seg-` pattern so model-fabricated free text can never echo transcript content into logs. New tests pin the payload invariants: storage-truth hydration, per-section dedup with cross-section reuse preserved, and the no-drop path staying silent.
- **Transcript stitching display transform (summary UX M3)** - new pure function `stitchTranscriptSegments` (`public/js/scribe-stitch.js`) merges adjacent same-speaker corrected rows into utterance blocks when the gap is at or below `STITCH_GAP_SECONDS` (2.0s), keeping constituent segment IDs and span timestamps so citations still resolve against stitched output. Display-only and not yet wired into any page; the M4 Transcript tab and M5 provenance popovers consume it. Ships with a dependency-free node test suite (`npm run test:js`).
- **Medical term correction is now on by default** - the curated lookup that fixes commonly misheard drug and condition names (`strands_agents/data/medical_lexicon.txt`, CPU-only post-ASR pass) now runs unless a deployment opts out with `MEDICAL_BOOST_ENABLED=0`. Defaults flipped in the code toggle (`nemo_pipeline.py`, unset/blank now means enabled), `.env.example`, and the docker-compose fallback; explicit `0` still disables. New regressions pin both the default-on and the opt-out behaviour.
- **Inline reference markers no longer appear in summary prose (summary UX M1)** - summary section text and key points are cleaned by a deterministic post-processing step after citation validation (`strip_inline_reference_text` in `strands_agents/api/summary_generation.py`), removing `[MM:SS-MM:SS]` style references, legacy `[corrected-XXXX to corrected-YYYY]` leakage, and hybrid forms while leaving bracketed clinical wording untouched. Provenance is unaffected: the structured per-section citations array (already validated server-side) is the audit trail, and both delivery paths (HTTP response and Mercure) receive the same clean prose. Strip events are logged as counts only.
- **Scaffold-aware corrected-row cue cleanup (M12)** - the post-visit cue cleanup no longer degrades strong live transcripts. Weak-tier flips (bare "okay"/"and I" cues and borrowed neighbor evidence) now apply only when the visit shows a weak-scaffold signal: enough strong-cue flips that the live labels clearly disagree with what was said (strong cues never contradicted a good scaffold across four gated sessions). Strong cues, the tiny-answer rule, and cross-row phrase completions ("How can I help" + "this afternoon") keep their historical behavior everywhere. Measured on the four gated sessions: +6.6pp and +11.1pp corrected strict attribution on the two consult-08 scaffolds, ties on the rest, and the windowed weak-scaffold rescue (+31.6pp worth of cleanup) fully preserved.
- **Real-time pacing mode for the corrected fixture eval** - `scripts/eval-corrected-fixtures.sh` accepts `EVAL_PACE=1x` to stream fixtures at real-time cadence with browser-like 250ms chunks (default stays the historical unpaced blast so existing baselines remain comparable; each fixture line now prints its pace and chunk size). The first paced run also produced a finding worth its own entry: pacing does NOT explain the streaming engine's eval-vs-browser live gap (paced 59.1% vs unpaced 59.4% vs the real browser replay's 79.3% at the same 60s horizon) - the eval's WebSocket feed differs from the browser path beyond cadence, so browser replays remain the only honest live-lane reference on the streaming engine. Recorded in the runtime footgun.
- **Word-echo corrected-row splits (M10)** - the post-visit echo splitter now also separates a clinician's word echo from the patient's answer ("I vomited twice. **Twice, okay.**"), not just the digit/age echo, using the same native-timestamp guard set shipped in M08/M09. Two safety constraints from the gate: the duplicate must be a content word (duplicated fillers and bare agreements never split), and the patient part must carry its own decisive cue - the splitter never guesses. On the consult-03 @165s specimen this lifts corrected strict attribution 63.2% to 65.8% and resolves the flagged patient-under-DOCTOR source chip (findings 2 to 1). Cue lexicon gained "i vomited".
- **Orphan-speaker row role relabeling (M11)** - the live role lane now fixes rows belonging to "orphan" speaker identities the streaming engine mints before its voice cache settles (a speaker whose mapped role a strictly higher-row-count speaker already holds). Orphan rows are judged with the corrected-lane cue lexicon after reassembling fragments with their immediate same-speaker neighbors only - joining across a turn is forbidden because that produced the gate's single false flip (a patient's "yes" inheriting the doctor's question cues). Runs as a fill-only pass inside the existing M20 row-exception mechanism: M20 and clinician row corrections keep precedence, payload shape is unchanged, and two-speaker sessions are structurally untouched. On the 2026-07-07 streaming manual run this lifts live strict attribution from 89.7% to 95.6% and halves-plus the confident-error rate (10.3% to 4.4%) with zero wrong flips. Cue lexicon gained the identity-confirmation opener ("can I confirm your name").
- **Local-dev engine default flipped to streaming in `.env.example`** - `NEMO_SESSION_ENGINE=streaming` is now the template default so compose-based local runs match `scripts/start-dev.sh`, after 2026-07-07 real-time replay evidence showed the windowed engine at 40.0% live strict attribution vs the streaming engine's 85-90% band. CI and fresh checkouts without a `.env` keep `windowed` via the `docker-compose.yml` fallback; rollback stays an env flip plus nemo-agent restart.
- **Native word timestamps for post-visit correction (M09)** - the second-pass ASR call now requests NeMo's own word-level timestamps (`timestamps=True`, with a compatibility retry for overridden models) and prefers them over the token-proportional estimate, which container probes showed drifting ~1.6s at 60s while native times stay within ~40ms of TextGrid truth and stable across clip lengths. The fixture gate re-run with native times strictly dominates the estimate-based candidate (same with-overlap attribution gain 90.6% to 90.9%, WER buckets byte-identical to baseline), eval runs reproduce the guarded M08 numbers exactly, and correction latency stayed in the warm range (8.7-10.6s) with zero timing-validation warnings. The M08 drift guards remain as defense in depth.
- **Identity/echo corrected-row split on word timing (M08)** - the post-stop correction pass now extracts best-effort per-word timings from the second-pass ASR (`return_hypotheses=True`, probe-proven token-proportional derivation in the new `strands_agents/post_visit_word_timing.py`) and `strands_agents/corrected_role_cues.py` splits a patient identity row from a clinician's echoed-age acknowledgement (`... I'm 26. 26, okay.`) only when every guard holds: digit echo pinned by duplicate text, identity cue present, positive timing gap, no next-row collision, and a tail-coherence check requiring the timed echo end to reach the live row end - added after the first eval run showed full-clip proportional estimates drift (~1.6s on a 60s clip), which had placed the echo chip inside patient-only speech and cost 4.4pp strict attribution vs the no-split counterfactual (83.3% -> 78.9%); with the guard the split contributes zero marginal regression and fires only on coherent timing. Timings that disagree with the display words are dropped entirely, plain-text transcriber seams keep working, and no browser/API payload shapes changed.
- **Word-timing boundary-split gate (M07)** - scored a fixture-only split of the consult-08 identity/echo corrected row (`PATIENT "My name's Python and I'm 26. 26, okay."`) using post-visit probe word timings, against the m04-final baseline at the same cutoff. Every gate axis held or improved: strict attribution 90.6% and incorrect-confident 9.4% unchanged (the probe-timed echo row straddles the TextGrid cross-talk window, which strict scoring excludes), source-chip findings 0, non-overlap WER 33.5% with identical error totals, seam re-reads 3, and with-overlap attribution improved 90.6% to 90.9% - unlike the earlier text-proportional split that regressed consult-08 to 81.8% by placing the echo in patient-only time. Verdict: promote word timing for narrow echo-boundary splits only (M08, pending approval); global word-timing realignment stays rejected. Evidence under `var/quality/post-visit-timestamps/20260707T-m07/`.
- **Corrected source-chip scorer** - added a fixture-only QA scorer for corrected transcript artifacts. It reads corrected transcript JSON files or run directories, reports Doctor/Patient source-chip rows whose text cues contradict the assigned role, and can emit JSON for saved QA evidence without importing FastAPI, NeMo, or browser code. Initial runs found 4 clear cue contradictions across the saved consult-02/03/08 corrected artifacts and one contextual consult-03 browser warning for the standalone `Yeah.` row after a doctor question.
- **Offline diarization benchmark plan** - added a fixture-only plan to benchmark post-stop full-audio diarization before changing runtime correction. The first inventory keeps current Sortformer v2.1 as the immediately runnable local candidate, records pyannote Community-1 as dependency/token-gated, and requires both an oracle diarization ceiling and a non-oracle Doctor/Patient labeling variant before any promotion beyond QA artifacts.
- **Post-visit word timestamp probe** - added fixture-only timestamp QA scripts for the corrected transcript path. `scripts/probe-post-visit-timestamps.py` confirmed the pinned `nvidia/parakeet-tdt-0.6b-v3` post-visit ASR result exposes token timestamps with `return_hypotheses=True` without enabling the known-crashy live multitalker timestamp mode, and `scripts/report-post-visit-word-alignment.py` maps those estimated word timings back to live transcript rows. Consult-03 @60s shows the "let's try and get you" seam repeat appears twice in ASR words, so the repeat is not just row allocation.
- **Corrected transcript fixture eval runner** - added `scripts/eval-corrected-fixtures.sh` so local QA can stream named PriMock57 WAV fixtures through the live WebSocket path, fetch post-stop corrected transcript artifacts, score live vs corrected rows side by side, and save replay evidence per fixture before changing alignment or model choices. The first consult-02/03/08 @60s smoke improved corrected strict attribution and WER on all three fixtures, with remaining seam repeats localized to consult-03.
- **Post-stop transcript correction before summary** - added a same-origin `/session/{id}/correction` flow that runs before summary generation, uses retained session audio to create corrected transcript rows, and stores them in the corrected-transcript lane that summaries already prefer. The browser now shows correction progress as part of the summary loading flow, reuses existing corrected rows on retry, and falls back to live-preview rows when correction is unavailable instead of blocking the note.
- **Corrected transcript QA endpoint** - added FastAPI `GET /session/{id}/corrected-transcript` so local testing can fetch the post-stop corrected rows and score the exact transcript artifact used by summaries.
- **Second-pass ASR evaluation and evidence-linked summaries** - added a fixture-only second-pass ASR runner for testing newer English Parakeet candidates after recording, separate corrected-transcript storage for memory and SQLite backends, and source-linked summary citations. Summaries now prefer corrected rows when present, validate citation IDs against stored corrected segments, and render optional source chips in the browser without changing the existing summary route or Mercure topic.
- **Session-long streaming transcription engine (M22, experimental; dev default via start-dev.sh)** - a new `NEMO_SESSION_ENGINE=streaming` engine wraps NVIDIA's SpeakerTaggedASR composite so one Sortformer speaker cache owns speaker identity for the whole visit, replacing per-window re-diarization and stitching (the mechanism behind mid-visit Doctor/Patient inversions). Hardened for real-time pacing after live browser testing exposed four defects invisible to accelerated evals (partial-chunk stepping, decode-clock word times, unordered emission, eager speaker-cap pinning); word times now come from inverting the diarizer's own activity stream. Final full-corpus result on honest timestamps: strict attribution avg 86.9% vs 61.6% windowed (uniform 85-90 band, every fixture +4 to +36pp), recall 87.5-92.7% (above windowed), incorrect-confident rows 10-15% vs 18-48%, zero errors. `scripts/start-dev.sh` now defaults to the streaming engine (banner shows the active engine); compose/CI keep `windowed` until Phase 4 flips the project default. Rollback is an env flip plus agent restart.
- **Stop-time finalize drain for live recordings (M21)** - pressing Stop on a live-microphone visit now mirrors the demo-replay drain: the WebSocket closes (triggering the server's final NeMo pass) while the Mercure stream stays open until the backend `finalized` event arrives (15s bounded timeout), so the held-back tail utterances render and the summary is generated from the complete transcript. Pre-fix, stop tore the stream down immediately and the finalize flush published to nobody (reproduced: 8 browser rows vs 11 server rows); post-fix the same procedure shows 11/11. New Session still tears down immediately. The dev panel State tab shows `segmentsReceivedVsStored` so delivery gaps are visible at a glance.
- **Post-finalize role-churn artifact (M21)** - role flips landing after the `session.quality` record closes (the tail settle window) are now reported: `role_inference.completed` logs carry a `post_finalize` flag, and when tail churn actually happened the role worker persists one additive `quality_tail` JSONL row (`tail_role_flips_accepted`/`suppressed` plus final counters) beside the session's quality record. Existing `session.quality` fields and cardinality are unchanged.
- **Per-row speaker correction (M20 Phase 2)** - clinicians can now click any transcript line to correct who said exactly that line (Doctor -> Patient -> Unknown), without relabeling the speaker's other rows. Every emitted row carries a stable server-minted `segment_id`; corrections are stored row-scoped (separate from speaker-level confirmed overrides), survive later automatic role updates, the finalize history rebuild, and summary generation, and sync to other open tabs via a backwards-compatible `row_overrides` field on the roles topic. Corrected rows show a small "Dr/Pt ✓" chip. This is an explicit correction feature for rows where mixed audio makes automatic attribution wrong - marking a row Unknown is a valid answer and counts as uncertain in quality metrics.
- **Session quality records** - finalized WebSocket sessions now emit a `session.quality` log/event with chunk counts, window and inference percentiles, held/emitted segment counts, role confidence/flip counters, error count, and speaker-stability counters; the same JSON row is appended under the agent's gitignored `var/quality/sessions.jsonl`, and the dev State tab exposes the latest record.
- **Fixture eval runner** - added `scripts/eval-fixtures.sh` to stream PriMock57 WAV fixtures through the live WebSocket path, pull history and `session.quality`, run `scripts/transcript-quality.py`, append `var/quality/trend.jsonl`, and print a compact current-vs-previous report for M16/M17/M19 verification.
- **Transcript attribution scoring** - extended `scripts/transcript-quality.py` and the fixture trend rows with TextGrid-based speaker attribution, non-overlap attribution, residual phantom-speaker counts, and role-flip counts for diarization quality decisions.
- **Role attribution diagnostics** - added speaker oracle accuracy, role-mapping gap metrics, and per-session role timeline artifacts so fixture runs show whether wrong transcript labels come from diarization identity mixing, role mapping, fallback, or suppressed flips.
- **Honest role-mapping ceiling and flip-counter cross-check** - `scripts/transcript-quality.py` now reports the best valid one-DOCTOR/one-PATIENT mapping accuracy and the real `role mapping headroom` (the free-role oracle overstates recoverable accuracy when diarization mixes one voice across both speaker IDs), and `scripts/role-timeline.py --quality-json` appends a `role_timeline.quality_check` row because `session.quality` flip counters are snapshotted at disconnect while late role decisions land only in the logs; `scripts/eval-fixtures.sh` records both and warns on counter mismatch.
- **Transcript word-level quality metrics** - `scripts/transcript-quality.py` now reports WER with substitution/insertion/deletion counts, clean-vs-overlap WER, segment density, fragment rates, and seam re-read counts; `scripts/eval-fixtures.sh` records those metrics in trend rows so M17 accuracy/readability changes can be accepted or reverted by corpus numbers.
- **PriMock57 ground-truth transcripts** - added `scripts/download-primock57-transcripts.sh` to fetch the CC BY 4.0 Praat TextGrid transcripts paired by name with each demo consultation WAV, enabling transcription-quality measurement against a reference.
- **Summary failure guidance** - when summary generation fails, the panel now shows an actionable fix note and a page-level warning banner ("AI model unavailable … See README_STACK.md") pointing at Ollama/Bedrock reachability, instead of a bare "Summary generation failed" message; the banner clears once a summary renders.
- **Live model-unavailable warning** - when the role/summary model is unreachable, the agent publishes a one-time `system_error` on the session roles topic so the browser shows the warning banner during the consultation, not only at summary time; it clears once a summary renders.
- **Pre-flight AI-model gate** - a consultation no longer starts (recording or replay) when the off-GPU role/summary model is unreachable. The browser checks a new `GET /agent/model-health` endpoint first and, if unavailable, shows an actionable banner and aborts instead of transcribing with no roles/summary. Added `scripts/check-ai-model.sh` (referenced by the UI) to diagnose and pull the model, replacing the unhelpful "See README_STACK.md" copy.
- **Dev Panel connection indicator** - the Dev Panel header shows a green "● connected" / "○ disconnected" state reflecting the live Mercure feed, matching the 0.3.0 mockup.
- **Settled speaker-confidence badge** - the "Identifying speakers…" badge no longer pulses indefinitely; once inference returns it shows a settled state ("Roles identified" / "Low confidence" / "Speakers unclear (N%)") with no flashing.
- **Log analysis and eval tooling** - added `scripts/analyze-logs.py` for process-quality reports and `scripts/eval-role-heuristic.py` for GPU-free scenario role-attribution evaluation.
- **Stack inventory documentation** - added `README_STACK.md` with the current model, service, runtime, topic, and dependency inventory for the medical scribe stack.
- **Clinical intelligence documentation** - added `README_CLINICAL_INTELLIGENCE.md` to explain the medical phrase normalisation and clinical RAG/hints layers, including toggles, safety boundaries, benefits, and pending GPU/SSE proof.
- **Synthetic demo consultation corpus** - added an FFmpeg/Flite generator, manifest, attribution notes, and documentation for five license-clean replay WAVs, including chest pain, role-flip, three-speaker, drug-vocabulary, and monologue cases.
- **Medical phrase normalisation** - added an opt-in medical lexicon and post-ASR correction fallback behind `MEDICAL_BOOST_ENABLED` while NeMo decode-time phrase boosting remains GPU-pending.
- **Clinical hints sidebar** - added the `scribe/session/{id}/hints` Mercure topic, summary-response hint fallback, browser subscription, and dismissible sidebar for assistive clinician-review suggestions.

### Changed

- **Corrected fixture source-chip reports** - `scripts/eval-corrected-fixtures.sh` now saves corrected source-chip QA reports beside every corrected fixture run (`source-chip-score.txt` and `source-chip-score.json`) and prints the scorer summary line in the final eval output. Findings warn by default; `CORRECTED_SOURCE_CHIP_FAIL_ON_FINDINGS=1` opts into failing the eval after the evidence is saved.
- **Stop-triggered corrected summaries** - Stop/finalized now starts the existing correction-before-summary path automatically for demo replay as well as live recording, keeping retained audio inside the grace window and removing the main Summarise button. The summary panel still exposes retry after a failed note.
- **Corrected mixed source-chip cleanup** - corrected transcript cleanup now splits high-confidence Doctor prompt + Patient answer rows into separate source chips and keeps short clinician prompt fragments like `age, please?` and `is it affected?` Doctor-owned. Identity rows with echoed age acknowledgements remain unsplit until word-level timing is promoted, because splitting them created a timed attribution regression. Final fixture gates: consult-02/03/08 @60s scored 100.0% / 96.8% / 90.6% corrected strict attribution with `findings=0`; consult-03 @165s scored 97.8% corrected strict attribution with `findings=0`.
- **Corrected source-chip role cleanup** - corrected transcript rows now use a focused Doctor/Patient cue helper to relabel clear patient first-person or body-location rows that inherited Doctor scaffolds, and to treat tiny answers after Doctor questions as patient-owned when context supports it. Fresh consult-02/03/08 @60s corrected artifacts score `findings=0` in the source-chip scorer, down from 4 saved fixture errors; consult-03 @165s also scores `findings=0`, with the 02:40 `Yeah.` row now Patient.
- **Corrected source-chip alignment** - post-visit correction now preserves full-session text anchors when a consumed one-word row would previously abort anchoring and fall back to proportional allocation. This keeps consult-03 corrected source chips from shifting patient answers into doctor rows or doctor questions into patient rows after short utterances like "oh".
- **Transcript card role safety** - mixed or row-corrected transcript cards now derive their visible header from row-level Doctor/Patient evidence. A card with disagreeing row roles shows `Review labels` instead of a confident stale speaker-level label, while summary requests still send the row-resolved roles.
- **Clinical hints feature removed** - removed the clinical hints sidebar, `CLINICAL_HINTS_ENABLED` flag, `scribe/session/{id}/hints` topic, `clinical_hints` summary payload, and rule-based hint generator. The project-authored clinical KB remains as CPU-only summary grounding via `strands_agents/clinical_context.py`.
- **Offline diarization promotion rejected** - kept the live preview and post-stop correction runtime unchanged after the offline diarization benchmark. Sortformer full-audio failed the strict Doctor/Patient attribution gate, and pyannote Community-1 is blocked pending explicit dependency/token approval, so no FastAPI, browser, storage, Docker, or model-loading integration milestone is opened.
- **Pyannote Community-1 benchmark gated** - checked pyannote availability without installing dependencies or using tokens. The host venv has no pyannote packages, `nemo-agent` lacks `pyannote.audio`, and no Hugging Face or pyannote token wiring is configured, so the pyannote benchmark remains blocked until dependency and access approval is explicit.
- **Rejected Sortformer full-audio diarization candidate** - added a fixture-only Sortformer v2.1 full-audio diarization probe and scorer adapter, then rejected consult-03 @60s before smoke fixtures. The candidate improved non-overlap WER from 22.6% to 19.0% and removed seam re-reads, but emitted 4 speaker IDs for a dyadic consult; constrained strict attribution fell from 96.8% to 0.0%, and the diagnostic free oracle reached only 87.5%, so it remains QA evidence only.
- **Rejected post-visit word-timed alignment candidate** - added a fixture-only builder for scoring post-visit ASR word timings against live speaker rows, then rejected the naive word-center assignment on consult-03 @60s. It improved non-overlap WER from 22.6% to 19.0% and removed seam re-reads, but strict attribution fell from 96.8% to 85.2% and incorrect-confident rows rose from 3.2% to 14.8%, so it remains QA evidence only and is not wired into runtime correction.
- **Rejected corrected seam-prefix trim** - tested a local corrected-row cleanup for the consult-03 "let's try and get you" seam repeat. The real replay reduced seam re-reads from 3 to 0 but raised corrected non-overlap WER from 22.6% to 25.0%, so the change was reverted and seam cleanup is deferred to a future word-level alignment pass.
- **Post-stop corrected transcript alignment** - corrected rows now use live-text anchors instead of proportional word spreading, preserve live rows when second-pass ASR drops a visible utterance, and apply cue-order role cleanup only inside the corrected artifact. On consult-03 @60s manual replay, live strict attribution was 87.1% with 36.6% non-overlap WER; corrected output scored 96.8% strict attribution with 26.8% non-overlap WER.
- **Summary requests merge instead of replacing history (M21)** - a summary POST can no longer shrink the server-stored transcript: browser rows are merged by `segment_id` (roles update only rows no correction or automatic exception owns), rows the browser missed or filtered stay stored and still reach the note, rows the server never emitted are skipped, and an empty store falls back to the old restore-from-browser behavior for reconnects. Blank-text rows remain excluded from the note text at read time.
- **Finalize flush window logging (M21)** - `nemo_session.window_continuity` console lines now include the existing `phase` field (`chunk`/`finalize`), so the finalize flush re-logging the last window index is distinguishable outside JSON mode.
- **Rejected held-tail speaker-anchor spike (M20 Phase 5)** - tested the one seam mechanism
  exposed by the Phase 0 window artifacts: using the prior window's canonicalized held rows
  as extra overlap-vote evidence without widening NeMo audio, enabling timestamps, or adding
  a GPU model. The mechanism passed focused unit checks but failed the c03 @83s median gate
  (65.0/65.0/65.0 strict vs the accepted 70.0), so it was reverted; a restore smoke returned
  c03 @83s strict attribution to 70.0. No Phase 5 speaker-identity runtime change remains.
- **Role-agent establishment hardening (M20 Phase 4)** - role inference no longer sends the current automatic mapping or mapping history back into the Strands prompt, so an early wrong UI label cannot anchor later decisions. Bounded role evidence now refreshes representative utterances from cue-rich rows, includes opener-derived doctor/patient cue counts and first-seen position, and caps each evidence row at 120 chars. High-precision clinician self-introduction / consultation-opener cues produce an `establishment_hint`; if the model returns the exact two-speaker inverse and no clinician override exists, the server keeps the opener-derived mapping and logs `role_inference.establishment_hint_guard`. Final gates: c03 @83s strict 70.0 across 3/3 runs, c02 strict 82.4 across 3/3 runs, full-corpus strict avg 61.6 with zero truncations.
- **Automatic row-level role exceptions (M20 Phase 3)** - after every speaker-mapping update, a CPU-only cue lane re-judges each identified transcript row against cheap, explainable wording cues (clinician questions, second-person body references, first-person symptom reports) and either relabels a row that contradicts its speaker's mapped role or marks it explicitly uncertain; blended question+answer rows and quoted/echoed symptom wording go uncertain rather than confidently wrong. Exceptions ride the roles topic as an additive `row_exceptions` field, render as tentative dashed chips ("Dr auto", "?") the clinician can override with one click, never touch user-corrected rows, and count uncertain rows as incorrect in strict attribution so uncertainty cannot inflate quality numbers. Cue thresholds were measured on the full baseline corpus (zero wrong flips); no LLM involvement, `max_tokens truncation` stays untouched by construction.
- **Eval trend report strict columns (M20)** - `scripts/eval-fixtures.sh` per-run and `--report` tables now lead with strict attribution (+delta), uncertainty coverage, incorrect-confident rate, best valid dyadic ceiling, and the diagnostic free oracle, replacing the delta-heavy legacy layout.
- **Row-preserving summary input (M20 Phase 2)** - the browser's summary request now sends one record per transcript row (with `segment_id` and the row-resolved role) instead of per coalesced same-speaker card, so summarising no longer replaces server history with row-losing aggregates; the summary transcript is rebuilt from the server-corrected rows so a stale client can never feed the note an uncorrected role.
- **Honest role-confidence badge (M20 Phase 1)** - role updates now carry a backwards-compatible `role_stability` field computed live from speaker-identity counters (anchor remap rate, phantom merges, pending contrary mapping), and the browser badge only shows green "Roles identified" when mapping confidence is high AND speaker identity stayed stable; a confident mapping over churning identities renders as amber "Roles assigned - verify labels" with a correction hint, so the consult-03 failure mode (90% badge over inverted rows) can no longer render as identified. The dev panel State tab shows the same `roleStability` object.
- **Eval history fetch race fix (M20)** - `scripts/eval-fixtures.sh` now fetches session history after the role-timeline settle window instead of before it, so scored attribution reflects the settled labels a clinician sees rather than a race against post-disconnect role flips (identical role-decision timelines previously scored 45% or 55% depending on fetch timing).
- **Strict doctor/patient attribution metrics (M20)** - `scripts/transcript-quality.py` now reports strict clean attribution (uncertain/UNKNOWN clean rows stay in the denominator as incorrect), labeled-row accuracy, uncertainty coverage, and incorrect-confident-row rate alongside the existing visible attribution and best-valid-dyadic ceiling, and marks the per-ID speaker oracle as free/diagnostic-only in its output. Hiding hard rows behind uncertainty can no longer raise the headline detection number.
- **Per-window speaker-continuity diagnostics (M20)** - live NeMo sessions log one `nemo_session.window_continuity` record per emission window (raw vs canonical speaker IDs, overlap-vote evidence, mapping reasons, remap/phantom-merge counts, emitted spans - never transcript text), and `scripts/eval-fixtures.sh` saves them per fixture as `window-continuity.jsonl` via the new `scripts/window-continuity.py`. Requires `LOG_FORMAT=json` on the agent container; the eval runner warns when no windows are captured.
- **Row-level attribution diagnostics (M20)** - `scripts/transcript-quality.py --row-diagnostics-json` writes a per-row artifact (expected vs visible role, overlap flag, confidently-wrong flag, best-valid-mapping fixability, and window/seam joins against the window artifact) so one wrong Doctor/Patient card can be traced without rescoring; the eval runner stores it as `row-diagnostics.json` beside each fixture's history.
- **Medical term correction safety** - the post-ASR fallback now has a reviewer/eval sidecar and CPU-only evaluator, preserves sentence-initial capitalization, tolerates missing/unreadable lexicon files, and disables risky prior variants (`heart attack`, `thyroid function tests`, `listen april`) unless reviewed.
- **Medical boost evaluator coverage** - the CPU-only medical boost evaluator now fails when active lexicon rows lack reviewer provenance/rationale or when the review table drifts from the active runtime lexicon.
- **Gruff PHP accepted-debt baseline** - added a PHP-specific baseline entry and Composer validation wrapper for the requested `blundergoat/strands-php-client` `dev-dev#98bd6598...` constraint so preflight stays green while the project deliberately tests that unreleased client branch without breaking `gruff-py`'s default baseline loader.
- **Transcript fragment readability** - live NeMo sessions now merge adjacent same-speaker word-sized fragments before publishing them, reducing clean-region fragment rates across PriMock57 without merging alternating-speaker ping-pong fragments or changing the browser payload shape.
- **Transcript punctuation readability** - server-side segment cleanup now inserts missing spaces after glued sentence punctuation before rows reach the browser, so ASR text like `started.My` renders as readable transcript text without changing payload shape.
- **Transcript overlap-ceiling spike** - added an eval-only separated-channel runner for named PriMock57 fixtures, but stopped the full-corpus ceiling path after batch mode exceeded GPU memory and WebSocket mode destabilized NeMo on c04; the runner now blocks accidental full-corpus runs unless explicitly allowed.
- **CI context validation workflow** - removed the GitHub Actions wrapper for context validation; the local `./scripts/context-validate.sh` check remains available for agent/workflow edits.
- **Transcript seam spike results** - recorded and rejected two M17 seam-residue mechanisms: NeMo word timestamps exposed the needed SDK surface but crashed the GPU path during live fixture eval, and raising the emission floor failed to reach zero seam repeats without risking short-utterance loss. The accepted runtime keeps the stable timestamp-free NeMo decode path and the previous 0.3s emission floor.
- **Strands PHP client dev upgrade** - Composer now uses the requested `blundergoat/strands-php-client` `dev-dev` commit `98bd6598...`; Symfony Strands calls use the new response-observer hook to add body-safe response counts to `strands.client.call` logs, and the scribe client retries transient Python proxy failures (`429/502/503/504`) twice with a short backoff.
- **Windowed transcript emission** - `TranscriptionSession` now transcribes only audio past an emission high-water mark (with a short context lead) instead of re-transcribing the whole session every chunk. Each stretch of speech reaches the browser exactly once, segments still forming at the buffer edge wait one chunk, the finalize step drains and publishes the held tail, and window speaker IDs are matched to the previous window so labels stay continuous. Fixes the duplicated/growing live transcript and removes the O(n²) GPU cost; measured on PriMock57 consultation-03 ground truth, 4-gram duplication dropped to 3%.
- **M16 reduced quality scope** - the 0.3.0 diarization milestone now ships phantom containment, confidence observability, flip damping, and diagnostic ceilings while deferring true seam-stable speaker identity to a future milestone; the deep diagnosis showed the original ≥90% attribution target is capped by window-seam identity instability.
- **Dyadic speaker containment** - NeMo sessions now cap visible speaker IDs to the configured consultation limit (`NEMO_SPEAKER_CAP`, default `2`) and merge stray window-local speaker IDs back into an established visible identity, with phantom-merge counts captured in session quality records.
- **Dev workspace layout** - transcript, summary, and dev rail now split the width 5/4/3; Clinical Hints render above a collapsible Dev Panel in the right rail; the Demo Audio picker moved into the header (placeholder "Select demo audio", icon-button height, chevron icon, Upload WAV entry in its menu) and the left fixture panel was removed, as was the "Speaker labels corrected" toast.
- **Ollama behind a compose profile** - the `ollama` service only starts under the `ollama` compose profile; `start-dev.sh` activates it when `ROLE_AGENT_MODEL_PROVIDER=ollama`, and Bedrock setups run a three-service stack. `check-ai-model.sh` gained a real Bedrock probe (inference-profile existence plus a one-token invoke) instead of echoing configuration.
- **Agent log defaults** - the NeMo agent now defaults to JSON logs in Compose, with documented `jq` commands for tracing chunk timing, errors, and Mercure publishes by `session_id`; `LOG_FORMAT=console` remains available for plain interactive logs.
- **Streaming demo replay** - demo audio now streams through the live transcription pipeline instead of a batch upload: the browser decodes the WAV to 16 kHz PCM, sends chunks over the same WebSocket as the microphone paced by the audible replay clock, and transcript rows arrive via Mercure exactly like a live visit. Removed the FastAPI `/session/{id}/replay` and `/session/{id}/replay/stop` endpoints, `replay_session.py`, and the Symfony replay proxy routes; this also retires the PHP upload-size and full-file NeMo GPU-memory footguns for demo audio.
- **Medical-only agent behavior** - removed Python-side mode selection for role inference, summaries, replay, and WebSocket ingest so the agent lane always uses DOCTOR/PATIENT role mapping and medical SOAP summaries.
- **Medical-only scribe UI** - removed the browser mode selector, stored mode preference, and mode query parameters from live WebSocket and replay requests.
- **Medical-only demo scenarios** - removed meeting, interview, TV/media, and lecture scenario fixtures from the developer scenario corpus.
- **Demo audio picker** - replaced the left-side dev scenario runner and duplicate header demo button with generated `tests/fixtures/audio/` WAV options that use a built-in-server-safe replay URL.
- **PriMock57 demo audio set** - excluded consultations 01, 09, and 10 from local fixture generation, manifest output, and the default M2 replay smoke.
- **PriMock57 replay clip length** - generate full-length PriMock57 demo consultations (previously capped to 90 seconds) so replay and transcription-quality checks cover the whole encounter; `NEMO_BUFFER_MAX_DURATION` (default 900s) bounds GPU memory.
- **Gruff TypeScript scope** - excluded the vendored Tailwind runtime from gruff-ts so analyzer findings focus on maintained frontend and workflow source.
- **Frontend structure** - split transcript rendering, replay, summary, and download behavior out of the core recording script for easier gruff-ts verification.
- **Gruff Python scope** - excluded one-off NeMo exploration scripts from gruff-py so Python analyzer findings focus on maintained runtime and test code.
- **Python API structure** - moved live streaming and role-inference queue workflows out of `server.py` while preserving FastAPI routes and browser-visible Mercure behavior.
- **Python quality gates** - scoped gruff-py to maintained runtime code and kept pytest as the behavioral test-quality gate for integration-heavy Python tests.
- **PHP complexity gate** - retired the bespoke cyclomatic checker and rewired Composer/preflight complexity checks to gruff-php.
- **PHP dependency bounds** - required PHP `>=8.3 <9.0` (8.3+ within PHP 8; the deployable image stays on 8.3) and moved `blundergoat/strands-php-client` from the moving `dev-dev` branch to the tagged 1.4 series.
- **PHP generated reference scope** - excluded the generated Symfony/Psalm `config/reference.php` from gruff-php instead of hand-editing generated output.
- **PHPUnit strictness** - enabled failure-on-warning, failure-on-deprecation, risky-test, output, and global-state strict flags.
- **Observable process logs** - added JSON-line logging on Python and PHP with `session_id`/`correlation_id` join keys, Strands SDK token/latency metrics, and Mercure delivery outcomes.
- **Pinned NeMo image build** - moved the GPU agent image to `nvcr.io/nvidia/nemo:26.02`, pinned `nemo_toolkit[asr]==2.7.3`, and made the container install the checked-in Python requirements file.
- **Python dependency floors** - raised FastAPI, Uvicorn, Pydantic, HTTPX, websockets, SSE Starlette, soundfile, Strands Agents, pytest, pytest-asyncio, and Ruff floors while keeping numpy at the NeMo-compatible 1.x floor until the GPU image is verified.
- **WebSocket server backend** - set Uvicorn to `websockets-sansio` in both Dockerfile and Compose entrypoints so local browser sessions use the same backend.
- **PHP dependency floors** - raised Mercure, Mercure Bundle, PHPUnit, and Infection within the PHP 8.3/Symfony 6.4 lane, and tightened `symfony/dotenv` back to the Symfony 6.4 series.
- **Clinical summary grounding** - added a CPU-only PoC clinical knowledge helper so generated SOAP summaries can include short documentation reminders without using the NeMo GPU.
- **Scribe workspace design** - refreshed the consultation UI toward the 0.3.0 mockup with a compact left demo-audio/dev rail, softer clinical palette, pill controls, and a transcript/summary split workspace.
- **Session summary panel states** - gave the summary panel explicit pending, generating, generated, and failed states with a status badge (`✓ Generated` / `Summary unavailable`) and a retry control, showed the pending placeholder only once transcript text exists, made the panel a fixed non-collapsible header (removed the toggle and chevron), and guarded against overlapping in-flight summary requests per session.
- **Consultation fonts** - loaded the Libre Franklin (UI) and IBM Plex Mono (dev/log) webfonts so the rendered consultation UI matches the 0.3.0 mockup typography instead of falling back to system fonts.
- **Demo audio dropdown** - replaced the demo-audio card list with a compact dropdown selector ("consultation-0X · complaint" plus a "PriMock57 consultation · doctor / patient" descriptor) matching the 0.3.0 mockup; picking a clip starts its replay and replay status still marks the chosen option.

### Fixed

- **Summary prose citations can no longer show invalid minute:second values** - the summary agent's citation rules now state explicitly that bracket timestamps are MM:SS with seconds 00-59 (with a conversion example: 196 seconds is [03:16], never [02:76]) and that segment IDs never appear inside brackets. The invalid formats had appeared twice in manual tests (`[02:98-03:01]`, `[02:76-02:79]`) plus one ID-in-brackets variant; a regenerated summary over a captured 126-row consultation produced 7/7 valid citations with the tightened instructions. Rendered source chips were never affected - this is prose-formatting only.
- **Post-visit row correction no longer wipes the role badge** - a transcript row
  correction sent after the reconnect grace window expired used to resurrect empty
  role state and broadcast it (`mapping={}`, zero confidence), dropping the header
  badge from `Roles identified (92%)` to `Speakers unclear (0%)` on the consult-03
  manual test. The row-override publish now peeks at role state instead of creating
  it and includes mapping/confidence only for a still-live visit, and the browser
  applies row corrections without letting a `manual_override` event move the earned
  confidence badge.
- **M21/M22 review hardening** - removed an unconditional transcript-bearing
  streaming-engine debug dump to `/tmp/engine-debug.jsonl`; live Stop now drains
  even when no rows are visible before finalize flushes the first rows; and late
  same-speaker or cross-speaker rows now stay chronological inside/coalesced across
  transcript cards before summary generation reads the DOM.
- **Role-agent tool payload size** - shrank the Strands `assign_roles` contract so the
  model passes only session ID, mapping, confidence, and terse reasoning while transcript
  rows stay in server-side pending state; role updates still publish the same browser
  `attributed_segments` payload, role-agent input now uses capped per-speaker evidence
  instead of `transcript_so_far`, and `session.quality` records role truncation events.
- **Suppressed role-flip handling** - a damped `assign_roles` flip now counts as a successful
  tool decision, so the role-agent runtime keeps the established DOCTOR/PATIENT mapping
  instead of falling through to the keyword fallback and applying the suppressed relabel.
- **Agent session isolation and role overrides** - role and summary Strands agents are now
  created per call instead of cached as singleton conversation objects, and server-side role
  mapping now preserves a user's manual speaker correction over later agent proposals.
- **Manual role override persistence** - speaker-label clicks now post through the same-origin
  Symfony `/scribe/{sessionId}/roles/override` proxy instead of a browser-to-FastAPI CORS
  request, so the visible correction is also saved in the server role state.
- **Structured role and summary agent contracts** - role inference now accepts only the
  compact `assign_roles` tool path and falls back to the keyword classifier when the tool is
  not invoked; summary generation now uses a Pydantic structured-output schema, drops the
  unused `duration_seconds` summary field, and has independent `SUMMARY_AGENT_*` model and
  token settings.
- **Python diagnostic log lines** - warning and error logs in the agent now put session IDs, error types, and error text into the plain message line, while exception-backed paths include tracebacks. Added an observability guard so `logger.error("event", extra={...})` regressions fail in pytest instead of hiding details in Docker logs.
- **Strands callback noise** - role and summary agents now pass the SDK's explicit null callback handler so model reasoning, tool banners, and streamed summary prose do not print into the container log stream.
- **Ollama host unreachable via stale `.env`** - the agent's `OLLAMA_HOST` is now pinned to the in-network `http://ollama:11434` in `docker-compose.yml` and is no longer overridable by `.env`. A stale `.env` value of `http://host.docker.internal:11434` (unreachable from the agent on WSL2) was silently making every summary 502 and forcing role inference onto the weak keyword heuristic across container recreates.
- **Demo Audio panel gap** - the demo-audio panel is now content-height (grid `auto` row) so the Upload WAV button sits directly under the selector and the Dev Panel fills the remaining rail, instead of a fixed 42vh panel with a large empty gap.
- **Consultation viewport layout** - the scribe page now fits the viewport height with the transcript and summary panels scrolling internally, instead of growing past the viewport and producing a page-level vertical scrollbar.
- **Agent image boto3/botocore conflict** - the NeMo base image's runtime venv (`/opt/venv`) shipped `botocore 1.42.61`, which shadowed the boto3/botocore that `strands-agents` installed into the system site and crashed the FastAPI agent at import (`cannot import name 'DocumentModifiedShape' from 'botocore.docs.utils'`), leaving the container unhealthy and blocking `setup-initial.sh`. The `docker/nemo/Dockerfile` now installs a matched `boto3==1.42.61`/`botocore==1.42.61` pair into `/opt/venv`, which also satisfies the base image's `aiobotocore<1.42.62` pin.
- **Ollama Compose wiring** - the agent now defaults to the bundled `ollama` service (`http://ollama:11434`), which starts with the stack; removed the `local` profile, added a `nemo-agent`→`ollama` dependency, and dropped the host port so it never clashes with a host-side Ollama. Fixes summaries returning 502 and role inference falling back to the heuristic when `host.docker.internal:11434` was unreachable (e.g. on WSL2).
- **Demo audio replay routing** - added same-origin Symfony proxies for replay and summary requests so the browser receives JSON from FastAPI instead of app-origin HTML errors.
- **Audible demo audio stop flow** - made Demo Audio replay attach the selected WAV to a browser audio player and added early replay stop/cancel handling before the current automatic summary flow.
- **Demo audio transcript pacing** - made replay transcript rows reveal from the browser audio clock and send the visible transcript snapshot to stop/summary routes so text cannot outrun what the user hears.
- **Large demo WAV replay** - raised local PHP upload limits for PriMock fixtures and made replay treat malformed success responses as recoverable UI errors.
- **Transcript empty state** - hid the start prompt as soon as transcript rows render, including dev-injected replay/test events.

### Security

- **Frontend transcript rendering** - moved transcript, summary, status, and dev-panel output away from HTML-string rendering so model and scenario text is inserted as text.
- **Deploy workflow actions** - pinned third-party AWS GitHub Actions to reviewed commit SHAs.
- **Env template placeholders** - replaced realistic-looking committed secret examples with obvious local placeholders and removed copied AWS credential slots from `.env.example`.
- **Remote health-check secret path** - moved the production API key secret path behind a required `SECRET_PATH` override instead of committing the deployed path.

### Removed

- **Multi-mode support** - removed Meeting, Interview, TV/Media, Lecture, and General modes, including the `?mode=` transport parameter, `_session_modes`, mode prompt dictionaries, and the browser mode selector.
- **Transcript download control** - removed the Download button, keyboard shortcut, and browser-side JSON/TXT export code from the scribe UI.

## [0.2.0] - 2026-03-16

Release covering tool-based role mapping, summaries, replay, transcript grouping, scenario gates, JS extraction, UI polish, developer guidance, multi-mode role inference, local-first defaults, SQLite persistence, manual speaker overrides, and full-stack hardening.

### Added

- **Gruff quality analyzers** - added TypeScript, Python, and PHP dev analyzers: `@blundergoat/gruff-ts`, `gruff-py`, and `blundergoat/gruff-php`.
- **Agent-neutral instruction layer** - added reusable AI guidance in `ai/instructions/` plus routing docs for agents that do not depend on Claude Code or Codex runtime files.
- **CI validation** - added router-table and skills-directory checks, plus a quick-reference commit instruction file.
- **`@tool` role assignment** - added Strands tool support for role state management while keeping the free-text JSON fallback.
- **Session summaries** - added six mode-specific summary prompts, `POST /session/{id}/summary`, Mercure summary publishing, UI display, and transcript export support.
- **Replay demo mode** - added WAV upload replay through NeMo with paced Mercure events, speed control, progress UI, and automatic role inference.
- **Transcript grouping** - merges consecutive same-speaker segments into chat blocks that relabel and download correctly.
- **Scenario assertions** - added duration, content, and fixture-structure validation for the scenario runner.
- **Ollama tool-calling footgun** - documented models that support `assign_roles`, including `qwen3.5:9b`.
- **Frontend extraction** - moved production code to `public/js/scribe.js` and dev-only panel code to `public/js/scribe-dev.js`.
- **Developer instrumentation** - added WebSocket frame/byte counters, Docker hot reload, template rebuild guidance, five multi-mode scenarios, and 37 Python tests.
- **Mode-aware role inference** - added six mode-specific prompts, browser-passed mode, context labels, and per-mode agent caching.
- **Role inference fallback** - falls back from LLM agent to mode-specific heuristics, then to graceful no-role output.
- **Manual speaker override** - lets users cycle roles, publishes overrides to Mercure, locks confirmed speakers, and exposes `POST /session/{id}/roles/override`.
- **SQLite persistence** - added `StorageBackend`, SQLite and memory backends, `SESSION_STORAGE=sqlite|memory`, WAL mode, and Docker data persistence.
- **Reconnect support** - added WebSocket reconnect grace, session resume, and Mercure Last-Event-ID event IDs.
- **Speaker and role UX** - added hallucination filtering, cold-start animation, flip toast, audio-level feedback, clipping warnings, keyboard shortcuts, and transcript accessibility attributes.
- **Runtime cleanup and protocol fields** - added orphan cleanup plus `segment_id`, `revision`, and `supersedes` fields for future reconciliation.
- **Local runtime support** - added optional CPU Ollama service, bundled Tailwind, Python hot reload, and expanded SQLite, role inference, hallucination, and session tests.

### Changed

- **BREAKING: PHP baseline is now 8.3+.** Upgrade local, CI, and deployment PHP from 8.2 to 8.3 before running Composer; this has no deprecation window because the PHP Gruff dev tool requires PHP 8.3.
- **Ollama default model** - changed `llama3.1:8b` to `qwen3.5:9b` to match local pulls, `.env.example`, and Docker Compose.
- **Role inference worker** - detects tool invocation via mapping-history growth and avoids duplicate role mapping application.
- **Role inference prompt and agent setup** - instructs tool calling with JSON fallback and passes `assign_roles` in the agent tool list.
- **Role flip detection** - moved client-side so Mercure reporting reflects visible mapping changes.
- **Developer scripts and labels** - simplified `start-dev.sh` flags and renamed TV/General start labels.
- **Agent guidance** - made Ask First paths, commit areas, evals, and lessons more project-specific.
- **Default role provider** - changed `ROLE_AGENT_MODEL_PROVIDER` from `bedrock` to `ollama` for local-first startup.
- **Confidence scoring** - uses a rolling last-five window instead of lifetime average.
- **Transcript context** - sends the first 500 and last 3000 characters to preserve opening context.
- **Agent parsing and prompt state** - extracts JSON from preamble text and caps mapping history to five entries.
- **Inference queue** - uses `maxsize=50` with non-blocking enqueue and drops overflow batches.
- **Async/runtime internals** - replaced deprecated event-loop access, reused one Mercure `httpx.AsyncClient`, switched `AudioBuffer` to `deque`, optimized relabeling by speaker map, and simplified session destruction.

### Removed

- **`ROLE_INFERENCE_SYSTEM_PROMPT`** - removed the unused backwards-compatibility alias.
- **Legacy live role SSE path** - removed the PHP `/roles/stream` endpoint, `RoleInferenceService::streamRoleInference()`, `RoleInferenceResult`, `fetchAuthoritativeSnapshot()`, and the Python `/session/{id}/roles/stream` endpoint.
- **Unused SSE support** - removed `sse-starlette` imports and SSE consumer tracking.
- **`docker-compose.no-gpu.yml`** - removed the unused no-GPU compose file because the app requires GPU transcription.

### Fixed

- **Instruction drift** - fixed the `blundergoat/strands-php-client` package name, footgun cross-reference, and CI instruction-file triggers.
- **Session cleanup** - clears confidence pulse, dev panel logs, speaker maps, summaries, and replay state.
- **Download fallback** - collects text from grouped segment spans instead of a single segment node.
- **E2E contracts** - uses UUID session IDs, checks the `/summary` endpoint, and asserts the extracted `scribe.js` reference.
- **Python tests** - repaired stale imports, fixtures, UUIDs, and `AudioBuffer` API expectations.
- **File upload security** - replaced user-shaped temp paths with `NamedTemporaryFile`.
- **Session ID validation** - rejects malformed IDs with HTTP 400 on all endpoints.
- **Error privacy** - publishes generic Mercure errors and truncates role inference logs with `error_type`.
- **Frontend/runtime issues** - declared `pcmStreamer`, filtered health-check log spam, and made the ready banner use configured ports.

### Tests

- **230 Python unit tests** cover tool/free-text role mapping, flip detection, agent creation, summaries, replay, heuristics, and scenario fixtures.
- **25 E2E contract tests** cover agent health, sessions, WebSocket, file transcription, PHP proxy, Mercure pub/sub, lifecycle, and cross-service shape matching.

### Security

- Session IDs are UUID-validated on all API endpoints.
- Temp files use secure generated paths.
- Mercure error messages are sanitized.
- Transcript content is stripped from application logs.

## [0.1.0] - 2026-03-15

First release: real-time audio transcription with speaker diarisation, role inference, and a developer scenario runner that works without GPU hardware.

### Added

- **Transcription UI** - added the Twig page with live transcript, recording controls, timer, JSON/text download, and reset.
- **Modes and theme** - added Medical, Meeting, Interview, TV/Media, Lecture, and General modes plus persisted light/dark theme.
- **Streaming clients** - added Mercure `StreamOrchestrator`, browser-side `PcmStreamer`, and WebSocket reconnect logic.
- **Role inference** - added Strands role updates, retroactive relabeling, and confidence badges.
- **Dev panel** - added dev-only scenario, transcript, inspector, pipeline, Mercure, WebSocket, state, and raw-event views.
- **Scenario runner** - added eight fixture-driven scenarios with validation, batch execution, progress, and JSON export.
- **Backend and agent APIs** - added ScribeController routes, FastAPI WebSocket ingest, NeMo diarisation, Mercure publishing, session lifecycle, and role assignment tooling.
- **Infrastructure and tooling** - added Terraform, GPU Docker Compose, Mercure, setup/start/preflight/health/load/e2e/context scripts, quality gates, PHPUnit, pytest, Playwright scaffolding, and project docs.

### Fixed

- `start-dev.sh` no longer crashes on unbound variables or undefined functions.
- Dev panel segment data updates retroactively.
- StreamOrchestrator `_active` flag ordering is correct.
- Python hot-reload uses the correct Docker volume mount path.

[Unreleased]: https://github.com/user/ambient-scribe/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/user/ambient-scribe/releases/tag/v0.2.0
[0.1.0]: https://github.com/user/ambient-scribe/releases/tag/v0.1.0
