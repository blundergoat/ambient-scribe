# Preflight Check Skill

Run all quality gates for PHP and Python. Fix failures before reporting.

## RFC 2119 Constraints

- **MUST** run: type-check (`composer analyse`), lint (`composer cs:check`, `ruff check`), tests (`composer test`, `pytest`)
- **SHOULD** run: formatter check, complexity/PHPMD checks
- **MAY** skip: formatter during active debugging sessions
- **MUST NOT** report complete if any MUST item fails

## Steps

1. MUST: `composer cs:check` — fix with `composer cs:fix` if violations, re-check
2. MUST: `composer analyse` — PHPStan Level 10
3. SHOULD: `composer analyse:complexity` — max 20
4. SHOULD: `composer analyse:messdetector` — PHPMD
5. MUST: `composer test` — PHPUnit
6. MUST: `ruff check strands_agents/` — fix with `ruff check --fix` if fixable, re-check
7. MUST: `NEMO_MODEL_PROVIDER=mock PYTHONPATH=strands_agents pytest tests/python/`
8. If any MUST step fails, fix and re-run. If unfixable, report exactly what failed.
9. Report summary: pass/fail for each gate.
