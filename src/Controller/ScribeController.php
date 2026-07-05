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
 * Serves the initial `/scribe` page and the small JSON helpers behind it.
 *
 * Use this controller when the clinician loads the page, downloads history, or refreshes role labels.
 * Python owns transcription and role inference; this class keeps the browser supplied with routing context.
 */
class ScribeController extends AbstractController
{
    /** Summaries should wait for agent work without hanging forever. */
    private const AGENT_WORKFLOW_TIMEOUT_SECONDS = 120;

    /**
     * Wires the page controller to the Python client, role snapshot service, and UI warning logger.
     *
     * @param StrandsClient        $strandsClient        Sends history requests to Python; no live audio is routed here.
     * @param RoleInferenceService $roleInferenceService Reads the role snapshot the UI can poll after recording.
     * @param LoggerInterface      $logger               Records fixture issues that only affect the developer audio picker.
     * @param HttpClientInterface  $httpClient           Proxies browser summary and model-health actions to FastAPI.
     * @param string               $agentEndpoint        FastAPI base URL; empty means summaries cannot be proxied.
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
     * Renders `/scribe` with fresh session IDs and browser connection settings.
     *
     * @return Response Twig response containing WebSocket, Mercure, role, and dev-audio setup for the page.
     */
    #[Route('/scribe', name: 'scribe_index', methods: ['GET'])]
    public function index(): Response
    {
        $sessionId  = Uuid::v4()->toRfc4122();
        $wsUrl      = $this->getParameter('nemo_websocket_url');
        $mercureUrl = $this->getParameter('mercure_url');

        $devPanelEnabled = $this->getParameter('kernel.environment') === 'dev';
        $audioFixtures   = [];
        // Hit when a developer opens `/scribe` locally and needs generated WAV replay choices.
        if ($devPanelEnabled) {
            /** @var string $projectDir Project root lets the dev panel find audio fixtures; absent means no picker data. */
            $projectDir    = $this->getParameter('kernel.project_dir');
            $audioFixtures = $this->loadDemoAudioFixtures($projectDir);
        }

        return $this->render('scribe/index.html.twig', [
            'session_id'            => $sessionId,
            'ws_url'                => $wsUrl,
            'mercure_url'           => $mercureUrl,
            'mercure_topic_raw'     => "scribe/session/{$sessionId}/raw",
            'mercure_topic_roles'   => "scribe/session/{$sessionId}/roles",
            'mercure_topic_summary' => "scribe/session/{$sessionId}/summary",
            'mercure_topic_hints'   => "scribe/session/{$sessionId}/hints",
            'enable_role_updates'   => true,
            'dev_panel_enabled'     => $devPanelEnabled,
            'audio_fixtures'        => $audioFixtures,
        ]);
    }

    /**
     * Serves one generated WAV fixture from the built-in-server-safe picker URL.
     *
     * Use this route in dev because PHP's built-in server 404s dotted `.wav` path segments
     * before Symfony sees them. The browser calls it after a user clicks a Demo Audio row.
     *
     * @param Request $request Browser request; missing filename means no row can be replayed.
     *
     * @return Response WAV response; 404 means the picker cannot replay that file.
     */
    #[Route('/scribe/demo-audio', name: 'scribe_demo_audio', methods: ['GET'])]
    public function demoAudio(Request $request): Response
    {
        $filename = $request->query->get('filename');

        // Empty query means the picker did not identify which WAV to replay.
        if (!\is_string($filename) || $filename === '') {
            return new Response('Not found', Response::HTTP_NOT_FOUND);
        }

        return $this->serveDemoAudioFixture($filename);
    }

    /**
     * Builds the dev-only WAV response for the selected picker file.
     *
     * Use when the browser asks Symfony for a generated test fixture before streaming replay.
     * Empty, unsafe, unknown, or missing files all return 404 from the user's perspective.
     *
     * @param string $filename Fixture filename selected in the UI; empty or unsafe names return 404.
     *
     * @return Response WAV response; 404 means the selected audio cannot be replayed.
     */
    private function serveDemoAudioFixture(string $filename): Response
    {
        // Demo audio is a local developer aid and should not be exposed in production.
        if ($this->getParameter('kernel.environment') !== 'dev') {
            return new Response('Not found', Response::HTTP_NOT_FOUND);
        }

        // The browser only needs simple generated WAV names from the manifest.
        if (!preg_match('/^[A-Za-z0-9._-]+\.wav$/', $filename)) {
            return new Response('Not found', Response::HTTP_NOT_FOUND);
        }

        /** @var string $projectDir Project root used to resolve checked-in fixture metadata. */
        $projectDir       = $this->getParameter('kernel.project_dir');
        $audioFixtures    = $this->loadDemoAudioFixtures($projectDir);
        $allowedFilenames = array_column($audioFixtures, 'filename');

        // Unknown files should not let the browser read arbitrary project paths.
        if (!\in_array($filename, $allowedFilenames, true)) {
            return new Response('Not found', Response::HTTP_NOT_FOUND);
        }

        $audioPath = $projectDir . '/tests/fixtures/audio/' . $filename;
        // Deleted generated audio leaves the row unavailable instead of exposing a broken response.
        if (!is_file($audioPath)) {
            return new Response('Not found', Response::HTTP_NOT_FOUND);
        }

        $response = new BinaryFileResponse($audioPath);
        $response->headers->set('Content-Type', 'audio/wav');
        $response->setContentDisposition(ResponseHeaderBag::DISPOSITION_INLINE, $filename);

        return $response;
    }

