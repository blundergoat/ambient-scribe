<?php

declare(strict_types=1);

namespace App\Service;

use Psr\Log\LoggerInterface;
use StrandsPhpClient\Exceptions\AgentErrorException;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;

/**
 * Streams role inference results from the Python agent via SSE.
 *
 * Uses StrandsClient::streamSse() to consume the Python agent's
 * /session/{id}/roles/stream endpoint, which emits progressive
 * role_update events as the Strands agent reasons about speaker roles.
 *
 * ARCHITECTURE:
 *   Browser → PHP (this service) → Python agent (SSE) → Strands Agent (Bedrock)
 *
 *   The PHP layer adds:
 *     - Per-request timeout (10s for role inference, shorter than the 120s default)
 *     - Stream cancellation on session teardown
 *     - Error normalisation (AgentErrorException → structured response)
 *     - Correlation ID forwarding (via session_id in the payload)
 *
 * WHY SSE (not a single POST)?
 *   Role inference is iterative. The agent's confidence increases as it sees
 *   more transcript context. streamSse() lets the browser receive progressive
 *   updates ("Identifying speakers..." → "Roles identified (92%)" ) without
 *   waiting for the agent to finish.
 */
class RoleInferenceService
{
    private const ROLE_INFERENCE_TIMEOUT = 15;
    private const ROLE_SNAPSHOT_TIMEOUT = 5;

    public function __construct(
        private readonly StrandsClient $strandsClient,
        private readonly LoggerInterface $logger,
    ) {
    }

