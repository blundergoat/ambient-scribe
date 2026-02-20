# Repository Guidelines

## Project Structure & Module Organization
Core PHP app code lives in `src/` with routes/controllers in `src/Controller/` and orchestration logic in `src/Service/`. Templates are in `templates/` (main UI: `templates/scribe.html.twig`), and the web entrypoint is `public/index.php`. Unit tests live under `tests/Unit/` and mirror app namespaces (`Controller/`, `Service/`).
The Python audio/transcription pipeline lives in `strands_agents/` (`nemo_pipeline.py` for diarization + ASR, `nemo_session.py` for session state, `transcription_agent.py` for DOCTOR/PATIENT role inference, `api/server.py` for HTTP endpoints). Frontend streaming helpers live in `assets/controllers/`. Runtime wiring is in `config/`, and repository quality scripts are in `scripts/`.

## Build, Test, and Development Commands
```bash
cp .env.example .env              # create local env file
docker compose up --build         # start full stack (app, nemo-pipeline, agent, mercure)
composer test                     # run PHPUnit suite
composer test:coverage            # generate coverage-html/ and coverage.xml
composer analyse                  # run PHPStan (level 10)
composer cs:check                 # dry-run PHP-CS-Fixer
composer cs:fix                   # apply code style fixes
composer preflight                # run full quality gate script
```
Use `docker compose down` to stop services.

## Coding Style & Naming Conventions
PHP follows PSR-12 with `declare(strict_types=1);`, 4-space indentation, short array syntax, ordered imports, and single quotes (enforced by `.php-cs-fixer.php`). Keep namespaces under `App\` and class/file names in PascalCase (`ScribeStreamOrchestrator.php`).
Tests use `*Test.php` files with descriptive `test...` method names. For Python and JS files, follow the existing style in this repo: clear module-level docs, readable names, and small focused functions.

## Testing Guidelines
Primary framework: PHPUnit 11 (`phpunit.xml.dist`). Place tests in `tests/Unit/...` matching production paths. Prefer deterministic unit tests with mocks for external dependencies (HTTP clients, event dispatching, Mercure).
Run a focused test during iteration, for example:
```bash
vendor/bin/phpunit tests/Unit/Service/ScribeOrchestratorTest.php
```
Coverage checks use an 80% minimum threshold in preflight (`composer preflight:coverage`).

## Commit & Pull Request Guidelines
Commit messages use conventional commits format -- see `.github/instructions/commit-messages.instructions.md` for full rules. Key points: `type(scope): subject` + body with 2-5 specific bullets. Never write vague one-liners. Keep commits scoped to one logical change.
PRs should include: what changed, why, test evidence (commands run), and screenshots/GIFs for UI updates. Link related issues and call out any `.env` or Docker/config changes explicitly.

## Security & Configuration Tips
Never commit secrets in `.env`. Use `.env.example` as the template. Default provider is AWS Bedrock for the Strands transcription agent; an NVIDIA GPU is required for the NeMo ASR pipeline. Replace development secrets before any production deployment. Audio data and transcripts may contain PHI/PII -- ensure no sensitive content is logged in production.
