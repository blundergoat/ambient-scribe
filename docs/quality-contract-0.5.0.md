# 0.5.0 Quality Contract

Status: frozen by T01.8 human ratification
Contract version: `0.5.0-frozen.1`
Ratifier: session user
Ratified date: `2026-07-17`
Frozen fixture split: see `M01-freeze-quality-contract-and-baseline.md`

This contract keeps transcript accuracy and SOAP-note faithfulness independent. Official
TextGrid speech can prove an ASR defect, but it never authorizes a SOAP claim that is absent,
garbled, ambiguous, or assigned to the wrong speaker in the selected persisted source.

## Evidence classes

| Class | Meaning | Permitted use |
| --- | --- | --- |
| `official_speech_truth` | The two official Doctor/Patient TextGrid tracks | Score transcript accuracy and establish what was spoken |
| `persisted_selected_source` | The exact retained rows selected for summary generation | Authorize or withhold a patient-specific SOAP claim |
| `retained_note` | A saved summary response or schema-v2 payload | Reproduce generated-note behavior only |
| `reported_not_reproduced` | A historical UI/reviewer report without its matching saved note/source pair | Seed a forward guard only when official speech and retained selected-source evidence independently justify that guard |
| `contract` | A human-approved prior milestone rule | Constrain evaluation; never substitute for speech/source evidence |

## Evidence registry for consult-5.3

No sealed holdout was opened, parsed, replayed, or hashed while preparing this registry.

| ID | Evidence class | Artifact identity |
| --- | --- | --- |
| `C53-DOC` | `official_speech_truth` | `tests/fixtures/audio/primock57-day5-consultation03-im-feeling-very-anxious.doctor.TextGrid`; SHA-256 `38b91e45c3f6470f695b944815fa3980b2b47c145b0aaa67e825fe2b3606160d`; 14,576 bytes |
| `C53-PAT` | `official_speech_truth` | `tests/fixtures/audio/primock57-day5-consultation03-im-feeling-very-anxious.patient.TextGrid`; SHA-256 `d55d04f79fcb8dc0e3a1a330355edb44a4746b6be55ace280b80a7a9055097c7`; 19,462 bytes |
| `C53-SOURCE` | `persisted_selected_source` | M02 accepted `whole_visit_corrected` source, session `0bb45417-1472-45df-8bf9-eb35bbab5e44`, attestation `ae029e319a8449a6b71608e1009e30b7`: `var/quality/m02-acceptance-20260715T044500Z/replays/primock57-day5-consultation03-im-feeling-very-anxious/corrected-transcript.json`; SHA-256 `2a0cc6a3ce5aad522d6fa732da6db05036d9842da16d3e61b54eed2e6eaaff47`; 113,669 bytes |
| `C53-NOTE-V1` | `retained_note` | M02 accepted note from `C53-SOURCE`: `var/quality/m02-acceptance-20260715T044500Z/replays/primock57-day5-consultation03-im-feeling-very-anxious/summary.json`; SHA-256 `27a542876fb6f28b6515015c6319928bf76e16bfe38526a3d6d5f0e62c67cddf`; 9,966 bytes |
| `C53-NOTE-V2` | `retained_note` | M06 schema-spike payload built from 435 selected corrected rows: `var/quality/m06-schema-spike-20260715T201755Z/consult-5.3/payload.json`; SHA-256 `1832a000c9a2b9e3f2f306d5a9ec1da5726e3ba51669e1d827fe01eed836a50f`; 72,788 bytes |
| `C53-NOTE-M08` | `retained_note` | Earlier retained note: `var/quality/note-fidelity-audit-20260713T234904Z/replays/primock57-day5-consultation03-im-feeling-very-anxious/summary.json`; SHA-256 `8a4be31b77965a297c7ec8408fa8618872988983cf0470c11e411dca9dd293a5`; 11,277 bytes |
| `C53-REPORT` | `reported_not_reproduced` | Archived 0.4.0 backlog entries `New note-fidelity specimens from the full run` and `Summariser silent repair CONFIRMED`: `.goat-flow/plans/_done/0.4.0-improve-prime/backlog.md`; SHA-256 `81dafcb80b7e1a4f4654a36f88790b1dd4455d08a027de194c66ea3b33742106`; 21,506 bytes. The matching `0c976876` note/source pair is not retained. |

The M01 “Read first” paths for the 0.4.0 prime plan now resolve under
`.goat-flow/plans/_done/0.4.0-improve-prime/`. The archived originals were read; this
path move changes no contract meaning.

## Consult-5.3 source-truth claim table

### C53-01 — Therapy name must not be silently reconstructed

- `speech_truth` (`official_speech_truth`): `C53-DOC` at 668.199-691.555 describes
  “talking therapy” and an uncertain “inter-behavioural” type; it does not say CBT or
  cognitive behavioural therapy.
- `selected_source_truth` (`persisted_selected_source`): `C53-SOURCE`
  `corrected-0378`–`corrected-0380` says “talking therapy or type / of / behavioral
  therapy”. No selected row contains “cognitive”.
- `required_note_behavior`: A note may use supported “talking therapy” or the source's
  bounded behavioural-therapy wording. It must not expand this to CBT or cognitive
  behavioural therapy. If the selected wording is treated as garbled, omit or qualify
  the therapy subtype rather than repair it from model knowledge.
- Retained-note result: `C53-NOTE-V1` and `C53-NOTE-V2` use talking/behavioral
  therapy and do not reproduce CBT. `C53-REPORT` reports a different note that wrote
  cognitive-behavioural therapy, but its matching saved payload is absent; classify that
  historical occurrence `reported_not_reproduced`, while retaining the guard because
  both speech and selected source exclude “cognitive”.

### C53-02 — Alcohol change requires two endpoints

- `speech_truth` (`official_speech_truth`): `C53-PAT` at 574.294-616.249 gives one
  current consumption snapshot: weekend drinking, uncertain weekday drinking, and no
  reliable tracking. It supplies no earlier alcohol baseline.
- `selected_source_truth` (`persisted_selected_source`): `C53-SOURCE`
  `corrected-0324`–`corrected-0343` contains one current snapshot only. Several detail
  rows are assigned `OTHER`, so attribution-sensitive quantities are unsafe unless
  supported by the retained Patient rows.
- `required_note_behavior`: Never state increased, reduced, resumed, stopped, or
  unchanged alcohol use without both an explicit baseline and current endpoint in the
  selected source. Current use may be documented only to the specificity and speaker
  certainty supported by the selected rows.
- Retained-note result: the retained notes contain current-use descriptions but no
  longitudinal change. `C53-REPORT` records an invented alcohol-change class without a
  matching retained note; classify the historical occurrence `reported_not_reproduced`
  and retain the two-endpoint guard.

### C53-03 — Chest-pain evidence is conflicting and conditional

- `speech_truth` (`official_speech_truth`): `C53-DOC` at 296.386-298.486 asks about
  chest pain; `C53-PAT` at 299.132-305.948 answers “No, well yeah” and limits pain to
  when the heart is beating fast.
- `selected_source_truth` (`persisted_selected_source`): `C53-SOURCE`
  `corrected-0172`–`corrected-0176` preserves the Doctor question and Patient wording
  “No well yeah it's painful ... when my heart is beating fast”.
- `required_note_behavior`: Preserve the conflict and conditional relationship. Do not
  turn it into either a clean chest-pain denial or an unqualified constant-positive
  claim.
- Retained-note result: `C53-NOTE-V2` `subjective-13` records pain when the heart is
  beating fast but drops the initial conflict; `C53-NOTE-V1` omits the screen. Neither
  artifact proves the full required behavior.

### C53-04 — Panic exchange is unsafe, not a clean denial

- `speech_truth` (`official_speech_truth`): `C53-DOC` at 409.607-422.616 asks first
  about being too overwhelmed to enter work/situations and then about panic attacks.
  `C53-PAT` has an unintelligible overlap at 420.768-422.038 and “No I wouldn't say so
  ... always managed to make it to work” at 424.038-429.483. The answer aligns at most
  ambiguously across the competing questions.
- `selected_source_truth` (`persisted_selected_source`): `C53-SOURCE` omits the
  standalone panic question and inserts “panic attack” into Patient
  `corrected-0240` before “No, I wouldn't say so”; this overresolves wording absent from
  the Patient speech track and cannot safely establish a panic denial.
- `required_note_behavior`: Omit the panic outcome or state explicitly that panic
  attacks were asked about and the response is unclear. Never document a clean denial
  from the current selected source.
- Retained-note result: `C53-NOTE-V2` omits a denial and emits
  `mental_health_screen_missing`; `C53-NOTE-V1` also avoids a panic-denial claim. The
  retained notes therefore do not authorize a clean denial.

### C53-05 — Recreational-drug denial is speech truth but not current source truth

- `speech_truth` (`official_speech_truth`): `C53-DOC` at 615.184-618.615 asks about
  smoking and other drugs; `C53-PAT` at 619.392-624.670 says she occasionally smokes
  and does not take drugs.
- `selected_source_truth` (`persisted_selected_source`): `C53-SOURCE`
  `corrected-0344`–`corrected-0347` splits the answer across speakers, assigns the key
  “smoke them ... I don't take that” row to `OTHER`, and does not safely preserve the
  word “drugs”. This is a transcript-lane failure, not permission to recover the gold
  denial in the note.
- `required_note_behavior`: Omit or explicitly qualify the recreational-drug outcome
  until an accepted persisted source safely supports both its wording and Patient
  speaker. Do not reconstruct “drugs” from the clinician question or official gold.
- Retained-note result: `C53-NOTE-V1` and `C53-NOTE-V2` omit the drug denial. That
  omission is source-faithful under the current selected artifact even though the
  transcript lane fails official truth.

### C53-06 — Suicidal-ideation denial is supported

- `speech_truth` (`official_speech_truth`): `C53-PAT` at 540.700-560.821 explicitly
  denies suicidal thoughts and distinguishes not wanting to continue “like this” from
  wanting not to go on.
- `selected_source_truth` (`persisted_selected_source`): `C53-SOURCE`
  `corrected-0305`–`corrected-0315` preserves the Patient denial, extreme-thought
  wording, family support, and “I don't want to go on like this” qualifier.
- `required_note_behavior`: Preserve the supported suicidal-ideation denial. Do not
  invert it, omit it from the required screen outcome, or reinterpret the contextual
  phrase as suicidal intent; retain the qualifier when summarizing the exchange.
- Retained-note result: all retained notes carry the denial. `C53-NOTE-V2`
  `subjective-23` preserves the denial but its review reason records that the contextual
  qualifier was dropped, so the full paired-state behavior remains a pinned guard.

### C53-07 — Blood tests were recommended; arrangement state must remain honest

- `speech_truth` (`official_speech_truth`): `C53-DOC` at 743.887-766.941 says tests
  are “probably worth having”, describes a GP follow-up after tests, and instructs the
  patient to call the support line to arrange them. `C53-PAT` at 767.077-768.618 says
  “OK, I'll call them up.”
- `selected_source_truth` (`persisted_selected_source`): `C53-SOURCE`
  `corrected-0418`–`corrected-0429` safely supports a clinician recommendation and
  instruction to call. The patient-intent wording appears in `corrected-0430` but is
  assigned `DOCTOR`, so the current selected source does not safely support the Patient
  actor for that subclaim.
- `required_note_behavior`: State that blood tests were recommended and that the
  patient was asked to call the support line to arrange them. Never say the tests or
  follow-up were ordered, booked, completed, confirmed, or already arranged. The final
  note may state that the patient intends to call only when an accepted selected source
  safely preserves the Patient speaker; until then, omit or qualify that actor-specific
  intent and fail the transcript attribution lane.
- Retained-note result: `C53-NOTE-V1` says “Blood tests ordered” and “GP follow-up
  arranged”; `C53-NOTE-V2` `key-point-03` says “ordered” and `plan-08` says
  “scheduled”. These are directly reproduced action-state strengthenings. The same
  notes also contain the safer instruction that the patient should call to arrange.

## T01.1 proof record

- Every pinned outcome above identifies `speech_truth`, `selected_source_truth`, and
  `required_note_behavior` independently.
- Official tracks are used only for transcript truth.
- Patient-specific note authorization comes only from `C53-SOURCE`.
- Historical CBT and alcohol-change occurrences are explicitly
  `reported_not_reproduced`; neither is reverse-engineered from pasted UI text.
- The blood-test strengthening is reproduced from retained note artifacts rather than
  inferred from a reviewer report.

## Threshold decision sheet

Decision states:

- `FROZEN`: fixed by the approved plan and not open to post-result adjustment.
- `HUMAN-RATIFIED`: approved by the session user on 2026-07-17 before `T03.3`.
- A threshold is evaluated on every retained run. A failed, fallback, timed-out, or
  incomplete run remains in its denominator and cannot be selectively replaced.
- Transcript and note scorecards are non-interchangeable. A gain in one cannot offset a
  failure in the other.

### Independent scorecards and criticality

The transcript scorecard answers whether the words and speakers shown to the clinician match
official speech truth. The SOAP scorecard answers whether the editable draft is supported by its
exact selected persisted source. They have different evidence, denominators, and failure states.
Use both when deciding whether a clinician can safely review the draft; never average them together.

| Level | Meaning | User-facing consequence | Verdict rule |
| --- | --- | --- | --- |
| `L0-INTEGRITY` | The run is not comparable: wrong/missing source, implicit or sealed fixture, drifted hash, selective rerun, missing required denominator, or unapproved identity/cap | The clinician or reviewer is not shown a quality claim based on this run | Mark the affected arm `invalid`; preserve it and stop promotion arithmetic |
| `L1-CLINICAL` | A never-lose clinical truth, source-grounding, speaker, polarity, actor/action, named-term, or critical resource invariant failed | The draft or transcript could state the wrong clinical fact or hide an unsafe gap | One occurrence fails the arm; a favourable average cannot dilute it |
| `L2-GUARD` | A frozen non-critical threshold or maximum regression budget failed | The candidate is worse in a bounded way a user would notice | Reject the candidate unless the predeclared rule explicitly makes the metric diagnostic-only |
| `L3-DIAGNOSTIC` | A predeclared observation has no promotion threshold for this candidate | The reviewer sees context without treating it as proof of improvement | Report every value and unavailable result; it cannot offset `L0`-`L2` |

Scorecard verdicts are conjunctions, not a blended score:

- `transcript_pass = transcript_integrity_pass AND every applicable TX primary/guard gate passes`.
- `note_pass = selected_source_integrity_pass AND every applicable NT primary/guard gate passes`.
- `candidate_eligible = transcript_pass AND note_pass AND resource_pass AND campaign_integrity_pass`.
- A candidate that changes only one lane must still pass the untouched lane's identity and
  non-regression gates. `not applicable` is allowed only when this contract declares it before the
  run; `unavailable` is not a pass for a required metric.
- No WER, latency, or review-precision gain can compensate for a critical wrong-speaker turn,
  unsupported SOAP claim, source mismatch, named-term reconstruction, action-state strengthening,
  OOM, or other `L0/L1` failure.

### Campaign-level decisions

| Decision | Current value | Owner | Rollback or stop trigger |
| --- | --- | --- | --- |
| Development corpus | `FROZEN`: exactly the ten ordered M01 stems | Plan contract | Any missing, duplicate, extra, reordered, implicit, or sealed stem invalidates the affected evidence; stop and create a new unique run directory |
| ASR repetitions per baseline/candidate arm | `HUMAN-RATIFIED`: `R_ASR=3` complete repetitions | Human acceptance owner | Stop before `T03.3` or any GPU arm if all three repetitions cannot be retained; never choose repetitions after seeing output |
| Note generations per consultation and arm | `HUMAN-RATIFIED`: `G_NOTE=3` independent requests | Human acceptance owner | Stop before provider generation without a fresh campaign approval; never replace a poor generation |
| Existing bounded retry allowance per note request | `HUMAN-RATIFIED`: `RETRY_NOTE=1` additional generation, so at most two generations per request | Human acceptance owner | Stop at the approved generation cap; preserve the failed attempt and retry |
| Corpus aggregation | `HUMAN-RATIFIED`: macro-per-fixture is primary; pooled-count is secondary; critical counts and worst-run gates remain conjunctive | Human acceptance owner | Stop before baseline if every fixture cannot be reported; secondary views never overturn the primary verdict |
| Repetition aggregation | `HUMAN-RATIFIED`: median of three is primary; every raw run and the worst run remain mandatory guards | Human acceptance owner | Stop before baseline if any repetition is missing; no selective run exclusion |
| Variance reporting | `HUMAN-RATIFIED`: all three runs required; report minimum, maximum, range, and median absolute deviation (`MAD`) | Human acceptance owner | A result whose required variance statistic is unavailable cannot be promoted |
| Baseline provider branch | `FROZEN (2026-07-17)`: `provider baseline: not-run` | Human acceptance owner | M01 makes no provider request; older notes remain defect specimens only, and any M02 campaign needs fresh approval after the default-off context seam exists |
| Baseline provider request/generation cap | `FROZEN`: `0 requests / 0 generations` for M01 | Human acceptance owner | Stop before any provider request; this zero-call approval cannot be carried into M02 |
| GPU/resource cap | `HUMAN-RATIFIED`: 150 minutes per repetition, 450 minutes per lane, 900 minutes combined, peak VRAM 14,000 MiB, and zero runtime/OOM failures | Human acceptance owner | Stop the arm on the first plan kill condition or cap breach; restore the accepted baseline before another arm |
| Clinician rubric and reviewer identity/role | `HUMAN-RATIFIED`: use the detailed two-clinician rubric below | Human clinical acceptance owner | No editable-and-signable claim without completed rubric evidence |
| Legacy all-TextGrid collision-sweep contamination | `HUMAN-RATIFIED`: Decision A accepts vocabulary-only exposure as not pristine; every content/replay/mining seal remains | Human holdout owner | Stop on any new sealed access or any claim that lexicon/decoder work was vocabulary-blind to the six holdouts |
| Minimum transcription-improvement policy | `HUMAN-RATIFIED`: `MI-A` below | Human release owner | Stop M03/M04 if no isolated transcription candidate earns a frozen non-zero primary gain with every guard passing |

#### Minimum transcription-improvement choice

- `MI-A — improvement required`: at least one M03 transcription candidate must be
  promoted with a frozen, non-zero gain on its predeclared primary metric and all guard
  budgets passing. A no-candidate result blocks 0.5.0.
- `MI-B — no-candidate release permitted`: M03 may close with every candidate rejected,
  no-candidate, or not-triggered if the untouched transcript baseline and all note gates
  pass. The release must make no transcription-improvement claim.
- Decision: `MI-A`, ratified by the session user on 2026-07-17.
- Owner: Human release owner.
- Rollback/stop: if `MI-A` is selected and no candidate qualifies, stop before M04;
  if `MI-B` is selected, remove no baseline behavior and explicitly report
  “no promoted transcription candidate”.

### Transcript scorecard decisions

Every metric is reported separately for live and corrected lanes and per fixture. Clean
and overlap buckets use word timing, never whole-row boundary contact.

