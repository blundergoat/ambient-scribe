<?php

/**
 * Unit coverage for Strands client telemetry middleware.
 *
 * The browser does not call this class directly; it runs when Symfony proxies history
 * or role snapshot requests to Python. The tests assert the header and safe log fields
 * operators use to join that proxy call to FastAPI logs without transcript text.
 */

declare(strict_types=1);

namespace App\Tests\Unit\Observability;

use App\Logging\JsonLineLogger;
use App\Observability\StrandsClientTelemetry;
use PHPUnit\Framework\TestCase;
use Psr\Log\LogLevel;
use StrandsPhpClient\Http\ResponseObserver;

/**
 * Verifies correlation injection and canonical call logging for Strands client requests.
 *
 * The suite keeps the SDK out of the loop and drives the middleware methods directly.
 */
final class StrandsClientTelemetryTest extends TestCase
{
    /**
     * Adds a correlation header before the history request leaves Symfony.
     *
     * @return void - No payload; failure means Python could not echo a joinable correlation ID.
     */
    public function testBeforeRequestInjectsCorrelationHeader(): void
    {
        $telemetry = new StrandsClientTelemetry(new JsonLineLogger($this->temporaryLogFile(), LogLevel::DEBUG));

        $result = $telemetry->beforeRequest(
            'http://agent/session/session-123/history',
            [],
            '{}',
        );

        self::assertArrayHasKey('X-Correlation-ID', $result['headers']);
        self::assertMatchesRegularExpression('/^[0-9a-f-]{36}$/', $result['headers']['X-Correlation-ID']);
        self::assertSame('{}', $result['body']);
    }

    /**
     * Logs a completed role/history proxy call with session and correlation IDs.
     *
     * @return void - No payload; failure means PHP and Python logs would not join for support review.
     */
    public function testAfterResponseLogsCanonicalClientCall(): void
    {
        $logFile = $this->temporaryLogFile();
        $telemetry = new StrandsClientTelemetry(new JsonLineLogger($logFile, LogLevel::DEBUG));
        $url = 'http://agent/session/session-abc/roles';
        $request = $telemetry->beforeRequest($url, ['X-Correlation-ID' => 'corr-fixed'], '{}');

        $telemetry->afterResponse($url, 200, 18.42);

        $event = $this->readFirstJsonLine($logFile);
        self::assertSame([
            'event' => 'strands.client.call',
            'session_id' => 'session-abc',
            'correlation_id' => 'corr-fixed',
            'path' => '/session/session-abc/roles',
            'status' => 200,
            'duration_ms' => 18.42,
            'header' => 'corr-fixed',
        ], [
            'event' => $event['event'],
            'session_id' => $event['session_id'],
            'correlation_id' => $event['correlation_id'],
            'path' => $event['path'],
            'status' => $event['status'],
            'duration_ms' => $event['duration_ms'],
            'header' => $request['headers']['X-Correlation-ID'],
        ]);
    }

    /**
     * Adds safe parsed-response counts to the same proxy log line.
     *
     * @return void - No payload; failure means operators lose result-shape evidence for UI support.
     */
    public function testPostJsonObserverAddsSafeResponseSummary(): void
    {
        $logFile = $this->temporaryLogFile();
        $telemetry = new StrandsClientTelemetry(new JsonLineLogger($logFile, LogLevel::DEBUG));
        $url = 'http://agent/session/session-abc/history';

        $telemetry->beforeRequest($url, ['X-Correlation-ID' => 'corr-shape'], '{}');
        $telemetry->afterPostJson($url, $this->historyResponseWithHiddenText(), 12.0);
        $telemetry->afterResponse($url, 200, 12.0);

        $event = $this->readFirstJsonLine($logFile);
        self::assertSame([
            'response_type' => 'post_json',
            'response_field_count' => 4,
            'segments' => 2,
            'roles' => 1,
            'confidence' => 0.876,
            'has_error' => false,
            'has_text' => false,
        ], [
            'response_type' => $event['response_type'],
            'response_field_count' => $event['response_field_count'],
            'segments' => $event['segments'],
            'roles' => $event['roles'],
            'confidence' => $event['confidence'],
            'has_error' => $event['has_error'],
            'has_text' => str_contains((string) json_encode($event, JSON_THROW_ON_ERROR), 'hidden transcript text'),
        ]);
    }

