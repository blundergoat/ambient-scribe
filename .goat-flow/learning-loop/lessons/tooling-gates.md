---
category: tooling-gates
last_reviewed: 2026-07-30
---

# Tooling and Quality-Gate Lessons

Lessons about gruff, goat-flow, composer gates, pinned containers, and probe tooling.
Split from `verification.md` on 2026-07-07 (bucket-size threshold).

## Lesson: `python -` heredoc scripts cannot also read piped stdin

**Created:** 2026-07-20
**What happened:** A development-corpus resolver piped validated JSON into
`"$PYTHON_BIN" - <<'PY'` and read it with `json.load(sys.stdin)`. The heredoc IS the
stdin that `python -` consumes as the program, so the piped JSON could never reach the
parser and the positive path would always have failed closed. ShellCheck SC2259 caught
the collision before any runtime test; the red-first smoke would have caught it next.
**Evidence:** `scripts/eval-fixtures.sh` (search: "corpus = json.loads(sys.argv[1])") and
`scripts/eval-corrected-fixtures.sh` (search: "corpus = json.loads(sys.argv[1])") retain
the corrected argv-passing form, matching the file's existing
`"$PYTHON_BIN" - "$TREND_FILE" <<'PY'` pattern.
**Prevention:** When an inline Python step needs both a heredoc program and input data,
pass the data as argv (`"$PYTHON_BIN" - "$data" <<'PY'` + `sys.argv[1]`) or a temp file;
never pipe into `python -`. Run shellcheck on shell edits before the first execution -
it catches this class statically.

## Lesson: Checkpoint byte checks must dereference snapshot symlinks

**Created:** 2026-07-18
**What happened:** A first manifest verifier hashed the Unified checkpoint correctly but used
`stat -c %s` on its Hugging Face snapshot path. The hash read the target bytes while `stat` reported the
76-byte symlink, creating a false size mismatch; the nested Docker command also consumed the verifier loop's
stdin before later rows were checked.
**Evidence:** Local-only gate artifacts; the findings above are the record.
**Prevention:** Verify container checkpoints outside any manifest-input loop, use `Path.stat()` or
`stat -Lc %s`, and preserve a matching content hash separately from link metadata.

## Lesson: Derive GPU call caps from frozen durations and runtime tail rules

**Created:** 2026-07-18
**Decision changed:** Compose production fan-out with every retry scope before
calling a cap conservative; a per-fixture recovery assumption is not a
structural ceiling when the retry helper is invoked per chunk.
**Trigger phase:** SCOPE
**Incident count:** 2 | **Latest occurrence:** 2026-07-25
**What happened:** An approval packet hand-counted 36 post-visit transcribe calls per ten-case arm. The
unchanged chunker correctly made 37: consult 1 is 559.2 seconds, so its 19.2-second remainder is above the
10-second merge threshold and becomes a fourth 180-second-series chunk. The discrepancy appeared only after
the complete TDT arm because the packet had copied the arithmetic instead of executing it over frozen WAV
durations.
**Evidence:** Local-only gate artifacts; the findings above are the record.
**2026-07-25 recurrence:** The flag-off recovery attestation's future-cap packet
correctly executed the production chunker and obtained 37 base calls, but its
proposed ten-call recovery allowance assumed
one extra call per fixture. The frozen source invokes the retry-capable helper
for every chunk, and device-not-ready recovery is local to each invocation;
only word-confidence fallback is request-wide. The frozen-runtime structural
vector is `8,6,8,10,6,6,6,6,10,8` = 74 calls, cumulative 146 from the current
72. Expected cumulative 110 and the one-recovery-per-fixture estimate 119
remain useful projections, but neither is a hard authorization ceiling.
**Prevention:** Before requesting a decode cap, execute the production
chunk-boundary function over frozen durations, inspect the lifetime of every
retry budget, multiply each fan-out branch by its maximum physical calls, and
preserve the ordered vectors. Label expected, policy-estimate, and structural
ceilings separately. Stop for renewed approval if runtime metadata differs.

## Lesson: Compare decoder changes at the recovery boundary

**Created:** 2026-07-24
**Decision changed:** Snapshot mutable vendor configuration immediately before and
after the mutation being judged; do not attribute changes made by the preceding
model call to a later recovery helper.
**Trigger phase:** VERIFY
**What happened:** The application-path recovery observer compared decoder configuration
immediately before call 1 with configuration immediately before call 2. NeMo
automatically persisted `compute_timestamps=true` when the first call requested
timestamps, so the observer incorrectly counted that call-owned side effect as a
recovery change and rejected a run whose application, timing, text, call-cap, and
CUDA gates all passed.
**Evidence:** Local-only gate artifacts. The raw rejection carried
`decoder_changed_paths_between_calls`, while an adjudication record beside it proved NeMo
declared the timestamp
side effect twice and the exact recovery helper has one attribute mutation plus an
applied-config drift guard.
**Prevention:** For a post-failure decoder mutation, capture configuration after the
failed call but before the helper, then again after the helper. If an already-spent
write-once probe used broader snapshots, preserve its failed result and adjudicate
only a vendor-declared side effect with source-hash, AST, focused-test, and runtime-log
evidence; never rewrite the raw result or spend another GPU call to make the gate green.

