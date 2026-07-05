# Code Map

Current repository map for fast orientation. Dependency caches and generated outputs are summarized, not expanded.

## Repository Tree

```
ambient-scribe/
├── src/ = PHP Symfony application code (PSR-4: App\)
│   ├── Controller/ScribeController.php = `/`, `/scribe`, demo audio, summary/model-health proxies, history, role snapshot, and role override routes
│   ├── Service/RoleInferenceService.php = PHP wrapper for Python role mapping calls
│   └── Kernel.php = Symfony kernel
├── config/ = Symfony config
│   ├── services.yaml = DI/autowiring and project parameters
│   ├── routes.yaml = route loading
│   └── packages/ = framework, Mercure, Strands client, Twig, and test config
├── templates/scribe/index.html.twig = main Scribe UI shell and injected runtime config
├── public/js/ = browser-side app assets
│   ├── scribe.js = shared browser state, role labels, safe DOM helpers, and theme controls
│   ├── scribe-streaming.js = Mercure streams, browser PCM capture, and demo WAV PCM streaming
│   ├── scribe-recording.js = live recording lifecycle and session resets
│   ├── scribe-transcript.js = transcript card rendering, relabeling, and visible segment snapshots
│   ├── scribe-output.js = streaming demo replay, summaries, and clinical hints
│   ├── scribe-actions.js = post-visit actions, JSON parsing, toggles, and shortcuts
│   ├── scribe-dev.js = development inspector panel
│   ├── scribe-fixtures.js = dev-only generated WAV fixture replay picker
│   └── tailwind.js = local Tailwind browser build asset
├── strands_agents/ = Python FastAPI, NeMo, storage, and Strands agent lane
│   ├── api/server.py = HTTP/WebSocket API, Mercure publishing, role queue, summaries, health
│   ├── agents/transcription_agent.py = role inference agent factory
│   ├── agents/summary_agent.py = medical summary agent factory
│   ├── tools/assign_roles.py = Pydantic role assignment tool and session role state
│   ├── nemo_pipeline.py = singleton Sortformer/Parakeet GPU pipeline wrapper
│   ├── nemo_session.py = per-WebSocket audio buffering and ffmpeg conversion
│   ├── session.py = in-memory transcript store with TTL cleanup
│   ├── session_lifecycle.py = active WebSocket lifecycle and reconnect cleanup
│   ├── storage.py = SQLite storage backend and storage protocol
│   └── requirements.txt = Python runtime dependencies, excluding NeMo toolkit
├── tests/ = automated tests and fixtures
│   ├── Unit/ = PHPUnit tests for Symfony controller/service code
│   ├── python/ = pytest suite for API, storage, lifecycle, role inference, Mercure, and NeMo seams
│   ├── e2e/ = Playwright and contract tests
│   └── fixtures/ = scribe scenario JSON plus generated demo audio metadata
├── docker/ = container support files
│   └── nemo/Dockerfile = NeMo GPU FastAPI image
├── Dockerfile = PHP Symfony app image
├── docker-compose.yml = local app, nemo-agent, mercure, and optional ollama services
├── infra/terraform/ = AWS production scaffolding
│   ├── bootstrap/ = remote state and lock-table setup
│   ├── environments/prod/ = production root module
│   └── modules/ = network, ECS, ALB, DNS, ECR, IAM, secrets, observability, WAF, DynamoDB
├── scripts/ = setup, health, preflight, deploy, Terraform, NeMo experiments, and installer scripts
├── docs/ = domain, infrastructure, deployment, workflow, troubleshooting, and coding standards
├── .github/ = Copilot instructions, skills, hooks, review instructions, and workflows
├── .goat-flow/ = GOAT Flow learning loop, skill docs, hooks, plans, scratchpad, and local logs
├── vendor/ = Composer dependencies; generated/vendor, do not edit
├── node_modules/ = npm dependencies and GOAT Flow package; generated/vendor, do not edit
└── var/ = Symfony runtime cache/logs; generated, do not edit
```

## Key Entry Points

| If you need to... | Start here |
|---|---|
| Add or change a Symfony route | `src/Controller/ScribeController.php` |
| Change Symfony DI, params, Mercure, Strands, or Twig config | `config/services.yaml`, `config/packages/` |
| Change browser audio capture, WebSocket, EventSource, replay, or summary UI | `public/js/`, `templates/scribe/index.html.twig` |
| Change FastAPI HTTP or WebSocket endpoints | `strands_agents/api/server.py` |
| Change NeMo model loading or GPU inference | `strands_agents/nemo_pipeline.py` |
| Change audio buffering or conversion | `strands_agents/nemo_session.py` |
| Change active session teardown or reconnect grace | `strands_agents/session_lifecycle.py` |
| Change transcript persistence | `strands_agents/session.py`, `strands_agents/storage.py` |
| Change role inference behavior | `strands_agents/agents/transcription_agent.py`, `strands_agents/tools/assign_roles.py` |
| Change summary generation | `strands_agents/agents/summary_agent.py`, `strands_agents/api/server.py` |
| Add or change Docker services | `docker-compose.yml` |
| Modify the NeMo container | `docker/nemo/Dockerfile` |
| Change AWS infrastructure | `infra/terraform/environments/prod/main.tf`, `infra/terraform/modules/` |
| Run PHP quality gates | `composer test`, `composer analyse`, `composer cs:check` |
| Run Python tests | `strands_agents/.venv/bin/pytest tests/python/ -q` |
| Run local umbrella checks | `scripts/preflight-checks.sh` |
| Update Copilot GOAT Flow setup | `.github/copilot-instructions.md`, `.github/skills/`, `.github/hooks/`, `.copilotignore` |
