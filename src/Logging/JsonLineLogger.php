<?php

declare(strict_types=1);

namespace App\Logging;

use Psr\Log\AbstractLogger;
use Psr\Log\LogLevel;

/**
 * Emits canonical JSON logs without adding Monolog as a dependency.
 *
 * Use this service for app and SDK logs that describe page setup, proxied role/history calls,
 * and process status. It keeps debug messages out by default and never logs request bodies.
 */
final class JsonLineLogger extends AbstractLogger
{
    /** PSR level order used to hide debug SDK chatter unless the operator opts in. */
    private const LEVEL_PRIORITY = [
        LogLevel::DEBUG     => 10,
        LogLevel::INFO      => 20,
        LogLevel::NOTICE    => 25,
        LogLevel::WARNING   => 30,
        LogLevel::ERROR     => 40,
        LogLevel::CRITICAL  => 50,
        LogLevel::ALERT     => 60,
        LogLevel::EMERGENCY => 70,
    ];

    /** Lowest event level emitted for the current `/scribe` process logs. */
    private readonly string $minimumLevel;

    /**
     * Creates a logger that writes JSON lines to stderr or a test stream.
     *
     * @param string      $streamUri    Destination URI; empty would make browser-proxy logs disappear.
     * @param string|null $minimumLevel Lowest PSR level to emit; null reads LOG_LEVEL and defaults to info.
     */
    public function __construct(
        private readonly string $streamUri = 'php://stderr',
        ?string                 $minimumLevel = null,
    ) {
        $envLevel           = getenv('LOG_LEVEL');
        $this->minimumLevel = strtolower($minimumLevel ?? ($envLevel !== false ? $envLevel : LogLevel::INFO));
    }

    /**
     * Writes one canonical JSON event if the level passes the configured threshold.
     *
     * @param string               $level   PSR level; unknown values are treated as info so app logs still appear.
     * @param string|\Stringable   $message Stable event slug, for example `strands.client.call`.
     * @param array<string, mixed> $context Extra fields; empty means the line is process-scoped only.
     *
     * @return void No payload; the log line is written to stderr or the configured test stream.
     */
    public function log($level, string|\Stringable $message, array $context = []): void
    {
        $normalizedLevel = strtolower($level);
        // Low-priority debug logs from the SDK are hidden unless the operator asks for them.
        if (!$this->shouldEmit($normalizedLevel)) {
            return;
        }

        $event = [
            'ts'     => gmdate('c'),
            'level'  => $normalizedLevel,
            'event'  => (string)$message,
            'logger' => 'php',
        ];

        // Session and correlation IDs are join keys, so keep them near the top of each line.
        if (array_key_exists('session_id', $context)) {
            $event['session_id'] = $context['session_id'];
            unset($context['session_id']);
        }

        // Correlation IDs connect PHP proxy calls to FastAPI request logs.
        if (array_key_exists('correlation_id', $context)) {
            $event['correlation_id'] = $context['correlation_id'];
            unset($context['correlation_id']);
        }

        $event    += $this->sanitizeContext($context);
        $jsonLine = json_encode($event, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);
        $stream   = fopen($this->streamUri, 'ab');

        // If stderr is unavailable, failing open keeps the clinician page from crashing.
        if ($stream === false) {
            return;
        }

        fwrite($stream, $jsonLine . PHP_EOL);
        fclose($stream);
    }

    /**
     * Checks the operator's LOG_LEVEL threshold for a single event.
     *
     * @param string $level Normalized PSR level; empty behaves like info for visibility.
     *
     * @return bool True when the event should be visible in process logs.
     */
    private function shouldEmit(string $level): bool
    {
        $eventPriority   = self::LEVEL_PRIORITY[$level] ?? self::LEVEL_PRIORITY[LogLevel::INFO];
        $minimumPriority = self::LEVEL_PRIORITY[$this->minimumLevel] ?? self::LEVEL_PRIORITY[LogLevel::INFO];

        return $eventPriority >= $minimumPriority;
    }

    /**
     * Sanitizes log context so JSON lines stay bounded and body-free.
     *
     * @param array<string, mixed> $context Extra fields from app or SDK logs; empty means no detail fields.
     *
     * @return array<string, mixed> JSON-ready context for one process event.
     */
    private function sanitizeContext(array $context): array
    {
        $sanitized = [];

        // Every context key becomes a stable JSON attribute in the process report.
        foreach ($context as $key => $value) {
            $sanitized[(string)$key] = $this->sanitizeValue($value);
        }

        return $sanitized;
    }

    /**
     * Converts arbitrary context values into safe JSON scalars or arrays.
     *
     * @param mixed $value Context value; null means the field is present but unknown.
     *
     * @return mixed JSON-ready value; unsupported objects become class names.
     */
    private function sanitizeValue(mixed $value): mixed
    {
        // Scalar values cover status, durations, confidence, and bounded IDs.
        if ($value === null || is_scalar($value)) {
            return $value;
        }

        // Exceptions are summarized without stack traces or request bodies.
        if ($value instanceof \Throwable) {
            return [
                'error_type' => $value::class,
                'error'      => substr($value->getMessage(), 0, 200),
            ];
        }

        // Arrays are kept recursive because role/status fields are already bounded.
        if (is_array($value)) {
            $nested = [];
            // Nested context still becomes flat JSON-safe evidence for the same browser action.
            foreach ($value as $nestedKey => $nestedValue) {
                $nested[(string)$nestedKey] = $this->sanitizeValue($nestedValue);
            }

            return $nested;
        }

        // Objects are summarized by class so SDK internals never dump into user-session logs.
        if (is_object($value)) {
            return $value::class;
        }

        return get_debug_type($value);
    }
}
