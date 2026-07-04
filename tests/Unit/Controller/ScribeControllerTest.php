<?php

/**
 * Unit coverage for the Symfony controller that drives the `/scribe` page.
 *
 * These tests replace Twig and JSON helpers with predictable responses so they can check what the browser
 * would receive. Use them when page config, transcript history, or role snapshot behavior changes.
 */

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

/**
 * Verifies the browser-facing Scribe controller without starting Symfony or Python.
 *
 * The suite focuses on what the clinician page sees: startup config, dev scenario choices,
 * history JSON, role JSON, and fallback responses when the Python agent cannot answer.
 */
final class ScribeControllerTest extends TestCase
{
    /**
     * Ensures users landing on `/` are sent to the actual scribe workspace.
     *
     * @return void No payload; failure means the browser would not reach `/scribe`.
     */
    public function testHomeRedirectsToScribe(): void
    {
        $controller = $this->createController();

        $response = $controller->home();

        self::assertInstanceOf(RedirectResponse::class, $response);
        self::assertSame(302, $response->getStatusCode());
        self::assertSame('/scribe', $response->getTargetUrl());
    }

    /**
     * Confirms the page gets fresh session and transport settings when a clinician opens `/scribe`.
     *
     * @return void No payload; failure means the page would start with broken connection settings.
     */
    public function testIndexRendersSessionConfigWithRoleUpdatesEnabled(): void
    {
        $controller = $this->createController([
            'kernel.environment' => 'prod',
            'kernel.project_dir' => '/tmp/nonexistent',
        ]);

        $response = $controller->index();

        $payload = $this->decodeJsonResponse($response);

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
        $this->assertMercureTopics($payload['parameters'], $sessionId);
    }

    /**
     * Checks every page topic the browser subscribes to for one consultation.
     *
     * @param array<string, mixed> $parameters Render parameters; empty would leave the page without SSE topics.
     * @param string $sessionId Session UUID; empty would produce topics no browser session can match.
     * @return void No payload; failure means the UI would subscribe to a wrong Mercure topic.
     */
    private function assertMercureTopics(array $parameters, string $sessionId): void
    {
        self::assertSame(
            "scribe/session/{$sessionId}/raw",
            $parameters['mercure_topic_raw'],
        );
        self::assertSame(
            "scribe/session/{$sessionId}/roles",
            $parameters['mercure_topic_roles'],
        );
        self::assertSame(
            "scribe/session/{$sessionId}/summary",
            $parameters['mercure_topic_summary'],
        );
        self::assertSame(
            "scribe/session/{$sessionId}/hints",
            $parameters['mercure_topic_hints'],
        );
    }

    /**
     * Shows the developer scenario picker is populated when a local tester opens the page in dev mode.
     *
     * @return void No payload; failure means fixture replay would not be available to testers.
     */
    public function testIndexLoadsScenarioFixturesInDevMode(): void
    {
        $projectDir = \dirname(__DIR__, 3);
        $controller = $this->createController([
            'kernel.environment' => 'dev',
            'kernel.project_dir' => $projectDir,
        ]);

        $response = $controller->index();
        $payload = $this->decodeJsonResponse($response);

        self::assertTrue($payload['parameters']['dev_panel_enabled']);
        self::assertNotEmpty($payload['parameters']['scenarios']);

        $firstScenario = $payload['parameters']['scenarios'][0];
        self::assertArrayHasKey('id', $firstScenario);
        self::assertArrayHasKey('name', $firstScenario);
        self::assertArrayHasKey('events', $firstScenario);
        self::assertArrayHasKey('expectedEndState', $firstScenario);
    }

    /**
     * Keeps the dev page usable when a tester has no scenario fixture file checked out.
     *
     * @return void No payload; failure means the developer panel could break local page loads.
     */
    public function testIndexHandlesMissingFixtureFileInDevMode(): void
    {
        $controller = $this->createController([
            'kernel.environment' => 'dev',
            'kernel.project_dir' => '/tmp/nonexistent-project',
        ]);

        $response = $controller->index();
        $payload = $this->decodeJsonResponse($response);

        self::assertTrue($payload['parameters']['dev_panel_enabled']);
        self::assertSame([], $payload['parameters']['scenarios']);
    }

    /**
     * Keeps the dev page visible and logs a warning when a tester edits scenarios into invalid JSON.
     *
     * @return void No payload; failure means a bad fixture could hide the clinician page.
     */
    public function testIndexHandlesInvalidScenarioFixtureJsonInDevMode(): void
    {
        $logger = $this->createMock(LoggerInterface::class);
        $logger->expects(self::once())
            ->method('warning')
            ->with(
                'Scribe scenarios fixture is invalid JSON',
                self::arrayHasKey('path'),
            );

        [$projectDir, $fixtureDir] = $this->createInvalidScenarioFixture();

        try {
            $controller = $this->createController([
                'kernel.environment' => 'dev',
                'kernel.project_dir' => $projectDir,
            ], logger: $logger);

            $response = $controller->index();
            $payload = $this->decodeJsonResponse($response);

            self::assertTrue($payload['parameters']['dev_panel_enabled']);
            self::assertSame([], $payload['parameters']['scenarios']);
        } finally {
            $this->removeInvalidScenarioFixture($projectDir, $fixtureDir);
        }
    }

    /**
     * Proves completed transcript history is passed through to the browser unchanged.
     *
     * @return void No payload; failure means the history endpoint changed what users can review.
     */
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
        ], $this->decodeJsonResponse($response));
    }

    /**
     * Maps a server-side Python agent error to bad gateway so the page can show a recoverable failure.
     *
     * @return void No payload; failure means serious agent errors would be reported with the wrong status.
     */
    public function testHistoryMapsServerAgentErrorToBadGateway(): void
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
        ], $this->decodeJsonResponse($response));
    }

    /**
     * Keeps a missing Python session as not found so the UI does not imply a transcript still exists.
     *
     * @return void No payload; failure means old history links could look like temporary outages.
     */
    public function testHistoryMapsMissingAgentSessionToNotFound(): void
    {
        $client = $this->createMock(StrandsClient::class);
        $client->expects(self::once())
            ->method('postJson')
            ->willThrowException(new AgentErrorException('Not found', statusCode: 404));

        $controller = $this->createController(client: $client);

        $response = $controller->history('abc-123');

        self::assertSame(404, $response->getStatusCode());
    }

    /**
     * Turns transport failures into service unavailable so the browser can retry later.
     *
     * @return void No payload; failure means connection loss would not be visible as a retryable outage.
     */
    public function testHistoryMapsTransportFailureToServiceUnavailable(): void
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
        ], $this->decodeJsonResponse($response));
    }

    /**
     * Confirms the role snapshot endpoint returns current DOCTOR/PATIENT labels for visible speakers.
     *
     * @return void No payload; failure means speaker labels would drift from Python's latest mapping.
     */
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
        ], $this->decodeJsonResponse($response));
    }

    /**
     * Ensures no-role-yet snapshots serialize as `{}` so the UI keeps speaker labels stable.
     *
     * @return void No payload; failure means the browser might read an empty mapping as a list.
     */
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

    /**
     * Decodes controller JSON in the same shape the browser would consume.
     *
     * @param JsonResponse $response Controller response; empty content means the page received no JSON payload.
     * @return array<string, mixed> Decoded JSON object; empty means the endpoint returned no visible fields.
     */
    private function decodeJsonResponse(JsonResponse $response): array
    {
        return json_decode(
            json: $response->getContent() ?: '',
            associative: true,
            depth: 512,
            flags: JSON_THROW_ON_ERROR,
        );
    }

    /**
     * Builds a controller whose framework helpers return test-readable responses.
     *
     * @param array<string, \UnitEnum|array|string|int|float|bool|null> $parameters Page config overrides; empty uses local UI URLs.
     * @param StrandsClient|null $client Null creates a stub so history calls never reach Python.
     * @param RoleInferenceService|null $roleInferenceService Null creates a stub so role polling stays local.
     * @param LoggerInterface|null $logger Null uses a no-op logger; tests inject one when warning output matters.
     * @return TestableScribeController Controller double that exposes the same page flow with deterministic responses.
     */
    private function createController(
        array $parameters = [],
        ?StrandsClient $client = null,
        ?RoleInferenceService $roleInferenceService = null,
        ?LoggerInterface $logger = null,
    ): TestableScribeController {
        return new TestableScribeController(
            $client ?? $this->createStub(StrandsClient::class),
            $roleInferenceService ?? $this->createStub(RoleInferenceService::class),
            $logger ?? new NullLogger(),
            $parameters + [
                'nemo_websocket_url' => 'ws://localhost:48101',
                'mercure_url' => 'http://localhost:48137/.well-known/mercure',
            ],
        );
    }

    /**
     * Creates a broken scenario file to mimic a tester editing fixtures into invalid JSON.
     *
     * @return array{0: string, 1: string} Project and fixture paths; empty never occurs because temp paths are created.
     */
    private function createInvalidScenarioFixture(): array
    {
        $projectDir = sys_get_temp_dir() . '/ambient-scribe-invalid-' . uniqid(prefix: '', more_entropy: true);
        $fixtureDir = $projectDir . '/tests/fixtures/scribe';
        mkdir(directory: $fixtureDir, permissions: 0777, recursive: true);
        file_put_contents(filename: $fixtureDir . '/scenarios.json', data: '{"scenarios": [}');

        return [$projectDir, $fixtureDir];
    }

    /**
     * Removes the temporary broken fixture after the invalid-JSON page test finishes.
     *
     * @param string $projectDir Temporary project root; empty would risk deleting the wrong local path.
     * @param string $fixtureDir Temporary fixture folder; empty would leave test files behind.
     * @return void No payload; the local filesystem is restored for the next controller test.
     */
    private function removeInvalidScenarioFixture(string $projectDir, string $fixtureDir): void
    {
        unlink(filename: $fixtureDir . '/scenarios.json');
        rmdir(directory: $fixtureDir);
        rmdir(directory: $projectDir . '/tests/fixtures');
        rmdir(directory: $projectDir . '/tests');
        rmdir(directory: $projectDir);
    }
}

