# Test Skill

Generate testing instructions after a milestone or coding session. Based on the doer-verifier principle: the coding agent MUST NOT verify its own work.

## Three Tracks

### Track 1 — Automated (run by this agent)

Exact commands to run, in order. Include expected output patterns.

```
# Example for this project:
./scripts/preflight-checks.sh
composer test -- --filter=RelevantTest
NEMO_MODEL_PROVIDER=mock PYTHONPATH=strands_agents pytest tests/python/ -k "test_relevant"
ruff check strands_agents/
```

List each command with what a passing result looks like and what a failure indicates.

### Track 2 — AI Verification (run by a SEPARATE fresh agent)

Pre-filled prompts for a different agent session (ideally a different model for cross-model verification). Each prompt should be self-contained — the verifier has no context from the coding session.

```
# Example prompt for verifier:
Read [files changed]. The intent was [goal]. Check:
1. Does the implementation match the stated goal?
2. Are there edge cases not covered by tests?
3. Does this change break any cross-layer contract? Check docs/footguns.md.
Report findings with file:line evidence.
```

Include 2-3 verification prompts covering: correctness, cross-boundary impact, and regression risk.

### Track 3 — Human Testing (numbered checklist for the developer)

Manual steps the developer should perform. Numbered, specific, with expected outcomes.

```
# Example:
1. Start the dev stack: ./scripts/start-dev.sh
2. Open /scribe in browser
3. Start recording — verify transcription segments appear within 5s
4. Stop recording — verify summary generates
5. Check browser console for errors
```

## Constraints

- MUST produce all three tracks
- Track 2 MUST be usable by a fresh agent with no prior context
- SHOULD recommend cross-model verification for Track 2 (e.g., if coded by Claude, verify with Codex or Gemini)
- MUST NOT mark the task as done based solely on Track 1 — Tracks 2 and 3 provide independent verification
- Adapt commands and steps to the actual change, not generic templates
