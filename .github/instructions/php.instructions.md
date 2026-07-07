---
applyTo: '**/*.php'
---

# PHP Conventions - Ambient Scribe

## Language & Framework

- PHP >=8.3, Symfony 6.4
- Every file starts with `declare(strict_types=1);`
- PSR-4 autoloading: `App\` -> `src/`, `App\Tests\` -> `tests/`

## Style (enforced by PHP-CS-Fixer)

- PSR-12 base
- 4-space indentation
- Single quotes for strings (unless interpolation needed)
- Short array syntax `[]`
- Ordered imports (alphabetical)
- Trailing commas in multiline arrays, arguments, and parameters
- No closing `?>` tag
- One blank line before `return` statements in methods with logic above

## Type System (PHPStan level 10)

- All method parameters and return types must be typed
- Use union types over `mixed` where possible
- Use `@param` and `@return` PHPDoc only when PHPStan can't infer the type (generics, array shapes)
- Nullable types use `?Type` syntax, not `Type|null`
- Use `readonly` on constructor-promoted properties that don't change

## Naming

- `PascalCase` for classes and interfaces
- `camelCase` for methods, properties, and variables
- `UPPER_SNAKE_CASE` for constants
- Test methods: `testMethodNameDescribesExpectedBehavior()`

## Symfony Patterns

- Constructor injection with `readonly` promoted properties
- Use `#[Autowire(service: 'name')]` for named services (see `config/packages/strands.yaml`)
- Controller actions use PHP 8 attributes for routing: `#[Route('/path', methods: ['POST'])]`
- Configuration in `config/packages/` YAML files, not PHP

## Service Wiring

- The `StrandsClient` is wired as `strands.client.scribe` in `config/packages/strands.yaml`
- Injected via `#[Autowire(service: 'strands.client.scribe')]` in controllers/services
- Don't create new client instances - use the wired service

## Testing

- PHPUnit 11 with `phpunit.xml.dist`
- Tests mirror production structure: `tests/Unit/Controller/ScribeControllerTest.php`
- Mock external dependencies (HTTP clients, event dispatchers, Mercure publishers)
- Use `$this->createMock()` for interfaces, `$this->createStub()` for simple stubs
- Coverage minimum: 80%

## Validation

```bash
composer cs:check        # Style check (dry-run)
composer cs:fix          # Auto-fix style
composer analyse         # PHPStan level 10
composer test            # PHPUnit
```
