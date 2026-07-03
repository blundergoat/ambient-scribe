# Backend — Ambient Scribe

PHP 8.3+ with Symfony 6.4. Two PHP source files handle the HTTP layer; the real-time audio pipeline is handled by the Python agent via WebSocket.

## File Layout

| File | Purpose |
|------|---------|
| `src/Controller/ScribeController.php` | Routes: `GET /scribe`, `GET /scribe/{id}/history`, `GET /scribe/{id}/roles` |
| `src/Service/RoleInferenceService.php` | Fetches current role mapping from the Python agent via StrandsClient |
| `src/Kernel.php` | Symfony kernel (standard, do not edit) |
| `config/services.yaml` | DI container: autowire + autoconfigure for `App\` namespace |
| `config/packages/strands.yaml` | StrandsClient wiring: `strands.client.scribe` with endpoint, timeout, auth |
| `config/packages/mercure.yaml` | Mercure hub configuration |
| `config/packages/framework.yaml` | Symfony framework config |
| `config/packages/twig.yaml` | Twig template engine config |
| `config/routes.yaml` | Route imports (controllers use PHP 8 attributes, not YAML routes) |

## Symfony Patterns

### Constructor Injection
All services use constructor injection with `readonly` promoted properties:
```php
public function __construct(
    #[Autowire(service: 'strands.client.scribe')]
    private readonly StrandsClient $strandsClient,
    private readonly RoleInferenceService $roleInferenceService,
    private readonly LoggerInterface $logger,
) {}
```

### Routing
PHP 8 attributes, not YAML:
```php
#[Route('/scribe/{sessionId}/history', name: 'scribe_history', methods: ['GET'])]
public function history(string $sessionId): JsonResponse
```

### StrandsClient
Wired as `strands.client.scribe` in `config/packages/strands.yaml`. Injected via `#[Autowire(service: 'strands.client.scribe')]`. Never create new client instances.

Methods used:
- `postJson(path, data, timeout)` — synchronous JSON request to the Python agent
- `streamSse(path, data, timeout)` — SSE streaming from the Python agent (future: clinical summaries)

### Error Handling
ScribeController handles two exception types from StrandsClient:
- `AgentErrorException` — Python agent returned an error (map to 502 or 404 based on status code)
- `StrandsException` — connection failure (map to 503 Service Unavailable)

Both return a JSON response with `session_id`, `segments`, and `error` fields.

## PHPStan Level 10

- All parameters and return types must be typed
- Use `?Type` syntax for nullable types (not `Type|null`)
- Use `@param` and `@return` PHPDoc only when PHPStan cannot infer (generics, array shapes)
- Array shapes use `array{key: Type}` or `array<string, mixed>` syntax
- Empty arrays that must serialize as `{}` use `new \stdClass()` instead of `[]`

## Testing

```bash
composer test                        # PHPUnit
composer test:coverage               # PHPUnit with coverage (needs XDEBUG_MODE=coverage)
composer mutate                      # Infection mutation testing
```

### Test Structure
Tests mirror production: `tests/Unit/Controller/ScribeControllerTest.php` maps to `src/Controller/ScribeController.php`.

### Test Patterns
- Use `TestableScribeController` (extends ScribeController) to override Symfony container methods
- Mock StrandsClient and RoleInferenceService with `$this->createMock()`
- Override `getParameter()`, `render()`, `json()`, `redirectToRoute()` for unit isolation
- Test both success paths and exception handling (AgentErrorException, StrandsException)

## Validation

```bash
composer cs:check                    # PHP-CS-Fixer dry-run
composer cs:fix                      # PHP-CS-Fixer auto-fix
composer analyse                     # PHPStan level 10
composer analyse:complexity          # Cyclomatic complexity check (max 20)
composer analyse:messdetector        # PHPMD static analysis
composer validate:composer           # Composer manifest validation
composer security:audit              # Dependency vulnerability scan
composer preflight                   # All of the above in sequence
```

## Service Wiring

Symfony autowires all classes in `src/` automatically. The only manual wiring is the StrandsClient in `config/packages/strands.yaml`:
```yaml
strands:
    agents:
        scribe:
            endpoint: '%env(AGENT_ENDPOINT)%'
            timeout: 120
            connect_timeout: 30
            auth:
                driver: 'null'
```

Per-request timeouts override the default: history fetch (10s), role snapshot (5s), default (120s).
