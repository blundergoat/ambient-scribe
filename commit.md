```
feat: add dev panel, scenario runner, mode selector, and milestone 2 enhancements

- Add 3-column dev layout: scenario panel (left), transcript (center), inspector panel (right) — gated by APP_ENV=dev, zero prod impact
- Add ScenarioRunner with 7 medical transcript scenarios (happy path, role flip, reconnect recovery, high volume, empty session, single speaker, late role update) for client-side testing without GPU/mic
- Add mode selector (Medical, Meeting, Interview, TV, Lecture, General) with per-mode role labels, avatars, and button text
- Redesign transcript segments with avatar circles, improved typography, and refined color system for light/dark themes
- Add session reset (New Session button) with fresh session ID
- Replace theme text toggle with sun/moon SVG icons
- Update ScribeController to load scenario fixtures in dev mode
- Add ScribeControllerTest coverage for dev panel params
- Enhance Python API server, NeMo session, and role inference tooling
- Update scripts (preflight, health-check, setup, start-dev)
- Update docs (architecture, domain-reference, footguns, local-dev)
- Update milestones 2 and 3 with revised scope
- Add e2e test scaffolding (Playwright config, test stubs)
- Add codex evals and playbooks
- Add .gitignore entries for node_modules/ and test-results/
```
