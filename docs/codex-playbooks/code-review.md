# Code Review Playbook

Review mode for changes in this repo.

## Priority Markers

- `P0`: data loss, security, broken runtime path, or user-visible failure
- `P1`: likely bug, contract drift, missing verification, or regression risk
- `P2`: maintainability or clarity issue with real downstream cost

## Review Rules

- Findings first, ordered by severity.
- Use file:line references for each finding.
- Keep summaries brief and secondary.
- Respect autonomy tiers: if the issue crosses an Ask First boundary, say so explicitly.
- Do not edit code in review mode unless the user asks.

## Output Template

- Findings:
- Open Questions / Assumptions:
- Change Summary:
- Residual Risk:
