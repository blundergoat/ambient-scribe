<?php

declare(strict_types=1);

namespace App\Controller;

use App\Service\RoleInferenceService;
use Psr\Log\LoggerInterface;
use StrandsPhpClient\Exceptions\AgentErrorException;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;
use Symfony\Bundle\FrameworkBundle\Controller\AbstractController;
use Symfony\Component\DependencyInjection\Attribute\Autowire;
use Symfony\Component\HttpFoundation\BinaryFileResponse;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\HttpFoundation\ResponseHeaderBag;
use Symfony\Component\Routing\Attribute\Route;
use Symfony\Component\Uid\Uuid;
use Symfony\Contracts\HttpClient\Exception\TransportExceptionInterface;
use Symfony\Contracts\HttpClient\HttpClientInterface;
use Symfony\Contracts\HttpClient\ResponseInterface;

/**
 * Serves the `/scribe` workspace page and every same-origin JSON route the page calls during and after a visit.
 *
 * Python owns transcription, correction, and role inference; on the proxy routes this class just validates the visit ID and forwards the call.
 * Every failure becomes JSON, so a Python outage leaves the clinician with a message rather than a broken screen.
 *
 * The routes fall into three groups:
 *
 * - getting started: the page itself, the model-health check that gates the start button, and the dev-only demo audio picker
 * - after stopping: transcript correction, summary generation, and the corrected rows the summary was built from
 * - any time: stored history, current speaker roles, and a manual role correction the clinician typed
 */
class ScribeController extends AbstractController
{
    /** Two minutes: summary and correction both wait on real agent work, but the browser tab must never hang indefinitely. */
    private const AGENT_WORKFLOW_TIMEOUT_SECONDS = 120;

    /**
     * Wires the page controller to Python, the role snapshot service, the JSON logger, and the client used for proxying.
     *
     * @param StrandsClient        $strandsClient        - Carries history reads to Python; live audio never passes through it.
     * @param RoleInferenceService $roleInferenceService - Answers the on-demand "who is doctor, who is patient" question.
     * @param LoggerInterface      $logger               - Records the one problem this class hits alone: an unreadable demo audio manifest.
     * @param HttpClientInterface  $httpClient           - Proxies every browser-triggered FastAPI call this controller makes.
     * @param string               $agentEndpoint        - FastAPI base URL; empty leaves every proxy URL relative and unresolvable.
     */
    public function __construct(
        #[Autowire(service: 'strands.client.scribe')]
        private readonly StrandsClient        $strandsClient,
        private readonly RoleInferenceService $roleInferenceService,
        private readonly LoggerInterface      $logger,
        private readonly HttpClientInterface  $httpClient,
        #[Autowire(env: 'AGENT_ENDPOINT')]
        private readonly string               $agentEndpoint,
    ) {
    }

    /**
     * Renders the workspace page with a fresh visit ID and every connection detail the browser needs to start streaming.
     *
     * This is the clinician's entry point: the first thing they see, and the only full page load in an entire consultation.
     *
     * @return Response - The rendered page, carrying the WebSocket and Mercure URLs, this visit's Mercure topics, and any demo audio rows.
     */
    #[Route('/scribe', name: 'scribe_index', methods: ['GET'])]
    public function index(): Response
    {
        $sessionId    = Uuid::v4()->toRfc4122();
        $webSocketUrl = $this->getParameter('nemo_websocket_url');
        $mercureUrl   = $this->getParameter('mercure_url');

        $devPanelEnabled = $this->getParameter('kernel.environment') === 'dev';
        $audioFixtures   = [];

        // Running locally, so the page also gets the Demo Audio list a developer uses to replay a generated consultation.
        if ($devPanelEnabled) {
            /** @var string $projectDir - Absolute project root, used to locate the checked-in fixture manifest. */
            $projectDir    = $this->getParameter('kernel.project_dir');
            $audioFixtures = $this->loadDemoAudioFixtures($projectDir);
        }

        return $this->render('scribe/index.html.twig', [
            'session_id'            => $sessionId,
            'ws_url'                => $webSocketUrl,
            'mercure_url'           => $mercureUrl,
            'mercure_topic_raw'     => "scribe/session/{$sessionId}/raw",
            'mercure_topic_roles'   => "scribe/session/{$sessionId}/roles",
            'mercure_topic_summary' => "scribe/session/{$sessionId}/summary",
            'enable_role_updates'   => true,
            'dev_panel_enabled'     => $devPanelEnabled,
            'audio_fixtures'        => $audioFixtures,
        ]);
    }

