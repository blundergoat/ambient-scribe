# Preflight Check Skill

Mechanical build verification with RFC 2119 constraints. Run all quality gates, fix failures, report structured results.

## MUST (cannot skip)

1. `composer cs:check` — PHP code style (PSR-12). If violations: `composer cs:fix`, then re-check.
2. `composer analyse` — PHPStan Level 10. Zero errors required.
3. `composer analyse:complexity` — Cyclomatic complexity (max 20).
4. `composer analyse:messdetector` — PHPMD.
5. `composer test` — PHPUnit test suite.
6. Python syntax: `python3 -m py_compile` on every `.py` file in `strands_agents/` (including subdirectories).
7. Python tests: `cd strands_agents && pytest ../tests/python/`

## SHOULD (skip only with documented reason)

8. `composer test:coverage` — Coverage minimum 80%.
9. `composer cs:fix` followed by formatter verification.
10. Dependency audit: `composer audit` for known vulnerabilities.

## MAY (skip during active debugging)

11. Full formatter run on unchanged files.

## Constraints

- MUST NOT report task complete if any MUST item fails.
- If a MUST item fails, attempt to fix and re-run that step.
- If fix attempt fails, report exactly what failed, the error output, and file:line location.

## Output Format

```
## Preflight Results

| Gate | Status | Notes |
|------|--------|-------|
| CS Check | ✅/❌ | |
| PHPStan L10 | ✅/❌ | |
| Complexity | ✅/❌ | |
| PHPMD | ✅/❌ | |
| PHPUnit | ✅/❌ | |
| Python syntax | ✅/❌ | |
| Python tests | ✅/❌ | |
| Coverage | ✅/⏭️ | |
| Dependency audit | ✅/⏭️ | |

**Result:** PASS / FAIL (with details)
```
