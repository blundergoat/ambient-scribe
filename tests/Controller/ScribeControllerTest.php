<?php

declare(strict_types=1);

namespace App\Tests\Controller;

use App\Controller\ScribeController;
use App\Service\RoleInferenceResult;
use App\Service\RoleInferenceService;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;
use Psr\Container\ContainerInterface;
use StrandsPhpClient\Exceptions\AgentErrorException;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;
use Symfony\Component\DependencyInjection\ParameterBag\ParameterBagInterface;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\RedirectResponse;
use Symfony\Component\HttpFoundation\StreamedJsonResponse;
use Symfony\Component\Routing\Generator\UrlGeneratorInterface;
use Twig\Environment as TwigEnvironment;

class ScribeControllerTest extends TestCase
{
    private MockObject&StrandsClient $strandsClient;
    private MockObject&RoleInferenceService $roleInferenceService;
    private MockObject&TwigEnvironment $twig;
    private ScribeController $controller;

    protected function setUp(): void
    {
        $this->strandsClient = $this->createMock(StrandsClient::class);
        $this->roleInferenceService = $this->createMock(RoleInferenceService::class);

        $this->controller = new ScribeController(
            $this->strandsClient,
            $this->roleInferenceService,
        );

        $parameterBag = $this->createMock(ParameterBagInterface::class);
        $parameterBag->method('get')
            ->willReturnMap([
                ['nemo_websocket_url', 'ws://test:8001'],
                ['mercure_url', 'http://test/.well-known/mercure'],
            ]);

        $this->twig = $this->createMock(TwigEnvironment::class);
        $this->twig->method('render')->willReturn('<html>test</html>');

        $router = $this->createMock(UrlGeneratorInterface::class);
        $router->method('generate')->willReturn('/scribe');

        $container = $this->createMock(ContainerInterface::class);
        $container->method('has')
            ->willReturnCallback(fn (string $id): bool => \in_array($id, ['parameter_bag', 'twig', 'router'], true));
        $container->method('get')
            ->willReturnCallback(fn (string $id) => match ($id) {
                'parameter_bag' => $parameterBag,
                'twig' => $this->twig,
                'router' => $router,
                default => null,
            });

        $this->controller->setContainer($container);
    }

    public function testHomeRedirectsToScribe(): void
    {
        $response = $this->controller->home();

        self::assertInstanceOf(RedirectResponse::class, $response);
        self::assertSame(302, $response->getStatusCode());
        self::assertStringContainsString('/scribe', $response->getTargetUrl());
    }

    public function testIndexRendersTemplateWithSessionConfig(): void
    {
        $capturedParams = [];
        $this->twig->expects(self::once())
            ->method('render')
            ->with(
                'scribe/index.html.twig',
                self::callback(function (array $params) use (&$capturedParams): bool {
                    $capturedParams = $params;

                    return true;
                }),
            )
            ->willReturn('<html>scribe</html>');

        $response = $this->controller->index();

        self::assertSame(200, $response->getStatusCode());
        self::assertArrayHasKey('session_id', $capturedParams);
        self::assertMatchesRegularExpression(
            '/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/',
            $capturedParams['session_id'],
        );
        self::assertSame('ws://test:8001', $capturedParams['ws_url']);
        self::assertSame('http://test/.well-known/mercure', $capturedParams['mercure_url']);
        self::assertStringEndsWith('/raw', $capturedParams['mercure_topic_raw']);
        self::assertStringEndsWith('/roles', $capturedParams['mercure_topic_roles']);
        self::assertStringContainsString($capturedParams['session_id'], $capturedParams['mercure_topic_raw']);
    }

    public function testHistoryReturnsJsonFromAgent(): void
    {
        $expected = ['segments' => [['speaker' => 'spk_0', 'text' => 'Hello']]];
        $this->strandsClient->method('postJson')->willReturn($expected);

        $response = $this->controller->history('abc-123');

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(200, $response->getStatusCode());
        self::assertStringContainsString('spk_0', (string) $response->getContent());
    }

    public function testHistoryHandlesAgentErrorExceptionWith502(): void
    {
        $this->strandsClient->method('postJson')
            ->willThrowException(new AgentErrorException('NeMo error', statusCode: 500));

        $response = $this->controller->history('abc-123');

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(502, $response->getStatusCode());

        $data = json_decode((string) $response->getContent(), true);
        self::assertSame('abc-123', $data['session_id']);
        self::assertSame([], $data['segments']);
        self::assertSame('NeMo error', $data['error']);
    }

    public function testHistoryHandlesAgentErrorExceptionWith404(): void
    {
        $this->strandsClient->method('postJson')
            ->willThrowException(new AgentErrorException('Not found', statusCode: 404));

        $response = $this->controller->history('abc-123');

        self::assertSame(404, $response->getStatusCode());
    }

    public function testHistoryHandlesStrandsExceptionWith503(): void
    {
        $this->strandsClient->method('postJson')
            ->willThrowException(new StrandsException('Connection refused'));

        $response = $this->controller->history('abc-123');

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(503, $response->getStatusCode());

        $data = json_decode((string) $response->getContent(), true);
        self::assertSame('abc-123', $data['session_id']);
        self::assertStringContainsString('Connection refused', $data['error']);
    }

    public function testRolesStreamReturnsStreamedJsonResponse(): void
    {
        $result = new RoleInferenceResult(
            mapping: ['spk_0' => 'DOCTOR', 'spk_1' => 'PATIENT'],
            confidence: 0.92,
            eventsReceived: 3,
        );

        $this->roleInferenceService->method('streamRoleInference')
            ->willReturnCallback(function (string $sessionId, callable $onUpdate) use ($result): RoleInferenceResult {
                $onUpdate(['mapping' => ['spk_0' => 'DOCTOR'], 'confidence' => 0.6]);
                $onUpdate(['mapping' => ['spk_0' => 'DOCTOR', 'spk_1' => 'PATIENT'], 'confidence' => 0.92]);

                return $result;
            });

        $response = $this->controller->rolesStream('session-xyz');

        self::assertInstanceOf(StreamedJsonResponse::class, $response);
    }

    public function testRolesReturnsCurrentMapping(): void
    {
        $this->roleInferenceService->method('getCurrentMapping')
            ->with('session-xyz')
            ->willReturn(['mapping' => ['spk_0' => 'DOCTOR'], 'confidence' => 0.85]);

        $response = $this->controller->roles('session-xyz');

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(200, $response->getStatusCode());

        $data = json_decode((string) $response->getContent(), true);
        self::assertSame('session-xyz', $data['session_id']);
        self::assertSame(['spk_0' => 'DOCTOR'], $data['mapping']);
        self::assertSame(0.85, $data['confidence']);
    }
}
