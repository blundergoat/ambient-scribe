<?php

/**
 * Symfony entry point for the clinician-facing scribe page.
 *
 * The browser reaches this file when a user opens `/scribe`, requests saved transcript history,
 * or asks for the latest role labels. Live microphone audio stays on the browser-to-Python path.
 * Keep this file focused on page setup and lightweight JSON responses for the UI.
 */

declare(strict_types=1);

namespace App\Controller;

use App\Service\RoleInferenceService;
use Psr\Log\LoggerInterface;
use StrandsPhpClient\Exceptions\AgentErrorException;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;
use Symfony\Bundle\FrameworkBundle\Controller\AbstractController;
use Symfony\Component\DependencyInjection\Attribute\Autowire;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Attribute\Route;
use Symfony\Component\Uid\Uuid;

/**
 * Serves the initial `/scribe` page and the small JSON helpers behind it.
 *
 * Use this controller when the clinician loads the page, downloads history, or refreshes role labels.
 * Python owns transcription and role inference; this class keeps the browser supplied with routing context.
 */
class ScribeController extends AbstractController
{
    /**
     * Wires the page controller to the Python client, role snapshot service, and UI warning logger.
     *
     * @param StrandsClient $strandsClient Sends history requests to Python; no live audio is routed here.
     * @param RoleInferenceService $roleInferenceService Reads the role snapshot the UI can poll after recording.
     * @param LoggerInterface $logger Records fixture issues that only affect the developer scenario picker.
     */
    public function __construct(
        #[Autowire(service: 'strands.client.scribe')]
        private readonly StrandsClient $strandsClient,
        private readonly RoleInferenceService $roleInferenceService,
        private readonly LoggerInterface $logger,
    ) {
    }

    /**
     * Renders `/scribe` with fresh session IDs and browser connection settings.
     *
     * @return Response Twig response containing WebSocket, Mercure, role, and dev-scenario setup for the page.
     */
    #[Route('/scribe', name: 'scribe_index', methods: ['GET'])]
    public function index(): Response
    {
        $sessionId = Uuid::v4()->toRfc4122();
        $wsUrl = $this->getParameter('nemo_websocket_url');
        $mercureUrl = $this->getParameter('mercure_url');

        $devPanelEnabled = $this->getParameter('kernel.environment') === 'dev';
        $scenarios = [];
        // Hit when a developer opens `/scribe` locally and needs the scenario picker for fixture replay.
        if ($devPanelEnabled) {
            /** @var string $projectDir Project root lets the dev panel find scenario fixtures; absent means no picker data. */
            $projectDir = $this->getParameter('kernel.project_dir');
            $path = $projectDir . '/tests/fixtures/scribe/scenarios.json';
            // Missing fixture file means the local tester sees an empty scenario list instead of a broken page.
            if (file_exists($path)) {
                $contents = file_get_contents($path);
                // A failed file read leaves the scenario picker empty while the clinical page still renders.
                if (\is_string($contents)) {
                    try {
                        /** @var array{scenarios?: list<array<string, mixed>>} $decoded Parsed dev fixtures; empty scenarios hide replay choices. */
                        $decoded = json_decode(
                            json: $contents,
                            associative: true,
                            depth: 512,
                            flags: JSON_THROW_ON_ERROR,
                        );
                        $scenarios = $decoded['scenarios'] ?? [];
                    } catch (\JsonException $e) {
                        $this->logger->warning('Scribe scenarios fixture is invalid JSON', [
                            'path' => $path,
                            'error' => $e->getMessage(),
                        ]);
                    }
                }
            }
        }

        return $this->render('scribe/index.html.twig', [
            'session_id' => $sessionId,
            'ws_url' => $wsUrl,
            'mercure_url' => $mercureUrl,
            'mercure_topic_raw' => "scribe/session/{$sessionId}/raw",
            'mercure_topic_roles' => "scribe/session/{$sessionId}/roles",
            'mercure_topic_summary' => "scribe/session/{$sessionId}/summary",
            'mercure_topic_hints' => "scribe/session/{$sessionId}/hints",
            'enable_role_updates' => true,
            'dev_panel_enabled' => $devPanelEnabled,
            'scenarios' => $scenarios,
        ]);
    }

    /**
     * Fetches a stored transcript when the browser asks to review a finished session.
     *
     * @param string $sessionId Session shown in the UI; empty should not occur because routes provide it.
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
                'segments' => [],
                'error' => $e->getMessage(),
            ], $e->statusCode >= 500 ? Response::HTTP_BAD_GATEWAY : Response::HTTP_NOT_FOUND);
        } catch (StrandsException $e) {
            return $this->json([
                'session_id' => $sessionId,
                'segments' => [],
                'error' => 'Agent unavailable: ' . $e->getMessage(),
            ], Response::HTTP_SERVICE_UNAVAILABLE);
        }
    }

    /**
     * Returns the latest speaker-to-role labels for the current browser session.
     *
     * @param string $sessionId Session currently visible in the page; empty should not occur from the route.
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
