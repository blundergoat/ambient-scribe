<?php

declare(strict_types=1);

namespace App\Tests\Unit\Service;

use App\Service\RoleInferenceResult;
use PHPUnit\Framework\TestCase;

final class RoleInferenceResultTest extends TestCase
{
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
}
