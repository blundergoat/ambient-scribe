---
category: tooling-gates
last_reviewed: 2026-07-18
---

# Tooling and Quality-Gate Lessons

Lessons about gruff, goat-flow, composer gates, pinned containers, and probe tooling.
Split from `verification.md` on 2026-07-07 (bucket-size threshold).

## Lesson: Checkpoint byte checks must dereference snapshot symlinks

**Created:** 2026-07-18
**What happened:** M00.9's first manifest verifier hashed the Unified checkpoint correctly but used
`stat -c %s` on its Hugging Face snapshot path. The hash read the target bytes while `stat` reported the
76-byte symlink, creating a false size mismatch; the nested Docker command also consumed the verifier loop's
stdin before later rows were checked.
**Evidence:** `var/quality/0.5.0-m00-unified-asr-20260718T034719Z/verification/m00.9-manifest-verify-attempt-1.txt`.
**Prevention:** Verify container checkpoints outside any manifest-input loop, use `Path.stat()` or
`stat -Lc %s`, and preserve a matching content hash separately from link metadata.

## Lesson: Derive GPU call caps from frozen durations and runtime tail rules

**Created:** 2026-07-18
**What happened:** M00A's approval packet hand-counted 36 post-visit transcribe calls per ten-case arm. The
unchanged chunker correctly made 37: consult 1 is 559.2 seconds, so its 19.2-second remainder is above the
10-second merge threshold and becomes a fourth 180-second-series chunk. The discrepancy appeared only after
the complete TDT arm because the packet had copied the arithmetic instead of executing it over frozen WAV
durations.
**Evidence:** `var/quality/0.5.0-m00a-unified-recovery-20260718T054731Z/verification/m00a.6-tdt-audit.txt`.
**Prevention:** Before requesting a decode cap, compute each planned chunk count from the frozen WAV duration
through the production chunk-boundary function (including final-tail merging), preserve the ordered vector,
and make the approval cap equal its sum. Stop for renewed approval if runtime metadata differs.

## Lesson: A host dry run does not prove a container import path

**Created:** 2026-07-18
**What happened:** M00.4's production-shaped evaluator passed mock and host dry tests, but its first
container run supplied `PYTHONPATH=/app/strands_agents`. Compose mounts that host directory at `/app`,
so `/app/post_visit_correction.py` existed while `/app/strands_agents/post_visit_correction.py` did not.
The first TDT case failed before model load, and an uncaught metadata import also prevented the required
empty failure artifact.
**Evidence:** `var/quality/0.5.0-m00-unified-asr-20260718T034719Z/arms/tdt/campaign-failure.txt`.
**Prevention:** Before approving a container-backed corpus runner, execute one import-only smoke check in
the real mounted container and force the failure-artifact writer through the same module path. A host dry
run proves corpus routing, not container module topology.

M00A repeated the import-topology trap one level earlier after splitting an oversized evaluator: a host
`runpy.run_path("scripts/second_pass_asr.py")` preflight did not add `scripts/` to `sys.path`, so its sibling
helper import failed before source access. Evidence:
`var/quality/0.5.0-m00a-unified-recovery-20260718T054731Z/verification/m00a.2-host-dry-preflight-failure.txt`.
Load a dependency-free leaf helper directly for host selection checks, and copy every sibling module beside
the container entry script before testing the `/app` application import. A second preflight then showed that
`bash -n` does not compile embedded Python: accidental indentation inside a column-zero here-document failed
at runtime. Extract and `compile()` embedded Python in a focused test whenever a shell runner uses it as a
blocking corpus gate.

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
`.goat-flow/plans/0.4.0-slice-2/M04-crosstalk-bleed-mechanism.md` (search: "candidate rejected and removed").
**Prevention:** Drop unrelated formatter churn when rolling a candidate back. Format new files,
but do not widen a debt-heavy symbol to chase formatter debt outside scope.

M00.4 repeated the trap when whole-file Ruff formatting touched the pre-existing
`transcribe_audio_with_nemo` symbol and made its inherited length finding look newly scoped. The
behavioral tests were green, but the changed-symbol hook correctly forced removal of every unrelated
formatting hunk before the task was accepted. Evidence:
`var/quality/0.5.0-m00-unified-asr-20260718T034719Z/verification/m00.4-static.txt`.
For debt-heavy hot paths, format new files directly, inspect the tracked diff before retaining
whole-file formatter output, and require the changed-symbol hook to report zero introduced findings.

M00A.5 repeated this during the pinned-checkpoint loader repair: the approved whole-file Ruff command
reformatted the legacy module and pulled the unchanged 110-line transcription function into Gruff's changed
scope. The formatter candidate and its failure output were retained, then every unrelated formatting hunk
was removed. The focused plan-owned files and loader tests stayed formatted, while the inherited whole-file
format discrepancy remained explicit. Do not use whole-file formatter churn to make a scoped verification
packet appear green; preserve the mismatch and prove the actual changed lines separately.

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
**Evidence:** `.goat-flow/plans/0.4.0-slice-2/M05-dual-identity-duplicates.md` (search: "120.002 seconds")
and `.goat-flow/plans/0.4.0-slice-2/M06-emission-starvation.md` (search: "120.001 seconds"). M06 repeated
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

## Lesson: Long wall-clock campaigns need an active suspend-gap watchdog

**Created:** 2026-07-17
**What happened:** A 0.5.0 baseline repetition resumed after the operator laptop slept. The
health monitor exposed an 18,389-second sample gap, while the campaign helper checked its frozen
9,000-second wall-clock cap only after a normal runner return. The run was stopped after eight
fixtures, preserved as failed, and the runtime was rolled back without using the partial result.
**Evidence:** `var/quality/0.5.0-baseline-20260717T091841Z/live/repetition-02/monitors/gap-summary.txt`
and `var/quality/0.5.0-baseline-20260717T091841Z/campaign-stopped-report.md`.
**Prevention:** Before another multi-hour campaign, require an approved active wall-clock watchdog
that stops work as soon as the cap is observed, rejects an over-limit monitor gap after resume,
and records its reason independently of the fixture runner. Use an explicit host sleep inhibitor
when available; an end-only duration check cannot enforce a live kill criterion.

The fresh retry on 2026-07-18 showed that binary presence is not inhibitor availability:
`command -v systemd-inhibit` passed, but the dedicated WSL guard exited immediately with
`Failed to connect to bus: No such file or directory` before its Windows child started. Evidence:
`var/quality/0.5.0-baseline-20260717T192617Z/campaign-stopped-report.md`. Before touching a GPU
runtime, actually acquire each inhibitor, prove its live request through the owning system, and
stop if any required layer is unavailable. In WSL, test the system bus rather than inferring it
from `/usr/bin/systemd-inhibit`; keep the host-side guard and active runner timeout independent.

The direct Windows retry then showed that parse success is not runtime proof. Windows PowerShell
5.1 parsed the guard but treated `0x80000001` and `0x80000000` as negative signed values, so both
`[uint32]` casts failed at execution. Because those were non-terminating errors, the script still
printed a PID and stayed alive without constructing the approved flags. Evidence:
`var/quality/0.5.0-baseline-20260717T202405Z/campaign-stopped-report.md`. Make guard setup errors
terminating, construct high-bit flags with a PowerShell-5.1-safe unsigned conversion, and print
readiness only after the native call returns a non-zero result. A parser-only check cannot certify
an operating-system request.

The same retry found that `powercfg.exe /requests` requires an elevated prompt on this host. The
non-elevated proof command exited 1, and no elevation was attempted. Before promising a host-power
verification in an approval packet, run the exact read-only command at the intended privilege
level. If it is unavailable, stop and obtain approval for a different non-elevated proof rather
than substituting one after runtime work begins.

The corrected native guard then needed longer than an arbitrary 20-second polling window to compile
and print readiness. The valid non-zero return arrived after the poll had already reported a
timeout. Evidence: `var/quality/0.5.0-baseline-20260717T203831Z/campaign-stopped-report.md`. Keep
the guard process and its error channel authoritative: allow the measured startup window, stop
immediately if the process exits, and do not classify a still-running startup as rejection merely
because an unratified short poll elapsed.

## Lesson: Container identity evidence must whitelist environment values

