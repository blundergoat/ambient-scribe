# goat-debug SKILL

Diagnosis-first debugging.

## Constraint
- If you want to "just try something" before tracing the code path, STOP.
- Diagnosis MUST be verified with file:line evidence and log/test output before proposing a fix.

## Workflow
1. Reproduce failure.
2. Trace code path with file:line evidence.
3. Verify diagnosis with logging or unit test.
4. Stop for human review.
