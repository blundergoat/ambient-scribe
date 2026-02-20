# Code Review Skill

Perform a thorough code review. Verify every finding before reporting it.

## Steps

1. Read ALL changed files thoroughly before commenting. Use `git diff` to identify what changed.
2. For each finding, verify it is real by reading the surrounding code - at least 50 lines of context.
3. Check for false positives. Do NOT report speculative issues or things that "might" be wrong.
4. Categorize findings:
   - **Critical**: Bugs, security issues, broken contracts between PHP/Python layers, missing error handling on external calls.
   - **Non-critical**: Style inconsistencies, naming suggestions, minor improvements.
5. For external review comments (Copilot, other AI tools), investigate each suggestion against the actual codebase before applying. Some suggestions cause breaking changes - verify by tracing the code path.
6. Check cross-layer impact: does a PHP change require a matching Python agent change? Does a config change need a Docker env var update?
7. Run `composer test && composer analyse` after any changes to confirm nothing broke.

Present findings with exact file:line references and evidence from the code.
