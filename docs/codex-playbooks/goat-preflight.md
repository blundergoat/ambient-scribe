# GOAT Preflight

Mechanical verification before you call work done.

## MUST
- Run build or validation steps for every touched layer.
- Run lint or static analysis for every touched layer.
- Run focused tests first, then broader suites when the change stabilises.
- Record every skipped check and why.

Ambient Scribe defaults:
- PHP: `composer test`, `composer analyse`, `composer cs:check`
- Python: `ruff check strands_agents`, `python3 -m pytest tests/python -q`
- Stack/runtime: `./scripts/preflight-checks.sh`, plus `docker compose config` or `./scripts/health-check-localdev.sh` when local wiring changed

## SHOULD
- Re-run the full PHPUnit suite if iteration used only a narrow subset.
- Run `./scripts/gpu-check.sh` after GPU, Docker, or NeMo runtime edits.
- Run `composer audit` if PHP dependencies changed.
- Run `./scripts/context-validate.sh` after workflow or instruction-file edits.

## MAY
- Capture timings, warnings, or skipped checks in task notes when follow-up work is likely.

## Dependency Audit
- PHP: `composer audit`
- Python: review pinned requirements and rerun Ruff/pytest when dependencies change
- Docker/runtime: `docker compose config` or the equivalent repo helper

## Output Template
- Touched layers:
- MUST checks run:
- SHOULD checks run:
- Skipped checks:
- Remaining risk:
