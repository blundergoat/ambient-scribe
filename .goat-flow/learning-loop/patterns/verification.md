---
category: verification
last_reviewed: 2026-07-09
---

# Verification Patterns

## Pattern: Verify Twig without assuming `bin/console`

**Created:** 2026-07-04

**Context:** This checkout has Symfony components and PHPUnit coverage, but no `bin/console` entrypoint. A direct `php bin/console lint:twig ...` check fails before reaching Twig.

**Approach:** For template-only changes, use available project gates first: `composer test`, `composer analyse`, `composer cs:check`, and `./scripts/preflight-checks.sh`. When a concrete render is needed, instantiate Twig via `vendor/autoload.php` or run a short-lived `php -S ... public/index.php` server and assert the rendered `/scribe` output.

## Pattern: Configure generated references out of gruff cleanup

**Created:** 2026-07-04

**Context:** `config/reference.php` identifies itself as auto-generated, but gruff-php can still report docs or waste findings there during full-project scans. Project instructions prohibit hand-editing generated output, even when a plan mentions that file as a finding source.

**Approach:** Read the generated file header first. If it is generated, add a narrow `paths.ignore` entry with an inline rationale, rerun `gruff-php analyse`, and update the plan evidence to state that generated output was configured out rather than edited.

## Pattern: Keep fixture evals on lightweight domain helpers

**Created:** 2026-07-04

**Context:** A local quality script needs to score fixture behavior, such as DOCTOR/PATIENT role attribution, without starting FastAPI, importing HTTP clients, or touching NeMo/GPU setup.

**Approach:** Extract the pure decision logic into a small importable helper, keep the CLI stdlib-only, and prove it with both the direct eval command and the log analyzer command. For the role-heuristic work this means `scripts/eval-role-heuristic.py` loads `strands_agents/api/role_heuristics.py` and `python scripts/eval-role-heuristic.py > /tmp/role-heuristic-eval.out && python scripts/analyze-logs.py /tmp/role-heuristic-eval.out` renders the report.

## Pattern: Doer-verifier milestone verification with fresh sessions

**Created:** 2026-07-07

**Context:** A phased task where each milestone needs sign-off and the implementing agent
must never self-assess (scribe summary UX task, five phased milestones, 2026-07-07).

**Approach:** After a milestone goes green locally, launch a FRESH agent session whose only
job is adversarial verification: it re-reads the diffs against the acceptance criteria,
re-runs every suite itself, writes its own throwaway probes (synthetic data only) for the
claims most likely to be wrong, and records per-criterion PASS/FAIL with file:line evidence
in `scribe-summary-ux-verify-M<N>.md` plus an overall verdict line. The implementing session
then applies cheap post-verdict hardening from the verifier's non-blocking concerns and
records trade-offs in the plan tracker. Tell the verifier which working-tree changes belong
to OTHER milestones so it attributes rather than fails on them, and hand it the exact
commands (test runners, lint invocations, stack URL) so a tooling miss does not masquerade
as a milestone failure.

## Pattern: Per-claim verdict triage for bot review feedback

**Created:** 2026-07-07

**Context:** A PR accumulates automated review findings from multiple bots (PR #3: 60
findings across Copilot, Codex, CodeRabbit, Cursor) and the user asks which are worth
acting on. Bulk-trusting or bulk-dismissing both fail: on PR #3 roughly 37 were still valid,
12 were right-when-filed but already fixed, and 8 were invalid or moot.

**Approach:** Verify every claim against HEAD before any opinion. Batch claims by subsystem
and fan out read-only verification agents, one batch each, with the claims quoted verbatim;
require a verdict per claim - VALID / FIXED (cite the fixing commit) / STALE / INVALID /
PARTIAL - plus file evidence with verbatim snippets. Rules that caught real errors: never
trust the bot's line numbers (search for the code); read BOTH sides of every cross-boundary
claim (JS + Python + PHP); check `git log -S` for whether the described mechanism ever
existed; personally re-verify the highest-severity claims and any claim you will headline.
Order the final answer by severity, list disagreements with evidence, and name which
resolution markers were wrong.

**Evidence:** PR #3 triage found both P1s real (one already fixed in-branch, one live), five
false "Addressed" markers, and two stale-not-wrong cross-boundary claims - none of which
survive a trust-the-bot or dismiss-the-bot strategy.

## Pattern: Preflight and repair scripts fail closed

**Created:** 2026-07-07

**Context:** A check/repair/eval script has a path it cannot or did not verify (missing
config, absent fixtures, unprobeable provider, failed sub-command). PR #3 review surfaced
eight fail-open variants of this in one pass: placeholder secret paths warning-and-passing,
Bedrock "assumed configured", substring model-tag matches, unchecked `ollama pull` exit
codes, a `cleanup()` swallowing failure exit codes, empty fixture sets printing green
reports, a mistyped `--case` writing an empty manifest at exit 0, and setup gates
disagreeing with composer's PHP pin.

**Approach:** Every path a script did not actually verify must exit non-zero or emit an
unmistakable SKIPPED status - never a pass. Concretely: identifier presence checks compare
exact `NAME:TAG` (bare names mean `:latest`); every mutating sub-command (`pull`, `up`,
`start`) has its exit code checked; `cleanup()`/trap handlers propagate the failure code
they were given; discovery steps (`find`-based fixture lists, `--case` filters) error on
zero matches with the generation command in the message; version gates mirror the
authoritative constraint file (`composer.json`) rather than restating it. Anchors:
`scripts/check-ai-model.sh` (search: "pull failed"), `scripts/start-dev.sh` (search:
"exit_code"), `scripts/eval-fixtures.sh` (search: "no WAV fixtures found"),
`scripts/health-check-remote.sh` (search: "Failing closed"), `strands_agents/api/server.py`
(search: "unknown ROLE_AGENT_MODEL_PROVIDER").

## Pattern: Judge note-pipeline behavior against persisted rows fetched from the agent API

**Created:** 2026-07-09

**Context:** Fidelity-checker verdicts depend on row SHAPE (lengths, punctuation, merge
boundaries), and the browser transcript renders the live lane while summaries normally run on
the corrected lane. During the 2026-07-08 (UTC) manual round, rows reconstructed from pasted
UI text produced verdicts that disagreed with the logged `summary.fidelity_*` outcome; the
persisted corrected rows reproduced it exactly (three flags, all identified as false
positives).

**Approach:** Fetch the rows the pipeline actually consumed - `GET
:${AGENT_PORT:-48101}/session/{id}/corrected-transcript` (or `/history` for the live lane) -
to a file, then import the module under test directly in the project venv
(`strands_agents/.venv/bin/python`) and run the real functions (`find_fidelity_violations`,
`_negative_finding_violation`) against those rows. Confirmed twice in one session: reproduced
the missed fabricated denial (session `203d1d35`) and the three false-positive flags (session
`d97a9bde`). Keep the scripts with the milestone that owns the fix so they become regression
tests: promote them into `scripts/` or `tests/` rather than leaving them in the plan directory.