    /**
     * Loads generated demo WAV metadata for the left-side dev audio picker.
     *
     * @param string $projectDir Project root; empty or missing fixtures produce no picker rows.
     *
     * @return list<array<string, mixed>> Audio rows; empty means the panel shows no generated files.
     */
    private function loadDemoAudioFixtures(string $projectDir): array
    {
        $manifestPath   = $projectDir . '/tests/fixtures/audio/generated-manifest.json';
        $audioDirectory = $projectDir . '/tests/fixtures/audio';
        // Missing manifest means the generated WAV corpus has not been created yet.
        if (!is_file($manifestPath)) {
            return [];
        }

        $manifestContents = file_get_contents($manifestPath);
        // A failed read leaves the page usable but without demo audio rows.
        if (!\is_string($manifestContents)) {
            return [];
        }

        try {
            /** @var mixed $manifestEntries Parsed manifest; non-list data is ignored below. */
            $manifestEntries = json_decode(
                json:        $manifestContents,
                associative: true,
                depth:       512,
                flags:       JSON_THROW_ON_ERROR,
            );
        } catch (\JsonException $e) {
            $this->logger->warning('Scribe audio fixture manifest is invalid JSON', [
                'path'  => $manifestPath,
                'error' => $e->getMessage(),
            ]);

            return [];
        }

        // Invalid manifest shape should not break the clinician page.
        if (!\is_array($manifestEntries)) {
            return [];
        }

        $audioFixtures = [];
        // Each manifest entry becomes one playable row when the WAV is present.
        foreach ($manifestEntries as $manifestEntry) {
            // Non-object rows are skipped so one bad entry does not hide good demos.
            if (!\is_array($manifestEntry)) {
                continue;
            }

            $filename = $manifestEntry['filename'] ?? '';
            // Unsafe or missing filenames are not exposed to the browser.
            if (!\is_string($filename) || !preg_match('/^[A-Za-z0-9._-]+\.wav$/', $filename)) {
                continue;
            }

            $audioPath = $audioDirectory . '/' . $filename;
            // Manifest rows without generated WAVs cannot be played yet.
            if (!is_file($audioPath)) {
                continue;
            }

            $complaint       = $manifestEntry['complaint'] ?? '';
            $edgeCase        = $manifestEntry['edge_case'] ?? '';
            $speakers        = $manifestEntry['speakers'] ?? [];
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
     * Proxies post-consult summary generation to FastAPI from the same browser origin.
     *
     * Use after a live stop or replay completion when transcript text exists. The response stays JSON
     * even when FastAPI is down, so the summary panel can show a plain retryable message.
     *
     * @param string  $sessionId Browser session UUID; invalid values mean no transcript can be summarized.
     * @param Request $request   Browser request; empty body means FastAPI should use stored transcript text.
     *
     * @return JsonResponse Summary payload; 404/502/503 are shown as recoverable panel states.
     */
    #[Route('/session/{sessionId}/summary', name: 'scribe_summary_proxy', methods: ['POST'])]
    public function summary(string $sessionId, Request $request): JsonResponse
    {
        // Invalid sessions cannot map to stored transcript text in FastAPI.
        if (!Uuid::isValid($sessionId)) {
            return $this->json(['detail' => 'Invalid session_id: must be a valid UUID'], Response::HTTP_BAD_REQUEST);
        }

        $summaryRequestBody  = $request->getContent();
        $agentRequestOptions = [
            'headers' => ['Accept' => 'application/json'],
            'timeout' => self::AGENT_WORKFLOW_TIMEOUT_SECONDS,
        ];

        // The browser sends its visible transcript subset so the note matches the screen.
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

            return $this->jsonAgentResponse($agentResponse, 'Summary generation failed');
        } catch (TransportExceptionInterface $e) {
            return $this->json([
                                   'detail' => 'Summary service unavailable: ' . $e->getMessage(),
                               ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Proxies the off-GPU model-health pre-flight check to FastAPI from the browser origin.
     *
     * The browser calls this before starting a consultation so it does not transcribe when
     * role inference and the summary would fail. An unreachable agent is reported as unavailable.
     *
     * @return JsonResponse `{available: bool, detail: string}` the UI uses to gate the start.
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

            return $this->jsonAgentResponse($agentResponse, 'Model health check failed');
        } catch (TransportExceptionInterface $e) {
            return $this->json([
                                   'available' => false,
                                   'detail'    => 'agent unreachable: ' . $e->getMessage(),
                               ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Converts a FastAPI JSON response into the browser-facing Symfony JSON shape.
     *
     * @param ResponseInterface $agentResponse  FastAPI response; empty or non-JSON bodies become a safe detail.
     * @param string            $fallbackDetail User-facing detail when FastAPI returns HTML or malformed JSON.
     *
     * @return JsonResponse Browser-safe JSON response with the upstream status preserved where possible.
     */
    private function jsonAgentResponse(ResponseInterface $agentResponse, string $fallbackDetail): JsonResponse
    {
        $statusCode   = $this->browserSafeStatusCode($agentResponse->getStatusCode());
        $responseBody = $agentResponse->getContent(false);

        try {
            $payload = json_decode(
                json:        $responseBody,
                associative: true,
                depth:       512,
                flags:       JSON_THROW_ON_ERROR,
            );
        } catch (\JsonException) {
            $payload = ['detail' => $fallbackDetail];
        }

        // Non-object JSON would leave the browser without a meaningful error/detail field.
        if (!\is_array($payload)) {
            $payload = ['detail' => $fallbackDetail];
        }

        return $this->json($payload, $statusCode);
    }

    /**
     * Builds an absolute FastAPI URL for same-origin browser proxy routes.
     *
     * @param string $path FastAPI path beginning with `/`; empty would point at the agent root.
     *
     * @return string Full FastAPI URL used by Symfony's HTTP client.
     */
    private function agentUrl(string $path): string
    {
        return rtrim($this->agentEndpoint, '/') . $path;
    }

    /**
     * Keeps proxy responses inside the HTTP status range browsers and Symfony accept.
     *
     * @param int $statusCode FastAPI status code; invalid values mean transport/proxy state is unknown.
     *
     * @return int Browser-safe HTTP status; 502 means the upstream status was unusable.
     */
    private function browserSafeStatusCode(int $statusCode): int
    {
        // Invalid upstream status codes should surface as a gateway error, not crash Symfony.
        if ($statusCode < 100 || $statusCode > 599) {
            return Response::HTTP_BAD_GATEWAY;
        }

        return $statusCode;
    }

    /**
     * Fetches a stored transcript when the browser asks to review a finished session.
     *
     * @param string $sessionId Session shown in the UI; empty should not occur because routes provide it.
     *
     * @return JsonResponse Transcript payload; empty segments mean Python had no saved words for that session.
     */
    #[Route('/scribe/{sessionId}/history', name: 'scribe_history', methods: ['GET'])]
    public function history(string $sessionId): JsonResponse
    {
        try {
            $result = $this->strandsClient->postJson(
                "/session/{$sessionId}/history",
                [],
                timeout: 10,
            );

            return $this->json($result);
        } catch (AgentErrorException $e) {
            return $this->json([
                                   'session_id' => $sessionId,
                                   'segments'   => [],
                                   'error'      => $e->getMessage(),
                               ], $e->statusCode >= 500 ? Response::HTTP_BAD_GATEWAY : Response::HTTP_NOT_FOUND);
        } catch (StrandsException $e) {
            return $this->json([
                                   'session_id' => $sessionId,
                                   'segments'   => [],
                                   'error'      => 'Agent unavailable: ' . $e->getMessage(),
                               ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Returns the latest speaker-to-role labels for the current browser session.
     *
     * @param string $sessionId Session currently visible in the page; empty should not occur from the route.
     *
     * @return JsonResponse Role snapshot; an empty mapping tells the UI to keep speakers as unknown.
     */
    #[Route('/scribe/{sessionId}/roles', name: 'scribe_roles', methods: ['GET'])]
    public function roles(string $sessionId): JsonResponse
    {
        $mapping = $this->roleInferenceService->getCurrentMapping($sessionId);

        // No assigned roles yet: keep JSON as `{}` so the browser does not treat mapping as a segment list.
        if (isset($mapping['mapping']) && \is_array($mapping['mapping']) && $mapping['mapping'] === []) {
            $mapping['mapping'] = new \stdClass();
        }

        return $this->json([
                               'session_id' => $sessionId,
                               ...$mapping,
                           ]);
    }

    /**
     * Proxies a manual speaker role correction to FastAPI from the browser origin.
     *
     * Use when a clinician clicks a transcript speaker label. The visible label changes immediately,
     * and this route persists the correction so later role-agent updates cannot overwrite it.
     *
     * @param string  $sessionId Browser session UUID; invalid values mean no role state can be corrected.
     * @param Request $request   JSON body with speaker_id and role; empty body lets FastAPI return validation JSON.
     *
     * @return JsonResponse Persisted role mapping; 503 means the page keeps only the local visible correction.
     */
    #[Route('/scribe/{sessionId}/roles/override', name: 'scribe_roles_override_proxy', methods: ['POST'])]
    public function rolesOverride(string $sessionId, Request $request): JsonResponse
    {
        // Invalid sessions cannot map to the role state FastAPI protects for the user.
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

            return $this->jsonAgentResponse($agentResponse, 'Role override failed');
        } catch (TransportExceptionInterface $e) {
            return $this->json([
                                   'detail' => 'Role override service unavailable: ' . $e->getMessage(),
                               ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Sends users from the bare app URL into the clinician scribe workspace.
     *
     * @return Response Redirect response that lands the browser on `/scribe`.
     */
    #[Route('/', name: 'home', methods: ['GET'])]
    public function home(): Response
    {
        return $this->redirectToRoute('scribe_index');
    }
}
