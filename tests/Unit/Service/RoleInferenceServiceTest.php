<?php

/**
 * Unit coverage for the PHP role snapshot service used by the `/scribe` page.
 *
 * These tests keep Python out of the loop while checking what the browser would receive from
 * `/scribe/{sessionId}/roles`. Use them when role snapshot fallback or payload shape changes.
 */

declare(strict_types=1);

namespace App\Tests\Unit\Service;

use App\Service\RoleInferenceService;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;

/**
 * Verifies one-shot role mapping lookups for the browser-visible role endpoint.
 *
 * The suite confirms successful DOCTOR/PATIENT snapshots and the empty mapping shown when Python is unreachable.
 */
final class RoleInferenceServiceTest extends TestCase
{
    /** Mocked Python client; it keeps tests from making network calls while representing the agent response. */
    private MockObject&StrandsClient $strandsClient;

    /** Service under test that turns Python role snapshots into browser-visible mapping payloads. */
    private RoleInferenceService $service;

    /**
     * Creates fresh mocks before each role snapshot scenario so tests cannot leak UI state.
     *
     * @return void No payload; the service fields are reset for the next browser-visible role case.
     */
    protected function setUp(): void
    {
        $this->strandsClient = $this->createMock(StrandsClient::class);
        $this->service = new RoleInferenceService(
            $this->strandsClient,
        );
    }

    /**
     * Confirms a Python role snapshot is returned unchanged for the browser role endpoint.
     *
     * @return void No payload; failure means role JSON would no longer match Python's mapping.
     */
    public function testGetCurrentMappingReturnsAgentResponse(): void
    {
        $expected = ['mapping' => ['spk_0' => 'DOCTOR', 'spk_1' => 'PATIENT'], 'confidence' => 0.95];
        $this->strandsClient->expects(self::once())
            ->method('postJson')
            ->willReturn($expected);

        $result = $this->service->getRoleSnapshot('session-map');

        self::assertSame($expected, $result);
    }

    /**
     * Keeps the transcript page usable with unknown roles when the Python role endpoint is down.
     *
     * @return void No payload; failure means transport loss could show stale or broken role labels.
     */
    public function testGetCurrentMappingFallsBackOnTransportError(): void
    {
        $this->strandsClient->expects(self::once())
            ->method('postJson')
            ->willThrowException(new StrandsException('network down'));

        $result = $this->service->getRoleSnapshot('session-456');

        self::assertInstanceOf(\stdClass::class, $result['mapping']);
        self::assertSame(0.0, $result['confidence']);
        self::assertSame('{}', json_encode($result['mapping']));
    }
}
