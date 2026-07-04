<?php

/**
 * Strands PHP client telemetry for proxy calls from Symfony to Python.
 *
 * The browser hits Symfony for history and role snapshots; those calls then go to FastAPI
 * through `StrandsClient`. This middleware injects a correlation header and logs the result
 * without touching controller code or request/response bodies.
 */

declare(strict_types=1);

namespace App\Observability;

use Psr\Log\LoggerInterface;
use Psr\Log\LogLevel;
use StrandsPhpClient\Http\RequestMiddleware;
use Symfony\Component\Uid\Uuid;

/**
 * Adds correlation and canonical JSON-call logs to Strands client requests.
 *
 * The Strands Symfony bundle autoconfigures this service as `strands.middleware`.
 * Use it for history and role snapshot calls where operators need to join PHP and Python logs.
 */
final class StrandsClientTelemetry implements RequestMiddleware
{
    /** @var array<string, string> Correlation IDs keyed by URL until the SDK calls afterResponse. */
    private array $correlationIdsByUrl = [];

    /**
     * Wires Strands client telemetry to the JSON process logger.
     *
     * @param LoggerInterface $logger Canonical logger; null is not expected from Symfony DI.
     */
    public function __construct(
        private readonly LoggerInterface $logger,
    ) {
    }

    /**
     * Adds `X-Correlation-ID` before the Python request is signed or sent.
     *
     * @param string $url FastAPI URL; empty is not expected from configured Strands agents.
     * @param array<string, string> $headers Request headers; empty means no correlation exists yet.
     * @param string $body JSON request body; empty means no request payload was sent.
     * @return array{headers: array<string, string>, body: string} Headers/body for the SDK request.
     */
    public function beforeRequest(string $url, array $headers, string $body): array
    {
        $correlationId = $headers['X-Correlation-ID'] ?? Uuid::v4()->toRfc4122();
        $headers['X-Correlation-ID'] = $correlationId;
        $this->correlationIdsByUrl[$url] = $correlationId;

        return ['headers' => $headers, 'body' => $body];
    }

    /**
     * Logs the completed Strands HTTP operation in the canonical schema.
     *
     * @param string $url FastAPI URL; empty means no session ID can be extracted.
     * @param int $statusCode HTTP status; zero means the SDK had no response from Python.
     * @param float $durationMs End-to-end SDK duration in milliseconds.
     * @param \Throwable|null $error Failure from the SDK; null means Python returned a usable response.
     * @return void No payload; the JSON logger records the browser-visible proxy outcome.
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

        // Failed proxy calls need the error class, not the request body, for support triage.
        if ($error !== null) {
            $context['error_type'] = $error::class;
        }

        $this->logger->log($this->levelForStatus($statusCode, $error), 'strands.client.call', $context);
    }

    /**
     * Extracts the user session ID from `/session/{id}/...` URLs.
     *
     * @param string $url FastAPI URL; empty means no browser session can be joined.
     * @return string|null Session ID for log joins, or null for non-session client calls.
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
     * Chooses a PSR level that matches the browser impact of the proxy call.
     *
     * @param int $statusCode HTTP status from Python; zero means no response reached Symfony.
     * @param \Throwable|null $error SDK error; null means normal completion.
     * @return string PSR level used by the JSON logger.
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