    /**
     * Confirms Symfony autoconfiguration will register parsed-result observer hooks.
     *
     * @return void - No payload; failure means the dev client's response hooks would not run.
     */
    public function testTelemetryImplementsResponseObserver(): void
    {
        $telemetry = new StrandsClientTelemetry(new JsonLineLogger($this->temporaryLogFile(), LogLevel::DEBUG));

        self::assertInstanceOf(ResponseObserver::class, $telemetry);
    }

    /**
     * Builds a history response like the page receives after a transcript restore.
     *
     * @return array<string, mixed> - Response with hidden transcript text; empty would not prove redaction.
     */
    private function historyResponseWithHiddenText(): array
    {
        return [
            'session_id' => 'session-abc',
            'segments' => [
                ['text' => 'hidden transcript text'],
                ['text' => 'also hidden'],
            ],
            'mapping' => ['spk_0' => 'DOCTOR'],
            'confidence' => 0.8764,
        ];
    }

    /**
     * Marks failed Python proxy calls as errors without logging request bodies.
     *
     * @return void - No payload; failure means a broken role/history request would be under-reported.
     */
    public function testAfterResponseUsesErrorLevelForFailures(): void
    {
        $logFile = $this->temporaryLogFile();
        $telemetry = new StrandsClientTelemetry(new JsonLineLogger($logFile, LogLevel::DEBUG));
        $url = 'http://agent/session/session-abc/history';
        $telemetry->beforeRequest($url, ['X-Correlation-ID' => 'corr-error'], '{}');

        $telemetry->afterResponse($url, 503, 5.0, new \RuntimeException('agent unavailable'));

        $event = $this->readFirstJsonLine($logFile);
        self::assertSame([
            'level' => 'error',
            'error_type' => \RuntimeException::class,
            'has_body' => false,
        ], [
            'level' => $event['level'],
            'error_type' => $event['error_type'],
            'has_body' => array_key_exists('body', $event),
        ]);
    }

    /**
     * Creates a writable temporary log target for one middleware scenario.
     *
     * @return string - Path to an empty file; empty means the test could not capture logs.
     */
    private function temporaryLogFile(): string
    {
        $logFile = tempnam(sys_get_temp_dir(), 'ambient-scribe-client-');
        self::assertIsString($logFile);

        return $logFile;
    }

    /**
     * Reads the first emitted JSON log line.
     *
     * @param string $logFile - File written by the JSON logger; empty means there is nothing to decode.
     * @return array<string, mixed> - Decoded log object; empty means the middleware emitted no fields.
     */
    private function readFirstJsonLine(string $logFile): array
    {
        $line = trim((string) file_get_contents($logFile));

        $decodedLogLine = json_decode(json: $line, associative: true, depth: 512, flags: JSON_THROW_ON_ERROR);

        // A non-object line means the test cannot represent what the browser-support log would show.
        if (!is_array($decodedLogLine)) {
            self::fail('Expected the first JSON log line to decode to an object.');
        }

        $logEvent = [];
        // Each log field must be named so tests read it like the support JSON line does.
        foreach ($decodedLogLine as $logFieldName => $logFieldValue) {
            // List-style JSON would not let support find fields like `session_id` or `status`.
            if (!is_string($logFieldName)) {
                self::fail('Expected JSON log fields to be named object keys.');
            }

            $logEvent[$logFieldName] = $logFieldValue;
        }

        return $logEvent;
    }
}
