<?php

declare(strict_types=1);

namespace App\Service;

use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;

/**
 * Fetches the current role mapping from the Python agent.
 *
 * Live role delivery is handled by the Mercure queue path (Python -> Mercure -> Browser).
 * This service provides a snapshot lookup for the PHP layer.
 */
class RoleInferenceService
{
    private const ROLE_SNAPSHOT_TIMEOUT = 5;

    public function __construct(
        private readonly StrandsClient $strandsClient,
    ) {
    }

    /**
     * Fetch the current role mapping (single request, no streaming).
     *
     * @return array<string, mixed>
     */
    public function getCurrentMapping(string $sessionId): array
    {
        try {
            return $this->strandsClient->postJson(
                "/session/{$sessionId}/roles",
                [],
                timeout: self::ROLE_SNAPSHOT_TIMEOUT,
            );
        } catch (StrandsException) {
            return ['mapping' => new \stdClass(), 'confidence' => 0.0];
        }
    }
}
