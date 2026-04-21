# Code Map

Repository tree with one-line descriptions. For deep dives, follow the "read when" links in CLAUDE.md.

## Repository Tree

```
ambient-scribe/
├── src/                              # PHP application (PSR-4: App\)
│   ├── Controller/
│   │   └── ScribeController.php      # 4 endpoints: UI, history, role stream, roles
│   ├── Service/
│   │   ├── RoleInferenceService.php   # Wraps Strands role attribution calls
│   │   └── RoleInferenceResult.php    # Data model for role inference results
│   └── Kernel.php                     # Symfony application kernel
│
├── strands_agents/                    # Python FastAPI + NeMo + Strands agent
│   ├── api/
│   │   └── server.py                  # FastAPI HTTP + WebSocket (5 endpoints, Mercure publishing)
│   ├── agents/
│   │   ├── transcription_agent.py     # Strands agent factory for role inference
│   │   └── __init__.py
│   ├── tools/
│   │   ├── assign_roles.py            # Pydantic tool: persists role mapping, detects flips
│   │   └── __init__.py
│   ├── nemo_pipeline.py               # NemoPipeline singleton — Sortformer + Parakeet wrapper
│   ├── nemo_session.py                # TranscriptionSession — per-WebSocket state + ffmpeg conversion
│   └── session.py                     # SessionStore — in-memory transcript history
│
├── config/                            # Symfony configuration
│   ├── services.yaml                  # DI container (autowiring, autoconfiguration)
│   ├── packages/
│   │   ├── framework.yaml             # Routing, session, validation
│   │   ├── mercure.yaml               # Mercure SSE config
│   │   ├── strands.yaml               # Strands agent client config
│   │   └── twig.yaml                  # Template engine
│   └── routes.yaml                    # Route definitions (attribute-based)
│
├── templates/
│   └── scribe/
│       └── index.html.twig            # Main UI shell: session config + external JS assets
│
├── tests/
│   ├── python/                        # pytest suite
│   │   ├── conftest.py                # Fixtures (mock models, audio files)
│   │   ├── test_api.py                # FastAPI endpoint tests
│   │   ├── test_nemo_pipeline.py      # NemoPipeline unit tests
│   │   ├── test_nemo_session.py       # TranscriptionSession tests
│   │   └── test_role_inference.py     # Role inference agent tests
│   └── fixtures/audio/                # OSCE test WAV files (abdominal pain, chest pain)
│
├── docker/
│   └── nemo/
│       └── Dockerfile                 # NeMo GPU container (nvcr.io/nvidia/nemo:25.09 base)
├── Dockerfile                         # PHP Symfony app container
├── docker-compose.yml                 # 3 services: nemo-agent, app, mercure
│
├── infra/terraform/                   # AWS deployment (ECS Fargate sidecar pattern)
│   ├── bootstrap/                     # One-time: S3 state bucket + DynamoDB lock
│   ├── environments/prod/             # Root module wiring all modules
│   └── modules/                       # 15 modules (network, ecs, ecr, iam, alb, dns, ...)
│
├── scripts/
│   ├── nemo_*.py                      # NeMo exploration, benchmarks, edge case tests
│   ├── setup-initial.sh               # First-time tool/dependency installation
│   ├── setup-verify.sh                # Environment verification
│   ├── preflight-checks.sh            # All quality gates (tests, lint, analysis, coverage)
│   ├── deploy.sh                      # Build → push to ECR → ECS redeploy
│   ├── terraform.sh                   # Terraform wrapper (AWS profile, init, plan, apply)
│   ├── dependencies-*.sh              # Install/update PHP + Python dependencies
│   └── installers/                    # Multi-agent framework installers
│
├── docs/                              # Project documentation (see router table in CLAUDE.md)
└── milestones/                        # Task breakdowns: M0–M4
```

## Key Entry Points

| If you need to... | Start here |
|---|---|
| Add a Symfony route or endpoint | `src/Controller/ScribeController.php` |
| Change service wiring or DI | `config/services.yaml`, `config/packages/*.yaml` |
| Modify the UI (audio capture, display) | `templates/scribe/index.html.twig`, `public/js/scribe.js` |
| Change WebSocket or HTTP endpoints | `strands_agents/api/server.py` |
| Modify NeMo inference (models, VRAM) | `strands_agents/nemo_pipeline.py` |
| Change audio buffering or ffmpeg conversion | `strands_agents/nemo_session.py` |
| Update role inference agent behaviour | `strands_agents/agents/transcription_agent.py` |
| Add/change Docker services | `docker-compose.yml` |
| Modify NeMo container | `docker/nemo/Dockerfile` |
| Change AWS infrastructure | `infra/terraform/environments/prod/main.tf` |
| Run quality checks | `composer preflight` or `scripts/preflight-checks.sh` |
| Add PHP tests | `tests/` (PHPUnit, `phpunit.xml.dist`) |
| Add Python tests | `tests/python/` (pytest, `conftest.py`) |
