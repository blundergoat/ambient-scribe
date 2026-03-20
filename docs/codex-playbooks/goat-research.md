# GOAT Research

Deep-read before planning a cross-domain change.

Hard gate: do not plan or edit until the human has reviewed the output.

## Files Involved
- MUST list the exact files read.
- MUST separate direct edit targets from boundary-reading targets.

## Request Flow
- MUST describe the end-to-end path as it works today.
- MUST include the entrypoint, hand-offs, outputs, and where state is stored.

## Boundaries Touched
- MUST name each boundary explicitly: PHP <-> Python, browser <-> WebSocket, Docker <-> env, infra <-> runtime, and so on.
- MUST say which side owns the contract today.

## Risks/Gotchas
- MUST list at least 3 items.
- Every item MUST include file:line evidence.
- SHOULD prefer existing footguns over hypotheticals.

## Output Template
- Files Involved:
- Request Flow:
- Boundaries Touched:
- Risks/Gotchas:
- Questions for the human:
