<?php

declare(strict_types=1);

namespace App\Tests\Unit\Service;

use App\Service\RoleInferenceResult;
use PHPUnit\Framework\TestCase;

final class RoleInferenceResultTest extends TestCase
{
    public function testDefaultConstructionIsSuccessful(): void
    {
        $result = new RoleInferenceResult();

        self::assertTrue($result->isSuccessful());
        self::assertSame([], $result->mapping);
        self::assertSame(0.0, $result->confidence);
        self::assertSame(0, $result->eventsReceived);
        self::assertFalse($result->cancelled);
        self::assertNull($result->error);
    }

    public function testConstructionWithAllParameters(): void
    {
        $result = new RoleInferenceResult(
            mapping: ['spk_0' => 'DOCTOR', 'spk_1' => 'PATIENT'],
            confidence: 0.95,
            eventsReceived: 5,
            cancelled: true,
            error: 'Stream timeout',
        );

        self::assertFalse($result->isSuccessful());
        self::assertSame(['spk_0' => 'DOCTOR', 'spk_1' => 'PATIENT'], $result->mapping);
        self::assertSame(0.95, $result->confidence);
        self::assertSame(5, $result->eventsReceived);
        self::assertTrue($result->cancelled);
        self::assertSame('Stream timeout', $result->error);
    }

    public function testToArrayIncludesResultFields(): void
    {
        $result = new RoleInferenceResult(
            mapping: ['spk_0' => 'DOCTOR'],
            confidence: 0.92,
            eventsReceived: 3,
            cancelled: false,
            error: null,
        );

        self::assertTrue($result->isSuccessful());
        self::assertSame([
            'mapping' => ['spk_0' => 'DOCTOR'],
            'confidence' => 0.92,
            'events_received' => 3,
            'cancelled' => false,
            'error' => null,
        ], $result->toArray());
    }

    public function testToArrayIncludesError(): void
    {
        $result = new RoleInferenceResult(error: 'Agent failed');

        self::assertSame('Agent failed', $result->toArray()['error']);
        self::assertFalse($result->isSuccessful());
    }
}
