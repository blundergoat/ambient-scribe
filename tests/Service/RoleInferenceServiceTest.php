<?php

declare(strict_types=1);

namespace App\Tests\Service;

use App\Service\RoleInferenceService;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;
use StrandsPhpClient\Exceptions\AgentErrorException;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;

class RoleInferenceServiceTest extends TestCase
{
    private MockObject&StrandsClient $strandsClient;
    private RoleInferenceService $service;

    protected function setUp(): void
    {
        $this->strandsClient = $this->createMock(StrandsClient::class);
        $this->service = new RoleInferenceService(
            $this->strandsClient,
            new NullLogger(),
        );
    }

    public function testStreamRoleInferenceSuccessfulStream(): void
    {
        $this->strandsClient->method('streamSse')
            ->willReturnCallback(function (string $path, array $payload, callable $onEvent): void {
                $onEvent(['mapping' => ['spk_0' => 'DOCTOR'], 'confidence' => 0.6]);
                $onEvent(['mapping' => ['spk_0' => 'DOCTOR', 'spk_1' => 'PATIENT'], 'confidence' => 0.92]);
            });

        $collectedEvents = [];
        $result = $this->service->streamRoleInference(
            'session-abc',
            function (array $event) use (&$collectedEvents): null {
                $collectedEvents[] = $event;

                return null;
            },
        );

        self::assertCount(2, $collectedEvents);
        self::assertTrue($result->isSuccessful());
        self::assertSame(2, $result->eventsReceived);
        self::assertSame(['spk_0' => 'DOCTOR', 'spk_1' => 'PATIENT'], $result->mapping);
        self::assertSame(0.92, $result->confidence);
        self::assertFalse($result->cancelled);
        self::assertNull($result->error);
    }

    public function testStreamRoleInferenceCancelledByConsumer(): void
    {
        $this->strandsClient->method('streamSse')
            ->willReturnCallback(function (string $path, array $payload, callable $onEvent): void {
                $onEvent(['mapping' => ['spk_0' => 'DOCTOR'], 'confidence' => 0.5]);
                // Simulate consumer cancellation — return false from the callback wrapper
                $result = $onEvent(['mapping' => ['spk_0' => 'PATIENT'], 'confidence' => 0.3]);
                // In real code, the StrandsClient would stop streaming when callback returns false
                if ($result === false) {
                    return;
                }
            });

        $callCount = 0;
        $result = $this->service->streamRoleInference(
            'session-cancel',
            function (array $event) use (&$callCount): ?bool {
                $callCount++;

                // Cancel after 2nd event
                return $callCount >= 2 ? false : null;
            },
        );

        self::assertSame(2, $result->eventsReceived);
        self::assertTrue($result->cancelled);
    }

    public function testStreamRoleInferenceHandlesAgentErrorException(): void
    {
        $this->strandsClient->method('streamSse')
            ->willThrowException(new AgentErrorException('Model timeout', statusCode: 504));

        $result = $this->service->streamRoleInference(
            'session-err',
            fn (array $event): null => null,
        );

        self::assertFalse($result->isSuccessful());
        self::assertSame('Model timeout', $result->error);
        self::assertSame(0, $result->eventsReceived);
        self::assertSame([], $result->mapping);
    }

    public function testStreamRoleInferenceHandlesStrandsException(): void
    {
        $this->strandsClient->method('streamSse')
            ->willThrowException(new StrandsException('Connection refused'));

        $result = $this->service->streamRoleInference(
            'session-transport',
            fn (array $event): null => null,
        );

        self::assertFalse($result->isSuccessful());
        self::assertStringContainsString('Connection refused', (string) $result->error);
        self::assertStringContainsString('Agent unavailable', (string) $result->error);
    }

    public function testStreamRoleInferenceTracksMappingFromPartialEvents(): void
    {
        $this->strandsClient->method('streamSse')
            ->willReturnCallback(function (string $path, array $payload, callable $onEvent): void {
                // Event with mapping but no confidence
                $onEvent(['mapping' => ['spk_0' => 'DOCTOR']]);
                // Event with confidence but no mapping
                $onEvent(['confidence' => 0.75]);
                // Event with neither (should not overwrite)
                $onEvent(['status' => 'processing']);
            });

        $result = $this->service->streamRoleInference(
            'session-partial',
            fn (array $event): null => null,
        );

        self::assertSame(3, $result->eventsReceived);
        self::assertSame(['spk_0' => 'DOCTOR'], $result->mapping);
        self::assertSame(0.75, $result->confidence);
    }

    public function testStreamRoleInferenceIgnoresNonArrayMapping(): void
    {
        $this->strandsClient->method('streamSse')
            ->willReturnCallback(function (string $path, array $payload, callable $onEvent): void {
                $onEvent(['mapping' => 'invalid', 'confidence' => 'not-a-number']);
            });

        $result = $this->service->streamRoleInference(
            'session-bad-data',
            fn (array $event): null => null,
        );

        self::assertSame([], $result->mapping);
        self::assertSame(0.0, $result->confidence);
    }

    public function testGetCurrentMappingReturnsAgentResponse(): void
    {
        $expected = ['mapping' => ['spk_0' => 'DOCTOR', 'spk_1' => 'PATIENT'], 'confidence' => 0.95];
        $this->strandsClient->method('postJson')->willReturn($expected);

        $result = $this->service->getCurrentMapping('session-map');

        self::assertSame($expected, $result);
    }

    public function testGetCurrentMappingReturnsFallbackOnException(): void
    {
        $this->strandsClient->method('postJson')
            ->willThrowException(new StrandsException('Agent down'));

        $result = $this->service->getCurrentMapping('session-fail');

        self::assertInstanceOf(\stdClass::class, $result['mapping']);
        self::assertSame(0.0, $result['confidence']);
        self::assertSame('{}', json_encode($result['mapping']));
    }
}
