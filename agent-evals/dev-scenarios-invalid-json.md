# Dev Scenarios Invalid JSON

**Skill:** goat-debug
**Agents:** all

**Origin:** real-incident (commit `4a3046e`)
**Agents:** all

## Bug Description

In dev mode, malformed `tests/fixtures/scribe/scenarios.json` crashed `/scribe` instead of degrading gracefully. The controller did not handle JSON parse failures.

**Related:** commit `4a3046e`

## Replay Prompt

```
The dev panel scenarios fixture can contain invalid JSON and /scribe should still render. Fix the narrowest issue and explain how you'd verify the failure path.
```

## Expected Outcome

1. Agent reads `src/Controller/ScribeController.php` and its unit tests
2. Agent catches the JSON parse failure, logs a warning, renders with an empty scenario list
3. Agent mentions the focused controller test for the failure path
4. Agent does NOT widen scope beyond the controller's JSON handling

## Failure Mode Tested

- **Narrow scope**: Defensive fix at a controller/config boundary without widening
- **VERIFY**: Agent explains how to verify the failure path with a focused test
