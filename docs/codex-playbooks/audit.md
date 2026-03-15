# Audit Playbook

Structured inspection for bugs, regressions, and missing verification.

MUST NOT propose fixes in this mode.

## Discovery

- Read the requested scope and identify the risky surfaces.
- Note what changed and what behaviour is supposed to stay stable.

## Verification

- Check the code, tests, docs, and runtime wiring.
- Prefer evidence from files, tests, or history over inference.

## Prioritisation

- Order findings by severity and likelihood.
- Lead with behavioural regressions and missing tests.

## Self-Check

- Ask: "Did I fabricate this?"
- Ask: "Can I point to file:line evidence?"
- Ask: "Did I accidentally turn this into a solution proposal?"

## Output Template

- Findings:
- Open Questions:
- Residual Risk:
