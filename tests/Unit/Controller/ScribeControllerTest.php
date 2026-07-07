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
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;
use StrandsPhpClient\Exceptions\AgentErrorException;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;
use Symfony\Component\HttpClient\Exception\TransportException;
use Symfony\Component\HttpClient\MockHttpClient;
use Symfony\Component\HttpClient\Response\MockResponse;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\RedirectResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Contracts\HttpClient\HttpClientInterface;

/**
 * Verifies the browser-facing Scribe controller without starting Symfony or Python.
 *
 * The suite focuses on what the clinician page sees: startup config, dev audio choices,
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
        self::assertSame([], $payload['parameters']['audio_fixtures']);

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
    }

    /**
     * Shows the developer audio picker is populated when a local tester opens the page in dev mode.
     *
     * @return void No payload; failure means generated WAV replay would not be available to testers.
     */
    public function testIndexLoadsAudioFixturesInDevMode(): void
    {
        [$projectDir, $fixtureDir] = $this->createAudioFixtureProject();
        $controller = $this->createController([
            'kernel.environment' => 'dev',
            'kernel.project_dir' => $projectDir,
        ]);

        try {
            $response = $controller->index();
            $payload = $this->decodeJsonResponse($response);

            self::assertTrue($payload['parameters']['dev_panel_enabled']);
            self::assertNotEmpty($payload['parameters']['audio_fixtures']);

            $firstFixture = $payload['parameters']['audio_fixtures'][0];
            self::assertSame('demo.wav', $firstFixture['filename']);
            self::assertSame('chest pain', $firstFixture['complaint']);
            self::assertSame('/scribe/demo-audio?filename=demo.wav', $firstFixture['url']);
        } finally {
            $this->removeAudioFixtureProject($projectDir, $fixtureDir);
        }
    }

    /**
     * Keeps the dev page usable when a tester has no generated audio manifest checked out.
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
        self::assertSame([], $payload['parameters']['audio_fixtures']);
    }

    /**
     * Keeps the dev page visible and logs a warning when a tester edits the audio manifest into invalid JSON.
     *
     * @return void No payload; failure means a bad fixture could hide the clinician page.
     */
    public function testIndexHandlesInvalidAudioFixtureJsonInDevMode(): void
    {
        $logger = $this->createMock(LoggerInterface::class);
        $logger->expects(self::once())
            ->method('warning')
            ->with(
                'Scribe audio fixture manifest is invalid JSON',
                self::arrayHasKey('path'),
            );

        [$projectDir, $fixtureDir] = $this->createInvalidAudioFixtureProject();

        try {
            $controller = $this->createController([
                'kernel.environment' => 'dev',
                'kernel.project_dir' => $projectDir,
            ], logger: $logger);

            $response = $controller->index();
            $payload = $this->decodeJsonResponse($response);

            self::assertTrue($payload['parameters']['dev_panel_enabled']);
            self::assertSame([], $payload['parameters']['audio_fixtures']);
        } finally {
            $this->removeAudioFixtureProject($projectDir, $fixtureDir);
        }
    }

    /**
     * Serves a known generated WAV to the browser in dev mode.
     *
     * @return void No payload; failure means Demo Audio rows cannot fetch fixture audio to stream.
     */
    public function testDemoAudioServesKnownFixtureInDevMode(): void
    {
        [$projectDir, $fixtureDir] = $this->createAudioFixtureProject();
        $controller = $this->createController([
            'kernel.environment' => 'dev',
            'kernel.project_dir' => $projectDir,
        ]);

        try {
            $request = Request::create('/scribe/demo-audio', 'GET', ['filename' => 'demo.wav']);
            $response = $controller->demoAudio($request);

            self::assertSame(200, $response->getStatusCode());
            self::assertSame('audio/wav', $response->headers->get('Content-Type'));
        } finally {
            $this->removeAudioFixtureProject($projectDir, $fixtureDir);
        }
    }

    /**
     * Rejects demo-audio requests that should not expose a WAV to the browser.
     *
     * @param string $environment App mode; prod means local fixtures must stay hidden.
     * @param array<string, string> $query Query values; empty means the UI did not select a file.
     * @return void No payload; failure means the Demo Audio route is too permissive.
     */
    #[DataProvider('demoAudioUnavailableRequestProvider')]
    public function testDemoAudioRejectsUnavailableFixtureRequests(string $environment, array $query): void
    {
        [$projectDir, $fixtureDir] = $this->createAudioFixtureProject();
        $controller = $this->createController([
            'kernel.environment' => $environment,
            'kernel.project_dir' => $projectDir,
        ]);

        try {
            $request = Request::create('/scribe/demo-audio', 'GET', $query);
            $response = $controller->demoAudio($request);

            self::assertSame(404, $response->getStatusCode());
        } finally {
            $this->removeAudioFixtureProject($projectDir, $fixtureDir);
        }
    }

    /**
     * Lists fixture-request states that should show a recoverable 404 in the UI.
     *
     * @return array<string, array{0: string, 1: array<string, string>}> Cases; empty query means no selected file.
     */
    public static function demoAudioUnavailableRequestProvider(): array
    {
        return [
            'missing filename' => ['dev', []],
            'unknown fixture' => ['dev', ['filename' => 'missing.wav']],
            'production mode' => ['prod', ['filename' => 'demo.wav']],
        ];
    }

    /**
     * Proxies post-consult summary requests through Symfony to FastAPI.
     *
     * @return void No payload; failure means automatic summary would hit an app-origin 404.
     */
    public function testSummaryProxyForwardsToAgent(): void
    {
        $sessionId = '00000000-0000-4000-8000-000000000099';
        $seenRequests = [];
        $httpClient = $this->createSummaryProxyHttpClient($sessionId, $seenRequests);
        $controller = $this->createController(httpClient: $httpClient, agentEndpoint: 'http://agent.test');
        $request = $this->createJsonPostRequest("/session/{$sessionId}/summary", [
            'segments' => [
                ['speaker_id' => 'spk_1', 'text' => 'I have a rash.', 'role' => 'PATIENT'],
            ],
        ]);
        $response = $controller->summary($sessionId, $request);

        self::assertSame(200, $response->getStatusCode());
        self::assertSame('Session Summary', $this->decodeJsonResponse($response)['title']);
        self::assertCount(1, $seenRequests);
        self::assertSame('POST', $seenRequests[0]['method']);
        self::assertSame("http://agent.test/session/{$sessionId}/summary", $seenRequests[0]['url']);
        self::assertStringContainsString('I have a rash.', $seenRequests[0]['options']['body']);
    }

    /**
     * Proxies post-stop correction requests before the summary is generated.
     *
     * @return void No payload; failure means the browser cannot create corrected transcript rows.
     */
    public function testCorrectionProxyForwardsToAgent(): void
    {
        $sessionId = '00000000-0000-4000-8000-000000000299';
        $seenRequests = [];
        $httpClient = new MockHttpClient(
            responseFactory: static function (string $method, string $url, array $options) use (&$seenRequests, $sessionId): MockResponse {
                $seenRequests[] = ['method' => $method, 'url' => $url, 'options' => $options];

                return new MockResponse(
                    body: json_encode([
                        'session_id' => $sessionId,
                        'status' => 'ready',
                        'segments' => 2,
                    ], JSON_THROW_ON_ERROR),
                    info: ['http_code' => 200, 'response_headers' => ['content-type' => 'application/json']],
                );
            },
            baseUri: 'http://agent.test',
        );
        $controller = $this->createController(httpClient: $httpClient, agentEndpoint: 'http://agent.test');
        $request = $this->createJsonPostRequest("/session/{$sessionId}/correction", [
            'segments' => [
                ['speaker_id' => 'spk_1', 'text' => 'Live preview text.', 'role' => 'PATIENT'],
            ],
        ]);
        $response = $controller->correction($sessionId, $request);

        self::assertSame(200, $response->getStatusCode());
        self::assertSame('ready', $this->decodeJsonResponse($response)['status']);
        self::assertCount(1, $seenRequests);
        self::assertSame('POST', $seenRequests[0]['method']);
        self::assertSame("http://agent.test/session/{$sessionId}/correction", $seenRequests[0]['url']);
        self::assertStringContainsString('Live preview text.', $seenRequests[0]['options']['body']);
    }

    /**
     * Proxies corrected transcript reads for the summary Transcript tab.
     *
     * @return void No payload; failure means the tab cannot show the rows the note used.
     */
    public function testCorrectedTranscriptProxyForwardsToAgent(): void
    {
        $sessionId = '00000000-0000-4000-8000-000000000399';
        $seenRequests = [];
        $httpClient = new MockHttpClient(
            responseFactory: static function (string $method, string $url, array $options) use (&$seenRequests, $sessionId): MockResponse {
                $seenRequests[] = ['method' => $method, 'url' => $url, 'options' => $options];

                return new MockResponse(
                    body: json_encode([
                        'session_id' => $sessionId,
                        'source' => 'corrected_segments',
                        'segments' => [
                            [
                                'segment_id' => 'corrected-0001',
                                'role' => 'PATIENT',
                                'text' => 'My skin is quite red.',
                                'start' => 3.1,
                                'end' => 4.2,
                            ],
                        ],
                    ], JSON_THROW_ON_ERROR),
                    info: ['http_code' => 200, 'response_headers' => ['content-type' => 'application/json']],
                );
            },
            baseUri: 'http://agent.test',
        );
        $controller = $this->createController(httpClient: $httpClient, agentEndpoint: 'http://agent.test');
        $response = $controller->correctedTranscript($sessionId);

        self::assertSame(200, $response->getStatusCode());
        self::assertSame('corrected-0001', $this->decodeJsonResponse($response)['segments'][0]['segment_id']);
        self::assertCount(1, $seenRequests);
        self::assertSame('GET', $seenRequests[0]['method']);
        self::assertSame("http://agent.test/session/{$sessionId}/corrected-transcript", $seenRequests[0]['url']);
    }

    /**
     * Rejects corrected transcript reads for malformed session IDs before any agent call.
     *
     * @return void No payload; failure means invalid IDs would reach FastAPI.
     */
    public function testCorrectedTranscriptProxyRejectsInvalidSessionId(): void
    {
        $seenRequests = [];
        $httpClient = new MockHttpClient(
            responseFactory: static function (string $method, string $url, array $options) use (&$seenRequests): MockResponse {
                $seenRequests[] = ['method' => $method, 'url' => $url];

                return new MockResponse(body: '{}', info: ['http_code' => 200]);
            },
            baseUri: 'http://agent.test',
        );
        $controller = $this->createController(httpClient: $httpClient, agentEndpoint: 'http://agent.test');
        $response = $controller->correctedTranscript('not-a-uuid');

        self::assertSame(400, $response->getStatusCode());
        self::assertCount(0, $seenRequests);
    }

    /**
     * Proxies manual role corrections through Symfony so the browser avoids FastAPI CORS.
     *
     * @return void No payload; failure means clicked speaker labels may not be protected server-side.
     */
    public function testRoleOverrideProxyForwardsToAgent(): void
    {
        $sessionId = '00000000-0000-4000-8000-000000000199';
        $seenRequests = [];
        $httpClient = new MockHttpClient(
            responseFactory: static function (string $method, string $url, array $options) use (&$seenRequests): MockResponse {
                $seenRequests[] = ['method' => $method, 'url' => $url, 'options' => $options];

                return new MockResponse(
                    body: json_encode([
                        'status' => 'ok',
                        'mapping' => ['spk_0' => 'DOCTOR'],
                    ], JSON_THROW_ON_ERROR),
                    info: ['http_code' => 200, 'response_headers' => ['content-type' => 'application/json']],
                );
            },
            baseUri: 'http://agent.test',
        );
        $controller = $this->createController(httpClient: $httpClient, agentEndpoint: 'http://agent.test');
        $request = $this->createJsonPostRequest("/scribe/{$sessionId}/roles/override", [
            'speaker_id' => 'spk_0',
            'role' => 'DOCTOR',
        ]);
        $response = $controller->rolesOverride($sessionId, $request);

        self::assertSame(200, $response->getStatusCode());
        self::assertSame(['spk_0' => 'DOCTOR'], $this->decodeJsonResponse($response)['mapping']);
        self::assertCount(1, $seenRequests);
        self::assertSame('POST', $seenRequests[0]['method']);
        self::assertSame("http://agent.test/session/{$sessionId}/roles/override", $seenRequests[0]['url']);
        self::assertStringContainsString('"speaker_id":"spk_0"', $seenRequests[0]['options']['body']);
    }

    /**
     * Converts upstream HTML errors into JSON so the browser does not show a parser exception.
     *
     * @return void No payload; failure means users could still see `Unexpected token '<'`.
     */
    public function testSummaryProxyTurnsNonJsonAgentErrorIntoJson(): void
    {
        $sessionId = '00000000-0000-4000-8000-000000000099';
        $httpClient = new MockHttpClient(
            new MockResponse('<br /><b>Warning</b>', ['http_code' => 502]),
            'http://agent.test',
        );
        $controller = $this->createController(httpClient: $httpClient, agentEndpoint: 'http://agent.test');

        $request = Request::create("/session/{$sessionId}/summary", 'POST');
        $response = $controller->summary($sessionId, $request);

        self::assertSame(502, $response->getStatusCode());
        self::assertSame([
            'detail' => 'Summary generation failed',
        ], $this->decodeJsonResponse($response));
    }

    /**
     * Converts summary transport failures into JSON the browser can render.
     *
     * @return void No payload; failure means agent outages could still leak as HTML or uncaught errors.
     * @throws TransportException When the mock client simulates an unreachable summary service.
     */
    public function testSummaryProxyMapsTransportFailureToJson(): void
    {
        $sessionId = '00000000-0000-4000-8000-000000000099';
        $httpClient = new MockHttpClient(
            static fn (): never => throw new TransportException('Connection refused'),
            'http://agent.test',
        );
        $controller = $this->createController(httpClient: $httpClient, agentEndpoint: 'http://agent.test');

        $request = Request::create("/session/{$sessionId}/summary", 'POST');
        $response = $controller->summary($sessionId, $request);

        self::assertSame(503, $response->getStatusCode());
        self::assertStringContainsString(
            'Summary service unavailable',
            $this->decodeJsonResponse($response)['detail'],
        );
    }

    /**
     * Converts correction transport failures into JSON so the summary can still fall back.
     *
     * @return void No payload; failure means agent outages could stop the summary flow.
     * @throws TransportException When the mock client simulates an unreachable correction service.
     */
    public function testCorrectionProxyMapsTransportFailureToJson(): void
    {
        $sessionId = '00000000-0000-4000-8000-000000000299';
        $httpClient = new MockHttpClient(
            static fn (): never => throw new TransportException('Connection refused'),
            'http://agent.test',
        );
        $controller = $this->createController(httpClient: $httpClient, agentEndpoint: 'http://agent.test');

        $request = Request::create("/session/{$sessionId}/correction", 'POST');
        $response = $controller->correction($sessionId, $request);

        self::assertSame(503, $response->getStatusCode());
        self::assertStringContainsString(
            'Correction service unavailable',
            $this->decodeJsonResponse($response)['detail'],
        );
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
     * @param HttpClientInterface|null $httpClient Null creates a mock so summary calls stay local.
     * @param string $agentEndpoint FastAPI base URL used by same-origin proxy tests; empty would make bad URLs.
     * @return TestableScribeController Controller double that exposes the same page flow with deterministic responses.
     */
    private function createController(
        array $parameters = [],
        ?StrandsClient $client = null,
        ?RoleInferenceService $roleInferenceService = null,
        ?LoggerInterface $logger = null,
        ?HttpClientInterface $httpClient = null,
        string $agentEndpoint = 'http://agent.test',
    ): TestableScribeController {
        return new TestableScribeController(
            $client ?? $this->createStub(StrandsClient::class),
            $roleInferenceService ?? $this->createStub(RoleInferenceService::class),
            $logger ?? new NullLogger(),
            $httpClient ?? new MockHttpClient(),
            $agentEndpoint,
            $parameters + [
                'nemo_websocket_url' => 'ws://localhost:48101',
                'mercure_url' => 'http://localhost:48137/.well-known/mercure',
            ],
        );
    }

    /**
     * Creates a mock FastAPI summary response and records the proxied request.
     *
     * @param string $sessionId Summary session UUID; empty would make the response unusable.
     * @param array<int, array<string, mixed>> $seenRequests Request log; empty before Summarise is clicked.
     * @return MockHttpClient Client that returns a successful summary payload.
     */
    private function createSummaryProxyHttpClient(string $sessionId, array &$seenRequests): MockHttpClient
    {
        return new MockHttpClient(
            responseFactory: static function (string $method, string $url, array $options) use (&$seenRequests, $sessionId): MockResponse {
                $seenRequests[] = ['method' => $method, 'url' => $url, 'options' => $options];

                return new MockResponse(
                    body: json_encode([
                        'session_id' => $sessionId,
                        'title' => 'Session Summary',
                        'sections' => [],
                    ], JSON_THROW_ON_ERROR),
                    info: ['http_code' => 200, 'response_headers' => ['content-type' => 'application/json']],
                );
            },
            baseUri: 'http://agent.test',
        );
    }

    /**
     * Builds a JSON POST request like the browser sends to same-origin proxies.
     *
     * @param string $proxyRoutePath Proxy URI; empty would not reach a controller route.
     * @param array<string, mixed> $payload Browser payload; empty means FastAPI should use stored state.
     * @return Request JSON request body for summary generation.
     */
    private function createJsonPostRequest(string $proxyRoutePath, array $payload): Request
    {
        return Request::create(
            uri: $proxyRoutePath,
            method: 'POST',
            content: json_encode($payload, JSON_THROW_ON_ERROR),
        );
    }

    /**
     * Creates a temporary project with one generated WAV fixture.
     *
     * @return array{0: string, 1: string} Project and fixture paths; empty never occurs because temp paths are created.
     */
    private function createAudioFixtureProject(): array
    {
        $projectDir = sys_get_temp_dir() . '/ambient-scribe-audio-' . uniqid(prefix: '', more_entropy: true);
        $fixtureDir = $projectDir . '/tests/fixtures/audio';
        mkdir(directory: $fixtureDir, permissions: 0777, recursive: true);
        file_put_contents(filename: $fixtureDir . '/demo.wav', data: 'RIFFdemo');
        file_put_contents(
            filename: $fixtureDir . '/generated-manifest.json',
            data: json_encode([
                [
                    'filename' => 'demo.wav',
                    'complaint' => 'chest pain',
                    'edge_case' => 'two speakers',
                    'speakers' => ['DOCTOR', 'PATIENT'],
                ],
            ], JSON_THROW_ON_ERROR),
        );

        return [$projectDir, $fixtureDir];
    }

    /**
     * Creates a broken audio manifest to mimic a tester editing fixture metadata into invalid JSON.
     *
     * @return array{0: string, 1: string} Project and fixture paths; empty never occurs because temp paths are created.
     */
    private function createInvalidAudioFixtureProject(): array
    {
        $projectDir = sys_get_temp_dir() . '/ambient-scribe-invalid-audio-' . uniqid(prefix: '', more_entropy: true);
        $fixtureDir = $projectDir . '/tests/fixtures/audio';
        mkdir(directory: $fixtureDir, permissions: 0777, recursive: true);
        file_put_contents(filename: $fixtureDir . '/generated-manifest.json', data: '[}');

        return [$projectDir, $fixtureDir];
    }

    /**
     * Removes temporary audio fixtures after dev audio tests finish.
     *
     * @param string $projectDir Temporary project root; empty would risk deleting the wrong local path.
     * @param string $fixtureDir Temporary fixture folder; empty would leave test files behind.
     * @return void No payload; the local filesystem is restored for the next controller test.
     */
    private function removeAudioFixtureProject(string $projectDir, string $fixtureDir): void
    {
        // Test projects may or may not include a dummy WAV depending on the fixture case.
        if (is_file($fixtureDir . '/demo.wav')) {
            unlink(filename: $fixtureDir . '/demo.wav');
        }

        // Every temporary audio project includes a generated manifest file.
        if (is_file($fixtureDir . '/generated-manifest.json')) {
            unlink(filename: $fixtureDir . '/generated-manifest.json');
        }

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
     * @param HttpClientInterface $httpClient Stubbed HTTP client for the summary proxy route.
     * @param string $agentEndpoint FastAPI base URL; empty would make summary proxy URLs invalid.
     * @param array<string, \UnitEnum|array|string|int|float|bool|null> $parameters Page config; empty means helpers can return null.
     */
    public function __construct(
        StrandsClient $strandsClient,
        RoleInferenceService $roleInferenceService,
        LoggerInterface $logger,
        HttpClientInterface $httpClient,
        string $agentEndpoint,
        private readonly array $parameters,
    ) {
        parent::__construct(
            strandsClient: $strandsClient,
            roleInferenceService: $roleInferenceService,
            logger: $logger,
            httpClient: $httpClient,
            agentEndpoint: $agentEndpoint,
        );
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
