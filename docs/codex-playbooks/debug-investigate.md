# Debug Investigate Playbook

Diagnosis-first debugging.

If you want to just try something before tracing the code path, STOP.

Hard gate: do not implement a fix until the human has reviewed the diagnosis.

## Required Trace

- Reproduce or restate the observed failure.
- Trace the request/code path through the relevant layers.
- Show the likely fault location with file:line evidence.
- Separate confirmed facts from hypotheses.

## Diagnosis Output

- Symptom:
- Reproduction status:
- Files read:
- Suspected fault location:
- Evidence:
- Competing hypotheses:
- Smallest next experiment:

## Rules

- No fixes in this mode.
- No "might be X" without evidence or a concrete next experiment.
- If the issue crosses a boundary, read both sides before concluding.