| ID | Metric and direction | Promotion threshold | Maximum regression budget | Repetitions / aggregation | Owner | Rollback trigger |
| --- | --- | --- | --- | --- | --- | --- |
| `TX-01` | Clean WER; lower is better | Macro `<=45%`, every fixture `<=60%`, and a primary gain `>=1.0` percentage point | At most `+0.5 pp` macro and `+2.0 pp` for any fixture | Campaign decisions above | Human transcript owner | Reject/restore candidate on ceiling or budget failure |
| `TX-02` | Overlap WER; lower is better | Macro `<=90%`, every fixture `<=100%`, and a primary gain `>=1.0 pp` | At most `+0.5 pp` macro and `+2.0 pp` for any fixture | Campaign decisions above | Human transcript owner | Reject/restore candidate on ceiling or budget failure |
| `TX-03` | Strict speaker attribution accuracy; higher is better | Macro `>=85%`, every fixture `>=75%`, and zero critical wrong-speaker turns | At most `-0.5 pp` macro and `-2.0 pp` for any fixture; zero critical loss or hidden role inversion | Campaign decisions above | Human transcript owner | Reject on any critical wrong-speaker turn or budget failure |
| `TX-04` | Critical-term recall; higher is better | Preserve every baseline-passed named span; a term-primary candidate must recover at least one whole named span | Zero loss of a previously correct critical term or scalar recall | Campaign decisions above | Human clinical/transcript owner | Reject on a lost correct term or unmet recovery threshold |
| `TX-05` | Critical-turn recall; higher is better | Preserve every baseline-passed named turn and every pinned response state | Zero loss, reassignment, inversion, or unsafe cleanup of a previously retained turn | Campaign decisions above | Human clinical/transcript owner | Reject on any dropped/reassigned pinned turn |
| `TX-06` | False clinical/general insertions; lower is better | Zero critical clinical insertion; non-critical macro `<=10%` and every fixture `<=20%` | Zero critical insertion; at most `+0.5 pp` macro and `+1.0 pp` for any fixture | Count and rate per fixture; macro primary and pooled secondary | Human clinical/transcript owner | Reject on first critical insertion or budget failure |
| `TX-07` | Word/critical-span omissions; lower is better | No new critical omission; clean macro/fixture `<=20%/35%`; overlap macro/fixture `<=85%/100%` | Zero new critical omission; at most `+0.5 pp` macro and `+2.0 pp` for any fixture | Count and rate per fixture; macro primary and pooled secondary | Human transcript owner | Reject on first new critical omission or budget failure |
| `TX-08` | Duplicate words/turns; lower is better | Duplicate-word rate `<=2%`; duplicate-turn rate `<=1%` | At most `+0.25 pp` duplicate words and zero new duplicate turns | Per fixture; macro primary and pooled secondary | Human transcript owner | Reject/restore on budget failure |
| `TX-09` | Turn coherence / assembly defect rate; higher coherence is better | Coherence `>=98%`, defect rate `<=2%`, and zero lexical borrowing/rewrite | At most `-0.5 pp` coherence; zero new named defect or lexical borrowing | Fixed-input and corpus views; macro primary | Human transcript owner | Reject on lexical/timestamp/source-unit invariant failure |
| `TX-10` | Word/row timing integrity and monotonicity | All invariants pass; `50 ms` comparison tolerance only, with zero tolerance for negative time, start-after-end, or source-ID changes | Zero unexplained timestamp/source-unit break | Per fixture; any critical failure fails the arm | Human transcript owner | Reject and restore on first unexplained break |
| `TX-11` | End-to-end and correction latency; lower is better | Live stable-row p95 `<=15 s`; correction-completion p95 `<=180 s`; repetition wall time `<=150 min` | At most `+10%` p95 while retaining every absolute ceiling | Per run; median-of-three primary and worst-run guard | Human operations owner | Reject/restore on cap or regression failure |
| `TX-12` | Peak VRAM; lower is better | Worst observed peak `<=14,000 MiB` | At most `+512 MiB` above baseline while retaining the hard cap | Peak per run; worst-run is always reported | Human GPU owner | Kill arm on OOM/illegal memory/device assertion; reject above cap |
| `TX-13` | Correction completion rate; higher is better | `100%` of scheduled attempts | Zero incomplete pinned or scheduled fixture | Completed / attempted, failures retained | Human transcript owner | Reject on new incompletion or threshold failure |
| `TX-14` | Live-fallback rate after correction; lower is better | `0%` | Zero fallback | Fallbacks / attempts, reason-stratified | Human transcript owner | Reject on disallowed fallback or budget failure |
| `TX-15` | OOM/runtime failure rate; lower is better | `0%`, including OOM, illegal memory access, device assertion, CPU fallback, or poisoned state | Zero runtime failure | Failures / attempts; no rerun replacement | Human GPU/operations owner | Immediate kill, preserve evidence, restart runtime before later arm |

Candidate-specific primary metrics are human-ratified as follows; all other `TX-*`
metrics remain guards:

- T10 mask: `TX-02` macro median overlap WER, absolute ceiling `90%`, gain `>=1.0 pp`.
- T11.L live lexicon: `TX-06` fixed-hypothesis false-rewrite count, absolute zero and
  at least one erroneous rewrite removed. It satisfies `MI-A` only when a visible
  transcript metric also has a frozen non-zero gain.
- T11 decoder bias: `TX-04` live named-term recall, with at least one whole named term
  recovered and no previously correct term lost.
- T13 correction phrase boosting: `TX-04` corrected named-term recall under the same
  one-whole-term recovery and zero-loss rule.
- Conditional T12 beam search: the remaining named corrected `TX-04` gap after T13,
  with at least one whole named term recovered and no previously correct term lost.
- T14 assembly: frozen named `TX-09` defect count reduced to zero, with at least one
  defect removed and byte-identical lexical tokens.

#### Transcript metric arithmetic

Every transcript result is first computed for one `fixture × lane × repetition`. Live and
corrected rows are never merged. Clean/overlap WER assigns every reference and hypothesis token by
its own word-centre timestamp: use a saved word timestamp when available, otherwise spread token
centres evenly across that token's source interval. A centre in a frozen half-open overlap span
`[start, end)` is overlap; every other centre is clean. Whole-row overlap contact is never used for
WER allocation.

The scorer must retain raw `S`, `I`, `D`, reference-word, hypothesis-word, event, attempt, and
sample counts. It reports each fixture/run value, macro-per-fixture and pooled-count views, every
repetition, and worst run. T01.8 selects the primary corpus and repetition aggregate before GPU
execution. Failed, timed-out, fallback, and incomplete attempts remain in the relevant attempt
denominator; an empty required denominator is `UNAVAILABLE`, never zero or a pass.

