# GOAT Debug

Diagnosis-first debugging.

If you want to just try something before tracing the code path, STOP.
Hard gate: do not implement a fix until the human has reviewed the diagnosis.

## Required Trace
- MUST reproduce or restate the observed failure.
- MUST trace the request or code path through the relevant layers.
- MUST show the likely fault location with file:line evidence.
- MUST separate confirmed facts from hypotheses.

## Diagnosis Output
- Symptom:
- Reproduction status:
- Files read:
- Suspected fault location:
- Evidence:
- Competing hypotheses:
- Smallest next experiment:

## Rules
- MUST NOT ship a fix in this mode.
- MUST NOT say "might be X" without evidence or a concrete next experiment.
- MUST read both sides before concluding when the issue crosses a boundary.