/**
 * Test double for the Scribe controller's framework helpers.
 *
 * It turns Twig rendering, redirects, and JSON helper calls into plain Symfony responses the tests can inspect.
 * Use it only in controller unit tests; the real app still runs through Symfony's controller base class.
 */
final class TestableScribeController extends ScribeController
{
    /**
     * Builds the test controller with page settings a browser would receive from Symfony.
     *
     * @param StrandsClient $strandsClient Stubbed Python client; never sends live audio or network calls.
     * @param RoleInferenceService $roleInferenceService Stubbed role service; null is not expected in this double.
     * @param LoggerInterface $logger Captures fixture warnings that matter to the developer panel.
     * @param array<string, \UnitEnum|array|string|int|float|bool|null> $parameters Page config; empty means helpers can return null.
     */
    public function __construct(
        StrandsClient $strandsClient,
        RoleInferenceService $roleInferenceService,
        LoggerInterface $logger,
        private readonly array $parameters,
    ) {
        parent::__construct($strandsClient, $roleInferenceService, $logger);
    }

    /**
     * Returns the page setting requested during `/scribe` rendering.
     *
     * @param string $name Parameter key such as `mercure_url`; empty means no matching page config exists.
     * @return \UnitEnum|array|string|int|float|bool|null Config value; null means the test did not provide it.
     */
    protected function getParameter(string $name): \UnitEnum|array|string|int|float|bool|null
    {
        // Missing test config mirrors an unset Symfony parameter, which would leave the page value absent.
        return $this->parameters[$name] ?? null;
    }

    /**
     * Converts a Twig render into JSON so tests can inspect the browser-visible page parameters.
     *
     * @param string $view Template name; empty would make the test response identify no page.
     * @param array<string, mixed> $parameters Page parameters; empty means the browser would get no setup data.
     * @param Response|null $response Optional base response; null means the test uses HTTP 200.
     * @return Response JSON response containing the template and parameters the UI would receive.
     */
    protected function render(
        string $view,
        array $parameters = [],
        ?Response $response = null,
    ): Response {
        return new JsonResponse([
            'view' => $view,
            'parameters' => $parameters,
        ], status: $response?->getStatusCode() ?? Response::HTTP_OK);
    }

    /**
     * Builds controller JSON responses without needing Symfony's container.
     *
     * @param mixed $visiblePayload Payload the browser would parse; null means the endpoint intentionally returns JSON null.
     * @param int $status HTTP status shown to the browser; 200 means the page can treat the response as usable.
     * @param array<string, string> $headers Extra headers for browser behavior; empty keeps Symfony defaults.
     * @param array<string, mixed> $context Ignored serializer context; empty is the normal controller path here.
     * @return JsonResponse Browser-visible JSON payload for the controller test.
     */
    protected function json(
        mixed $visiblePayload,
        int $status = 200,
        array $headers = [],
        array $context = [],
    ): JsonResponse {
        $jsonResponse = new JsonResponse();
        $jsonResponse->setData($visiblePayload);
        $jsonResponse->setStatusCode($status);
        $jsonResponse->headers->add($headers);

        return $jsonResponse;
    }

    /**
     * Converts Symfony route redirects into concrete URLs the test can assert.
     *
     * @param string $route Route name chosen by the controller; empty would redirect to `/`.
     * @param array<string, mixed> $parameters Route parameters; empty means the target has no dynamic parts.
     * @param int $status Redirect status the browser receives; 302 means a normal temporary redirect.
     * @return RedirectResponse Redirect response that shows where the browser would land.
     */
    protected function redirectToRoute(
        string $route,
        array $parameters = [],
        int $status = 302,
    ): RedirectResponse {
        $target = $route === 'scribe_index' ? '/scribe' : '/' . ltrim($route, '/');

        return new RedirectResponse($target, $status);
    }
}
