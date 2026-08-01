# ADR-004: Gruff-py Runtime Scope with Pytest Test Gate

**Status:** Implemented
**Date:** 2026-07-04
**Author(s):** Codex
**Ticket/Context:** 0.3.0 gruff-py runtime cleanup

## Decision

Gruff-py is the advisory-to-error gate for maintained runtime Python code, not
for the integration-heavy Python test tree. `.gruff-py.yaml` ignores
`tests/python/**` and `tests/e2e/**`; `strands_agents/.venv/bin/pytest
tests/python/ -q` remains the behavioral test gate.

## Context

After that runtime cleanup, gruff-py reported zero runtime findings but still
reported 270 findings, all in tests. The largest groups were fixture/private
state and integration-test shape rules: `test-quality.private-reflection`,
`test-quality.loop-in-test`, `test-quality.mystery-guest`, and
`waste.unused-parameter`.

The test files intentionally exercise FastAPI route seams, module-level session
state, replay task cleanup, and cross-service contracts. Rewriting those tests
only to satisfy static test-shape rules would be a separate test architecture
project and risks weakening behavioral coverage.

## Failure Mode Comparison

| Option | What fails | Why rejected or accepted |
| --- | --- | --- |
| Fix every test-shape finding now | Large rewrite of private-state and integration tests during a runtime cleanup milestone | Rejected: too much blast radius for a runtime-only cleanup and likely to weaken coverage while chasing style findings. |
| Disable gruff-py entirely | Runtime Python regressions lose the new static gate | Rejected: production code was made clean and should stay gated. |
| Scope gruff-py to runtime and keep pytest for tests | Static test-shape findings are not enforced by gruff-py | Accepted: preserves behavior coverage while keeping runtime code under gruff-py. |

## Reversibility

This is a two-way door. Re-enable gruff-py on tests only with a dedicated
test-architecture milestone that introduces public test seams or rule-specific
test config, then proves `pytest tests/python/ -q` still passes.