**Created:** 2026-07-18
**What happened:** The T03.3 JSON-runtime preflight wrote the full container environment to an
evidence file. A filename-only scan showed that it included credential-bearing variables, so the
unsafe artifact was deleted before the root was sealed and replaced with a non-secret whitelist.
**Evidence:** `var/quality/0.5.0-baseline-20260717T204434Z/identity/identity-capture-correction.md`.
**Prevention:** Never persist `docker inspect`'s complete `.Config.Env`. Select only named runtime
identity fields needed by the gate, scan the evidence root for credential assignments before
sealing, and record the security correction without copying or printing secret values.

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

The 0.5.0 baseline helper repeated this error before GPU decode. `bash -n` printed a syntax error,
but the surrounding command continued and wrote `exit_code=0`; the independently captured
ShellCheck exit exposed the false record. Its stopped-run collector then assumed `artifacts/`
existed and raised a secondary `find` error after the real runner guard had already returned `2`.
Evidence: `var/quality/0.5.0-baseline-20260717T083838Z/preflight/helper-verification-failure.md`
and `live/repetition-01/runner-exit.txt` below the same root. Capture the tested command's literal
exit before writing success, and make post-run collectors record an absent output directory as an
explicit unavailable/zero-stage result without masking the primary failure.

The stopped 2026-07-18 retry added the opposite exit-code trap: a post-stop process-count pipeline
expected `rg` to find nothing, but `pipefail` propagated that normal exit 1 before `wc -l` could
record zero. Evidence: `var/quality/0.5.0-baseline-20260717T192617Z/preflight/post-stop-runtime.txt`
and `post-stop-runtime-corrected.txt` beside it. When zero matches is the passing state, handle it
explicitly or use a counter that exits zero; never let an expected absence abort later integrity
checks.

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
**Evidence:** `scripts/analyze-logs.py` (search: "class ProcessQualityStats"), `tests/python/test_observability.py` (search: "Dataclasses resolve postponed annotations through sys.modules during script import"), `.goat-flow/plans/0.4.0-slice-1/M08-summary-context-tail-loss.md` (search: "Phase 1 probe harness corrections").

Full pytest caught that the test helper loaded `scripts/analyze-logs.py` with `importlib.util.module_from_spec()` but did not register it in `sys.modules` before executing the module. Python dataclasses resolving postponed annotations then failed during import.

The same mistake recurred in the M08 context-measurement probe when it dynamically loaded a
module containing dataclasses. This recurrence confirms the registration step belongs in the
probe template, not only in one test helper.

**Lesson:** When test-loading a hyphenated Python script that defines dataclasses or postponed annotations, insert the module into `sys.modules` before `spec.loader.exec_module(module)`, then rerun the full test gate that found the issue.

## Lesson: Provider probes must modify the SDK-formatted request in place

**Created:** 2026-07-10
**Evidence:** `.goat-flow/plans/0.4.0-slice-1/M08-summary-context-tail-loss.md` (search: "Phase 1 probe harness corrections"), `strands_agents/agents/summary_agent.py` (search: "max_tokens=SUMMARY_AGENT_MAX_TOKENS").

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

M00A later bound the exact downloaded revision, verified its file size and SHA-256, and loaded it through
`ASRModel.restore_from`. Construction still failed on the same encoder argument before the first transcript
chunk. Exact checkpoint provenance prevents drift; it does not adapt a checkpoint config to an older runtime.
Evidence: `var/quality/0.5.0-m00a-unified-recovery-20260718T054731Z/verification/m00a.6-unified-terminal.txt`.

**Lesson:** Treat model-card recommendations as candidates, not implementation facts.
Before planning product wiring for a newer ASR checkpoint, restore the exact local artifact inside the pinned
Docker runtime and require successful construction plus a fixture score. A hash match alone is not runtime
compatibility proof; any config adaptation, dependency upgrade, or image change needs a separate scope and
approval packet.

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

The same omission recurred in the 0.5.0 T03.3 no-download preflight on 2026-07-17. Container,
health, CUDA, provider, source, and GPU identity all passed, but the sanitized runtime check omitted
`LOG_FORMAT`; live repetition 01 then stopped before its first fixture because the running value was
`console`. Evidence: `var/quality/0.5.0-baseline-20260717T083838Z/live/repetition-01/runner.log`.
Treat the exact running `LOG_FORMAT=json` check as a blocking preflight predicate, not merely an
identity field recorded after the runner rejects the service.

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