## Lesson: A host dry run does not prove a container import path

**Created:** 2026-07-18
**What happened:** A production-shaped evaluator passed mock and host dry tests, but its first
container run supplied `PYTHONPATH=/app/strands_agents`. Compose mounts that host directory at `/app`,
so `/app/post_visit_correction.py` existed while `/app/strands_agents/post_visit_correction.py` did not.
The first TDT case failed before model load, and an uncaught metadata import also prevented the required
empty failure artifact.
**Evidence:** Local-only gate artifacts; the findings above are the record.
**Prevention:** Before approving a container-backed corpus runner, execute one import-only smoke check in
the real mounted container and force the failure-artifact writer through the same module path. A host dry
run proves corpus routing, not container module topology.

A later recovery run repeated the import-topology trap one level earlier after splitting an oversized evaluator: a host
`runpy.run_path("scripts/second_pass_asr.py")` preflight did not add `scripts/` to `sys.path`, so its sibling
helper import failed before source access. Evidence:
a local-only artifact.
Load a dependency-free leaf helper directly for host selection checks, and copy every sibling module beside
the container entry script before testing the `/app` application import. A second preflight then showed that
`bash -n` does not compile embedded Python: accidental indentation inside a column-zero here-document failed
at runtime. Extract and `compile()` embedded Python in a focused test whenever a shell runner uses it as a
blocking corpus gate.

## Lesson: Placement proofs must not default missing telemetry to zero

**Created:** 2026-07-13
**What happened:** A first CPU-only Ollama verifier converted an absent `/api/ps`
`size_vram` field to zero. A changed or incomplete API response could therefore certify that
the user's local model left the GPU to NeMo without reporting any placement measurement.
**Evidence:** `scripts/install-ollama.sh` (search: "missing VRAM measurement").
**Prevention:** Treat absent device-placement fields as invalid evidence. Accept CPU-only model
placement only when the exact loaded model explicitly reports zero VRAM; fail closed otherwise.

## Lesson: Whole-file patch replacement can drop executable mode

**Created:** 2026-07-13
**What happened:** A milestone replaced `scripts/check-ai-model.sh` through a delete/add patch. Bash syntax
and ShellCheck were clean, but `stat` showed the operator command had changed from executable to
mode `0644`, so direct use would have failed before any model check ran.
**Evidence:** `scripts/check-ai-model.sh` (search: "Diagnose the off-GPU models") and
a local-only artifact (focused static evidence).
**Prevention:** After replacing any executable script as a whole, compare `stat -c '%a %n'` with
HEAD, restore the original executable mode before behavioral tests, and include mode in staging
review. Prefer in-place hunks when a full replacement is unnecessary.

**Follow-up (2026-07-26, 0.5.2 insertion classification):** The hash-verified WSL sleep-guard helper had mode `0644`, so its first direct launch failed before readiness. No replay, fixture mutation, or GPU inference occurred; the preserved failure is a local-only artifact (search: `Permission denied`). Preflight executable mode as part of the process contract. When interpreter execution is permitted, invoke a non-executable shell helper explicitly with Bash rather than treating a direct-exec failure as a replay attempt.

## Lesson: Formatter-only churn can inherit class-wide Gruff debt

**Created:** 2026-07-12
**What happened:** A milestone retained two Ruff-only line wraps in `TranscriptionSession` after removing
the rejected behavior. The changed-symbol hook then surfaced the class's pre-existing size debt
as changed scope even though no behavior remained.
**Evidence:** `.goat-flow/hooks/gruff-code-quality.sh` (search: "symbol-aware scope").
**Prevention:** Drop unrelated formatter churn when rolling a candidate back. Format new files,
but do not widen a debt-heavy symbol to chase formatter debt outside scope.

A later run repeated the trap when whole-file Ruff formatting touched the pre-existing
`transcribe_audio_with_nemo` symbol and made its inherited length finding look newly scoped. The
behavioral tests were green, but the changed-symbol hook correctly forced removal of every unrelated
formatting hunk before the task was accepted. Evidence:
a local-only artifact.
For debt-heavy hot paths, format new files directly, inspect the tracked diff before retaining
whole-file formatter output, and require the changed-symbol hook to report zero introduced findings.

A later recovery step repeated this during the pinned-checkpoint loader repair: the approved whole-file Ruff command
reformatted the legacy module and pulled the unchanged 110-line transcription function into Gruff's changed
scope. The formatter candidate and its failure output were retained, then every unrelated formatting hunk
was removed. The focused plan-owned files and loader tests stayed formatted, while the inherited whole-file
format discrepancy remained explicit. Do not use whole-file formatter churn to make a scoped verification
packet appear green; preserve the mismatch and prove the actual changed lines separately.

## Lesson: Evidence CLI documentation is part of its gate

**Created:** 2026-07-12
**What happened:** Tests and Ruff passed before Gruff found eight missing docs. A later milestone repeated the
sequence: its duplicate scorer was behaviorally green before Gruff found undocumented public
evidence fields whose null/empty meanings operators need to interpret the report.
**Evidence:** `scripts/fold-attribution-score.py` (search: "def build_report") and
`scripts/duplicate-transcript-score.py` (search: "class VisibleHistoryRow") now document empty,
UNKNOWN, legacy-ID, and absent-arrival meanings.
**Prevention:** Run direct Gruff immediately after the first behavioral green. Do not tick an
evidence CLI until every public field and empty/null outcome has operator-facing documentation.

## Lesson: Diagnostic state is unavailable until the retained artifact proves it

**Created:** 2026-07-12
**What happened:** A milestone stored `late_slot_births` and `revision_resyncs` in session continuity,
but its logger and extractor omitted both fields. The 1x replay therefore could not recover them.
The same replay and accepted baseline each had 10 phantom merges but zero duplicate pairs, so the
merge count was also rejected as causal evidence.
**Evidence:** `strands_agents/nemo_session.py` (search: "continuity_log_fields") and
a local-only artifact.
**Prevention:** Before relying on instrumentation, prove the exact retained artifact contains the
field. Record a missing field as unavailable, never zero, and reject fixes unsupported by the
user-visible acceptance metric.

## Lesson: A correction timeout cannot be interpreted as byte drift

**Created:** 2026-07-12
**What happened:** A flag-OFF c02 stream finalized with zero quality errors, but the correction
HTTP call timed out after 120 seconds before producing the artifact needed for canonical hashing.
Health and CUDA stayed live, so neither a matching nor mismatching byte result existed.
**Evidence:** `scripts/eval-corrected-fixtures.sh` (search: "request_correction"). A later run
repeated the timeout boundary with max-hold behavior explicitly off, confirming it is not a byte
or release-policy result.
**Prevention:** Separate correction availability from byte comparison: retain the timeout timeline,
health/CUDA proof, and partial artifacts, then stop before retrying or labeling the result drift.

## Lesson: Keep evidence grounding separate from wording classification

**Created:** 2026-07-12
**What happened:** A connector refinement passed 39/39 behavior contracts, but combining
TextGrid ownership gates with the word matcher raised Gruff Halstead volume to 407 and lowered its
maintainability index to 63.2. Separating reference grounding from decoder-word classification
restored both scorer and runtime Gruff to A/100 without changing the focused result.
**Evidence:** `scripts/duplicate-transcript-score.py` (search: "grounded_decoder_repeat_match") and
`scripts/duplicate-transcript-score.py` (search: "decoder_variant_word_match").
**Prevention:** Keep oracle/reference ownership checks separate from process-local wording rules;
run their shared end-to-end contract after refactoring so evidence semantics cannot drift.

## Lesson: Exact decoder wording cannot close duplicate-identity acceptance

**Created:** 2026-07-12
**What happened:** A milestone closed no-fix after its scorer found zero exact pairs in a 1x replay. The
user's next browser check exposed a minimal two-row case with different IDs, one TextGrid Patient,
0.29 seconds overlap, and no genuine overlap; small decoder wording differences kept the scorer at
zero.
**Evidence:** `scripts/duplicate-transcript-score.py` (search: "rows_by_normalized_text") and
a local-only artifact.
**Prevention:** Treat exact wording as a high-precision signal, not the acceptance boundary. Before
closing duplicate identity, include controlled decoder-variant matching grounded by overlapping
time, different IDs, and one TextGrid speaker, then require a manual browser check.

## Lesson: Run every safety predicate against the retained target

**Created:** 2026-07-12
**What happened:** A planning probe incorrectly reported equal vocabulary for the manual
decoder-variant pair because a nested jq expression shadowed the word being compared. The first
real scorer run then returned zero: role, overlap, timing, a shared four-word phrase, and 0.875
word-LCS all passed, but the two eight-word rows had different vocabulary. The same faulty probe
also labeled two canonical candidates safe; the tested classifier later proved each contained
distinct non-connector words and the accepted 20 scored zero, not the planned two.
**Evidence:** Local-only gate artifacts, including a `diagnose-canonical-grounded-pairs`
record beside the run.
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
**Decision changed:** Preflight compound runtime commands as reviewable units, resolve
dynamic process targets in a separate read-only step, and use an existing reviewed
helper or a small checked script when the command crosses the hook's complexity boundary.
**Incident count:** 3 | **Latest occurrence:** 2026-07-28
**What happened:** During post-visit timestamp work, an inline `docker compose exec`
probe with a long embedded Python heredoc was blocked by the PreToolUse hook as too complex
to review safely. During the later d2c09 full-replay closeout, the hook likewise rejected
a null-command truncation, a combined dynamic-target guard stop, and a compound pair of
adjudication pipelines. Each rejection occurred outside the replay and spent no GPU or
provider call; splitting artifact creation from target resolution and then using the
exact resolved pane/PID let cleanup complete without weakening the hook.

A later milestone repeated the orchestration trap through a yielding tool boundary. A CPU
proof launched the trapped fixture wrapper through a nested execution cell;
the cell returned without retaining the inner terminal session, so the child
was terminated outside its normal `EXIT` path after mono preparation. Eight
resting fixture hashes were temporarily mismatched. The approved restore helper
recovered all ten stereo hashes, and the same proof passed when launched in a
persistent terminal session and polled by its session ID.

**Prevention:** For GPU/runtime probes that need more than a few shell steps, add a small
fixture-only script with `apply_patch`, compile it, and then run the script through the
container. This gives the hook and reviewer a stable artifact instead of a dense terminal
blob. For one-shot operator cleanup, first resolve and record the exact target read-only,
then issue one exact action in a separate command; create a new artifact with `touch` or
the producing tool instead of a null-command redirect that resembles destructive truncation.
For mutating commands whose cleanup depends on shell traps, use an execution
surface that preserves a process/session handle across yields and poll that
exact handle to terminal completion. After an interrupted or ambiguous yield,
check for live children, verify the cleanup invariant directly, and run the
approved recovery before another attempt.

## Lesson: Long wall-clock campaigns need an active suspend-gap watchdog

**Created:** 2026-07-17
**What happened:** A 0.5.0 baseline repetition resumed after the operator laptop slept. The
health monitor exposed an 18,389-second sample gap, while the campaign helper checked its frozen
9,000-second wall-clock cap only after a normal runner return. The run was stopped after eight
fixtures, preserved as failed, and the runtime was rolled back without using the partial result.
**Evidence:** Local-only gate artifacts; the findings above are the record.
**Prevention:** Before another multi-hour campaign, require an approved active wall-clock watchdog
that stops work as soon as the cap is observed, rejects an over-limit monitor gap after resume,
and records its reason independently of the fixture runner. Use an explicit host sleep inhibitor
when available; an end-only duration check cannot enforce a live kill criterion.

The fresh retry on 2026-07-18 showed that binary presence is not inhibitor availability:
`command -v systemd-inhibit` passed, but the dedicated WSL guard exited immediately with
`Failed to connect to bus: No such file or directory` before its Windows child started. Evidence:
a local-only artifact. Before touching a GPU
runtime, actually acquire each inhibitor, prove its live request through the owning system, and
stop if any required layer is unavailable. In WSL, test the system bus rather than inferring it
from `/usr/bin/systemd-inhibit`; keep the host-side guard and active runner timeout independent.

The direct Windows retry then showed that parse success is not runtime proof. Windows PowerShell
5.1 parsed the guard but treated `0x80000001` and `0x80000000` as negative signed values, so both
`[uint32]` casts failed at execution. Because those were non-terminating errors, the script still
printed a PID and stayed alive without constructing the approved flags. Evidence:
a local-only artifact. Make guard setup errors
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
timeout. Evidence was local-only. Keep
the guard process and its error channel authoritative: allow the measured startup window, stop
immediately if the process exits, and do not classify a still-running startup as rejection merely
because an unratified short poll elapsed.

The 2026-07-24 corpus-off arm watchdog then used tmux sessions named
rediar-m05-corpus-off and rediar-m05-corpus-off-watchdog. Tmux target lookup treated the
runner name as a prefix, so after the runner exited, has-session -t rediar-m05-corpus-off
matched the watchdog itself and kept its monitor loop alive. Evidence:
a local-only artifact (search:
TMUX-SESSIONS-BEFORE-STOP) and driver-exit.txt (search: CORPUS_ARM_DRIVER_EXIT=0). Use
exact target syntax (`-t =<session-name>`) for session-scoped liveness and stop commands. Pane-scoped
commands such as `send-keys` require an exact pane target (`-t '=<session-name>:<window>.<pane>'` or
a captured pane ID); a bare exact session is rejected as "can't find pane." Also make the owned
runner-exit sentinel authoritative instead of inferring completion from a prefix-matched session.
Evidence was local-only
and `sleep-guard-stop.txt` beside it.

The same diagnostic guard showed that a tmux server's inherited `PATH` may not contain a Windows
bridge executable available to an interactive shell. Its first launch exited before readiness with
`powershell.exe: not found`; the retry used the resolved absolute executable path and acquired the
guard. Evidence: `sleep-guard-attempt1.log` and `sleep-guard-readiness.txt` in that diagnostic root.
Resolve cross-host executables before launching a detached session and treat readiness, not process
creation, as the acquisition gate.

## Lesson: Container identity evidence must whitelist environment values

**Created:** 2026-07-18
**Decision changed:** Container state probes must select only named, reviewed
non-secret fields; never include the complete environment in terminal output or
durable evidence.
**Trigger phase:** VERIFY
**Incident count:** 2
**Latest occurrence:** 2026-07-30
**What happened:** The T03.3 JSON-runtime preflight wrote the full container environment to an
evidence file. A filename-only scan showed that it included credential-bearing variables, so the
unsafe artifact was deleted before the root was sealed and replaced with a non-secret whitelist.

