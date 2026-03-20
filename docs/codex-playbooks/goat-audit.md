# GOAT Audit

Structured inspection for bugs, regressions, and missing verification.

MUST NOT propose fixes in this mode.

## Pass 1: Discovery
- Read the requested scope and identify the risky surfaces.

## Pass 2: Behaviour
- Compare intended behaviour to the current code, tests, docs, and runtime wiring.

## Pass 3: Verification
- Check code, tests, docs, and history.
- Prefer file, test, or commit evidence over inference.

## Pass 4: Fabrication Check
- Ask: "Did I fabricate this?"
- Ask: "Can I point to file:line evidence?"
- Ask: "Did I drift into solution mode?"

## Output Template
- Findings:
- Open Questions:
- Residual Risk:
