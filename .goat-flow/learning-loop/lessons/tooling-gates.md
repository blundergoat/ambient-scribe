---
category: tooling-gates
last_reviewed: 2026-07-13
---

# Tooling and Quality-Gate Lessons

Lessons about gruff, goat-flow, composer gates, pinned containers, and probe tooling.
Split from `verification.md` on 2026-07-07 (bucket-size threshold).

## Lesson: Placement proofs must not default missing telemetry to zero

**Created:** 2026-07-13
**What happened:** M00's first CPU-only Ollama verifier converted an absent `/api/ps`
`size_vram` field to zero. A changed or incomplete API response could therefore certify that
the user's local model left the GPU to NeMo without reporting any placement measurement.
**Evidence:** `scripts/install-ollama.sh` (search: "missing VRAM measurement").
**Prevention:** Treat absent device-placement fields as invalid evidence. Accept CPU-only model
placement only when the exact loaded model explicitly reports zero VRAM; fail closed otherwise.

## Lesson: Whole-file patch replacement can drop executable mode

**Created:** 2026-07-13
**What happened:** M00 replaced `scripts/check-ai-model.sh` through a delete/add patch. Bash syntax
and ShellCheck were clean, but `stat` showed the operator command had changed from executable to
mode `0644`, so direct use would have failed before any model check ran.
**Evidence:** `scripts/check-ai-model.sh` (search: "Diagnose the off-GPU models") and
`var/quality/m00-default-models-infra-20260712T201108Z/` (focused static evidence).
**Prevention:** After replacing any executable script as a whole, compare `stat -c '%a %n'` with
HEAD, restore the original executable mode before behavioral tests, and include mode in staging
review. Prefer in-place hunks when a full replacement is unnecessary.

## Lesson: Formatter-only churn can inherit class-wide Gruff debt

**Created:** 2026-07-12
**What happened:** M04 retained two Ruff-only line wraps in `TranscriptionSession` after removing
the rejected behavior. The changed-symbol hook then surfaced the class's pre-existing size debt
as changed scope even though no behavior remained.
**Evidence:** `.goat-flow/hooks/gruff-code-quality.sh` (search: "symbol-aware scope") and
`.goat-flow/plans/0.4.1/M04-crosstalk-bleed-mechanism.md` (search: "candidate rejected and removed").
**Prevention:** Drop unrelated formatter churn when rolling a candidate back. Format new files,
but do not widen a debt-heavy symbol to chase formatter debt outside scope.

## Lesson: Evidence CLI documentation is part of its gate

**Created:** 2026-07-12
**What happened:** M04 tests/Ruff passed before Gruff found eight missing docs. M05 repeated the
sequence: its duplicate scorer was behaviorally green before Gruff found undocumented public
evidence fields whose null/empty meanings operators need to interpret the report.
**Evidence:** `scripts/fold-attribution-score.py` (search: "def build_report") and
`scripts/duplicate-transcript-score.py` (search: "class VisibleHistoryRow") now document empty,
UNKNOWN, legacy-ID, and absent-arrival meanings.
**Prevention:** Run direct Gruff immediately after the first behavioral green. Do not tick an
evidence CLI until every public field and empty/null outcome has operator-facing documentation.

## Lesson: Diagnostic state is unavailable until the retained artifact proves it

**Created:** 2026-07-12
**What happened:** M05 stored `late_slot_births` and `revision_resyncs` in session continuity,
but its logger and extractor omitted both fields. The 1x replay therefore could not recover them.
The same replay and accepted baseline each had 10 phantom merges but zero duplicate pairs, so the
merge count was also rejected as causal evidence.
**Evidence:** `strands_agents/nemo_session.py` (search: "continuity_log_fields") and
`var/quality/m05-dual-identity-duplicates-20260712T060345Z/phase0-mechanism-verdict.md`.
**Prevention:** Before relying on instrumentation, prove the exact retained artifact contains the
field. Record a missing field as unavailable, never zero, and reject fixes unsupported by the
user-visible acceptance metric.

## Lesson: A correction timeout cannot be interpreted as byte drift

**Created:** 2026-07-12
**What happened:** M05's flag-OFF c02 stream finalized with zero quality errors, but the correction
HTTP call timed out after 120 seconds before producing the artifact needed for canonical hashing.
Health and CUDA stayed live, so neither a matching nor mismatching byte result existed.
**Evidence:** `.goat-flow/plans/0.4.1/M05-dual-identity-duplicates.md` (search: "120.002 seconds")
and `.goat-flow/plans/0.4.1/M06-emission-starvation.md` (search: "120.001 seconds"). M06 repeated
the boundary with max-hold behavior explicitly off, confirming it is not a byte or release-policy
result.
**Prevention:** Separate correction availability from byte comparison: retain the timeout timeline,
health/CUDA proof, and partial artifacts, then stop before retrying or labeling the result drift.

## Lesson: Keep evidence grounding separate from wording classification

**Created:** 2026-07-12
**What happened:** M05's connector refinement passed 39/39 behavior contracts, but combining
TextGrid ownership gates with the word matcher raised Gruff Halstead volume to 407 and lowered its
maintainability index to 63.2. Separating reference grounding from decoder-word classification
restored both scorer and runtime Gruff to A/100 without changing the focused result.
**Evidence:** `scripts/duplicate-transcript-score.py` (search: "grounded_decoder_repeat_match") and
`scripts/duplicate-transcript-score.py` (search: "decoder_variant_word_match").
**Prevention:** Keep oracle/reference ownership checks separate from process-local wording rules;
run their shared end-to-end contract after refactoring so evidence semantics cannot drift.

## Lesson: Exact decoder wording cannot close duplicate-identity acceptance

**Created:** 2026-07-12
**What happened:** M05 closed no-fix after its scorer found zero exact pairs in a 1x replay. The
user's next browser check exposed a minimal two-row case with different IDs, one TextGrid Patient,
0.29 seconds overlap, and no genuine overlap; small decoder wording differences kept the scorer at
zero.
**Evidence:** `scripts/duplicate-transcript-score.py` (search: "rows_by_normalized_text") and
`var/quality/m05-dual-identity-duplicates-20260712T060345Z/manual-3c092379-minimal-exact-report.json`.
**Prevention:** Treat exact wording as a high-precision signal, not the acceptance boundary. Before
closing duplicate identity, include controlled decoder-variant matching grounded by overlapping
time, different IDs, and one TextGrid speaker, then require a manual browser check.

## Lesson: Run every safety predicate against the retained target

**Created:** 2026-07-12
**What happened:** M05's planning probe incorrectly reported equal vocabulary for the manual
decoder-variant pair because a nested jq expression shadowed the word being compared. The first
real scorer run then returned zero: role, overlap, timing, a shared four-word phrase, and 0.875
word-LCS all passed, but the two eight-word rows had different vocabulary. The same faulty probe
also labeled two canonical candidates safe; the tested classifier later proved each contained
distinct non-connector words and the accepted 20 scored zero, not the planned two.
**Evidence:**
`var/quality/m05-dual-identity-duplicates-20260712T060345Z/d3-implementation-20260712T085346Z/diagnose-grounded-threshold-connector.json`
and `diagnose-canonical-grounded-pairs.json` beside it.
**Prevention:** Before implementing a safety filter, run its complete predicate against the retained
target and save one PHI-safe boolean/count record per condition. Treat ad-hoc jq joins as planning
signals only, especially when nested `.` scopes can change which operand `index()` receives.

## Lesson: Fixture CLIs should load helpers without mutating `sys.path`

**Created:** 2026-07-06
**What happened:** The first corrected source-chip scorer CLI inserted `strands_agents`
into `sys.path` so it could import the helper during local script execution. Gruff flagged
`design.runtime-sys-path-mutation` because that path can shadow later imports for the whole
process.
**Evidence:** `scripts/corrected-source-chip-score.py` (search: "def load_score_module") now
loads the helper by file path, and `strands_agents/corrected_source_chip_score.py`
(search: "class SourceChipArtifactScore") keeps the pure scorer importable for tests.
**Prevention:** For fixture-only CLIs that wrap repo-local helpers, prefer
`importlib.util.spec_from_file_location` or package-level imports that are already available
from the caller environment. Do not add repo directories to `sys.path` inside the script.

## Lesson: Long runtime probes should become small scripts before execution

**Created:** 2026-07-06
**What happened:** During M03 post-visit timestamp work, an inline `docker compose exec`
probe with a long embedded Python heredoc was blocked by the PreToolUse hook as too complex
to review safely.
**Prevention:** For GPU/runtime probes that need more than a few shell steps, add a small
fixture-only script with `apply_patch`, compile it, and then run the script through the
container. This gives the hook and reviewer a stable artifact instead of a dense terminal blob.

## Lesson: Semantic anchors should prefer function names over escaped route strings (2026-07-04)

`./scripts/context-validate.sh` rejected a generated footgun citation that used an escaped decorator string for the WebSocket route in `strands_agents/api/server.py`. The route existed, but the checker did not accept the escaped quote form.

