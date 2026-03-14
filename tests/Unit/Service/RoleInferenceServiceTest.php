<?php

declare(strict_types=1);

namespace App\Tests\Unit\Service;

use App\Service\RoleInferenceService;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;

final class RoleInferenceServiceTest extends TestCase
{
    public function testStreamRoleInferenceCollectsLatestMappingAndConfidence(): void
    {
        $client = $this->createMock(StrandsClient::class);
        $client->expects(self::once())
            ->method('streamSse')
            ->willReturnCallback(
                static function (string $path, array $payload, callable $onEvent): void {
                    self::assertSame('/session/session-123/roles/stream', $path);
                    self::assertSame(['session_id' => 'session-123'], $payload);

                    $onEvent([
                        'mapping' => ['spk_0' => 'DOCTOR'],
                        'confidence' => 0.87,
                    ]);
                },
            );

        $service = new RoleInferenceService($client, new NullLogger());

        $result = $service->streamRoleInference('session-123', static fn (array $event): ?bool => null);

        self::assertSame(['spk_0' => 'DOCTOR'], $result->mapping);
        self::assertSame(0.87, $result->confidence);
        self::assertSame(1, $result->eventsReceived);
        self::assertNull($result->error);
    }

    public function testGetCurrentMappingFallsBackOnTransportError(): void
    {
        $client = $this->createMock(StrandsClient::class);
        $client->expects(self::once())
            ->method('postJson')
            ->willThrowException(new StrandsException('network down'));

        $service = new RoleInferenceService($client, new NullLogger());

        self::assertSame(
            ['mapping' => [], 'confidence' => 0.0],
            $service->getCurrentMapping('session-456'),
        );
    }
}
