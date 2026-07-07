# Conventions - Ambient Scribe

Real-time medical transcription system. Browser captures microphone audio, streams raw PCM over WebSocket to a Python agent layer (NeMo GPU diarization + ASR), then delivers live transcripts via Mercure SSE back to the browser.

## Stack

- **PHP >=8.3, Symfony 6.4** - `src/`, PSR-12, PHPStan level 10, `declare(strict_types=1)` in every file
- **Python 3.12+, FastAPI** - `strands_agents/`, NeMo Parakeet (GPU diarization + ASR), Strands SDK
- **Frontend** - Twig template (`templates/scribe/index.html.twig`) loads `public/js/scribe.js`; no bundler, no npm build step
- **Mercure** - JWT-authenticated SSE for real-time transcript delivery
- **Docker** - `docker-compose.yml` with services: `nemo-agent` (GPU), `mercure`, `app` (Symfony)
- **Infrastructure** - Terraform in `infra/terraform/`, AWS deployment
- **Playwright** - E2E tests in `tests/e2e/`, configured in `playwright.config.js`

## Architecture

```
Browser (PcmStreamer, raw PCM) -> WebSocket -> FastAPI (server.py)
                                                 |
                                           NemoPipeline (GPU diarization + ASR)
                                                 |
                                           TranscriptionAgent (Strands -> Bedrock)
                                                 |
                                           Mercure SSE -> Browser (live transcript)
                                                 |
ScribeController (Symfony) <- session history <- SessionStore
```

Session ID (UUID v4) flows through every layer: PHP -> Twig -> JS -> WebSocket -> Python -> Mercure. All layers must use the same session ID.

## Commands

```bash
# PHP
composer test                  # PHPUnit tests
composer analyse               # PHPStan level 10
composer cs:check              # PHP-CS-Fixer dry-run
composer cs:fix                # PHP-CS-Fixer auto-fix
composer preflight             # All checks (test + analyse + cs:check)
composer preflight:coverage    # Preflight with 80% coverage gate
composer mutate                # Infection mutation testing

# Python
strands_agents/.venv/bin/pytest tests/python/ -q  # Python tests

# E2E
npx playwright test            # Browser tests (uses fake media devices)

# Docker
docker compose up -d           # Start all services
docker compose logs -f app     # Follow Symfony logs
```

## Conventions

Do:
- Run `composer preflight` before every commit
- Use constructor injection with `readonly` promoted properties
- Use `#[Autowire(service: 'strands.client.scribe')]` for the StrandsClient (wired in `config/packages/strands.yaml`)
- Use PHP 8 attributes for routing: `#[Route('/path', methods: ['GET'])]`
- Keep NeMo inference on the GPU; role inference uses Bedrock or CPU Ollama only
- Use `run_in_executor` for NeMo inference calls (ThreadPoolExecutor)
- Return `\stdClass()` for empty JSON objects (not `[]`) so they serialize as `{}` not `[]`

Don't:
- Don't skip `declare(strict_types=1)` in any PHP file
- Don't use `mixed` when a union type is possible (PHPStan level 10)
- Don't hardcode WebSocket or Mercure URLs; use container parameters from `config/packages/`
- Don't create new StrandsClient instances; use the DI-wired service
- Don't run NeMo and role inference on the same GPU
- Don't change the PHP<->Python API contract without updating both sides (footgun #4)

## Cross-Layer Awareness

Before editing any layer, check:
1. PHP service wiring: `config/services.yaml`, `config/packages/strands.yaml`
2. Python agent contracts: Pydantic models in `strands_agents/api/`, WebSocket message formats
3. Twig template: `templates/scribe/index.html.twig`
4. Docker Compose env vars: `docker-compose.yml`
5. PHPStan at level 10: `composer analyse`

## Hard Constraints

- **GPU exclusivity:** NeMo owns the GPU. Role inference must use Bedrock or CPU Ollama.
- **Session ID coupling:** UUID flows through PHP -> Twig -> JS -> WS -> Mercure. All layers must match.
- **Local dependency:** `blundergoat/strands-php-client` is a path dependency at `../strands-php-client`.
- **PHPStan level 10:** No suppressions without justification in the commit message.
