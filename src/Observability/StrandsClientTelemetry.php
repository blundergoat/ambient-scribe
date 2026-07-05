<?php

/**
 * Strands PHP client telemetry for proxy calls from Symfony to Python.
 *
 * The browser hits Symfony for history and role snapshots; Symfony then calls FastAPI
 * through `StrandsClient`. This middleware adds a join key and safe counts so support
 * can debug what the page showed without logging transcript, prompt, or summary text.
 */

declare(strict_types=1);

namespace App\Observability;

use Psr\Log\LoggerInterface;
use Psr\Log\LogLevel;
use StrandsPhpClient\Http\RequestMiddleware;
use StrandsPhpClient\Http\ResponseObserver;
use StrandsPhpClient\Response\AgentResponse;
use StrandsPhpClient\Streaming\StreamResult;
use StrandsPhpClient\Streaming\StreamSseSummary;
use Symfony\Component\Uid\Uuid;

/**
 * Adds correlation and canonical JSON-call logs to Strands client requests.
 *
 * The Strands Symfony bundle autoconfigures it as request middleware and a response observer.
 * Use it when the page restores history or role labels and support needs to join PHP and
 * Python logs without seeing clinical content.
 */
final class StrandsClientTelemetry implements RequestMiddleware, ResponseObserver
{
    /** @var array<string, string> Correlation IDs keyed by URL until the SDK calls afterResponse. */
    private array $correlationIdsByUrl = [];

    /** @var array<string, array<string, mixed>> Safe parsed-result summaries keyed by URL. */
    private array $responseSummariesByUrl = [];

    /**
     * Wires Strands client telemetry to the JSON process logger.
     *
     * @param LoggerInterface $logger - Canonical logger; null is not expected from Symfony DI.
     */
    public function __construct(
        private readonly LoggerInterface $logger,
    ) {
    }

    /**
     * Adds `X-Correlation-ID` before the Python request is sent.
     *
     * @param string $url - FastAPI URL; empty means support cannot join the call to a browser session.
     * @param array<string, string> $headers - Request headers; empty means no correlation exists yet.
     * @param string $body - JSON body; empty means the user action sent no extra payload.
     * @return array{headers: array<string, string>, body: string} Headers/body for the SDK request.
     */
    public function beforeRequest(string $url, array $headers, string $body): array
    {
        // e.g. the clinician reopened a finished session and the page fetched `/history`.
        // No incoming correlation means this proxy call starts a new support join key.
        $correlationId = $headers['X-Correlation-ID'] ?? Uuid::v4()->toRfc4122();
        $headers['X-Correlation-ID'] = $correlationId;
        $this->correlationIdsByUrl[$url] = $correlationId;

        return ['headers' => $headers, 'body' => $body];
    }

    /**
     * Logs the completed Strands HTTP operation in the canonical schema.
     *
     * @param string $url - FastAPI URL; empty means no session ID can be extracted.
     * @param int $statusCode - HTTP status; zero means the SDK had no response from Python.
     * @param float $durationMs - SDK duration; zero means the call ended before timing was useful.
     * @param \Throwable|null $error - SDK failure; null means Python returned a usable response.
     * @return void - No payload; the JSON logger records the browser-visible proxy outcome.
     */
    public function afterResponse(
        string $url,
        int $statusCode,
        float $durationMs,
        ?\Throwable $error = null,
    ): void {
        $correlationId = $this->correlationIdsByUrl[$url] ?? null;
        unset($this->correlationIdsByUrl[$url]);

        $context = [
            'session_id' => $this->sessionIdFromUrl($url),
            'correlation_id' => $correlationId,
            'path' => (string) (parse_url($url, PHP_URL_PATH) ?: ''),
            'status' => $statusCode,
            'duration_ms' => round($durationMs, 2),
        ];

        // Successful client calls add response shape, so support sees whether the page got rows or roles.
        if (isset($this->responseSummariesByUrl[$url])) {
            $context = array_merge($context, $this->responseSummariesByUrl[$url]);
            unset($this->responseSummariesByUrl[$url]);
        }

        // Failed proxy calls need the error class, not the request body, for support triage.
        if ($error !== null) {
            $context['error_type'] = $error::class;
        }

        $this->logger->log($this->levelForStatus($statusCode, $error), 'strands.client.call', $context);
    }

    /**
     * Stores safe invoke result counts until the final client log line is emitted.
     *
     * @param string $url - Agent URL; empty means no session or route can be joined.
     * @param AgentResponse $response - Parsed agent answer; empty text is not logged either way.
     * @param float $durationMs - SDK duration; zero is allowed because afterResponse logs the canonical value.
     * @return void - No payload; the next client log line receives these safe fields.
     */
    public function afterInvoke(string $url, AgentResponse $response, float $durationMs): void
    {
        $this->responseSummariesByUrl[$url] = [
            'response_type' => 'invoke',
            'tokens_total' => $response->usage->totalTokens(),
            'tools_used' => count($response->toolsUsed),
            'interrupted' => $response->isInterrupted(),
        ];
    }

    /**
     * Stores safe typed-stream result counts until the final client log line is emitted.
     *
     * @param string $url - Agent URL; empty means no session or route can be joined.
     * @param StreamResult $result - Parsed stream result; empty text still logs only counts.
     * @param float $durationMs - SDK duration; zero is allowed because afterResponse logs the canonical value.
     * @return void - No payload; the next client log line receives these safe fields.
     */
    public function afterStream(string $url, StreamResult $result, float $durationMs): void
    {
        $this->responseSummariesByUrl[$url] = [
            'response_type' => 'stream',
            'tokens_total' => $result->usage->totalTokens(),
            'stream_events' => $result->totalEvents,
            'stream_text_events' => $result->textEvents,
            'stream_cancelled' => $result->cancelled,
            'interrupted' => $result->isInterrupted(),
        ];
    }

    /**
     * Stores safe custom-endpoint response counts until the final client log line is emitted.
     *
     * @param string $url - FastAPI URL; empty means no session ID can be extracted.
     * @param array<string, mixed> $response - Parsed JSON response; empty means Python returned no fields.
     * @param float $durationMs - SDK duration; zero is allowed because afterResponse logs the canonical value.
     * @return void - No payload; the next client log line receives safe response-shape fields.
     */
    public function afterPostJson(string $url, array $response, float $durationMs): void
    {
        $responseSummary = $this->summarizePostJsonResponse($response);
        $this->responseSummariesByUrl[$url] = $responseSummary;
    }

    /**
     * Stores safe raw-SSE stream counts until the final client log line is emitted.
     *
     * @param string $url - Agent URL; empty means no session or route can be joined.
     * @param StreamSseSummary $summary - Sanitized stream summary; zero events means no visible stream update.
     * @param float $durationMs - SDK duration; zero is allowed because afterResponse logs the canonical value.
     * @return void - No payload; the next client log line receives these safe fields.
     */
    public function afterStreamSse(string $url, StreamSseSummary $summary, float $durationMs): void
    {
        $this->responseSummariesByUrl[$url] = [
            'response_type' => 'stream_sse',
            'tokens_total' => $summary->usage?->totalTokens() ?? 0,
            'stream_events' => $summary->totalEvents,
            'stream_text_events' => $summary->textEvents,
            'stream_cancelled' => $summary->cancelled,
            'stream_terminal_type' => $summary->terminalType,
            'stop_reason' => $summary->stopReason,
        ];
    }

    /**
     * Extracts the user session ID from `/session/{id}/...` URLs.
     *
     * @param string $url - FastAPI URL; empty means no browser session can be joined.
     * @return string|null - Session ID for log joins, or null for non-session client calls.
     */
    private function sessionIdFromUrl(string $url): ?string
    {
        $path = (string) (parse_url($url, PHP_URL_PATH) ?: '');

        // Only session-scoped Python calls can join to a visible browser transcript.
        if (preg_match('#/session/([^/]+)/#', $path, $matches) !== 1) {
            return null;
        }

        return $matches[1];
    }

    /**
     * Builds a body-safe summary for custom JSON endpoints.
     *
     * @param array<string, mixed> $response - Parsed response; empty means Python returned no visible fields.
     * @return array<string, mixed> - Safe counts for the client log; never includes transcript or SOAP text.
     */
    private function summarizePostJsonResponse(array $response): array
    {
        $summary = [
            'response_type' => 'post_json',
            'response_field_count' => count($response),
            'has_error' => isset($response['error']) || isset($response['detail']),
        ];

        // History responses include transcript rows; log only the count the user can restore.
        if (isset($response['segments']) && is_array($response['segments'])) {
            $summary['segments'] = count($response['segments']);
        }

        // Role snapshots include a mapping; log only how many visible speakers have roles.
        if (isset($response['mapping'])) {
            $summary['roles'] = $this->countRoleMappings($response['mapping']);
        }

        // Confidence is already a bounded UI percentage signal, not transcript content.
        if (isset($response['confidence']) && is_numeric($response['confidence'])) {
            $summary['confidence'] = round((float) $response['confidence'], 3);
        }

        return $summary;
    }

    /**
     * Counts role mappings from array or object payloads.
     *
     * @param mixed $mapping - Role mapping from Python; null/empty means the UI still shows unknown speakers.
     * @return int - Number of speakers with assigned roles; zero means no visible role labels yet.
     */
    private function countRoleMappings(mixed $mapping): int
    {
        // Python may return `{}` and Symfony may represent it as an object for the browser.
        if ($mapping instanceof \stdClass) {
            return count(get_object_vars($mapping));
        }

        // A normal role snapshot is an associative array keyed by speaker ID.
        if (is_array($mapping)) {
            return count($mapping);
        }

        return 0;
    }

    /**
     * Chooses a PSR level that matches the browser impact of the proxy call.
     *
     * @param int $statusCode - HTTP status from Python; zero means no response reached Symfony.
     * @param \Throwable|null $error - SDK error; null means the user received a normal response.
     * @return string - PSR level used by the JSON logger; empty is never returned.
     */
    private function levelForStatus(int $statusCode, ?\Throwable $error): string
    {
        // No response or a 5xx means the browser's history/role request failed operationally.
        if ($error !== null || $statusCode >= 500 || $statusCode === 0) {
            return LogLevel::ERROR;
        }

        // A 4xx is usually a missing/invalid session visible to the user, not an outage.
        if ($statusCode >= 400) {
            return LogLevel::WARNING;
        }

        return LogLevel::INFO;
    }
}