| ID | Frozen numerator | Frozen denominator | Per-run value and aggregation rule |
| --- | --- | --- | --- |
| `TX-01` | Clean-region substitutions + insertions + deletions | Clean-region official reference words | `clean_WER=(S+I+D)/N_ref`; may exceed 100%; retain counts per fixture/lane/run, then report both pooled counts and macro fixture values under the T01.8 primary choice |
| `TX-02` | Overlap-region substitutions + insertions + deletions | Overlap-region official reference words | `overlap_WER=(S+I+D)/N_ref` using the same independent token-centre rule; report raw annotation-token and declared tag-clean sensitivity separately, without switching the primary result after inspection |
| `TX-03` | Clean, reference-resolvable visible rows carrying the correct Doctor/Patient role | All clean, reference-resolvable visible rows, including `OTHER`, uncertain, and unlabeled rows as incorrect | `strict_attribution=correct/eligible`; report confident-wrong and uncertain counts separately; per fixture/lane/run before the frozen aggregate |
| `TX-04` | Named critical spans whose required words, bounded state, and speaker all match an allowed expectation | All named critical spans registered for the fixture and lane | `critical_term_recall=passed_spans/required_spans`; one span is one unit, so partial tokens, garble, wrong terms, or wrong speakers do not earn fractional credit |
| `TX-05` | Named critical turns present with the required wording/state, speaker, source identity, and timing relationship | All named critical turns registered for the fixture and lane | `critical_turn_recall=passed_turns/required_turns`; report every failed turn ID and never pool it away |
| `TX-06` | Classified false-insertion words after surplus words are separated into duplicates, wrong/garbled substitutions, and false insertions; critical clinical insertion events are counted independently | Official reference words for the matching region; critical-event denominator is its frozen named checks | `false_insertion_rate=classified_false_insertions/N_ref`; retain raw `I` separately for WER, zero critical events is a count result, and zero reference words makes the rate unavailable |
| `TX-07` | Classified official words missing from the clinician transcript, including a reference word displaced by a substitution; named critical-span omissions are counted independently | Official reference words for the matching region; critical omissions use all frozen named spans | `omission_rate=classified_missing_words/N_ref`; retain raw `D` separately for WER, and any new critical omission is `L1-CLINICAL` |
| `TX-08` | Surplus repeated hypothesis words and surplus repeated emitted turns not licensed by official speech | Hypothesis words and emitted turns respectively | Report `duplicate_word_rate=surplus_words/N_hyp` and `duplicate_turn_rate=surplus_turns/N_turns`; retain both counts and rates per fixture/lane/run |
| `TX-09` | Eligible adjacent source-unit transitions that preserve wording, chronology, speaker, and source identity; defect view counts transitions that do not | All eligible adjacent source-unit transitions | Report `turn_coherence=coherent/eligible` and `assembly_defect_rate=defects/eligible`; fewer than two eligible units is unavailable, and lexical borrowing/rewrite is independently `L1-CLINICAL` |
| `TX-10` | Timed words/rows satisfying non-negative, start-not-after-end, monotonic-order, source-unit, and frozen tolerance checks | All timed words/rows requiring those checks | Report passing share plus every failed invariant ID; one unexplained source/timing break fails the arm regardless of the share |
| `TX-11` | Sum of observed stable-row or correction-completion latency seconds for the named latency family | Completed eligible events in that family | Report count, mean, median, p50/p95 or other T01.8-named statistic per run; never mix live display latency with correction latency; missing required samples are unavailable |
| `TX-12` | Maximum observed GPU-memory bytes in each run | All resource samples captured at the frozen cadence | Report peak and sample count per run, then the worst run across the arm; a missing required resource trace is `L0-INTEGRITY`, and OOM/device failures also score in `TX-15` |
| `TX-13` | Correction attempts that finish and persist the complete attested corrected source | Every scheduled correction attempt, including timeout and failure | `correction_completion=completed/attempted`; report each reason and preserve all ten fixtures in every approved repetition |
| `TX-14` | Correction attempts whose selected note/transcript source falls back to live rows | Every scheduled correction attempt | `live_fallback_rate=fallbacks/attempted`; stratify reason and keep failed correction attempts in the denominator |
| `TX-15` | Runtime-failed attempts, with OOM, illegal-memory, device-assertion, and poisoned-state attempts counted separately | Every scheduled runtime attempt in the arm | `runtime_failure_rate=failed/attempted`; report each critical subtype and worst run; one critical GPU failure triggers the kill rule even when the aggregate is small |

For a corpus macro, first aggregate the approved repetitions within each fixture using the
T01.8-frozen repetition rule, then use `sum(fixture_metric)/10`. For a pooled rate, sum raw
numerators and denominators across the exact ten fixtures before division. For count-only and
never-lose metrics, report the sum and every event ID; zero events cannot be inferred from a missing
artifact. Resource promotion always also considers the worst run, even if another primary aggregate
is approved.

### SOAP-note scorecard decisions

Each note is scored only against the exact persisted source units selected for that
artifact. Official TextGrid truth can mark a transcript miss but cannot clear a note.

