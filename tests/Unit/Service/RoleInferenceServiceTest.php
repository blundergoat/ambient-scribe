<?php

declare(strict_types=1);

namespace App\Tests\Unit\Service;

use App\Service\RoleInferenceService;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;

final class RoleInferenceServiceTest extends TestCase
{
    private MockObject&StrandsClient $strandsClient;
    private RoleInferenceService $service;

    protected function setUp(): void
    {
        $this->strandsClient = $this->createMock(StrandsClient::class);
        $this->service = new RoleInferenceService(
            $this->strandsClient,
        );
    }

    public function testGetCurrentMappingReturnsAgentResponse(): void
    {
        $expected = ['mapping' => ['spk_0' => 'DOCTOR', 'spk_1' => 'PATIENT'], 'confidence' => 0.95];
        $this->strandsClient->expects(self::once())
            ->method('postJson')
            ->willReturn($expected);

        $result = $this->service->getCurrentMapping('session-map');

        self::assertSame($expected, $result);
    }

    public function testGetCurrentMappingFallsBackOnTransportError(): void
    {
        $this->strandsClient->expects(self::once())
            ->method('postJson')
            ->willThrowException(new StrandsException('network down'));

        $result = $this->service->getCurrentMapping('session-456');

        self::assertInstanceOf(\stdClass::class, $result['mapping']);
        self::assertSame(0.0, $result['confidence']);
        self::assertSame('{}', json_encode($result['mapping']));
    }
}
