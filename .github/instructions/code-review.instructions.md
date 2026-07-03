---
applyTo: '**'
---

# Code Review Guidelines - Ambient Scribe

You are reviewing a real-time medical transcription system with a PHP/Symfony backend, a Python GPU-accelerated NeMo ASR pipeline, a Strands agent layer for role inference, and Mercure SSE for transcript delivery. Every review comment should account for the cross-layer nature of this project.

## Project Context

- PHP >=8.3, Symfony 6.4, `declare(strict_types=1)` everywhere, PSR-12 formatting
- Namespace: `App\` (src/), `App\Tests\` (tests/)
- PHPStan level 10 - type errors are blockers
- PHP-CS-Fixer enforces: short arrays, single quotes, ordered imports, trailing commas
- Python 3.12+, NeMo multitalker Parakeet for diarization + ASR (GPU-accelerated)
- Strands agent for six-mode role inference (AWS Bedrock or CPU Ollama)
- WebSocket audio pipeline: browser captures audio and sends it to the Python backend
- Real-time transcript delivery via Mercure (JWT-authenticated SSE)
- `blundergoat/strands-php-client` is a local path dependency at `../strands-php-client`

## What to Flag

### Correctness (Blockers)
- Type mismatches or missing return types (PHPStan level 10)
- Breaking changes to the Python pipeline's request/response models without updating PHP callers
- Mercure JWT signing using secrets shorter than 32 characters (HS256 minimum)
- Missing `declare(strict_types=1)` in new PHP files
- NeMo pipeline changes that break diarization or ASR accuracy
- Audio WebSocket handler changes that drop frames or introduce latency regressions

### Cross-Layer Consistency (High Priority)
- Python endpoint changes without matching PHP `StrandsClient` call updates
- New environment variables added in one place but not in `.env.example`, `docker-compose.yml`, or `scripts/start-dev.sh`
- Symfony service wiring changes that don't match `config/packages/strands.yaml` agent definitions
- Twig template changes that assume streaming when sync mode is also supported (and vice versa)
- Docker Compose changes that break the service dependency chain (nemo-pipeline -> agent -> mercure -> app)
- GPU resource allocation changes that affect NeMo model loading or inference

### Architecture
- `ScribeController` is the single entry point; orchestrators handle sequencing -- don't add transcription logic to the controller
- The single `StrandsClient` is injected via `#[Autowire(service: 'strands.client.scribe')]` from `config/packages/strands.yaml` -- don't hardcode service references
- Audio flows through the WebSocket pipeline to `nemo_pipeline.py` for diarization + ASR, then `strands_agents/agents/transcription_agent.py` for mode-specific role inference via Strands/Bedrock or CPU Ollama
- Session state lives in `nemo_session.py` on the Python side -- PHP is stateless between requests
- Mercure publishes raw segments, role updates, and summaries to the browser in real time -- don't mix sync and streaming patterns

### Style and Convention
- 4-space indentation, single quotes, short array syntax `[]`
- `camelCase` methods/variables, `PascalCase` classes
- Trailing commas in multiline arrays, arguments, and parameters
- `declare(strict_types=1)` at the top of every PHP file
- Ordered imports (alphabetical)

### Security
- No secrets in `.env` committed to git -- only dev-safe defaults
- `MERCURE_JWT_SECRET` must be >= 32 characters for HS256
- `APP_SECRET` must not be the default value in production
- No raw user input passed to agent prompts without the controller's validation
- Audio data must be validated before processing (format, size, duration limits)
- PHI/PII considerations: transcripts contain medical data -- ensure no logging of transcript content in production

## What NOT to Flag

- Empty `MERCURE_*` variables in `start-dev.sh` -- this is intentional (sync-only mode, no Mercure)
- Python agent using in-memory session storage -- this is valid for local dev; production runtime currently supports SQLite persistence, not DynamoDB
- `docker-compose.override.yml` not existing -- it's optional and gitignored
- Large NeMo model files not in the repository -- they are downloaded at container build time

## Review Checklist for Common PR Types

### NeMo Pipeline Changes
- [ ] Diarization accuracy not regressed (multi-speaker separation)
- [ ] ASR word error rate not regressed
- [ ] GPU memory usage within bounds for target hardware
- [ ] Session management in `nemo_session.py` handles concurrent sessions correctly
- [ ] Audio frame handling in the WebSocket pipeline is robust to dropped/reordered frames

### Transcription Agent Changes
- [ ] Mode-specific role inference logic is correct for all supported modes
- [ ] Strands agent prompt changes don't break structured output parsing
- [ ] Bedrock model configuration is consistent across environments
- [ ] Health endpoint still works (`GET /health`)

### Docker / Infrastructure Changes
- [ ] Service dependency chain preserved (healthchecks, depends_on)
- [ ] GPU passthrough configuration correct for NeMo container
- [ ] Port mappings consistent with `scripts/start-dev.sh` defaults
- [ ] Environment variables match between `docker-compose.yml` and `.env.example`
- [ ] NVIDIA Container Toolkit requirements documented if changed

### Frontend (Twig) Changes
- [ ] Audio capture and WebSocket connection handled correctly
- [ ] EventSource error handling present for Mercure streaming
- [ ] Graceful degradation when Mercure is unavailable
- [ ] No hardcoded URLs -- uses Symfony/Twig variables

## Scope and Hygiene

- PRs should be atomic -- one logical change. Flag PRs that mix formatting with behavioral changes.
- If a PR reveals deeper issues, the fix should be scoped to the reported problem. Broader refactors belong in follow-up PRs.
- Every PR should include a verification story: what changed, how it was tested, what commands were run.

## Running Verification

```bash
composer test          # PHPUnit
composer analyse       # PHPStan level 10
composer cs:check      # PHP-CS-Fixer dry-run
composer preflight     # All of the above in sequence
```