| ID | Metric and direction | Promotion threshold | Maximum regression budget | Repetitions / aggregation | Owner | Rollback trigger |
| --- | --- | --- | --- | --- | --- | --- |
| `NT-01` | Critical unsupported patient-specific claims; lower is better | `FROZEN`: zero | `FROZEN`: zero | Any occurrence fails its generation and arm | Human clinical owner | Reject prompt/context/exemplar or detector interaction on first occurrence |
| `NT-02` | All unsupported-anywhere claims; lower is better | Zero unsupported patient-specific propositions | Zero | Per note and consultation; every note must pass, with macro secondary | Human clinical owner | Reject/restore on first unsupported claim |
| `NT-03` | Mis-cited claims and unresolved citation IDs; lower is better | Zero mis-cited claims and zero unresolved IDs | Zero | Per claim and note | Human provenance owner | Reject on first mis-citation or unresolved ID |
| `NT-04` | Polarity and response-state errors; lower is better | Zero errors at every criticality | Zero, including all consult-5.3 pins | Per response state and consultation | Human clinical owner | Reject on clean denial/positive invented from ambiguous or absent source |
| `NT-05` | Actor and action-state errors; lower is better | Zero errors at every criticality | Zero, including ordered/booked/completed strengthening in pinned cases | Per action state and consultation | Human clinical owner | Reject on first actor/action error |
| `NT-06` | Certainty/hedge preservation errors; lower is better | Zero errors at every criticality | Zero, including all prior never-lose cases | Per family and consultation | Human clinical owner | Reject/defer family or arm on first certainty error |
| `NT-07` | Unsupported named-term reconstruction; lower is better | `FROZEN`: zero | `FROZEN`: zero | Any occurrence fails its generation and arm | Human clinical owner | Reject on first medication/allergy/therapy/diagnosis reconstruction |
| `NT-08` | Number, range, and duration provenance errors; lower is better | Zero errors at every criticality | Zero, including every pinned range | Per numeric proposition and note | Human clinical owner | Reject/defer on first provenance error |
| `NT-09` | Longitudinal claims without two endpoints; lower is better | `FROZEN`: zero | `FROZEN`: zero | Any occurrence fails its generation and arm | Human clinical owner | Reject on first unsupported change-over-time claim |
| `NT-10` | Critical-screen coverage with faithful response state; higher is better | `100%` per registered screen/state and zero unsafe false positives | Zero lost supported suicidality denial and zero invented panic/drug denial | Per screen/state confusion table; every case primary and macro secondary | Human clinical owner | Reject on pinned loss or any unsafe false positive |
| `NT-11` | Deterministic review precision by family; higher is better | High risk `100%`; medium/low risk `>=90%` per family | Zero high-risk false positives; at most one medium/low false positive per family per ten-note arm | Independent family numerator/denominator; never pooled | Human review-safety owner | Reject/defer failing family, never dilute into aggregate |
| `NT-12` | Deterministic review recall / false negatives by severity | High risk `100%`; medium/low risk `>=90%` per family | Zero high-risk false negatives; at most one medium/low false negative per family per arm | Independent family and risk-class table | Human review-safety owner | Reject/defer on never-lose failure |
| `NT-13` | Selected-source identity, completeness, and source-unit resolution | `FROZEN`: exact attested source, no truncation, all cited IDs resolve | `FROZEN`: zero mismatch | Per note; any failure blocks scoring as a comparable note | Human provenance owner | Block generation/adjudication; do not substitute a different lane |
| `NT-14` | Clinician edit burden / editable-and-signable outcome | Zero critical corrections/rewrite-required notes; at most two substantive edits, five wording-only edits, and ten minutes per note under the detailed rubric | Zero note classified rewrite-required and zero rubric-budget regression | Every scheduled note and all ten consultations must pass individually | Human clinical acceptance owner | Reject arm/readiness on first rubric failure |
| `NT-15` | Provider request/generation completion and failure rate | `100%` of scheduled request/generation slots complete; zero provider failures | Zero cap breach, selective replacement, or provider failure | Requests and generations ledgered exactly once | Human provider owner | Stop at cap or campaign kill condition |
| `NT-16` | T09 typed-support shadow precision/recall/abstention | Diagnostic targets: high-risk precision/recall `100%`, medium/low `>=90%`, abstention `<=20%` | Zero production effect; not a 0.5.0 product-promotion gate | Consult-5.3/consult-2.9 only; no corpus-wide aggregate claim | Human shadow-pilot owner | Stop T09 on any note/retry/review/payload/event/UI effect |

#### SOAP-note metric arithmetic

A note is comparable only after `NT-13` proves its exact attested selected source, completeness, and
source-unit IDs. A source failure stays in the campaign attempt ledger, receives `NOT-SCORABLE-SOURCE`
for dependent metrics, and blocks promotion; it is never dropped or replaced. Official TextGrid
truth may diagnose the transcript but never supplies missing note evidence.

The scoring unit is an atomic patient-specific proposition with a stable claim/proposition ID. A
structured claim is used directly; prose is split by the versioned deterministic proposition rule.
One proposition may be evaluated in several independent risk families, but it appears at most once
in a family's numerator and denominator. Every result is reported per note, consultation,
generation, and family before the T01.8-frozen corpus/repetition aggregate.

| ID | Frozen numerator | Frozen denominator | Per-note value and aggregation rule |
| --- | --- | --- | --- |
| `NT-01` | Critical patient-specific propositions not supported by the exact selected source | All critical patient-specific propositions in the note | Report `critical_unsupported_count` and `count/N_critical_claims`; one occurrence is `L1-CLINICAL`; no claims means count zero but rate unavailable |
| `NT-02` | All patient-specific propositions not entailed at the same speaker, polarity, certainty, time, and state by selected source units | All patient-specific propositions in the note | `unsupported_claim_rate=unsupported/N_claims`; report count and stable claim IDs per note, then the frozen aggregate without hiding a bad note |
| `NT-03` | Claims whose cited units do not support them, and citation IDs that do not resolve, as separate counts | Claims requiring source support, and all emitted citation IDs respectively | Report `miscitation_rate=miscited/N_supported_claims` and `unresolved_id_rate=unresolved/N_citation_ids`; a claim can be grounded but mis-cited, or unsupported anywhere, so `NT-02` and `NT-03` never substitute for each other |
| `NT-04` | Scored response propositions with wrong polarity, answered/unanswered state, conflict, ambiguity, or question-to-answer alignment | All scored response-state propositions | `response_state_error_rate=errors/N_response_states`; competing questions require timing and semantic fit, so adjacency alone cannot earn a clean positive or denial |
| `NT-05` | Actor/action propositions assigning the wrong person or strengthening recommended/intended/requested into ordered/booked/completed/confirmed | All scored actor/action propositions | `actor_action_error_rate=errors/N_actor_actions`; report actor and action-state errors separately and fail on any pinned strengthening |
| `NT-06` | Certainty-bearing propositions that lose, add, or move a required hedge, conflict, or limitation | All certainty-bearing propositions | `certainty_error_rate=errors/N_certainty_claims`; report each family and never let confident prose erase unclear source evidence |
| `NT-07` | Named medication, allergy, therapy, diagnosis, or other clinical term not present with safe speaker/state support in selected source units | All named clinical-term propositions | `unsupported_named_term_rate=errors/N_named_terms`; one reconstruction is `L1-CLINICAL`, including a correct gold term silently repaired from garble |
| `NT-08` | Numeric propositions whose value, range, unit, duration, or bound lacks matching selected-source provenance | All numeric/range/duration propositions | `numeric_provenance_error_rate=errors/N_numeric_claims`; preserve separate number, range, and duration failure IDs |
| `NT-09` | Longitudinal propositions without both an explicit baseline and a current endpoint in the selected source | All longitudinal change propositions | `unsupported_longitudinal_rate=errors/N_longitudinal_claims`; one unsupported increase/decrease/resume/stop/unchanged claim is `L1-CLINICAL` |
| `NT-10` | Frozen critical-screen expectations whose required source-conditioned behavior is satisfied | All registered critical-screen expectations for the consultation | `faithful_screen_coverage=passed/N_expected`; supported outcomes must be present, while unsafe/missing source outcomes pass only through the contract's omission or explicit qualification state |
| `NT-11` | Correct review markers in one predeclared detector family (`TP`) | All markers shown in that same family (`TP+FP`) | `review_precision=TP/(TP+FP)` per family and risk class; zero shown markers is unavailable, not 100%; never pool families or use unit-majority confidence for one risky term |
| `NT-12` | Correctly marked known defects in one family/risk class (`TP`) | All adjudicated defects in that same family/risk class (`TP+FN`) | `review_recall=TP/(TP+FN)` and `false_negatives=FN`; zero adjudicated positives is unavailable, and one new high-severity never-lose miss fails the arm |
| `NT-13` | Notes whose source attestation, lane, terminal completeness, hash, and every cited source-unit ID match | Every attempted comparable note | `source_identity_pass_rate=matching/attempted`; one mismatch is `L0-INTEGRITY` and blocks downstream quantitative comparison |
| `NT-14` | Clinician-recorded critical corrections, substantive edits, wording-only edits, edit seconds, signable notes, and rewrite-required notes | Completed clinician reviews and reviewed notes, as applicable to each rubric item | Report raw per-note counts/time plus `signable/reviewed` and `rewrite_required/reviewed`; no automated proxy can supply a clinician verdict, and missing required review is unavailable |
| `NT-15` | Completed provider requests and completed generations, with failed requests/generations counted separately | Every approved request and every approved generation slot respectively | Report completion/failure rates and exact ledger counts; a zero-request approved branch is `not-run`, not 100% completion, and no failed output may be selectively replaced |
| `NT-16` | Correct typed-support shadow findings (`TP`), detected eligible findings (`TP+FN`), and abstained eligible cases | Adjudicated shadow findings, eligible findings, and all eligible consult-5.3/consult-2.9 cases respectively | Report precision, recall, and `abstention_rate=abstained/eligible` by risk class; zero denominator is unavailable and any production/UI effect kills the pilot |

