<?php

declare(strict_types=1);

namespace App\Service;

use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;

/**
 * Answers "who is the doctor and who is the patient in this visit, right now?"
 *
 * During a consultation the clinician watches roles appear beside transcript lines as Python infers them, pushed live over Mercure.
 * This class serves the other route to the same answer: one snapshot on demand, behind `/scribe/{sessionId}/roles`.
 *
 * It never touches audio and never waits on the live stream.
 * When Python cannot answer, speakers fall back to unlabelled instead of the request failing, so the visible transcript stays put.
 */
class RoleInferenceService
{
    /** Five seconds is long enough for Python to answer and short enough that a slow agent never freezes the visible transcript page. */
    private const ROLE_SNAPSHOT_TIMEOUT = 5;

    /**
     * Wires this service to the client that carries requests to the Python agent.
     *
     * @param StrandsClient $strandsClient - Sends the role request to Python under the shared timeout and retry rules.
     */
    public function __construct(
        private readonly StrandsClient $strandsClient,
    ) {
    }

    /**
     * Fetches the latest speaker-to-role answer so the page can relabel the speakers already visible in the transcript.
     *
     * Use this when something needs roles immediately instead of waiting for Python's next live update to arrive.
     *
     * @param string $sessionId - Visit shown on screen; an unknown or empty ID simply means Python holds no role state for it.
     *
     * @return array<string, mixed> - Snapshot carrying `mapping` and `confidence`; an empty mapping means the UI leaves every speaker unlabelled.
     */
    public function getRoleSnapshot(string $sessionId): array
    {
        try {
            return $this->strandsClient->postJson(
                "/session/{$sessionId}/roles",
                [],
                timeout: self::ROLE_SNAPSHOT_TIMEOUT,
            );
        } catch (StrandsException) {
            // Say the clinician is mid-consultation when the agent restarts: no role answer arrives inside the five-second window.
            // Returning an empty mapping keeps the transcript text on screen with every speaker shown as unlabelled.
            return ['mapping' => new \stdClass(), 'confidence' => 0.0];
        }
    }
}
