<?php

declare(strict_types=1);

namespace App\Tests\Unit\Controller;

use App\Controller\ScribeController;
use App\Service\RoleInferenceService;
use PHPUnit\Framework\TestCase;
use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;
use StrandsPhpClient\Exceptions\AgentErrorException;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\RedirectResponse;
use Symfony\Component\HttpFoundation\Response;

final class ScribeControllerTest extends TestCase
{
    public function testHomeRedirectsToScribe(): void
    {
        $controller = $this->createController();

        $response = $controller->home();

        self::assertInstanceOf(RedirectResponse::class, $response);
        self::assertSame(302, $response->getStatusCode());
        self::assertSame('/scribe', $response->getTargetUrl());
    }

    public function testIndexRendersSessionConfigWithRoleUpdatesEnabled(): void
    {
        $controller = $this->createController([
            'kernel.environment' => 'prod',
            'kernel.project_dir' => '/tmp/nonexistent',
        ]);

        $response = $controller->index();

        self::assertInstanceOf(JsonResponse::class, $response);
        $payload = json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR);

        self::assertSame('scribe/index.html.twig', $payload['view']);
        self::assertSame('ws://localhost:48101', $payload['parameters']['ws_url']);
        self::assertSame(
            'http://localhost:48137/.well-known/mercure',
            $payload['parameters']['mercure_url'],
        );
        self::assertTrue($payload['parameters']['enable_role_updates']);
        self::assertFalse($payload['parameters']['dev_panel_enabled']);
        self::assertSame([], $payload['parameters']['scenarios']);

        $sessionId = $payload['parameters']['session_id'];
        self::assertMatchesRegularExpression('/^[0-9a-f-]{36}$/', $sessionId);
        self::assertSame(
            "scribe/session/{$sessionId}/raw",
            $payload['parameters']['mercure_topic_raw'],
        );
        self::assertSame(
            "scribe/session/{$sessionId}/roles",
            $payload['parameters']['mercure_topic_roles'],
        );
    }

    public function testIndexLoadsScenarioFixturesInDevMode(): void
    {
        $projectDir = \dirname(__DIR__, 3);
        $controller = $this->createController([
            'kernel.environment' => 'dev',
            'kernel.project_dir' => $projectDir,
        ]);

        $response = $controller->index();
        $payload = json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR);

        self::assertTrue($payload['parameters']['dev_panel_enabled']);
        self::assertNotEmpty($payload['parameters']['scenarios']);

        $firstScenario = $payload['parameters']['scenarios'][0];
        self::assertArrayHasKey('id', $firstScenario);
        self::assertArrayHasKey('name', $firstScenario);
        self::assertArrayHasKey('events', $firstScenario);
        self::assertArrayHasKey('expectedEndState', $firstScenario);
    }

    public function testIndexHandlesMissingFixtureFileInDevMode(): void
    {
        $controller = $this->createController([
            'kernel.environment' => 'dev',
            'kernel.project_dir' => '/tmp/nonexistent-project',
        ]);

        $response = $controller->index();
        $payload = json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR);

        self::assertTrue($payload['parameters']['dev_panel_enabled']);
        self::assertSame([], $payload['parameters']['scenarios']);
    }

    public function testIndexHandlesInvalidScenarioFixtureJsonInDevMode(): void
    {
        $logger = $this->createMock(LoggerInterface::class);
        $logger->expects(self::once())
            ->method('warning')
            ->with(
                'Scribe scenarios fixture is invalid JSON',
                self::arrayHasKey('path'),
            );

        $projectDir = sys_get_temp_dir() . '/ambient-scribe-invalid-' . uniqid('', true);
        $fixtureDir = $projectDir . '/tests/fixtures/scribe';
        mkdir($fixtureDir, 0777, true);
        file_put_contents($fixtureDir . '/scenarios.json', '{"scenarios": [}');

        try {
            $controller = $this->createController([
                'kernel.environment' => 'dev',
                'kernel.project_dir' => $projectDir,
            ], logger: $logger);

            $response = $controller->index();
            $payload = json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR);

            self::assertTrue($payload['parameters']['dev_panel_enabled']);
            self::assertSame([], $payload['parameters']['scenarios']);
        } finally {
            unlink($fixtureDir . '/scenarios.json');
            rmdir($fixtureDir);
            rmdir($projectDir . '/tests/fixtures');
            rmdir($projectDir . '/tests');
            rmdir($projectDir);
        }
    }

    public function testHistoryReturnsAgentPayload(): void
    {
        $client = $this->createMock(StrandsClient::class);
        $client->expects(self::once())
            ->method('postJson')
            ->with('/session/session-123/history', [], 10)
            ->willReturn([
                'session_id' => 'session-123',
                'segments' => [
                    ['speaker_id' => 'spk_0', 'text' => 'Good morning'],
                ],
            ]);

        $controller = $this->createController(client: $client);

        $response = $controller->history('session-123');

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame([
            'session_id' => 'session-123',
            'segments' => [
                ['speaker_id' => 'spk_0', 'text' => 'Good morning'],
            ],
        ], json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR));
    }

    public function testHistoryHandlesAgentErrorExceptionWith502(): void
    {
        $client = $this->createMock(StrandsClient::class);
        $client->expects(self::once())
            ->method('postJson')
            ->willThrowException(new AgentErrorException('NeMo error', statusCode: 500));

        $controller = $this->createController(client: $client);

        $response = $controller->history('abc-123');

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(502, $response->getStatusCode());
        self::assertSame([
            'session_id' => 'abc-123',
            'segments' => [],
            'error' => 'NeMo error',
        ], json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR));
    }

    public function testHistoryHandlesAgentErrorExceptionWith404(): void
    {
        $client = $this->createMock(StrandsClient::class);
        $client->expects(self::once())
            ->method('postJson')
            ->willThrowException(new AgentErrorException('Not found', statusCode: 404));

        $controller = $this->createController(client: $client);

        $response = $controller->history('abc-123');

        self::assertSame(404, $response->getStatusCode());
    }

    public function testHistoryHandlesStrandsExceptionWith503(): void
    {
        $client = $this->createMock(StrandsClient::class);
        $client->expects(self::once())
            ->method('postJson')
            ->willThrowException(new StrandsException('Connection refused'));

        $controller = $this->createController(client: $client);

        $response = $controller->history('abc-123');

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(503, $response->getStatusCode());
        self::assertSame([
            'session_id' => 'abc-123',
            'segments' => [],
            'error' => 'Agent unavailable: Connection refused',
        ], json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR));
    }

    public function testRolesReturnsCurrentMapping(): void
    {
        $roleInferenceService = $this->createMock(RoleInferenceService::class);
        $roleInferenceService->expects(self::once())
            ->method('getCurrentMapping')
            ->with('session-xyz')
            ->willReturn(['mapping' => ['spk_0' => 'DOCTOR'], 'confidence' => 0.85]);

        $controller = $this->createController(roleInferenceService: $roleInferenceService);

        $response = $controller->roles('session-xyz');

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame([
            'session_id' => 'session-xyz',
            'mapping' => ['spk_0' => 'DOCTOR'],
            'confidence' => 0.85,
        ], json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR));
    }

    public function testRolesSerializesEmptyMappingAsObject(): void
    {
        $roleInferenceService = $this->createMock(RoleInferenceService::class);
        $roleInferenceService->expects(self::once())
            ->method('getCurrentMapping')
            ->with('session-empty')
            ->willReturn(['mapping' => [], 'confidence' => 0.0]);

        $controller = $this->createController(roleInferenceService: $roleInferenceService);

        $response = $controller->roles('session-empty');

        self::assertStringContainsString('"mapping":{}', $response->getContent() ?: '');
    }

    private function createController(
        array $parameters = [],
        ?StrandsClient $client = null,
        ?RoleInferenceService $roleInferenceService = null,
        ?LoggerInterface $logger = null,
    ): TestableScribeController {
        return new TestableScribeController(
            $client ?? $this->createMock(StrandsClient::class),
            $roleInferenceService ?? $this->createMock(RoleInferenceService::class),
            $logger ?? new NullLogger(),
            $parameters + [
                'nemo_websocket_url' => 'ws://localhost:48101',
                'mercure_url' => 'http://localhost:48137/.well-known/mercure',
            ],
        );
    }
}

final class TestableScribeController extends ScribeController
{
    /**
     * @param array<string, \UnitEnum|array|string|int|float|bool|null> $parameters
     */
    public function __construct(
        StrandsClient $strandsClient,
        RoleInferenceService $roleInferenceService,
        LoggerInterface $logger,
        private readonly array $parameters,
    ) {
        parent::__construct($strandsClient, $roleInferenceService, $logger);
    }

    public function getParameter(string $name): \UnitEnum|array|string|int|float|bool|null
    {
        return $this->parameters[$name] ?? null;
    }

    public function render(
        string $view,
        array $parameters = [],
        ?Response $response = null,
    ): Response {
        return new JsonResponse([
            'view' => $view,
            'parameters' => $parameters,
        ], $response?->getStatusCode() ?? Response::HTTP_OK);
    }

    public function json(
        mixed $data,
        int $status = 200,
        array $headers = [],
        array $context = [],
    ): JsonResponse {
        return new JsonResponse($data, $status, $headers);
    }

    public function redirectToRoute(
        string $route,
        array $parameters = [],
        int $status = 302,
    ): RedirectResponse {
        $target = $route === 'scribe_index' ? '/scribe' : '/' . ltrim($route, '/');

        return new RedirectResponse($target, $status);
    }
}
