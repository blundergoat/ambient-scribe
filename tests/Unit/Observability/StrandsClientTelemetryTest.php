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
use StrandsPhpClient\Response\AgentResponse;
use StrandsPhpClient\Response\Usage;
use StrandsPhpClient\Streaming\StreamResult;
use StrandsPhpClient\Streaming\StreamSseSummary;

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

        $this->assertFirstLogLineContains($logFile, [
            'response_type' => 'post_json',
            'response_field_count' => 4,
            'response_duration_ms' => 12,
            'segments' => 2,
            'roles' => 1,
            'confidence' => 0.876,
            'has_error' => false,
        ], 'hidden transcript text');
    }

    /**
     * Adds safe invoke metadata to the proxy log without agent answer text.
     *
     * @return void - No payload; failure means sync agent support logs may expose answer text.
     */
    public function testInvokeObserverAddsSafeResponseSummary(): void
    {
        $logFile = $this->temporaryLogFile();
        $telemetry = new StrandsClientTelemetry(new JsonLineLogger($logFile, LogLevel::DEBUG));
        $url = 'http://agent/session/session-abc/invoke';

        $telemetry->beforeRequest($url, ['X-Correlation-ID' => 'corr-invoke'], '{}');
        $telemetry->afterInvoke($url, $this->hiddenInvokeResponse(), 6.789);
        $telemetry->afterResponse($url, 200, 6.789);

        $this->assertFirstLogLineContains($logFile, [
            'response_type' => 'invoke',
            'response_duration_ms' => 6.79,
            'tokens_total' => 5,
            'tools_used' => 1,
            'interrupted' => false,
        ], 'hidden invoke text');
    }

    /**
     * Adds safe typed-stream metadata to the proxy log without streamed text.
     *
     * @return void - No payload; failure means stream support logs may expose answer text.
     */
    public function testStreamObserverAddsSafeResponseSummary(): void
    {
        $logFile = $this->temporaryLogFile();
        $telemetry = new StrandsClientTelemetry(new JsonLineLogger($logFile, LogLevel::DEBUG));
        $url = 'http://agent/session/session-abc/stream';

        $telemetry->beforeRequest($url, ['X-Correlation-ID' => 'corr-stream'], '{}');
        $telemetry->afterStream($url, $this->hiddenStreamResult(), 8.123);
        $telemetry->afterResponse($url, 200, 8.123);

        $this->assertFirstLogLineContains($logFile, [
            'response_type' => 'stream',
            'response_duration_ms' => 8.12,
            'tokens_total' => 11,
            'stream_events' => 5,
            'stream_text_events' => 2,
            'stream_cancelled' => true,
            'interrupted' => false,
        ], 'hidden stream text');
    }

    /**
     * Adds safe raw-SSE stream metadata to the proxy log.
     *
     * @return void - No payload; failure means role stream support loses terminal status.
     */
    public function testStreamSseObserverAddsSafeResponseSummary(): void
    {
        $logFile = $this->temporaryLogFile();
        $telemetry = new StrandsClientTelemetry(new JsonLineLogger($logFile, LogLevel::DEBUG));
        $url = 'http://agent/session/session-abc/roles/stream';

        $telemetry->beforeRequest($url, ['X-Correlation-ID' => 'corr-sse'], '{}');
        $telemetry->afterStreamSse($url, $this->roleStreamSummary(), 9.555);
        $telemetry->afterResponse($url, 200, 9.555);

        $this->assertFirstLogLineContains($logFile, [
            'response_type' => 'stream_sse',
            'response_duration_ms' => 9.56,
            'tokens_total' => 3,
            'stream_events' => 4,
            'stream_text_events' => 2,
            'stream_cancelled' => false,
            'stream_terminal_type' => 'complete',
            'stop_reason' => 'end_turn',
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
     * Builds a hidden invoke response like a future summary call may return.
     *
     * @return AgentResponse - Response with hidden answer text; empty would not prove redaction.
     */
    private function hiddenInvokeResponse(): AgentResponse
    {
        return new AgentResponse(
            text: 'hidden invoke text',
            usage: new Usage(inputTokens: 2, outputTokens: 3),
            toolsUsed: [['name' => 'lookup_clinical_context']],
        );
    }

    /**
     * Builds a hidden typed stream response like a future stream call may return.
     *
     * @return StreamResult - Stream with hidden text; empty would not prove redaction.
     */
    private function hiddenStreamResult(): StreamResult
    {
        return new StreamResult(
            text: 'hidden stream text',
            usage: new Usage(totalTokens: 11),
            textEvents: 2,
            totalEvents: 5,
            cancelled: true,
        );
    }

    /**
     * Builds a role-SSE summary like the browser receives during speaker labeling.
     *
     * @return StreamSseSummary - Sanitized stream counts; zero events would not prove logging.
     */
    private function roleStreamSummary(): StreamSseSummary
    {
        return new StreamSseSummary(
            totalEvents: 4,
            textEvents: 2,
            terminalType: 'complete',
            usage: new Usage(inputTokens: 1, outputTokens: 2),
            stopReason: 'end_turn',
        );
    }

    /**
     * Checks selected support-log fields and optional hidden text redaction.
     *
     * @param string $logFile - JSON log file; empty means no support event was written.
     * @param array<string, mixed> $expectedFields - Fields the UI support flow needs; empty is not useful.
     * @param string|null $hiddenText - Text that must not appear; null means no redaction sample applies.
     * @return void - No payload; failure means support logs lost safe fields or exposed hidden text.
     */
    private function assertFirstLogLineContains(
        string $logFile,
        array $expectedFields,
        ?string $hiddenText = null,
    ): void {
        $event = $this->readFirstJsonLine($logFile);
        $actualFields = [];

        // Each expected field is copied by name so missing support fields fail clearly.
        foreach ($expectedFields as $fieldName => $expectedValue) {
            self::assertArrayHasKey($fieldName, $event);
            $actualFields[$fieldName] = $event[$fieldName];
        }

        self::assertSame($expectedFields, $actualFields);

        // Null hidden text means this scenario has no PHI-style redaction sample.
        if ($hiddenText === null) {
            return;
        }

        self::assertFalse(str_contains((string) json_encode($event, JSON_THROW_ON_ERROR), $hiddenText));
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