Count and rate metrics always retain both values. A corpus macro uses the mean of the ten
consultation-level values only when all ten required denominators are available; otherwise the
required aggregate is unavailable. A pooled view sums frozen numerators and denominators. The
T01.8 decision names which is primary, but every critical occurrence and every per-note result
remains visible under either choice.

### Consult-5.3 executable outcome matrix

This matrix binds the claim table to scorecard failures. Use it when evaluating the anxiety visit
so a fluent draft cannot replace the exact selected-source state. A future ASR candidate may change
`selected_source_truth` only through a new accepted persisted artifact and hash; official speech
alone never changes the note rule.

| ID | Transcript-truth requirement | Current selected-source note requirement | Critical gates |
| --- | --- | --- | --- |
| `C53-01` | Preserve talking therapy and uncertain/bounded behavioural wording; do not output CBT/cognitive wording as spoken | Allow supported talking/behavioural wording; omit or qualify a garbled subtype; never reconstruct CBT | `TX-04`, `NT-07`; any CBT expansion is `L1-CLINICAL` |
| `C53-02` | Preserve the current alcohol snapshot and speaker safety without inventing a past endpoint | Describe only safely supported current use; never claim increased, reduced, resumed, stopped, or unchanged use without two selected-source endpoints | `TX-05`, `NT-09`; any one-endpoint change claim is `L1-CLINICAL` |
| `C53-03` | Preserve “No, well yeah” and pain conditional on a fast heartbeat | Preserve the conflict and condition; never emit a clean denial or an unqualified constant-positive claim | `TX-05`, `NT-04`, `NT-06`; flattened conflict is `L1-CLINICAL` |
| `C53-04` | Preserve the two competing Doctor questions, overlap, and ambiguous Patient response; the current Patient-row panic insertion fails this turn | Omit the panic outcome or state that panic was asked about and the response is unclear; never emit a clean denial | `TX-05`, `NT-04`; overresolved wording or a clean denial is `L1-CLINICAL` |
| `C53-05` | Preserve the official Patient recreational-drug denial with safe text and speaker; the current garbled `OTHER` row fails | Omit or explicitly qualify the outcome until a newly accepted selected source supports both wording and Patient speaker; never recover it from gold or the question | `TX-04`, `TX-05`, `NT-04`, `NT-07`; current transcript lane fails while faithful SOAP abstention passes |
| `C53-06` | Preserve the explicit Patient suicidal-ideation denial and the “not like this” contextual qualifier | Include the supported denial without turning the qualifier into intent or dropping the paired context | `TX-05`, `NT-04`, `NT-10`; omission, inversion, or bare decontextualized denial is `L1-CLINICAL` |
| `C53-07` | Preserve that blood tests were recommended, the Patient intends to call to arrange them, and the Patient speaker; the current wrong-role intent row fails attribution | State recommended/asked to call to arrange; never say ordered, booked, completed, confirmed, or already arranged. With the current wrong-role source, omit or qualify Patient intent; require it only after an accepted source safely preserves the Patient speaker | `TX-03`, `TX-05`, `NT-05`; action strengthening or unsupported actor intent is `L1-CLINICAL` |

### Clinician edit-burden rubric decisions

| Rubric item | Threshold | Owner | Rollback or stop trigger |
| --- | --- | --- | --- |
| Critical clinical corrections per note | `0` | Human clinical acceptance owner | Any critical correction rejects the note/arm |
| Notes requiring full or material rewrite | `0`; rewrite-required means any critical correction, source mismatch, unsupported claim requiring replacement/deletion, material actor/polarity/action correction, section reconstruction, more than two substantive edits, or more than ten review minutes | Human clinical acceptance owner | Any rewrite-required note blocks editable-and-signable acceptance |
| Maximum substantive edits per note | `2`; a substantive edit changes a clinical proposition, polarity, actor, certainty, time, value, action state, or section placement | Human clinical acceptance owner | Reject on the per-note limit |
| Maximum wording-only edits per note | `5`; wording-only means style changes with no clinical meaning change | Human clinical acceptance owner | Record separately and reject above the per-note limit |
| Maximum review/edit time per note | `10 minutes` | Human clinical acceptance owner | Reject above the per-note limit |
| Required reviewer count, role, and blinding | Two independent registered clinicians, blinded to arm identity and each other's ratings; a project implementer cannot be the sole reviewer | Human clinical acceptance owner | No clinician acceptance claim without both required reviews |
| Disagreement resolution | A third independent registered clinician adjudicates; any unresolved critical disagreement fails | Human clinical acceptance owner | Unresolved critical disagreement blocks readiness |
| Corpus acceptance aggregation | Every scheduled note and all ten consultations pass individually; `100%` signable, with no pooled compensation | Human clinical acceptance owner | No post-result change from per-note to pooled acceptance |

