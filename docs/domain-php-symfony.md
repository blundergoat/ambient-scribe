# Domain: PHP / Symfony

Conventions and patterns for the PHP layer. This service serves the UI, manages sessions, and proxies history/role requests. It does NOT touch the audio hot path.

## Conventions

- **PHP 8.3**, Symfony 6.4, PSR-4 namespace `App\`
- Attribute-based routing (no YAML route definitions for controllers)
- Strict typing: `declare(strict_types=1)` in every file
- Services autowired and autoconfigured via `config/services.yaml`

## Controller Pattern

`ScribeController` is the only controller. It follows this pattern:

```php
#[Route('/scribe', name: 'scribe_index', methods: ['GET'])]
public function index(): Response
{
    $sessionId = Uuid::v4()->toRfc4122();
    // ... pass session config to Twig
}
```

Session IDs are generated server-side (UUID v4) and passed to the template, which forwards them to the WebSocket and Mercure topic URLs. See `docs/footguns.md` FG-4 for the full coupling chain.

## Service Wiring

| File | Purpose |
|---|---|
| `config/services.yaml` | DI container (autowiring, autoconfiguration) |
| `config/packages/framework.yaml` | Framework config (routing, session, validation) |
| `config/packages/mercure.yaml` | Mercure hub URL and JWT config |
| `config/packages/strands.yaml` | Strands agent client config |
| `config/packages/twig.yaml` | Template engine config |

When adding a new service: rely on autowiring first. Only add explicit wiring in `services.yaml` if the class has non-type-hintable constructor parameters.

## Twig Integration

Single template: `templates/scribe/index.html.twig`. Contains inline JS for:
- `MediaRecorder` audio capture (WebM/Opus)
- WebSocket streaming to FastAPI
- Mercure SSE subscription via `StreamOrchestrator`
- Tailwind CSS styling

Template variables are set in `ScribeController::index()`. When adding new config, pass it as a Twig variable — don't hardcode URLs in JS.

## Quality Standards

| Tool | Level | Command |
|---|---|---|
| PHPStan | Level 10 (strictest) | `composer analyse` |
| PHP-CS-Fixer | PSR-12 | `composer cs:check` / `composer cs:fix` |
| PHPMD | design, codesize, unusedcode | `composer analyse:messdetector` |
| Cyclomatic complexity | Max 20 | `composer analyse:complexity` |
| Coverage | Minimum 80% | `composer test:coverage` |
| Mutation testing | infection/infection | `composer mutate` |

## Commands

```bash
composer preflight              # All quality checks
composer test                   # PHPUnit tests
composer test:coverage          # Tests with HTML + clover coverage
composer analyse                # PHPStan level 10
composer analyse:complexity     # Cyclomatic complexity (max 20)
composer analyse:messdetector   # PHPMD
composer cs:check               # PHP-CS-Fixer dry-run
composer cs:fix                 # Auto-fix code style
composer validate:composer      # Validate composer.json/lock
composer security:audit         # Known vulnerability check
composer mutate                 # Mutation testing
composer preflight:coverage     # All checks + 80% coverage threshold
composer preflight:mutate       # All checks + mutation testing
```

## Feature Checklist

After implementing any PHP feature, verify:

1. Service wiring: `config/services.yaml`, relevant `config/packages/*.yaml`
2. Route registration: controller attribute `#[Route(...)]`
3. Twig template updates if UI changes needed
4. PHPUnit tests covering the new code path
5. PHPStan passes at Level 10: `composer analyse`
6. Code style passes: `composer cs:check`
7. Full preflight passes: `composer preflight`
