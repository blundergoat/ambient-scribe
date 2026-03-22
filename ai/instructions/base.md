# Base Instructions — Ambient Scribe

Real-time medical transcription system. Browser captures microphone audio, streams it over WebSocket to a Python agent layer for GPU-accelerated ASR, then delivers live transcripts via Mercure SSE.

## Stack

- **PHP >=8.2, Symfony 6.4** — `src/`, PSR-12, PHPStan level 10, `declare(strict_types=1)` everywhere
- **Python 3.12+, FastAPI** — `strands_agents/`, NeMo Parakeet (GPU diarization + ASR), Strands SDK (Bedrock role inference)
- **Frontend** — Twig template (`templates/scribe/index.html.twig`) that loads external JS (`public/js/scribe.js`), PcmStreamer for audio capture
- **Mercure** — JWT-authenticated SSE for real-time transcript delivery
- **Docker** — `docker-compose.yml`, services: `nemo-agent` (GPU), `mercure`, `app` (Symfony)
- **Infrastructure** — Terraform in `infra/terraform/`, AWS deployment

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

## Hard Constraints

- **GPU exclusivity:** NeMo owns the GPU. Role inference MUST use Bedrock or CPU Ollama — never local GPU.
- **ThreadPoolExecutor:** NeMo inference MUST use `run_in_executor`.
- **Session ID coupling:** UUID flows PHP -> Twig -> JS -> WS -> Mercure. All layers MUST match.
- **Local dependency:** `blundergoat/strands-php-client` is a path dependency at `../strands-php-client`.

## Cross-Layer Awareness

Changes in one layer often affect others. Before editing, check:
1. PHP service wiring (`config/services.yaml`, `config/packages/strands.yaml`)
2. Python agent contracts (Pydantic models in `strands_agents/api/`, WebSocket formats)
3. Twig template (`templates/scribe/index.html.twig`)
4. Docker Compose environment variables (`docker-compose.yml`)
5. PHPStan at level 10 (`composer analyse`)

## Verification Commands

```bash
composer test              # PHPUnit
composer analyse           # PHPStan level 10
composer cs:check          # PHP-CS-Fixer dry-run
composer preflight         # All of the above
pytest tests/python/       # Python tests
ruff check strands_agents/ # Python lint
```