    /**
     * Stream role inference for a session.
     *
     * Calls the Python agent's SSE endpoint and invokes $onUpdate for each
     * progressive role_update event. Returns false from $onUpdate to cancel.
     *
     * @param string                              $sessionId  The session UUID.
     * @param callable(array<string, mixed>): ?bool $onUpdate  Called for each role_update event.
     *                                                          Return false to cancel the stream.
     *
     * @return RoleInferenceResult Summary of the inference session.
     */
    public function streamRoleInference(string $sessionId, callable $onUpdate): RoleInferenceResult
    {
        $eventsReceived = 0;
        $lastMapping = [];
        $lastConfidence = 0.0;
        $cancelled = false;
        $streamError = null;

        $this->logger->debug('RoleInference: starting stream', [
            'session_id' => $sessionId,
            'timeout' => self::ROLE_INFERENCE_TIMEOUT,
        ]);

        try {
            $this->strandsClient->streamSse(
                "/session/{$sessionId}/roles/stream",
                ['session_id' => $sessionId],
                function (array $event) use ($sessionId, $onUpdate, &$eventsReceived, &$lastMapping, &$lastConfidence, &$cancelled, &$streamError): ?bool {
                    $eventsReceived++;

                    $eventType = \is_string($event['type'] ?? null) ? $event['type'] : null;
                    if (isset($event['mapping']) && \is_array($event['mapping'])) {
                        /** @var array<string, string> $mapping */
                        $mapping = $event['mapping'];
                        $lastMapping = $mapping;
                    }
                    if (isset($event['confidence']) && is_numeric($event['confidence'])) {
                        $lastConfidence = (float) $event['confidence'];
                    }

                    if ($eventType === 'error') {
                        $streamError = \is_string($event['message'] ?? null) && $event['message'] !== ''
                            ? $event['message']
                            : 'Role inference failed';

                        $onUpdate($event);

                        $this->logger->warning('RoleInference: stream reported error', [
                            'session_id' => $sessionId,
                            'events_received' => $eventsReceived,
                            'error' => $streamError,
                        ]);

                        return false;
                    }

                    $result = $onUpdate($event);

                    if ($result === false) {
                        $cancelled = true;
                        $this->logger->debug('RoleInference: cancelled by consumer', [
                            'session_id' => $sessionId,
                            'events_received' => $eventsReceived,
                        ]);

                        return false;
                    }

                    return null;
                },
                timeout: self::ROLE_INFERENCE_TIMEOUT,
            );
        } catch (AgentErrorException $e) {
            $this->logger->warning('RoleInference: agent error', [
                'session_id' => $sessionId,
                'status_code' => $e->statusCode,
                'error' => $e->getMessage(),
            ]);

            return new RoleInferenceResult(
                mapping: $lastMapping,
                confidence: $lastConfidence,
                eventsReceived: $eventsReceived,
                cancelled: $cancelled,
                error: $e->getMessage(),
            );
        } catch (StrandsException $e) {
            $this->logger->error('RoleInference: transport error', [
                'session_id' => $sessionId,
                'error' => $e->getMessage(),
            ]);

            return new RoleInferenceResult(
                mapping: $lastMapping,
                confidence: $lastConfidence,
                eventsReceived: $eventsReceived,
                cancelled: $cancelled,
                error: 'Agent unavailable: ' . $e->getMessage(),
            );
        }

        if ($streamError !== null) {
            return new RoleInferenceResult(
                mapping: $lastMapping,
                confidence: $lastConfidence,
                eventsReceived: $eventsReceived,
                cancelled: false,
                error: $streamError,
            );
        }

        if ($eventsReceived === 0) {
            $message = 'Role inference stream ended before any events were received';
            $this->logger->warning('RoleInference: empty stream', [
                'session_id' => $sessionId,
                'timeout' => self::ROLE_INFERENCE_TIMEOUT,
            ]);

            return new RoleInferenceResult(
                mapping: $lastMapping,
                confidence: $lastConfidence,
                eventsReceived: 0,
                cancelled: false,
                error: $message,
            );
        }

        // Reconcile against the authoritative snapshot so a dropped final SSE
        // frame does not leave PHP with stale role state.
        if (!$cancelled) {
            $snapshot = $this->fetchAuthoritativeSnapshot($sessionId);
            if ($snapshot !== null) {
                $lastMapping = $snapshot['mapping'];
                $lastConfidence = $snapshot['confidence'];
            }
        }

        $this->logger->debug('RoleInference: stream complete', [
            'session_id' => $sessionId,
            'events_received' => $eventsReceived,
            'final_confidence' => $lastConfidence,
            'cancelled' => $cancelled,
        ]);

        return new RoleInferenceResult(
            mapping: $lastMapping,
            confidence: $lastConfidence,
            eventsReceived: $eventsReceived,
            cancelled: $cancelled,
        );
    }

    /**
     * Fetch the current role mapping without streaming (single request).
     *
     * Uses postJson() with a short timeout for quick lookups.
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

    /**
     * @return array{mapping: array<string, string>, confidence: float}|null
     */
    private function fetchAuthoritativeSnapshot(string $sessionId): ?array
    {
        try {
            $snapshot = $this->strandsClient->postJson(
                "/session/{$sessionId}/roles",
                [],
                timeout: self::ROLE_SNAPSHOT_TIMEOUT,
            );
        } catch (StrandsException $e) {
            $this->logger->warning('RoleInference: snapshot lookup failed after stream', [
                'session_id' => $sessionId,
                'error' => $e->getMessage(),
            ]);

            return null;
        }

        if (!isset($snapshot['mapping']) || !\is_array($snapshot['mapping'])) {
            $this->logger->warning('RoleInference: snapshot returned invalid mapping', [
                'session_id' => $sessionId,
                'snapshot' => $snapshot,
            ]);

            return null;
        }

        if (!is_numeric($snapshot['confidence'] ?? null)) {
            $this->logger->warning('RoleInference: snapshot returned invalid confidence', [
                'session_id' => $sessionId,
                'snapshot' => $snapshot,
            ]);

            return null;
        }

        /** @var array<string, string> $mapping */
        $mapping = $snapshot['mapping'];

        return [
            'mapping' => $mapping,
            'confidence' => (float) $snapshot['confidence'],
        ];
    }
}
