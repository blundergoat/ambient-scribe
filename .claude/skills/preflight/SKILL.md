# Preflight Check Skill

Run all quality gates for PHP and Python. Fix any failures before reporting results.

## Steps

1. Run PHP code style check: `composer cs:check`. If violations found, run `composer cs:fix` then re-check.
2. Run PHPStan static analysis: `composer analyse` (Level 10).
3. Run cyclomatic complexity check: `composer analyse:complexity` (max 20).
4. Run PHPMD mess detector: `composer analyse:messdetector`.
5. Run PHPUnit tests: `composer test`.
6. Run Python lint: `ruff check strands_agents/` (or via venv). If fixable violations found, run `ruff check --fix strands_agents/` then re-check.
7. Run Python tests: `NEMO_MODEL_PROVIDER=mock PYTHONPATH=strands_agents pytest tests/python/`.
8. If any step fails, fix the issue and re-run that step.
9. Report a summary of all results - pass/fail for each gate.

If ALL gates pass, report success. If any gate still fails after attempted fixes, report exactly what failed and why.