**Lesson:** For learning-loop citations, prefer stable function-name anchors such as `(search: "async def transcribe_stream")` over quoted decorator or route literals that require escaping.

## Lesson: Goat-flow installed skill edits can drift from package templates (2026-07-04)

Updating installed goat-plan skill copies under `.agents/skills/`, `.claude/skills/`, and `.github/skills/` removed stale project path text, but `goat-flow audit` still compares those files to the package template in `node_modules/@blundergoat/goat-flow/workflow/skills/goat-plan/SKILL.md`.

**Lesson:** When changing installed goat-flow skill text for project policy, run `goat-flow audit` and either accept/report template drift or make the change upstream in the package before claiming the audit is clean.

## Lesson: Gruff context docs need marker vocabulary (2026-07-04)

During M05, comments clearly described user-visible error handling but still failed `docs.missing-error-behavior-doc` because gruff's context-doc rule looks for marker words such as `reports`, `fallback`, `recover`, or `throws`.

**Lesson:** When fixing gruff context-doc findings, read the rule vocabulary and include the expected marker word in plain English instead of relying on semantically similar prose.

## Lesson: Gruff env placeholders are exact-token sensitive (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `.env.example` (search: "APP_SECRET=changeme"), `.env.example` (search: "MERCURE_JWT_SECRET=changemechangemechangemechangeme"), `strands_agents/.venv/lib/python3.12/site-packages/gruffpy/rule/sensitive_data/hardcoded_env_value_rule.py` (search: "_PLACEHOLDER_VALUES").

During M06, intuitive placeholders such as `<generate-app-secret>` and `allowlists.secretPreviews` still left `sensitive-data.hardcoded-env-value` findings. Reading the rule showed the env-secret detector only skips exact placeholder tokens or low-entropy values, and the high-entropy detector in gruff-py 0.4.1 does not consult `secretPreviews`.

**Lesson:** When gruff-py flags `.env.example`, read the sensitive-data rule before tuning config; prefer exact known placeholders such as `changeme` or low-entropy repeated local placeholders, then rerun JSON output to prove the warning disappeared.

## Lesson: Gruff PHP display filters do not lower the exit threshold (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `composer.json` (search: "vendor/bin/gruff-php analyse"), `.goat-flow/plans/0.3.0/M07-fix-gruff-php-findings.md` (search: "rewired").

During M07, a complexity-only gruff-php command was initially considered for the retired cyclomatic alias. The command still failed while unrelated advisory findings existed, because gruff-php's report selection changes displayed findings but the configured `minimumSeverity.analyse` threshold still controls the process exit.

**Lesson:** When replacing a legacy quality gate with gruff-php, use the full `gruff-php analyse` command unless the tool documentation explicitly says a selector changes exit semantics; prove the alias with a failing and then clean run before marking the plan checkbox complete.

## Lesson: Generic Gruff baseline filenames collide across tool lanes (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `gruff-php-baseline.json` (search: "gruff.baseline.v2"), `composer.json` (search: "--baseline=gruff-php-baseline.json"), `scripts/preflight-checks.sh` (search: "--baseline=gruff-php-baseline.json").

During M14 review, a PHP accepted-debt baseline was written as `gruff-baseline.json`. `gruff-py` also auto-loads that filename, rejected the PHP `gruff.baseline.v2` schema, and exited with a baseline error even though the Python findings were clean.

**Lesson:** When multiple Gruff implementations share a repo, do not put implementation-specific accepted debt in the generic `gruff-baseline.json`; use tool-specific baseline filenames and pass them explicitly in that tool's Composer/script gate.

## Lesson: Validation wrappers must check exit codes before success text (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/preflight-checks.sh` (search: "composer_validate_exit"), `scripts/validate-composer.sh` (search: "ALLOWED_STRANDS_CLIENT_WARNING").

During M14 review, direct `composer validate --strict` exited 1 for the intentionally commit-pinned Strands PHP client, but preflight had been grepping for "is valid" and therefore reported the step green despite the non-zero exit. The fix moved the exception into a wrapper that checks the exit code and allows only the reviewed warning.

**Lesson:** Validation steps should key off the command exit code first; if one warning is intentionally accepted, encode that exact exception in a wrapper instead of grepping for success text in mixed success/warning output.

## Lesson: SDK observability plans must match installed vendor contracts (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `.goat-flow/plans/0.3.0/M08-observability-and-eval.md` (search: "ResponseObserver"), `vendor/blundergoat/strands-php-client/src/Http/RequestMiddleware.php` (search: "interface RequestMiddleware"), `src/Observability/StrandsClientTelemetry.php` (search: "implements RequestMiddleware").