The T03.3 retry showed that a named tmux session is not sufficient by itself. The pane kept its
wrapper shell as the foreground process group while the runner used another group; with stdin still
attached to the pane, the runner stopped under terminal job control during `docker compose exec -T`
before fixture decoding began. Evidence:
`var/quality/0.5.0-baseline-20260717T204434Z/live/repetition-01-stalled-status.txt`. For unattended
tmux campaigns, detach stdin explicitly (for example, `< /dev/null`) and require an early smoke
proof that the runner is not in `T` state and has crossed its runtime preflight before starting the
multi-hour evidence clock.

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

The 0.5.0 corpus-picker change repeated the trap in a developer script: focused tests, Ruff, JSON
parsing, and compilation passed after an allowlist was added, while direct Gruff found the file had
grown from an inherited 1,004 lines to 1,029. Compacting the new allowlist and redundant vertical
space brought it to 998 lines with `Composite: A (100.00 / 100)`. Run direct Gruff before expanding
a near-limit file even when the change is metadata-only and behavior tests are already green.

## Lesson: Repeated JSON blocks need identity-scoped patch anchors

**Created:** 2026-07-17
**What happened:** A patch added consult-2.9 and consult-5.3 metadata by matching repeated
`lane_ids` / `region_labels` / empty-array blocks in the development manifest. The patch applied
cleanly but attached both records to the first two consultations instead of their named fixtures.
An immediate fixture-ID grep caught the placement before tests or evidence were recorded.
**Evidence:** `tests/fixtures/audio/development-corpus-0.5.0.json` (search:
`consult-2.9-medication-allergy`) and `tests/python/test_development_corpus.py` (search:
`test_manifest_registers_consult_29_cross_lane_evidence`).
**Prevention:** For repeated JSON/YAML objects, include the unique parent ID in every patch hunk,
then assert the expected array position and ID before inspecting child values. A valid parse proves
syntax only; it does not prove metadata landed on the user-visible record it describes.

## Lesson: A schema switch orphans every test stub beneath it - guard the provider boundary

**Created:** 2026-07-15
**What happened:** M06 replaced the summary generation path (`_generate_validated_draft` →
`_generate_validated_v2_draft`). Two tests stubbed the OLD helper by name; after the switch the
stubs patched dead code and the live path ran to `create_summary_agent()` — on a box carrying
real AWS credentials for approved replay campaigns. An estimated 3-6 UNAUTHORIZED Bedrock
generations occurred across two pytest invocations before the 8-19s suite runtimes exposed it
(one test even PASSED on real model output). Root causes: name-based stubs one level above the
boundary, and no fail-fast at the boundary itself.
**Evidence:** `.goat-flow/logs/sessions/2026-07-14-prime-m01-source-integrity.md` (search:
"UNAUTHORIZED"); the guard in `tests/python/conftest.py` (search: "_no_summary_provider_calls").
**Prevention:** (1) A session-scoped autouse conftest guard replaces
`agents.create_summary_agent` with a raiser, so any unpatched generation path fails fast and
free; tests patch their helper OVER the stub. (2) When renaming/replacing a function, grep the
TESTS for the old name before running anything — a stub that still patches the old name is a
live-fire path, not a failing test. (3) Watch suite runtime: an 8s jump in a sub-second file
means network. Bonus finding: a per-test autouse fixture perturbed event-loop timing enough to
trip a latent grace-destroy/dead-executor race in the transcription tests two files away —
prefer session-scoped single-setattr guards, and treat new order-dependent failures after a
conftest change as YOUR change until bisected (`git stash push -- <file>` isolates it fast).

The 0.5.0 baseline helper repeated the boundary failure during GPU replay. It recorded
`role-provider-events-exit=0` but enforced only the SOAP scan, while a second narrower scan omitted
the structured role event fields and falsely reported zero paid-provider activity. Live repetition
02 then started after live 01 had already issued 726 role-agent completions; the two rejected runs
retained 1,454 `role_inference.completed` events with `tool_invoked=true`. Evidence:
`var/quality/0.5.0-baseline-20260717T212133Z/campaign-stopped-report.md` (search: "Blocking failure").
When a campaign cap is zero, enforce every provider scan before the next repetition and key the
gate to the structured completion event plus `tool_invoked`, not a partial token-field vocabulary.
If normal runtime behavior cannot meet the frozen cap, stop at the human contract gate before
inventing a provider-free substitute.