    /**
     * Hands back one generated demo WAV after a developer clicks a row in the Demo Audio list.
     *
     * Exists only because PHP's built-in dev server rejects dotted `.wav` path segments before Symfony ever sees them.
     *
     * @param Request $request - Browser request; a missing or empty `filename` means no row was identified and nothing can be replayed.
     *
     * @return Response - The WAV bytes for playback, or 404 whenever the file cannot be served for any reason.
     */
    #[Route('/scribe/demo-audio', name: 'scribe_demo_audio', methods: ['GET'])]
    public function demoAudio(Request $request): Response
    {
        $filename = $request->query->get('filename');

        // The link arrived without a usable filename, so there is nothing to replay and the picker row simply fails to play.
        if (!\is_string($filename) || $filename === '') {
            return new Response('Not found', Response::HTTP_NOT_FOUND);
        }

        return $this->serveDemoAudioFixture($filename);
    }

    /**
     * Applies every safety check before returning a demo WAV, so this route can never be used to read arbitrary project files.
     *
     * Use this for anything reachable from the Demo Audio list; empty, unsafe, unknown, and missing files all look identical to the user.
     *
     * @param string $filename - Filename taken from the picker link; anything that is not a known generated WAV is refused.
     *
     * @return Response - The WAV bytes, or 404 which the picker shows as a row that will not play.
     */
    private function serveDemoAudioFixture(string $filename): Response
    {
        // Outside local development there is no Demo Audio list at all, so the route behaves as though it does not exist.
        if ($this->getParameter('kernel.environment') !== 'dev') {
            return new Response('Not found', Response::HTTP_NOT_FOUND);
        }

        // Anything that is not a plain generated WAV name is refused, stopping a hand-edited link from walking out of the fixtures directory.
        if (!preg_match('/^[A-Za-z0-9._-]+\.wav$/', $filename)) {
            return new Response('Not found', Response::HTTP_NOT_FOUND);
        }

        /** @var string $projectDir - Absolute project root, used to locate the checked-in fixture manifest. */
        $projectDir       = $this->getParameter('kernel.project_dir');
        $audioFixtures    = $this->loadDemoAudioFixtures($projectDir);
        $allowedFilenames = array_column($audioFixtures, 'filename');

        // The name is well formed but the manifest does not list it, so it is refused rather than read off disk.
        if (!\in_array($filename, $allowedFilenames, true)) {
            return new Response('Not found', Response::HTTP_NOT_FOUND);
        }

        $audioPath = $projectDir . '/tests/fixtures/audio/' . $filename;

        // The manifest still lists it but the generated file was deleted, so the row cannot play until fixtures are regenerated.
        if (!is_file($audioPath)) {
            return new Response('Not found', Response::HTTP_NOT_FOUND);
        }

        $response = new BinaryFileResponse($audioPath);
        $response->headers->set('Content-Type', 'audio/wav');
        $response->setContentDisposition(ResponseHeaderBag::DISPOSITION_INLINE, $filename);

        return $response;
    }

