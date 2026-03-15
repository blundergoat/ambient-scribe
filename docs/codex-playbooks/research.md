# Research Playbook

Deep-read before planning a cross-domain change.

Hard gate: do not plan or edit until the human has reviewed the output.

## Files Involved

- List the exact files read.
- Separate direct edit targets from boundary-reading targets.

## Request Flow

- Describe the end-to-end path as it works today.
- Include the entrypoint, hand-offs, outputs, and where state is stored.

## Boundaries Touched

- Name each boundary explicitly: PHP <-> Python, browser <-> WebSocket, Docker <-> env, infra <-> runtime, etc.
- Call out which side owns the contract today.

## Risks/Gotchas

- Minimum 3 items.
- Every item must include file:line evidence.
- Prefer existing footguns over hypotheticals.

## Output Template

- Files Involved:
- Request Flow:
- Boundaries Touched:
- Risks/Gotchas:
- Questions for the human:
