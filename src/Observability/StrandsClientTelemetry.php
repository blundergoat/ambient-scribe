<?php

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
 * Records what happened on every call PHP makes to Python, and stamps each one so the two sides can be read together.
 *
 * The Strands bundle attaches this automatically, so it runs whenever the page restores history or refreshes role labels.
 * Support uses the result to answer "the clinician says history did not load" without ever opening the transcript itself.
 *
 * Each call produces exactly one `strands.client.call` line, built in two stages:
 *
 * - before the request, an `X-Correlation-ID` header is added so the PHP and FastAPI logs share a join key
 * - after the response, timing, status, and counts are logged, while text, bodies, and clinical content are left out entirely
 */
final class StrandsClientTelemetry implements RequestMiddleware, ResponseObserver
{
    /** @var array<string, string> Correlation IDs held per URL between sending a request and logging its response. */
    private array $correlationIdsByUrl = [];

    /** @var array<string, array<string, mixed>> Counts describing each parsed response, held until the single call line is written. */
    private array $responseSummariesByUrl = [];

    /**
     * Wires the telemetry hooks to the JSON logger every PHP process writes through.
     *
     * @param LoggerInterface $logger - Receives the finished call line; in practice this is always the app's JSON line logger.
     */
    public function __construct(
        private readonly LoggerInterface $logger,
    ) {
    }

    /**
     * Stamps an outgoing Python request with the correlation ID that will later join it to the FastAPI log line.
     *
     * This is the first half of every logged call, and it runs before Python has done any work at all.
     *
     * @param string                $url     - FastAPI URL about to be called; an empty URL means the finished line cannot name a visit.
     * @param array<string, string> $headers - Outgoing headers; no correlation header yet means this call starts a fresh join key.
     * @param string                $body    - Outgoing JSON body; empty means the clinician's action carried no payload, as with a history read.
     *
     * @return array{headers: array<string, string>, body: string} - The same request with the correlation header guaranteed to be present.
     */
    public function beforeRequest(string $url, array $headers, string $body): array
    {
        // Say the clinician reopened a finished visit and the page asked for `/history`: nothing upstream set a header, so one is minted here.
        $correlationId                   = $headers['X-Correlation-ID'] ?? Uuid::v4()->toRfc4122();
        $headers['X-Correlation-ID']     = $correlationId;
        $this->correlationIdsByUrl[$url] = $correlationId;

        return ['headers' => $headers, 'body' => $body];
    }

    /**
     * Writes the one line that says how a call to Python ended, including whatever the page got back.
     *
     * This is the second half of every logged call, and it runs whether Python answered, refused, or never replied.
     *
     * @param string          $url        - FastAPI URL that was called; a URL outside `/session/{id}/` means the line carries no visit ID.
     * @param int             $statusCode - HTTP status Python returned; zero means no response arrived at all, such as a container that is down.
     * @param float           $durationMs - How long the call took; zero means it ended before any useful timing was collected.
     * @param \Throwable|null $error      - Failure raised by the SDK; null means Python answered and the page received something usable.
     *
     * @return void - Nothing is returned; the visible outcome for the clinician was already decided by the caller.
     */
    public function afterResponse(
        string      $url,
        int         $statusCode,
        float       $durationMs,
        ?\Throwable $error = null,
    ): void {
        $correlationId = $this->correlationIdsByUrl[$url] ?? null;
        unset($this->correlationIdsByUrl[$url]);

        $context = [
            'session_id'     => $this->sessionIdFromUrl($url),
            'correlation_id' => $correlationId,
            'path'           => (string)(parse_url($url, PHP_URL_PATH) ?: ''),
            'status'         => $statusCode,
            'duration_ms'    => round($durationMs, 2),
        ];

        // Python parsed cleanly, so the counts stashed by the matching hook are folded in and support can see whether rows or roles came back.
        if (isset($this->responseSummariesByUrl[$url])) {
            $context = array_merge($context, $this->responseSummariesByUrl[$url]);
            unset($this->responseSummariesByUrl[$url]);
        }

        // The call failed, so record which kind of failure it was; the request body stays out because it may hold what was said in the room.
        if ($error !== null) {
            $context['error_type'] = $error::class;
        }

        $this->logger->log($this->levelForStatus($statusCode, $error), 'strands.client.call', $context);
    }

    /**
     * Notes the shape of a completed agent invocation, ready for the call line that follows.
     *
     * Required by the observer interface. Every page action here reaches Python by plain JSON post, so nothing currently triggers this hook.
     *
     * @param string        $url        - Agent URL that was invoked; used only to match these counts to the pending call line.
     * @param AgentResponse $response   - The agent's parsed answer; its text is deliberately ignored and only counts are taken.
     * @param float         $durationMs - Time spent parsing the answer; zero is fine because `afterResponse` logs the authoritative duration.
     *
     * @return void - Nothing is returned; these counts are held in memory until the call line is written.
     */
    public function afterInvoke(string $url, AgentResponse $response, float $durationMs): void
    {
        $this->responseSummariesByUrl[$url] = [
            'response_type'        => 'invoke',
            'response_duration_ms' => $this->roundedResponseDurationMs($durationMs),
            'tokens_total'         => $response->usage->totalTokens(),
            'tools_used'           => count($response->toolsUsed),
            'interrupted'          => $response->isInterrupted(),
        ];
    }

    /**
     * Notes the shape of a completed typed stream, ready for the call line that follows.
     *
     * Required by the observer interface. Every page action here reaches Python by plain JSON post, so nothing currently triggers this hook.
     *
     * @param string       $url        - Agent URL that was streamed; used only to match these counts to the pending call line.
     * @param StreamResult $result     - The parsed stream; its text is ignored, and an empty stream still records its event counts.
     * @param float        $durationMs - Time spent parsing the stream; zero is fine because `afterResponse` logs the authoritative duration.
     *
     * @return void - Nothing is returned; these counts are held in memory until the call line is written.
     */
    public function afterStream(string $url, StreamResult $result, float $durationMs): void
    {
        $this->responseSummariesByUrl[$url] = [
            'response_type'        => 'stream',
            'response_duration_ms' => $this->roundedResponseDurationMs($durationMs),
            'tokens_total'         => $result->usage->totalTokens(),
            'stream_events'        => $result->totalEvents,
            'stream_text_events'   => $result->textEvents,
            'stream_cancelled'     => $result->cancelled,
            'interrupted'          => $result->isInterrupted(),
        ];
    }

    /**
     * Notes the shape of a plain JSON response, ready for the call line that follows.
     *
     * This is the hook behind the everyday page actions: restoring history and reading the current speaker roles.
     *
     * @param string               $url        - FastAPI URL that answered; used only to match these counts to the pending call line.
     * @param array<string, mixed> $response   - Python's parsed JSON; an empty array means Python answered with no fields for the page to show.
     * @param float                $durationMs - Time spent parsing; zero is fine because `afterResponse` logs the authoritative duration.
     *
     * @return void - Nothing is returned; these counts are held in memory until the call line is written.
     */
    public function afterPostJson(string $url, array $response, float $durationMs): void
    {
        $responseSummary                         = $this->summarizePostJsonResponse($response);
        $responseSummary['response_duration_ms'] = $this->roundedResponseDurationMs($durationMs);
        $this->responseSummariesByUrl[$url]      = $responseSummary;
    }

    /**
     * Notes the shape of a raw server-sent event stream, ready for the call line that follows.
     *
     * Required by the observer interface. Every page action here reaches Python by plain JSON post, so nothing currently triggers this hook.
     *
     * @param string           $url        - Agent URL that streamed; used only to match these counts to the pending call line.
     * @param StreamSseSummary $summary    - Counts already stripped of text by the SDK; zero events means nothing ever reached the screen.
     * @param float            $durationMs - Time spent parsing; zero is fine because `afterResponse` logs the authoritative duration.
     *
     * @return void - Nothing is returned; these counts are held in memory until the call line is written.
     */
    public function afterStreamSse(string $url, StreamSseSummary $summary, float $durationMs): void
    {
        $tokensTotal = 0;

        // The SDK can report a finished stream with no usage block at all, so zero tokens is logged rather than leaving the field missing.
        if ($summary->usage !== null) {
            $tokensTotal = $summary->usage->totalTokens();
        }

        $this->responseSummariesByUrl[$url] = [
            'response_type'        => 'stream_sse',
            'response_duration_ms' => $this->roundedResponseDurationMs($durationMs),
            'tokens_total'         => $tokensTotal,
            'stream_events'        => $summary->totalEvents,
            'stream_text_events'   => $summary->textEvents,
            'stream_cancelled'     => $summary->cancelled,
            'stream_terminal_type' => $summary->terminalType,
            'stop_reason'          => $summary->stopReason,
        ];
    }

    /**
     * Pulls the visit ID out of a `/session/{id}/...` URL so the line can be traced to one clinician's page.
     *
     * Use this for every logged call; it is what makes a support search by visit ID return anything at all.
     *
     * @param string $url - FastAPI URL that was called; an empty URL simply yields no visit ID.
     *
     * @return string|null - The visit ID, or null for calls such as `/agent/model-health` that belong to no single visit.
     */
    private function sessionIdFromUrl(string $url): ?string
    {
        $path = (string)(parse_url($url, PHP_URL_PATH) ?: '');

        // Not a session-scoped route, so there is no visit to attach; the line is still logged, just without an ID to search on.
        if (preg_match('#/session/([^/]+)/#', $path, $matches) !== 1) {
            return null;
        }

        return $matches[1];
    }

    /**
     * Describes a JSON response by counting what it contained, never by copying any of it.
     *
     * Use this to answer "did the page get its rows back?" from the logs without exposing what was said in the room.
     *
     * @param array<string, mixed> $response - Python's parsed JSON; an empty array means Python answered with no fields at all.
     *
     * @return array<string, mixed> - Counts and flags only; transcript rows, summary text, and SOAP notes are never included.
     */
    private function summarizePostJsonResponse(array $response): array
    {
        $summary = [
            'response_type'        => 'post_json',
            'response_field_count' => count($response),
            'has_error'            => isset($response['error']) || isset($response['detail']),
        ];

        // A history response carries the transcript itself, so only the number of rows the clinician could restore is recorded.
        if (isset($response['segments']) && is_array($response['segments'])) {
            $summary['segments'] = count($response['segments']);
        }

        // A role response carries the speaker mapping, so only the number of speakers that now have a label is recorded.
        if (isset($response['mapping'])) {
            $summary['roles'] = $this->countRoleMappings($response['mapping']);
        }

        // Confidence is already just the percentage shown next to the role labels, so it is safe to keep verbatim.
        if (isset($response['confidence']) && is_numeric($response['confidence'])) {
            $summary['confidence'] = round((float)$response['confidence'], 3);
        }

        return $summary;
    }

    /**
     * Rounds a parsing duration to two decimals so log lines stay readable.
     *
     * @param float $durationMs - Duration measured by an SDK hook; zero means no usable timing was captured.
     *
     * @return float - The same duration in milliseconds, rounded; zero still means timing was unavailable rather than instant.
     */
    private function roundedResponseDurationMs(float $durationMs): float
    {
        return round($durationMs, 2);
    }

    /**
     * Counts how many speakers currently carry a role label, whatever shape the mapping arrived in.
     *
     * Use this instead of reading the mapping directly, since the answer is a number rather than who said what.
     *
     * @param mixed $mapping - Speaker-to-role mapping from Python; empty or unusable means the page is still showing unlabelled speakers.
     *
     * @return int - How many speakers have a role; zero means the clinician sees no DOCTOR or PATIENT labels yet.
     */
    private function countRoleMappings(mixed $mapping): int
    {
        // Defensive: Python's JSON always decodes to an array here, but the app's own role payload uses an object for an empty mapping.
        if ($mapping instanceof \stdClass) {
            return count(get_object_vars($mapping));
        }

        // The ordinary shape straight from Python: one entry per speaker, keyed by speaker ID.
        if (is_array($mapping)) {
            return count($mapping);
        }

        return 0;
    }

    /**
     * Picks the log level that matches how badly the clinician's action actually went.
     *
     * Use this so a scan of error lines shows real outages, not the routine "that visit has no data yet" answers.
     *
     * @param int             $statusCode - Status Python returned; zero means nothing came back, such as the agent container being down.
     * @param \Throwable|null $error      - SDK failure if there was one; null means a response arrived, even an unsuccessful one.
     *
     * @return string - PSR level for this call: error for an outage, warning for a rejected request, info for everything that worked.
     */
    private function levelForStatus(int $statusCode, ?\Throwable $error): string
    {
        // Nothing came back, or Python broke: the page could not restore history or refresh roles, so this is an outage rather than an empty answer.
        if ($error !== null || $statusCode >= 500 || $statusCode === 0) {
            return LogLevel::ERROR;
        }

        // Python rejected the request, usually an expired or unknown visit ID; the clinician sees an empty panel, not a broken system.
        if ($statusCode >= 400) {
            return LogLevel::WARNING;
        }

        return LogLevel::INFO;
    }
}
