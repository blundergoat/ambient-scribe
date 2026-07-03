# Code Review — Ambient Scribe

## Priority Order

1. **Correctness** — Does the medical transcription flow work end-to-end?
2. **Security** — Patient data handling, auth boundaries, session isolation
3. **Cross-stack consistency** — PHP ↔ Python API contracts match, Mercure topics aligned
4. **Performance** — Real-time streaming latency, NeMo pipeline efficiency

## Approval Criteria

- [ ] PHPStan level 10 passes
- [ ] Python tests pass through `strands_agents/.venv/bin/pytest tests/python/ -q`
- [ ] No hardcoded credentials or patient data in logs
- [ ] Cross-service contracts (PHP ↔ Python) validated
- [ ] Docker Compose builds and runs

## Anti-Patterns to Flag

- Session ID mismatches across PHP/Twig/JS/WebSocket/Mercure layers
- Mercure topic names that don't match between publisher and subscriber
- NeMo model configuration changes without performance testing
- Session data stored outside the designated session service
- Twig templates with inline business logic

## Don't Nitpick

- PSR-12 formatting (handled by CS fixer)
- Python formatting (handled by ruff)
- Import ordering
