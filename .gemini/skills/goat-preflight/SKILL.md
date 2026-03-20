# goat-preflight SKILL

RFC 2119 constraints. Mechanical verification before work is done.

## MUST
- Run build/lint for touched layers (PHP: `composer analyse`, Python: `ruff check`).
- Run focused tests (`vendor/bin/phpunit` or `pytest`).
- Record every skipped check and why.

## SHOULD
- Run formatter (`composer cs:fix`).
- Run full test suite (`composer test`).
- Run `./scripts/preflight-checks.sh`.

## MAY
- Skip formatter during active debugging.

## Verification Gate
1. Build/lint passes?
2. Tests pass?
3. Skipped checks documented?