On 2026-07-30, a final-state probe formatted the complete container
environment alongside its status fields. Credential-bearing values were
therefore emitted into the tool transcript even though no repository artifact
was created. The values were not copied or reused, and later checks selected
only the required non-secret state.
**Evidence:** Local-only gate artifacts; the findings above are the record.
The recurrence is recorded here rather than in a durable raw-output artifact so
the unsafe values are not propagated.
**Prevention:** Never print or persist `docker inspect`'s complete `.Config.Env`.
Select only named, reviewed non-secret runtime identity fields needed by the
gate, scan the evidence root for credential assignments before sealing, and
record the security correction without copying or printing secret values.

## Lesson: Gruff context docs need marker vocabulary (2026-07-04)

In one review, comments clearly described user-visible error handling but still failed `docs.missing-error-behavior-doc` because gruff's context-doc rule looks for marker words such as `reports`, `fallback`, `recover`, or `throws`.

**Lesson:** When fixing gruff context-doc findings, read the rule vocabulary and include the expected marker word in plain English instead of relying on semantically similar prose.

## Lesson: Gruff env placeholders are exact-token sensitive (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `.env.example` (search: "APP_SECRET=changeme"), `.env.example` (search: "MERCURE_JWT_SECRET=changemechangemechangemechangeme"), `strands_agents/.venv/lib/python3.12/site-packages/gruffpy/rule/sensitive_data/hardcoded_env_value_rule.py` (search: "_PLACEHOLDER_VALUES").

In another review, intuitive placeholders such as `<generate-app-secret>` and `allowlists.secretPreviews` still left `sensitive-data.hardcoded-env-value` findings. Reading the rule showed the env-secret detector only skips exact placeholder tokens or low-entropy values, and the high-entropy detector in gruff-py 0.4.1 does not consult `secretPreviews`.

**Lesson:** When gruff-py flags `.env.example`, read the sensitive-data rule before tuning config; prefer exact known placeholders such as `changeme` or low-entropy repeated local placeholders, then rerun JSON output to prove the warning disappeared.

## Lesson: Gruff PHP display filters do not lower the exit threshold (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `composer.json` (search: "vendor/bin/gruff-php analyse").

In a later review, a complexity-only gruff-php command was initially considered for the retired cyclomatic alias. The command still failed while unrelated advisory findings existed, because gruff-php's report selection changes displayed findings but the configured `minimumSeverity.analyse` threshold still controls the process exit.

**Lesson:** When replacing a legacy quality gate with gruff-php, use the full `gruff-php analyse` command unless the tool documentation explicitly says a selector changes exit semantics; prove the alias with a failing and then clean run before marking the plan checkbox complete.

## Lesson: Generic Gruff baseline filenames collide across tool lanes (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `gruff-php-baseline.json` (search: "gruff.baseline.v2"), `composer.json` (search: "--baseline=gruff-php-baseline.json"), `scripts/preflight-checks.sh` (search: "--baseline=gruff-php-baseline.json").

In a PHP review, a PHP accepted-debt baseline was written as `gruff-baseline.json`. `gruff-py` also auto-loads that filename, rejected the PHP `gruff.baseline.v2` schema, and exited with a baseline error even though the Python findings were clean.

**Lesson:** When multiple Gruff implementations share a repo, do not put implementation-specific accepted debt in the generic `gruff-baseline.json`; use tool-specific baseline filenames and pass them explicitly in that tool's Composer/script gate.

## Lesson: Validation wrappers must check exit codes before success text (2026-07-05)

**Created:** 2026-07-05
**Evidence:** `scripts/preflight-checks.sh` (search: "composer_validate_exit"), `scripts/validate-composer.sh` (search: "ALLOWED_STRANDS_CLIENT_WARNING").

In the same PHP review, direct `composer validate --strict` exited 1 for the intentionally commit-pinned Strands PHP client, but preflight had been grepping for "is valid" and therefore reported the step green despite the non-zero exit. The fix moved the exception into a wrapper that checks the exit code and allows only the reviewed warning.

**Lesson:** Validation steps should key off the command exit code first; if one warning is intentionally accepted, encode that exact exception in a wrapper instead of grepping for success text in mixed success/warning output.

The 0.5.0 baseline helper repeated this error before GPU decode. `bash -n` printed a syntax error,
but the surrounding command continued and wrote `exit_code=0`; the independently captured
ShellCheck exit exposed the false record. Its stopped-run collector then assumed `artifacts/`
existed and raised a secondary `find` error after the real runner guard had already returned `2`.
Evidence was local-only
and `live/repetition-01/runner-exit.txt` below the same root. Capture the tested command's literal
exit before writing success, and make post-run collectors record an absent output directory as an
explicit unavailable/zero-stage result without masking the primary failure.

The stopped 2026-07-18 retry added the opposite exit-code trap: a post-stop process-count pipeline
expected `rg` to find nothing, but `pipefail` propagated that normal exit 1 before `wc -l` could
record zero. Evidence was local-only
and `post-stop-runtime-corrected.txt` beside it. When zero matches is the passing state, handle it
explicitly or use a counter that exits zero; never let an expected absence abort later integrity
checks.

The 1.14.0 setup verification added a smaller form of the same mistake: one invocation passed five
paths to the unary `test -x` predicate and received exit 2 (`too many arguments`). The executable
files were valid; the verifier was not. Run unary shell predicates once per target (or use a
bounded checker that reports each path), and treat exit 2 as a broken check rather than a failed
artifact.

