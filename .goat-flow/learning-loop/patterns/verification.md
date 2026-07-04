---
category: verification
last_reviewed: 2026-07-04
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

**Approach:** Extract the pure decision logic into a small importable helper, keep the CLI stdlib-only, and prove it with both the direct eval command and the log analyzer command. For M08 this means `scripts/eval-role-heuristic.py` loads `strands_agents/api/role_heuristics.py` and `python scripts/eval-role-heuristic.py > /tmp/m08-eval.out && python scripts/analyze-logs.py /tmp/m08-eval.out` renders the report.