    /**
     * Reads the generated audio manifest and returns only the rows that can actually be played right now.
     *
     * Fills the Demo Audio list beside the transcript, and every problem here shortens that list rather than failing the page load.
     *
     * @param string $projectDir - Absolute project root; a manifest missing beneath it simply produces no rows.
     *
     * @return list<array<string, mixed>> - Playable rows for the picker; empty means the panel shows no demo audio at all.
     */
    private function loadDemoAudioFixtures(string $projectDir): array
    {
        $manifestPath   = $projectDir . '/tests/fixtures/audio/generated-manifest.json';
        $audioDirectory = $projectDir . '/tests/fixtures/audio';

        // The generated corpus has never been created in this checkout, so the panel appears with no rows to click.
        if (!is_file($manifestPath)) {
            return [];
        }

        $manifestContents = file_get_contents($manifestPath);

        // The manifest exists but could not be read, for instance after a permissions change, so the panel is left empty.
        if (!\is_string($manifestContents)) {
            return [];
        }

        try {
            /** @var mixed $manifestEntries - Parsed manifest; anything that is not an array is ignored below. */
            $manifestEntries = json_decode(
                json:        $manifestContents,
                associative: true,
                depth:       512,
                flags:       JSON_THROW_ON_ERROR,
            );
        } catch (\JsonException $manifestParseError) {
            // A fixture generation run was interrupted, or someone hand-edited the file, leaving truncated JSON on disk.
            // The warning tells a developer why their picker is empty, while the clinician page still loads normally.
            $this->logger->warning('Scribe audio fixture manifest is invalid JSON', [
                'path'  => $manifestPath,
                'error' => $manifestParseError->getMessage(),
            ]);

            return [];
        }

        // Valid JSON, but not the list of entries this expects, so it is ignored rather than trusted.
        if (!\is_array($manifestEntries)) {
            return [];
        }

        $audioFixtures = [];

        // Each manifest entry becomes at most one playable row; anything unusable is skipped so the good rows still appear.
        foreach ($manifestEntries as $manifestEntry) {
            // This entry is a bare value rather than a described fixture, so there is nothing to build a row from.
            if (!\is_array($manifestEntry)) {
                continue;
            }

            $filename = $manifestEntry['filename'] ?? '';

            // The entry names no safe WAV file, so it never becomes a link the browser could be pointed at.
            if (!\is_string($filename) || !preg_match('/^[A-Za-z0-9._-]+\.wav$/', $filename)) {
                continue;
            }

            $audioPath = $audioDirectory . '/' . $filename;

            // Listed in the manifest but never generated, so the row is left out instead of failing when someone clicks it.
            if (!is_file($audioPath)) {
                continue;
            }

            $complaint = $manifestEntry['complaint'] ?? '';
            $edgeCase  = $manifestEntry['edge_case'] ?? '';
            $speakers  = $manifestEntry['speakers'] ?? [];

            // Missing or wrongly typed description fields fall back to blank or empty, so one incomplete entry still yields a playable row.
            $audioFixtures[] = [
                'filename'  => $filename,
                'complaint' => \is_string($complaint) ? $complaint : '',
                'edge_case' => \is_string($edgeCase) ? $edgeCase : '',
                'speakers'  => \is_array($speakers) ? array_values($speakers) : [],
                'url'       => '/scribe/demo-audio?filename=' . rawurlencode($filename),
            ];
        }

        return $audioFixtures;
    }