The 2026-07-24 d2c09 chunk probe restoration verifier assumed `restore-exit.txt` contained only a
scalar and prepended another `restore_exit=` label. The runner had correctly written key/value event
lines, so the first verifier synthesized `restore_exit=restore_exit=0...` and failed after the
runtime was already healthy. Evidence:
a local-only artifact
(the corrected artifact) and the session log. When consuming an owned status artifact, extract one
anchored key/value record, assert it occurs exactly once, and then compare its value; never relabel
unparsed multi-line content as a scalar.

The same probe's first static call-cap verifier searched the whole source for the word `retry`; its
docstring truthfully said that the probe bypassed the retry wrapper, so the verifier rejected the
very property it was meant to prove. The corrected
`probe-static-verification.txt` parses the AST, counts the one direct `transcribe` call, and rejects
only calls to the known retry-capable production wrappers. Use syntax/call-target checks for
executable-path claims; comments and docstrings are evidence about intent, not reachable calls.

Its first credential scan then embedded a character class containing both quote types in a
single-quoted shell argument. The shell split the pattern and `rg` treated the remainder as a file,
exiting 2 rather than reporting a scan result. The corrected scan passes only the evidence path as
argv to an inline Python scanner and assigns distinct exits to clean, matched, and scanner-error
states. For non-trivial patterns containing shell metacharacters or both quote types, keep the
pattern inside the parser's source or a reviewed pattern file instead of composing it through shell
quoting; always distinguish a tool error from the expected no-match state.

The follow-up confidence-fallback probe first ran a negated privacy `rg` inside a composite command
without fail-fast mode. The pattern matched the harmless identifier `recognized_text_fingerprint`,
yet the command continued and printed `PRIVACY-STATIC: PASS`. A second substring assertion then
confused the safe plural key `exception_messages_emitted` with the forbidden exact key
`exception_message`. The corrected verifier enables fail-fast mode, parses the Python AST, and
checks exact call targets and exact retained keys; longer parsers live in reviewed helper files
instead of hook-sensitive inline commands. Evidence:
a local-only artifact,
`verify_inputs.py`, and `probe-output-verification.json`. Success labels must be downstream of the
assertion they describe, and privacy checks over source should match syntax or exact identifiers,
not substrings that also occur in safe metadata names. Its first handoff readback also assumed the
restored flag would occur exactly once, although the handoff intentionally states it in both the
operator-state and manual-test sections. Use an exact count only when uniqueness is part of the
document contract; otherwise assert presence or first classify the intended repetitions. The
corrected count is preserved in `handoff-verification.txt` in the same evidence root.

The one-call timestamp-alignment follow-up then used `rg -c` to record an expected zero second-call
count. Ripgrep exited 1 and printed no `0`, so the next key/value record joined the unfinished
`second_call_markers=` line even though the raw log correctly contained no second call. Branch on
the no-match exit and emit an explicit zero before composing a ledger, then parse every generated
record and assert its field count. Evidence:
a local-only artifact.

Its first final background scan then matched the verifier shell because that same command line
contained the literal evidence-directory target in a variable assignment. Bracketing the first
letter of a process pattern prevents a pattern argument from matching itself, but it does not
protect against another literal copy elsewhere in the polling command. Run each `pgrep` absence
check in an isolated command that contains only the bracketed executable/script pattern, record
exit 1 explicitly, and combine the already-captured results afterward. Evidence:
`background-process-scan-exits.txt` and `final-worktree-runtime-verification.txt` in the same root.

The 2026-07-25 bounded corpus-on midpoint verifier repeated both count and
process-topology assumptions. Its first version used rg -c for an expected zero and received no
printed zero; after that was normalized, it still treated one managed runner as
one matching OS process even though the approved timeout wrapper and its bash
child correctly produced two matches. The arm itself, its tmux owner, and its
evidence remained valid. Evidence:
a local-only artifact
and verify-midpoint.sh in the same sealed root. Normalize no-match exits before
integer comparisons, and distinguish a logical runner owner from its expected
wrapper-child process graph. Prove uniqueness at the managed session or pane
layer, then assert the exact allowed child topology instead of assuming one
grep row per logical runner.

The 2026-07-25 naming-migration audit then filtered `git diff` output through `^[+-][^+-]` to keep
only payload lines. That character class silently excludes every diff line whose own content starts
with `-` or `+`, which is every Markdown list item, so a generated-index check printed no matches and
read as clean while the superseded labels were still present. A direct read of the regenerated index
files showed the real state, and the second audit used the source lines rather than the filtered
diff. When auditing a diff of list-based prose, strip exactly one leading `+`/`-` column instead of
rejecting a line by its second character, and confirm any empty audit result against a direct read of
at least one line the audit should have matched.

## Lesson: SDK observability plans must match installed vendor contracts (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `composer.lock` (search: "blundergoat/strands-php-client"),
`vendor/blundergoat/strands-php-client/src/Http/RequestMiddleware.php` (search:
"interface RequestMiddleware"), `vendor/blundergoat/strands-php-client/src/Http/ResponseObserver.php`
(search: "interface ResponseObserver"), and `src/Observability/StrandsClientTelemetry.php`
(search: "implements RequestMiddleware, ResponseObserver").

In one plan, the plan described PHP client 1.5.x `ResponseObserver` hooks, but the then-installed
1.4.0 client exposed only `RequestMiddleware` with `beforeRequest()` and `afterResponse()`.
Implementing from the plan text alone would have created a class against an absent interface.
The current locked development client now exposes both interfaces, which reinforces that this
contract must be re-read from the installed dependency for each change.

**Lesson:** Before implementing SDK instrumentation from a plan, verify the installed vendor interface and lockfile version, then update the plan with the actual contract used.

## Lesson: Dataclass script imports need sys.modules registration (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `scripts/analyze-logs.py` (search: "class ProcessQualityStats"), `tests/python/test_observability.py` (search: "Dataclasses resolve postponed annotations through sys.modules during script import").

Full pytest caught that the test helper loaded `scripts/analyze-logs.py` with `importlib.util.module_from_spec()` but did not register it in `sys.modules` before executing the module. Python dataclasses resolving postponed annotations then failed during import.

The same mistake recurred in a context-measurement probe when it dynamically loaded a
module containing dataclasses. This recurrence confirms the registration step belongs in the
probe template, not only in one test helper.

**Lesson:** When test-loading a hyphenated Python script that defines dataclasses or postponed annotations, insert the module into `sys.modules` before `spec.loader.exec_module(module)`, then rerun the full test gate that found the issue.

## Lesson: Provider probes must modify the SDK-formatted request in place

**Created:** 2026-07-10
**Evidence:** `strands_agents/agents/summary_agent.py` (search: "max_tokens=SUMMARY_AGENT_MAX_TOKENS").

A first Bedrock token-cap probe passed a new `inferenceConfig` beside the request generated
by the installed Strands formatter. That formatter had already embedded `inferenceConfig`, so the
duplicate wrapper made the probe invalid before it could measure the real request.

**Lesson:** Build provider probes through the installed runtime formatter, inspect the resulting
request shape, and override an existing nested limit such as `inferenceConfig.maxTokens` in place.
Do not assume the wrapper leaves provider options for the caller to add again.

## Lesson: GPU image import gates need a local/pending split (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `docker/nemo/Dockerfile` (search: "nvcr.io/nvidia/nemo:26.02").

In another plan, the plan required a `docker run nvcr.io/nvidia/nemo:26.02 ...` import check before dependency edits. The image pull is multi-GB and was stopped locally, so claiming the import passed would have been false while blocking all GPU-free package and contract checks would have stalled useful work.

**Lesson:** For heavyweight GPU images, split verification into local manifest/package-manager gates and an explicit GPU-host build/import gate. Mark the GPU gate human-pending unless the container actually builds and imports in the current session.

## Lesson: Name GPU-pending fallbacks as fallbacks (2026-07-04)

**Created:** 2026-07-04
**Evidence:** `strands_agents/medical_lexicon.py` (search: "normaliser for common clinical terms").

In a later plan, the plan targeted NeMo decode-time phrase boosting, but the exact multitalker transducer API still needs a pinned-container GPU spike. Shipping the useful local path as "phrase boosting" without naming that distinction would make reviewers think the GPU decoding contract had been proven.

**Lesson:** When a plan's ideal implementation depends on unrun GPU/provider proof, label the shipped local path as a fallback in code, docs, changelog, and plan status. Leave the original proof checkbox unchecked and add a focused unit test for the fallback's real contract.

## Lesson: New ASR checkpoints need pinned-container proof (2026-07-06)

**Created:** 2026-07-06
**Evidence:** `scripts/eval-second-pass.sh` (search: "run_container_asr"), `scripts/second_pass_asr.py` (search: "ASRModel.from_pretrained").

During the second-pass accuracy spike, `nvidia/parakeet-unified-en-0.6b` looked like
the right newer English ASR candidate from the model card, but failed inside the pinned
`nvcr.io/nvidia/nemo:26.02` + `nemo_toolkit[asr]==2.7.3` container with
`ConformerEncoder.__init__() got an unexpected keyword argument 'att_chunk_context_size'`.
The fallback `nvidia/parakeet-tdt-0.6b-v3` loaded and produced scoreable artifacts in the
same container, proving the issue was model/runtime API compatibility rather than the
fixture runner.

A recovery run later bound the exact downloaded revision, verified its file size and SHA-256, and loaded it through
`ASRModel.restore_from`. Construction still failed on the same encoder argument before the first transcript
chunk. Exact checkpoint provenance prevents drift; it does not adapt a checkpoint config to an older runtime.
Evidence was local-only.

**Lesson:** Treat model-card recommendations as candidates, not implementation facts.
Before planning product wiring for a newer ASR checkpoint, restore the exact local artifact inside the pinned
Docker runtime and require successful construction plus a fixture score. A hash match alone is not runtime
compatibility proof; any config adaptation, dependency upgrade, or image change needs a separate scope and
approval packet.

