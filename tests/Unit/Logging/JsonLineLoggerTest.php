<?php

/**
 * Unit coverage for the JSON logger used by Symfony and the Strands PHP client.
 *
 * These tests write to temporary files instead of stderr so they can inspect the exact
 * JSON a support tool would read after a clinician uses `/scribe`.
 */

declare(strict_types=1);

namespace App\Tests\Unit\Logging;

use App\Logging\JsonLineLogger;
use PHPUnit\Framework\TestCase;
use Psr\Log\LogLevel;

/**
 * Verifies canonical JSON-line logging without booting the Symfony container.
 *
 * The suite focuses on user-visible process evidence: event name, join IDs,
 * status fields, and debug filtering.
 */
final class JsonLineLoggerTest extends TestCase
{
    /**
     * Confirms session and correlation IDs are hoisted for log joins.
     *
     * @return void No payload; failure means PHP logs would not join to Python logs.
     */
    public function testWritesCanonicalJsonLine(): void
    {
        $logFile = $this->temporaryLogFile();
        $logger = new JsonLineLogger($logFile, LogLevel::DEBUG);

        $logger->info('strands.client.call', [
            'session_id' => 'session-123',
            'correlation_id' => 'corr-123',
            'status' => 200,
            'duration_ms' => 12.5,
        ]);

        $event = $this->readFirstJsonLine($logFile);
        self::assertSame('info', $event['level']);
        self::assertSame('strands.client.call', $event['event']);
        self::assertSame('session-123', $event['session_id']);
        self::assertSame('corr-123', $event['correlation_id']);
        self::assertSame(200, $event['status']);
        self::assertSame(12.5, $event['duration_ms']);
    }

    /**
     * Keeps SDK debug chatter out of normal operator logs unless LOG_LEVEL asks for it.
     *
     * @return void No payload; failure means routine page loads would create noisy logs.
     */
    public function testFiltersBelowMinimumLevel(): void
    {
        $logFile = $this->temporaryLogFile();
        $logger = new JsonLineLogger($logFile, LogLevel::INFO);

        $logger->debug('Strands postJson request', ['path' => '/session/example/history']);

        self::assertSame('', file_get_contents($logFile));
    }

    /**
     * Guarantees a logging call can never throw into the clinician request path.
     *
     * @return void No payload; failure means a bad context byte would 500 the scribe page.
     */
    public function testInvalidUtf8ContextIsSubstitutedInsteadOfThrowing(): void
    {
        $logFile = $this->temporaryLogFile();
        $logger = new JsonLineLogger($logFile, LogLevel::DEBUG);

        $logger->error('strands.client.failed', [
            'detail' => "broken \xC3 byte",
        ]);

        $event = $this->readFirstJsonLine($logFile);
        self::assertSame('strands.client.failed', $event['event']);
        // The invalid byte is replaced with U+FFFD rather than crashing the caller.
        self::assertStringContainsString("broken \u{FFFD} byte", $event['detail']);
    }

    /**
     * Creates a writable temporary log target for one logger scenario.
     *
     * @return string Path to an empty file; empty means the test could not capture logs.
     */
    private function temporaryLogFile(): string
    {
        $logFile = tempnam(sys_get_temp_dir(), 'ambient-scribe-log-');
        self::assertIsString($logFile);

        return $logFile;
    }

    /**
     * Reads the first JSON log line the same way `analyze-logs.py` would.
     *
     * @param string $logFile File written by the test logger; empty means there is nothing to decode.
     * @return array<string, mixed> Decoded log object; empty means the logger emitted no fields.
     */
    private function readFirstJsonLine(string $logFile): array
    {
        $line = trim((string) file_get_contents($logFile));

        return json_decode(json: $line, associative: true, depth: 512, flags: JSON_THROW_ON_ERROR);
    }
}
