# Preflight Playbook

Mechanical verification before you call work done.

## MUST

- Run the project build/validation path for touched layers.
- Run lint/static analysis for touched layers.
- Run type-checking when the stack has a distinct type-check step.
- Run dependency audit checks.
- Record any skipped checks and why.

Ambient Scribe defaults:
- PHP: `composer test`, `composer analyse`, `composer cs:check`
- Python agent: `ruff check strands_agents` when Ruff is available, plus `python3 -m pytest tests/python -q`
- Stack validation: `./scripts/preflight-checks.sh`

## SHOULD

- Run the full PHPUnit suite if you only ran focused tests during iteration.
- Run formatter/fixer checks when style-sensitive files changed.
- Validate the live stack with `./scripts/health-check-localdev.sh` when localdev wiring changed.
- Run `./scripts/gpu-check.sh` when GPU, Docker, or NeMo runtime wiring changed.

## Dependency Audit

- PHP: `composer audit`
- Python: review pinned agent requirements if Python dependencies changed
- Docker/runtime: `docker compose config` or the equivalent script-backed validation

## Output Template

- Touched layers:
- MUST checks run:
- SHOULD checks run:
- Skipped checks:
- Remaining risk:
