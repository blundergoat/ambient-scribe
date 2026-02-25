<?php

declare(strict_types=1);

namespace App\Tests\Service;

use App\Service\RoleInferenceResult;
use PHPUnit\Framework\TestCase;

class RoleInferenceResultTest extends TestCase
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

    public function testToArrayReturnsAllFields(): void
    {
        $result = new RoleInferenceResult(
            mapping: ['spk_0' => 'DOCTOR'],
            confidence: 0.8,
            eventsReceived: 3,
            cancelled: false,
            error: null,
        );

        $array = $result->toArray();

        self::assertSame([
            'mapping' => ['spk_0' => 'DOCTOR'],
            'confidence' => 0.8,
            'events_received' => 3,
            'cancelled' => false,
            'error' => null,
        ], $array);
    }

    public function testToArrayWithError(): void
    {
        $result = new RoleInferenceResult(error: 'Agent failed');

        $array = $result->toArray();

        self::assertSame('Agent failed', $array['error']);
        self::assertFalse($result->isSuccessful());
    }
}
