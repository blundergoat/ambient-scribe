<?php

declare(strict_types=1);

namespace App\Service;

/**
 * Summary of a role inference streaming session.
 *
 * Returned by RoleInferenceService::streamRoleInference() to give the
 * caller a snapshot of the final state, including whether the stream
 * completed normally, was cancelled, or hit an error.
 */
final readonly class RoleInferenceResult
{
    /**
     * @param array<string, string> $mapping        Final speaker→role mapping (e.g., {"spk_0": "DOCTOR"}).
     * @param float                 $confidence      Final confidence score (0.0–1.0).
     * @param int                   $eventsReceived  Number of SSE events processed.
     * @param bool                  $cancelled       True if the consumer cancelled the stream early.
     * @param string|null           $error           Error message if the stream failed, null on success.
     */
    public function __construct(
        public array $mapping = [],
        public float $confidence = 0.0,
        public int $eventsReceived = 0,
        public bool $cancelled = false,
        public ?string $error = null,
    ) {
    }

    public function isSuccessful(): bool
    {
        return $this->error === null;
    }

    /**
     * @return array<string, mixed>
     */
    public function toArray(): array
    {
        return [
            'mapping' => $this->mapping,
            'confidence' => $this->confidence,
            'events_received' => $this->eventsReceived,
            'cancelled' => $this->cancelled,
            'error' => $this->error,
        ];
    }
}
