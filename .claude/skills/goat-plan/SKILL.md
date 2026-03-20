# Plan Skill

4-phase planning workflow with human gates between each phase. Complexity determines which phases apply.

## Phases

### Phase 1 — Feature Brief (all complexities)

Produce a concise brief: problem statement, proposed approach, files affected, boundaries crossed, success criteria. For **Hotfix**: compress to a single brief paragraph and stop — skip remaining phases.

### Phase 2 — Mob Elaboration (Standard and above)

Expand the brief with three lenses, each completing before the next begins:
1. **SKEPTIC** — What could go wrong? Edge cases, failure modes, footgun conflicts.
2. **ANALYST** — What does the data/code actually say? Read files, trace flows, cite evidence.
3. **STRATEGIST** — What's the simplest path that handles the skeptic's risks?

Visible disagreement between lenses is preserved, not smoothed over. For **Standard** features: proceed directly to Phase 4 after elaboration (skip SBAO).

### Phase 3 — SBAO Ranking (System/Infra only)

Rank elaborated options using SBAO (Situation, Behaviour, Action, Outcome):
- Situation: current state and constraints
- Behaviour: what each option changes
- Action: concrete implementation steps
- Outcome: expected result + risks

This is the **Triangular Tension Pass**: SKEPTIC, ANALYST, and STRATEGIST each evaluate the ranked options. Each lens completes fully before the next begins.

### Phase 4 — Milestones

Break the chosen approach into deliverable milestones. Each milestone: scope, files, acceptance criteria, test plan. Milestones should be independently shippable where possible.

## Human Gates

- After Phase 1: human reviews brief before elaboration
- After Phase 2: human reviews elaboration before ranking/milestones
- After Phase 3: human reviews ranking before milestones
- After Phase 4: human approves milestones before implementation begins

## Constraints

- MUST NOT write application code during planning
- MUST stop at each human gate — do not auto-advance
- MUST include /goat-investigate output (or inline equivalent) before Phase 2
- Hotfix: single brief only. Standard: skip SBAO. System/Infra: all four phases.
