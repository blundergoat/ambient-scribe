<?php

declare(strict_types=1);

namespace App\Service;

use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;

/**
 * Reads a current role snapshot from the Python agent for the Symfony controller.
 *
 * Use it when `/scribe/{sessionId}/roles` needs a one-shot answer for the browser.
 * It intentionally does not stream audio or role events; those stay in Python and Mercure.
 */
class RoleInferenceService
{
    /** Five seconds keeps a role poll from blocking the visible transcript page for too long. */
    private const ROLE_SNAPSHOT_TIMEOUT = 5;

    /**
     * Connects the snapshot service to the Strands client used for Python agent requests.
     *
     * @param StrandsClient $strandsClient Sends snapshot requests to Python; null is never expected in Symfony DI.
     */
    public function __construct(
        private readonly StrandsClient $strandsClient,
    ) {
    }

    /**
     * Fetches the role snapshot the browser can use to relabel visible transcript speakers.
     *
     * @param string $sessionId Session visible in the UI; empty means Python returns no useful role state.
     *
     * @return array<string, mixed> Mapping payload; empty mapping means the UI should leave speakers unknown.
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
            // Python is unavailable, so the page keeps transcript text visible without showing stale roles.
            return ['mapping' => new \stdClass(), 'confidence' => 0.0];
        }
    }
}
