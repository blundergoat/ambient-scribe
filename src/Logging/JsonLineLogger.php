<?php

declare(strict_types=1);

namespace App\Logging;

use Psr\Log\AbstractLogger;
use Psr\Log\LogLevel;

/**
 * Writes one JSON line per event so support can reconstruct what a clinician's page actually did.
 *
 * This is the only logger the PHP side uses, and it deliberately avoids Monolog.
 * The lines it writes trace back to something a person did in the browser: opening `/scribe`, restoring history, asking for a summary.
 *
 * Two rules make it safe to leave switched on during a real consultation:
 *
 * - values are reduced to scalars, arrays, and class names, and an exception to its class plus a trimmed message
 * - a failure inside the logger is swallowed rather than thrown, so a broken log line can never break the visible page
 */
final class JsonLineLogger extends AbstractLogger
{
    /** PSR levels ranked so the SDK's debug chatter stays hidden until an operator sets `LOG_LEVEL` to debug. */
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

    /** Quietest level this process will print; anything below it is dropped before a line is ever built. */
    private readonly string $minimumLevel;

    /**
     * Creates the logger that every page request and proxied Python call writes through.
     *
     * @param string      $streamUri    - Where lines are written; the default is container stderr, and tests pass a memory stream instead.
     * @param string|null $minimumLevel - Quietest level to print; null falls back to `LOG_LEVEL`, and then to info when that is unset too.
     */
    public function __construct(
        private readonly string $streamUri = 'php://stderr',
        ?string                 $minimumLevel = null,
    ) {
        $configuredLevel    = getenv('LOG_LEVEL');
        $this->minimumLevel = strtolower($minimumLevel ?? ($configuredLevel !== false ? $configuredLevel : LogLevel::INFO));
    }

    /**
     * Writes one event as a single JSON line, or drops it silently when it is quieter than the configured level.
     *
     * Every browser action that reaches PHP ends here, which is what lets support replay a visit from the logs alone.
     *
     * @param string               $level   - PSR level; an unrecognised value is ranked as info rather than treated as debug noise.
     * @param string|\Stringable   $message - Stable event slug such as `strands.client.call`, chosen so support can filter on it.
     * @param array<string, mixed> $context - Fields describing the event; empty means the line says only that the process did this, for no one visit.
     *
     * @return void - Nothing is returned; the caller cannot tell whether the line was written, which is what keeps logging off the critical path.
     */
    public function log($level, string|\Stringable $message, array $context = []): void
    {
        $normalizedLevel = strtolower($level);

        // Below the operator's threshold, so this event is dropped before any string work happens.
        if (!$this->shouldEmit($normalizedLevel)) {
            return;
        }

        $event = [
            'ts'     => gmdate('c'),
            'level'  => $normalizedLevel,
            'event'  => (string)$message,
            'logger' => 'php',
        ];

        // The visit ID is how support ties this line to one clinician's page, so it is lifted out of the context and placed up front.
        if (array_key_exists('session_id', $context)) {
            $event['session_id'] = $context['session_id'];
            unset($context['session_id']);
        }

        // The correlation ID is what joins this PHP line to the matching FastAPI line for the same click.
        if (array_key_exists('correlation_id', $context)) {
            $event['correlation_id'] = $context['correlation_id'];
            unset($context['correlation_id']);
        }

        $event += $this->sanitizeContext($context);
        $jsonLine = json_encode($event, JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);

        // Encoding failed on something the substitute flag cannot repair: an INF or NAN number, or context nested past the encoder's depth limit.
        // A stub line still records that the event happened, even though its fields are lost.
        if ($jsonLine === false) {
            $jsonLine = json_encode([
                'ts'     => $event['ts'],
                'level'  => $normalizedLevel,
                'event'  => 'logging.encode_failed',
                'logger' => 'php',
            ], JSON_UNESCAPED_SLASHES);
        }

        // Even the stub failed, which a non-UTF-8 level string can cause here, so the event is abandoned rather than thrown back at the page.
        if ($jsonLine === false) {
            return;
        }

        $stream = fopen($this->streamUri, 'ab');

        // The log destination cannot be opened, so the line is dropped; a clinician mid-consultation never sees a logging error.
        if ($stream === false) {
            return;
        }

        fwrite($stream, $jsonLine . PHP_EOL);
        fclose($stream);
    }

    /**
     * Decides whether one event is loud enough to print under the operator's current `LOG_LEVEL`.
     *
     * Use this before building a line, so filtered-out events cost nothing during a live consultation.
     *
     * @param string $level - Already-lowercased PSR level; an empty or unknown value is ranked as info, so it prints whenever info does.
     *
     * @return bool - True when the event should appear in process logs, false when the operator has asked for a quieter feed.
     */
    private function shouldEmit(string $level): bool
    {
        $eventPriority   = self::LEVEL_PRIORITY[$level] ?? self::LEVEL_PRIORITY[LogLevel::INFO];
        $minimumPriority = self::LEVEL_PRIORITY[$this->minimumLevel] ?? self::LEVEL_PRIORITY[LogLevel::INFO];

        return $eventPriority >= $minimumPriority;
    }

    /**
     * Normalises the caller's context fields into keys and values one JSON line can carry.
     *
     * Use this on the way into every line; it bounds what a value may become, though what a caller passes stays the caller's choice.
     *
     * @param array<string, mixed> $context - Fields supplied by the app or the Strands SDK; empty means this event carries no detail of its own.
     *
     * @return array<string, mixed> - The same fields with string keys and JSON-safe values, ready to merge into the event line.
     */
    private function sanitizeContext(array $context): array
    {
        $sanitized = [];

        // PHP turns a numeric-looking key into an integer, so each key is cast back to a string and the line stays a JSON object.
        foreach ($context as $key => $value) {
            $sanitized[(string)$key] = $this->sanitizeValue($value);
        }

        return $sanitized;
    }

    /**
     * Reduces one context value to a form a JSON line can safely carry.
     *
     * Use this for every field; it is what stops an object's internals or an exception's stack trace from reaching the log.
     *
     * @param mixed $value - One context field; null is kept as-is because "the field was present but unknown" is itself useful to support.
     *
     * @return mixed - A scalar, null, or array of the same; anything else is reduced to its class name so only the shape is recorded.
     */
    private function sanitizeValue(mixed $value): mixed
    {
        // Already printable, and this covers most fields: status codes, durations, confidence scores, and visit IDs.
        if ($value === null || is_scalar($value)) {
            return $value;
        }

        // An exception reached the log, so record its class and a trimmed message; `mb_substr` counts characters so it cannot cut one in half.
        if ($value instanceof \Throwable) {
            return [
                'error_type' => $value::class,
                'error'      => mb_substr($value->getMessage(), 0, 200),
            ];
        }

        // An array value keeps its structure rather than being flattened into the line, so a grouped field stays readable.
        if (is_array($value)) {
            $nested = [];

            // Same treatment as the top-level context: keys cast to strings, and every value recursed until it is a safe leaf.
            foreach ($value as $nestedKey => $nestedValue) {
                $nested[(string)$nestedKey] = $this->sanitizeValue($nestedValue);
            }

            return $nested;
        }

        // Some SDK object slipped into the context, so only its class name is logged and its contents never reach the operator's terminal.
        if (is_object($value)) {
            return $value::class;
        }

        return get_debug_type($value);
    }
}