### Promotion and rollback arithmetic

The arithmetic below applies to every later T04-T15 candidate. Use it before reading candidate
results so a technically fluent run cannot move its own goalposts.

#### Frozen run-set construction

- `F = 10` ordered development fixtures, exactly as frozen in M01.
- `R_ASR = 3` repetitions per ASR arm, ratified at T01.8. One complete live arm has
  `F × R_ASR` scheduled attempts and
  one complete corrected arm has another `F × R_ASR` attempts.
- `G_NOTE = 3` independent requests per consultation/note arm, and `RETRY_NOTE = 1`
  additional generation is the existing bounded retry allowance. A full ten-consultation arm has
  at most `30 requests / 60 generations`. The provider request and
  generation ceilings are derived and approved before that campaign; a retry is a generation and
  never an invisible replacement.
- M01's provider branch is fixed separately at `0 requests / 0 generations` and `not-run`.
- The expected run set is the ordered Cartesian product of the approved arm identity, all ten
  fixture IDs, required lanes, and approved repetition/generation IDs. Missing, failed, fallback,
  and timed-out members stay in this set. An extra, duplicate, reordered, implicit, selectively
  rerun, or sealed member makes the affected arm `invalid`.

#### Aggregation sequence

1. Verify immutable input/output hashes, source/lane/model/config identity, caps, and exact run-set
   membership before metric arithmetic.
2. Compute raw numerators and denominators for every scheduled member. Preserve `UNAVAILABLE`,
   failure, fallback, and not-scorable states rather than filling them with zero.
3. Apply the T01.8-frozen repetition rule within each fixture. Always report each repetition and
   worst run even when mean or median is primary.
4. Apply the T01.8-frozen corpus rule: macro uses the ten fixture values; pooled uses summed raw
   counts. Always report the secondary view without using it to overturn the primary verdict.
5. Evaluate independent `L0/L1` gates before primary gain or regression budgets. Then evaluate the
   named primary metric, every lane guard, the other scorecard, resource caps, and campaign caps.

For a lower-is-better metric, `gain = baseline_primary - candidate_primary`. For a
higher-is-better metric, `gain = candidate_primary - baseline_primary`. A positive value is an
improvement in both formulas. For each guard, `regression` is the amount by which the candidate
moves in the wrong direction from baseline; it must be no greater than that metric's frozen maximum
regression budget and must also satisfy its absolute threshold. Equality passes only when the frozen
comparison says `<=` or `>=`; no rounding is applied before comparison.

#### Candidate verdict

| Verdict | Exact condition | Required evidence |
| --- | --- | --- |
| `promote` | Campaign integrity passes; the candidate's one predeclared primary metric meets its frozen absolute threshold and required gain; every applicable TX and NT guard, never-lose gate, resource cap, and other-scorecard gate passes; human acceptance is recorded | Complete ordered ledger, raw/aggregate arithmetic, failures/fallbacks, immutable hashes, reviewer decision, and rollback proof |
| `reject` | Any validly measured primary threshold/gain, regression budget, `L1` gate, resource gate, or cross-scorecard gate fails | Preserve the complete failed arm and identify every failed gate; do not tune another variable inside the same A/B |
| `no-candidate` | Installed-runtime proof shows the proposed API/behavior does not exist or cannot express the one-variable hypothesis without an unapproved substitute | Runtime version/origin, probe command/output, zero candidate runs, and no production diff |
| `not-triggered` | The plan's named prerequisite defect or trigger is absent after the preceding accepted arm | Exact trigger metric/specimen, result, and zero candidate runs; no substitute experiment |
| `not-run` | A human-approved zero-call/zero-run branch applies, as for M01 provider generation | Approval identity, exact zero caps, and ledger showing zero requests/generations/runs |
| `invalid` | `L0-INTEGRITY` fails, including wrong corpus/source/hash/order, sealed access, selective rerun, unapproved cap/identity, missing required denominator, or nondeterministic scorer output | Preserve all artifacts, contamination/discrepancy record, and stop before interpreting candidate quality |

`promote` arithmetic is:

`candidate_eligible AND primary_absolute_pass AND primary_gain_pass AND every_guard_pass AND human_acceptance`.

The `AND` terms are never replaced by an average, weighted score, best-case repetition, favourable
consultation subset, or secondary aggregate. Under `MI-A`, at least one valid M03 candidate must
reach `promote` with a frozen non-zero primary gain. Under `MI-B`, zero promoted candidates may
release only with no transcription-improvement claim and every untouched baseline/note gate passing.

#### Kill and rollback evidence

Immediately stop the active arm on sealed-content access, implicit/all-corpus execution, provider
or GPU cap breach, provider generation overlapping NeMo/GPU work, OOM/illegal-memory/device
assertion/poisoned state, selected-source mismatch/truncation, nondeterministic scorer output,
selective replacement, or any plan-specific kill condition. Preserve partial output and mark every
unattempted scheduled member; do not restart the arm under the same evidence identity.

Every candidate packet records, before execution, its one changed variable, exact inverse
`apply_patch`, baseline identity/hash, focused restore command, and verification command. After a
rejection, restore only that variable, rerun the focused baseline identity/behavior proof, verify
hashes, and retain both the rejected and restored evidence. Rollback never erases a failed run and
never uses destructive Git reset or checkout.

### Universal rollback rules

- Owner: the Txx candidate owner executes rollback; the human acceptance owner decides
  promotion.
- Rollback trigger: any primary threshold, independent never-lose gate, resource cap,
  source-integrity invariant, clinical critical-failure rule, or campaign integrity rule
  fails.
- Rollback action: reject or disable only the candidate variable and restore the frozen
  M01 identity with a reviewed inverse `apply_patch`; each candidate packet must record
  the exact non-destructive command before the change. Destructive Git reset/checkout is
  prohibited.
- Rollback proof: rerun the candidate's focused baseline proof and identity/hash check;
  preserve both failed candidate and restored-baseline evidence.

## T01.2 proof record

- Every transcript and note metric named by M01 has a threshold, regression budget,
  repetition/aggregation field, owner, and rollback trigger.
- All numeric values, provider/run-set rules, resource caps, aggregation choices, and
  clinician rubric values were ratified by the session user on 2026-07-17.
- The release owner selected `MI-A`; a no-candidate result blocks 0.5.0 and is never
  silently called a transcription improvement.