## Lesson: Gruff PHP file intent must precede the strict-types declaration

**Created:** 2026-07-11
**What happened:** A milestone added a documented test-only PHP router, but its file docblock followed
`declare(strict_types=1)`. PHP lint, PHP-CS-Fixer, and PHPStan all passed while preflight failed
`docs.missing-file-phpdoc`; gruff recognizes the intent only at the file header.
**Evidence:** `scripts/e2e-router.php` (search: "Route isolated browser tests") now places the
3-8-line intent block immediately after `<?php`, before the strict-types declaration.
**Prevention:** For every new PHP file, put the file-intent docblock directly after `<?php` and
before `declare(strict_types=1)`, then run the direct gruff-php gate as well as PHP lint/style.

## Lesson: Structured fixture gates require the running JSON log mode

**Created:** 2026-07-12
**What happened:** A flag-OFF trio stopped in preflight because the restored normal agent
used `LOG_FORMAT=console` while `EVAL_REQUIRE_STRUCTURED_LOGS=1` required JSON. No fixture ran.
**Evidence:** Local-only gate artifacts; the findings above are the record.
**Prevention:** Before a structured fixture run, verify `LOG_FORMAT=json` in the running agent as
well as feature flags and CUDA; restore normal log mode after the evidence run.

The same omission recurred in the 0.5.0 T03.3 no-download preflight on 2026-07-17. Container,
health, CUDA, provider, source, and GPU identity all passed, but the sanitized runtime check omitted
`LOG_FORMAT`; live repetition 01 then stopped before its first fixture because the running value was
`console`. Evidence was local-only.
Treat the exact running `LOG_FORMAT=json` check as a blocking preflight predicate, not merely an
identity field recorded after the runner rejects the service.

The same session found that a bare `nohup ... &` child launched by a one-shot command runner was
reaped immediately with empty logs. For long evals, verify both the saved child PID and the first
fixture line; when the runner reaps descendants, keep a managed parent session waiting on the
`nohup` child while a separate monitor records health and progress. A later milestone showed that an
explicitly interrupted assistant turn can also end that managed process group mid-fixture without
an application sentinel. Put multi-hour eval, log capture, and health polling in independent OS
sessions, then verify their session IDs differ from the launching command before relying on them.
A live-only gate reconfirmed the check: three saved background PIDs vanished with an empty
run log and no fixture directory, while named `tmux` sessions immediately produced the first
fixture UUID and completed. Treat an empty log after the launch check as no run, never as a gate
failure or pass.

The T03.3 retry showed that a named tmux session is not sufficient by itself. The pane kept its
wrapper shell as the foreground process group while the runner used another group; with stdin still
attached to the pane, the runner stopped under terminal job control during `docker compose exec -T`
before fixture decoding began. Evidence:
a local-only artifact. For unattended
tmux campaigns, detach stdin explicitly (for example, `< /dev/null`) and require an early smoke
proof that the runner is not in `T` state and has crossed its runtime preflight before starting the
multi-hour evidence clock.

## Lesson: Run changed-symbol Gruff before a hot-path module crosses its size gate

**Created:** 2026-07-13
**What happened:** A Phase 1 release branch passed its CPU behavior tests and Ruff, but direct
changed-symbol Gruff found the edited release method at 124 lines / 64.6 maintainability and then
found the module at 1,031 lines after the method was extracted. Neither issue was a runtime test
failure, and the module had been below the file threshold before the diagnostic additions.
**Evidence:** The extracted policy lives in `strands_agents/nemo_streaming_engine.py`
(search: "class _ReleasePolicy"); the gate output itself was local-only.
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

A later milestone repeated the same boundary when catalog transaction helpers were first placed inline: the generator
started at 998 lines and reached 1,218 before the explicit line-count check. Moving manifest-owned naming,
allowlist, merge, and publication rules into `scripts/demo_audio_catalog.py` (search:
`def merge_picker_catalog`) returned `scripts/generate-demo-consultation-audio.py` to 983 lines. For a file
already within one small edit of its threshold, allocate the focused sibling before adding the first helper,
then run the direct line-count/Gruff probe after each substantive slice.

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
**What happened:** A milestone replaced the summary generation path (`_generate_validated_draft` →
`_generate_validated_v2_draft`). Two tests stubbed the OLD helper by name; after the switch the
stubs patched dead code and the live path ran to `create_summary_agent()` — on a box carrying
real AWS credentials for approved replay campaigns. An estimated 3-6 UNAUTHORIZED Bedrock
generations occurred across two pytest invocations before the 8-19s suite runtimes exposed it
(one test even PASSED on real model output). Root causes: name-based stubs one level above the
boundary, and no fail-fast at the boundary itself.
**Evidence:** `tests/python/conftest.py` (search: "_no_summary_provider_calls").
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
a local-only artifact (search: "Blocking failure").
When a campaign cap is zero, enforce every provider scan before the next repetition and key the
gate to the structured completion event plus `tool_invoked`, not a partial token-field vocabulary.
If normal runtime behavior cannot meet the frozen cap, stop at the human contract gate before
inventing a provider-free substitute.
