<?php

declare(strict_types=1);

namespace App\Tests\Unit\Controller;

use App\Controller\ScribeController;
use App\Service\RoleInferenceService;
use PHPUnit\Framework\TestCase;
use StrandsPhpClient\StrandsClient;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Response;

final class ScribeControllerTest extends TestCase
{
    public function testIndexRendersSessionConfigWithRoleUpdatesEnabled(): void
    {
        $client = $this->createMock(StrandsClient::class);
        $roleInferenceService = $this->createMock(RoleInferenceService::class);
        $controller = new TestableScribeController(
            $client,
            $roleInferenceService,
            [
                'nemo_websocket_url' => 'ws://localhost:48101',
                'mercure_url' => 'http://localhost:48137/.well-known/mercure',
                'kernel.environment' => 'prod',
                'kernel.project_dir' => '/tmp/nonexistent',
            ],
        );

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
        $client = $this->createMock(StrandsClient::class);
        $roleInferenceService = $this->createMock(RoleInferenceService::class);

        // Use the real project dir so the fixture file is found
        $projectDir = \dirname(__DIR__, 3);
        $controller = new TestableScribeController(
            $client,
            $roleInferenceService,
            [
                'nemo_websocket_url' => 'ws://localhost:48101',
                'mercure_url' => 'http://localhost:48137/.well-known/mercure',
                'kernel.environment' => 'dev',
                'kernel.project_dir' => $projectDir,
            ],
        );

        $response = $controller->index();
        $payload = json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR);

        self::assertTrue($payload['parameters']['dev_panel_enabled']);
        self::assertNotEmpty($payload['parameters']['scenarios']);

        // Verify scenario structure
        $firstScenario = $payload['parameters']['scenarios'][0];
        self::assertArrayHasKey('id', $firstScenario);
        self::assertArrayHasKey('name', $firstScenario);
        self::assertArrayHasKey('events', $firstScenario);
        self::assertArrayHasKey('expectedEndState', $firstScenario);
    }

    public function testIndexHandlesMissingFixtureFileInDevMode(): void
    {
        $client = $this->createMock(StrandsClient::class);
        $roleInferenceService = $this->createMock(RoleInferenceService::class);
        $controller = new TestableScribeController(
            $client,
            $roleInferenceService,
            [
                'nemo_websocket_url' => 'ws://localhost:48101',
                'mercure_url' => 'http://localhost:48137/.well-known/mercure',
                'kernel.environment' => 'dev',
                'kernel.project_dir' => '/tmp/nonexistent-project',
            ],
        );

        $response = $controller->index();
        $payload = json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR);

        self::assertTrue($payload['parameters']['dev_panel_enabled']);
        self::assertSame([], $payload['parameters']['scenarios']);
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

        $roleInferenceService = $this->createMock(RoleInferenceService::class);
        $controller = new TestableScribeController($client, $roleInferenceService, []);

        $response = $controller->history('session-123');

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame([
            'session_id' => 'session-123',
            'segments' => [
                ['speaker_id' => 'spk_0', 'text' => 'Good morning'],
            ],
        ], json_decode($response->getContent() ?: '', true, 512, JSON_THROW_ON_ERROR));
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
        private readonly array $parameters,
    ) {
        parent::__construct($strandsClient, $roleInferenceService);
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
}
