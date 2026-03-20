# Dev Scenarios Invalid JSON

**Origin:** real-incident (commit `4a3046e`)
**Agents:** all

- Bug description: In dev mode, malformed `tests/fixtures/scribe/scenarios.json` crashed `/scribe` instead of degrading gracefully.
- Replay prompt: The dev panel scenarios fixture can contain invalid JSON and `/scribe` should still render. Fix the narrowest issue and explain how you'd verify the failure path.
- Expected outcome: Read `src/Controller/ScribeController.php` and its unit tests, catch the JSON parse failure, log a warning, render with an empty scenario list, and mention the focused controller test.
- Failure mode tested: Narrow defensive fix at a controller/config boundary.