    /**
     * Generates the visit summary by forwarding the request to Python from the page's own origin.
     *
     * Runs when the clinician stops recording, or finishes a replay, and asks for a note.
     * The reply is JSON even during an outage, so the summary panel can offer a retry instead of breaking.
     *
     * @param string  $sessionId - Visit UUID from the page; anything that is not a UUID cannot match stored transcript text.
     * @param Request $request   - Browser request; an empty body tells Python to summarise the transcript it already holds.
     *
     * @return JsonResponse - The summary payload, or a 400/502/503 the panel shows as a recoverable state with a retry.
     */
    #[Route('/session/{sessionId}/summary', name: 'scribe_summary_proxy', methods: ['POST'])]
    public function summary(string $sessionId, Request $request): JsonResponse
    {
        // A malformed visit ID can never match anything Python stored, so it is rejected before any network call is made.
        if (!Uuid::isValid($sessionId)) {
            return $this->json(['detail' => 'Invalid session_id: must be a valid UUID'], Response::HTTP_BAD_REQUEST);
        }

        $summaryRequestBody  = $request->getContent();
        $agentRequestOptions = [
            'headers' => ['Accept' => 'application/json'],
            'timeout' => self::AGENT_WORKFLOW_TIMEOUT_SECONDS,
        ];

        // The clinician has trimmed or relabelled what is on screen, so those exact rows are sent and the note matches what they see.
        if ($summaryRequestBody !== '') {
            $agentRequestOptions['headers']['Content-Type'] = 'application/json';
            $agentRequestOptions['body']                    = $summaryRequestBody;
        }

        try {
            $agentResponse = $this->httpClient->request(
                'POST',
                $this->agentUrl("/session/{$sessionId}/summary"),
                $agentRequestOptions,
            );

            return $this->toBrowserJson($agentResponse, 'Summary generation failed');
        } catch (TransportExceptionInterface $agentUnreachable) {
            // The agent container was restarting, or a long consultation ran past two minutes, so no reply ever arrived.
            // The panel gets a 503 and offers a retry, and the transcript the clinician is reading stays untouched.
            return $this->json([
                                   'detail' => 'Summary service unavailable: ' . $agentUnreachable->getMessage(),
                               ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Runs the post-visit transcript correction pass by forwarding the request to Python from the page's own origin.
     *
     * Runs after the clinician stops recording and before a summary is asked for, so the note can be built from cleaned-up rows.
     *
     * @param string  $sessionId - Visit UUID from the page; anything that is not a UUID cannot match the retained audio.
     * @param Request $request   - Browser request carrying the rows now on screen; an empty body tells Python to use its stored rows.
     *
     * @return JsonResponse - Correction status; an unavailable result is non-fatal and lets the page fall back to the live rows.
     */
    #[Route('/session/{sessionId}/correction', name: 'scribe_correction_proxy', methods: ['POST'])]
    public function correction(string $sessionId, Request $request): JsonResponse
    {
        // A malformed visit ID can never match retained audio or corrected storage, so it is rejected up front.
        if (!Uuid::isValid($sessionId)) {
            return $this->json(['detail' => 'Invalid session_id: must be a valid UUID'], Response::HTTP_BAD_REQUEST);
        }

        $correctionRequestBody = $request->getContent();
        $agentRequestOptions   = [
            'headers' => ['Accept' => 'application/json'],
            'timeout' => self::AGENT_WORKFLOW_TIMEOUT_SECONDS,
        ];

        // The page sends the rows currently on screen, so any speaker label the clinician already fixed survives the correction pass.
        if ($correctionRequestBody !== '') {
            $agentRequestOptions['headers']['Content-Type'] = 'application/json';
            $agentRequestOptions['body']                    = $correctionRequestBody;
        }

        try {
            $agentResponse = $this->httpClient->request(
                'POST',
                $this->agentUrl("/session/{$sessionId}/correction"),
                $agentRequestOptions,
            );

            return $this->toBrowserJson($agentResponse, 'Transcript correction failed');
        } catch (TransportExceptionInterface $agentUnreachable) {
            // The agent was restarting, or correction ran past the two-minute ceiling on a long visit, so no corrected rows came back.
            // This is deliberately non-fatal: the page moves on and the summary is built from the live rows instead.
            return $this->json([
                                   'detail' => 'Correction service unavailable: ' . $agentUnreachable->getMessage(),
                               ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Fetches the corrected transcript rows that a generated note was actually built from.
     *
     * Runs when the clinician opens the Transcript tab beside a summary to check the note against the text behind it.
     *
     * @param string $sessionId - Visit UUID from the page; anything that is not a UUID cannot match corrected storage.
     *
     * @return JsonResponse - Corrected rows; empty segments mean correction never ran and the tab falls back to the live rows.
     */
    #[Route('/session/{sessionId}/corrected-transcript', name: 'scribe_corrected_transcript_proxy', methods: ['GET'])]
    public function correctedTranscript(string $sessionId): JsonResponse
    {
        // A malformed visit ID can never match corrected storage, so it is rejected before any network call is made.
        if (!Uuid::isValid($sessionId)) {
            return $this->json(['detail' => 'Invalid session_id: must be a valid UUID'], Response::HTTP_BAD_REQUEST);
        }

        try {
            $agentResponse = $this->httpClient->request(
                'GET',
                $this->agentUrl("/session/{$sessionId}/corrected-transcript"),
                [
                    'headers' => ['Accept' => 'application/json'],
                    'timeout' => 10,
                ],
            );

            return $this->toBrowserJson($agentResponse, 'Corrected transcript unavailable');
        } catch (TransportExceptionInterface $agentUnreachable) {
            // The clinician opened the Transcript tab while the agent was down, so the corrected rows could not be fetched.
            // Empty segments come back instead, and the tab shows the live rows the page is already holding.
            return $this->json([
                                   'session_id' => $sessionId,
                                   'segments'   => [],
                                   'detail'     => 'Corrected transcript service unavailable: ' . $agentUnreachable->getMessage(),
                               ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Checks that the off-GPU models Python needs are loaded, before a consultation is allowed to start.
     *
     * Runs as the clinician reaches for Start, so they are never recording a visit whose roles and summary would fail later.
     *
     * @return JsonResponse - `{available, detail}` the page uses to enable the start button or explain why it is blocked.
     */
    #[Route('/agent/model-health', name: 'scribe_model_health_proxy', methods: ['GET'])]
    public function modelHealth(): JsonResponse
    {
        try {
            $agentResponse = $this->httpClient->request(
                'GET',
                $this->agentUrl('/agent/model-health'),
                [
                    'headers' => ['Accept' => 'application/json'],
                    'timeout' => 8,
                ],
            );

            return $this->toBrowserJson($agentResponse, 'Model health check failed');
        } catch (TransportExceptionInterface $agentUnreachable) {
            // The clinician opened the page before the agent container finished booting, so the check could not run at all.
            // Reporting unavailable keeps the start button blocked, which is the safe answer while models may still be loading.
            return $this->json([
                                   'available' => false,
                                   'detail'    => 'agent unreachable: ' . $agentUnreachable->getMessage(),
                               ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Converts whatever FastAPI returned into JSON the page can always read, whatever state Python was in.
     *
     * Use this on every proxied route, so a panel never has to cope with an HTML error page or an empty body.
     *
     * @param ResponseInterface $agentResponse  - The raw FastAPI response; a non-JSON or empty body is replaced by the fallback detail.
     * @param string            $fallbackDetail - Plain message shown to the clinician when Python's own wording cannot be used.
     *
     * @return JsonResponse - Browser-safe JSON, keeping Python's status code whenever that code is one a browser can be given.
     */
    private function toBrowserJson(ResponseInterface $agentResponse, string $fallbackDetail): JsonResponse
    {
        $statusCode   = $this->browserSafeStatusCode($agentResponse->getStatusCode());
        $responseBody = $agentResponse->getContent(false);

        try {
            $agentPayload = json_decode(
                json:        $responseBody,
                associative: true,
                depth:       512,
                flags:       JSON_THROW_ON_ERROR,
            );
        } catch (\JsonException) {
            // Python returned an HTML error page rather than JSON, which is what a crashed worker behind a proxy produces.
            // The panel shows the plain fallback message instead of raw markup.
            $agentPayload = ['detail' => $fallbackDetail];
        }

        // Valid JSON but not an object, such as a bare string or number, which would leave the panel with no detail to display.
        if (!\is_array($agentPayload)) {
            $agentPayload = ['detail' => $fallbackDetail];
        }

        return $this->json($agentPayload, $statusCode);
    }

    /**
     * Builds the absolute FastAPI URL behind a same-origin proxy route.
     *
     * @param string $path - FastAPI path starting with `/`; an empty path would target the agent root rather than a route.
     *
     * @return string - Full URL for the HTTP client, with any trailing slash on the configured endpoint removed first.
     */
    private function agentUrl(string $path): string
    {
        return rtrim($this->agentEndpoint, '/') . $path;
    }

    /**
     * Keeps a proxied status code inside the range Symfony and browsers will accept.
     *
     * @param int $statusCode - Status Python reported; anything outside 100-599 means the proxy state cannot be expressed to a browser.
     *
     * @return int - The original code where usable, otherwise 502 so the page shows a gateway error.
     */
    private function browserSafeStatusCode(int $statusCode): int
    {
        // Symfony throws on a status outside this range, so an unusable upstream code becomes a gateway error the panel can render.
        if ($statusCode < 100 || $statusCode > 599) {
            return Response::HTTP_BAD_GATEWAY;
        }

        return $statusCode;
    }

    /**
     * Returns the stored transcript for a visit the clinician has come back to.
     *
     * Runs when a finished consultation is reopened and the page needs to repopulate a transcript it no longer holds in memory.
     *
     * @param string $sessionId - Visit ID from the URL; the route always supplies one, so it is passed to Python exactly as given.
     *
     * @return JsonResponse - Transcript payload; empty segments mean Python stored no words for that visit.
     */
    #[Route('/scribe/{sessionId}/history', name: 'scribe_history', methods: ['GET'])]
    public function history(string $sessionId): JsonResponse
    {
        try {
            $storedTranscript = $this->strandsClient->postJson(
                "/session/{$sessionId}/history",
                [],
                timeout: 10,
            );

            return $this->json($storedTranscript);
        } catch (AgentErrorException $agentRefused) {
            // Python answered but refused: usually the visit has aged out of the store, or the clinician followed a stale link.
            // The page receives empty segments with the reason, and shows an empty transcript rather than an error screen.
            return $this->json([
                                   'session_id' => $sessionId,
                                   'segments'   => [],
                                   'error'      => $agentRefused->getMessage(),
                               ], $agentRefused->statusCode >= 500 ? Response::HTTP_BAD_GATEWAY : Response::HTTP_NOT_FOUND);
        } catch (StrandsException $agentUnreachable) {
            // Python never answered at all, for example while the agent container is restarting after a deploy.
            // The page still renders, with an empty transcript and a message saying the agent is unavailable.
            return $this->json([
                                   'session_id' => $sessionId,
                                   'segments'   => [],
                                   'error'      => 'Agent unavailable: ' . $agentUnreachable->getMessage(),
                               ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Returns the current speaker-to-role labels for a visit.
     *
     * Use this when roles are needed immediately rather than waiting for Python's next live update to arrive over Mercure.
     *
     * @param string $sessionId - Visit ID from the URL; the route always supplies one, so it goes straight to the snapshot service.
     *
     * @return JsonResponse - Visit ID plus the role snapshot; an empty mapping tells the page to keep every speaker unlabelled.
     */
    #[Route('/scribe/{sessionId}/roles', name: 'scribe_roles', methods: ['GET'])]
    public function roles(string $sessionId): JsonResponse
    {
        $roleSnapshot = $this->roleInferenceService->getRoleSnapshot($sessionId);

        // No speaker has a role yet, and an empty PHP array would encode as `[]` rather than as an object.
        // Forcing `{}` keeps `mapping` an object in the JSON contract whether or not any role has been assigned.
        if (isset($roleSnapshot['mapping']) && \is_array($roleSnapshot['mapping']) && $roleSnapshot['mapping'] === []) {
            $roleSnapshot['mapping'] = new \stdClass();
        }

        return $this->json([
                               'session_id' => $sessionId,
                               ...$roleSnapshot,
                           ]);
    }

    /**
     * Saves a speaker role the clinician corrected by hand, so later inference cannot overwrite it.
     *
     * Runs the moment they click a speaker label and pick the right one; the label changes on screen at once, and this makes it stick.
     *
     * @param string  $sessionId - Visit UUID from the page; anything that is not a UUID cannot match the role state Python protects.
     * @param Request $request   - JSON body naming the speaker and the role; an empty body lets Python return its own validation message.
     *
     * @return JsonResponse - The saved mapping; a 503 means only the on-screen correction survives and nothing was persisted.
     */
    #[Route('/scribe/{sessionId}/roles/override', name: 'scribe_roles_override_proxy', methods: ['POST'])]
    public function rolesOverride(string $sessionId, Request $request): JsonResponse
    {
        // A malformed visit ID can never match the role state Python holds, so it is rejected before any network call is made.
        if (!Uuid::isValid($sessionId)) {
            return $this->json(['detail' => 'Invalid session_id: must be a valid UUID'], Response::HTTP_BAD_REQUEST);
        }

        try {
            $agentResponse = $this->httpClient->request(
                'POST',
                $this->agentUrl("/session/{$sessionId}/roles/override"),
                [
                    'headers' => [
                        'Accept'       => 'application/json',
                        'Content-Type' => 'application/json',
                    ],
                    'body'    => $request->getContent(),
                    'timeout' => 8,
                ],
            );

            return $this->toBrowserJson($agentResponse, 'Role override failed');
        } catch (TransportExceptionInterface $agentUnreachable) {
            // The clinician relabelled a speaker while the agent was unreachable, so the correction was never written down.
            // It stays correct on their screen, but a later role update from Python can undo it.
            return $this->json([
                                   'detail' => 'Role override service unavailable: ' . $agentUnreachable->getMessage(),
                               ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Redirects the bare app URL to the scribe workspace.
     *
     * Runs when someone opens the site root, so there is no landing page to get lost on before a consultation starts.
     *
     * @return Response - Redirect that lands the browser on `/scribe`.
     */
    #[Route('/', name: 'home', methods: ['GET'])]
    public function home(): Response
    {
        return $this->redirectToRoute('scribe_index');
    }
}