During M08, the plan described PHP client 1.5.x `ResponseObserver` hooks, but the installed 1.4.0 client only exposes `RequestMiddleware` with `beforeRequest()` and `afterResponse()`. Implementing from the plan text alone would have created a class against an absent interface.

**Lesson:** Before implementing SDK instrumentation from a plan, verify the installed vendor interface and lockfile version, then update the plan with the actual contract used.

## Lesson: Goat-flow setup-green can still hide cross-agent drift (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `.claude/skills/goat/SKILL.md` (search: "goat-flow-skill-version"), `.github/skills/goat/SKILL.md` (search: "goat-flow-skill-version"), `.github/hooks/hooks.json` (search: "\"postToolUse\"").

During a Codex goat-flow 1.13.1 repair, `goat-flow setup . --agent codex` reported `0 audit checks failed` after codex config, hooks, and skills were synced. The exact requested `goat-flow audit . --harness --agent codex` still exited non-zero because the audit drift section also compared installed `.claude/skills/`, `.github/skills/`, and `.github/hooks/hooks.json` copies against package templates.

**Lesson:** When the exact audit command is the acceptance gate, trust the audit exit code and its top-level `drift.status`, not only the setup prompt's numbered checks. If drift remains, sync every named installed agent copy before declaring the audit clean.

## Lesson: Dataclass script imports need sys.modules registration (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `scripts/analyze-logs.py` (search: "class ProcessQualityStats"), `tests/python/test_observability.py` (search: "Dataclasses resolve postponed annotations through sys.modules during script import"), `.goat-flow/plans/0.4.0/M08-summary-context-tail-loss.md` (search: "Phase 1 probe harness corrections").

Full pytest caught that the test helper loaded `scripts/analyze-logs.py` with `importlib.util.module_from_spec()` but did not register it in `sys.modules` before executing the module. Python dataclasses resolving postponed annotations then failed during import.

The same mistake recurred in the M08 context-measurement probe when it dynamically loaded a
module containing dataclasses. This recurrence confirms the registration step belongs in the
probe template, not only in one test helper.

**Lesson:** When test-loading a hyphenated Python script that defines dataclasses or postponed annotations, insert the module into `sys.modules` before `spec.loader.exec_module(module)`, then rerun the full test gate that found the issue.

## Lesson: Provider probes must modify the SDK-formatted request in place

**Created:** 2026-07-10
**Evidence:** `.goat-flow/plans/0.4.0/M08-summary-context-tail-loss.md` (search: "Phase 1 probe harness corrections"), `strands_agents/agents/summary_agent.py` (search: "max_tokens=SUMMARY_AGENT_MAX_TOKENS").

The first M08 Bedrock token-cap probe passed a new `inferenceConfig` beside the request generated
by the installed Strands formatter. That formatter had already embedded `inferenceConfig`, so the
duplicate wrapper made the probe invalid before it could measure the real request.

**Lesson:** Build provider probes through the installed runtime formatter, inspect the resulting
request shape, and override an existing nested limit such as `inferenceConfig.maxTokens` in place.
Do not assume the wrapper leaves provider options for the caller to add again.

## Lesson: GPU image import gates need a local/pending split (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `.goat-flow/plans/0.3.0/M09-dependency-upgrades.md` (search: "Phase-0 import one-liner"), `docker/nemo/Dockerfile` (search: "nvcr.io/nvidia/nemo:26.02").

During M09, the plan required a `docker run nvcr.io/nvidia/nemo:26.02 ...` import check before dependency edits. The image pull is multi-GB and was stopped locally, so claiming the import passed would have been false while blocking all GPU-free package and contract checks would have stalled useful work.

**Lesson:** For heavyweight GPU images, split verification into local manifest/package-manager gates and an explicit GPU-host build/import gate. Mark the GPU gate human-pending unless the container actually builds and imports in the current session.

## Lesson: Name GPU-pending fallbacks as fallbacks (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `.goat-flow/plans/0.3.0/M11-medical-phrase-boosting.md` (search: "decode-time GPU spike pending"), `strands_agents/medical_lexicon.py` (search: "normaliser for common clinical terms").

During M11, the plan targeted NeMo decode-time phrase boosting, but the exact multitalker transducer API still needs a pinned-container GPU spike. Shipping the useful local path as "phrase boosting" without naming that distinction would make reviewers think the GPU decoding contract had been proven.

**Lesson:** When a plan's ideal implementation depends on unrun GPU/provider proof, label the shipped local path as a fallback in code, docs, changelog, and plan status. Leave the original proof checkbox unchecked and add a focused unit test for the fallback's real contract.

## Lesson: New ASR checkpoints need pinned-container proof (2026-07-06)

**Created:** 2026-07-06
**Evidence:** `scripts/eval-second-pass.sh` (search: "run_container_asr"), `scripts/second_pass_asr.py` (search: "ASRModel.from_pretrained"), `.goat-flow/plans/second-pass-accuracy/M01-prove-second-pass-eval.md` (search: "att_chunk_context_size").

During the second-pass accuracy spike, `nvidia/parakeet-unified-en-0.6b` looked like
the right newer English ASR candidate from the model card, but failed inside the pinned
`nvcr.io/nvidia/nemo:26.02` + `nemo_toolkit[asr]==2.7.3` container with
`ConformerEncoder.__init__() got an unexpected keyword argument 'att_chunk_context_size'`.
The fallback `nvidia/parakeet-tdt-0.6b-v3` loaded and produced scoreable artifacts in the
same container, proving the issue was model/runtime API compatibility rather than the
fixture runner.

**Lesson:** Treat model-card recommendations as candidates, not implementation facts.
Before planning product wiring for a newer ASR checkpoint, run it inside the exact pinned
Docker runtime and record a fixture score or a precise compatibility failure.

## Lesson: Gruff PHP file intent must precede the strict-types declaration

**Created:** 2026-07-11
**What happened:** M01 added a documented test-only PHP router, but its file docblock followed
`declare(strict_types=1)`. PHP lint, PHP-CS-Fixer, and PHPStan all passed while preflight failed
`docs.missing-file-phpdoc`; gruff recognizes the intent only at the file header.
**Evidence:** `scripts/e2e-router.php` (search: "Route isolated browser tests") now places the
3-8-line intent block immediately after `<?php`, before the strict-types declaration.
**Prevention:** For every new PHP file, put the file-intent docblock directly after `<?php` and
before `declare(strict_types=1)`, then run the direct gruff-php gate as well as PHP lint/style.

## Lesson: Structured fixture gates require the running JSON log mode

**Created:** 2026-07-12
**What happened:** An M04 flag-OFF trio stopped in preflight because the restored normal agent
used `LOG_FORMAT=console` while `EVAL_REQUIRE_STRUCTURED_LOGS=1` required JSON. No fixture ran.
**Evidence:** `var/quality/m04-crosstalk-bleed-20260711T193941Z/phase1c-flag-off-trio-eval.log`.
**Prevention:** Before a structured fixture run, verify `LOG_FORMAT=json` in the running agent as
well as feature flags and CUDA; restore normal log mode after the evidence run.

The same session found that a bare `nohup ... &` child launched by a one-shot command runner was
reaped immediately with empty logs. For long evals, verify both the saved child PID and the first
fixture line; when the runner reaps descendants, keep a managed parent session waiting on the
`nohup` child while a separate monitor records health and progress. M06 later showed that an
explicitly interrupted assistant turn can also end that managed process group mid-fixture without
an application sentinel. Put multi-hour eval, log capture, and health polling in independent OS
sessions, then verify their session IDs differ from the launching command before relying on them.
The M06 live-only gate reconfirmed the check: three saved background PIDs vanished with an empty
run log and no fixture directory, while named `tmux` sessions immediately produced the first
fixture UUID and completed. Treat an empty log after the launch check as no run, never as a gate
failure or pass.

## Lesson: Run changed-symbol Gruff before a hot-path module crosses its size gate

**Created:** 2026-07-13
**What happened:** M06's Phase 1 release branch passed its CPU behavior tests and Ruff, but direct
changed-symbol Gruff found the edited release method at 124 lines / 64.6 maintainability and then
found the module at 1,031 lines after the method was extracted. Neither issue was a runtime test
failure, and the module had been below the file threshold before the diagnostic additions.
**Evidence:** `var/quality/m06-emission-starvation-20260712T211015Z/phase1-gruff-direct.log` and
`strands_agents/nemo_streaming_engine.py` (search: "class _ReleasePolicy").
**Prevention:** On a near-threshold hot-path module, run changed-symbol Gruff after each substantive
diagnostic or policy slice. Extract a named policy before the release method crosses 100 lines,
then tighten comments and contracts while checking the file remains below its configured limit.

The cadence refinement first stored the fixed browser tick as another per-session engine
attribute. Focused tests passed, but Gruff exposed the extra state on a class already carrying 21
attributes. Replacing it with a module policy constant kept the file at 999 lines and a
`--diff HEAD` hook scan reported zero new findings. Prefer a constant for a fixed application
contract; use Gruff's new-only diff to distinguish introduced findings from inherited symbol debt.
