<?php

declare(strict_types=1);

namespace App\Controller;

use App\Service\RoleInferenceService;
use StrandsPhpClient\Exceptions\AgentErrorException;
use StrandsPhpClient\Exceptions\StrandsException;
use StrandsPhpClient\StrandsClient;
use Symfony\Bundle\FrameworkBundle\Controller\AbstractController;
use Symfony\Component\DependencyInjection\Attribute\Autowire;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\HttpFoundation\StreamedJsonResponse;
use Symfony\Component\Routing\Attribute\Route;
use Symfony\Component\Uid\Uuid;

/**
 * Controller for the Ambient Medical Scribe application.
 *
 * Endpoints:
 *   GET  /scribe                    - Renders the transcript UI with session config
 *   GET  /scribe/{id}/history       - Returns the full transcript for a completed session
 *   POST /scribe/{id}/roles/stream  - Streams progressive role inference via SSE
 *   GET  /scribe/{id}/roles         - Returns the current role mapping snapshot
 *
 * ARCHITECTURE NOTE:
 *   Symfony serves the initial page and provides session history + role inference.
 *   Live audio flows directly from the browser to Python via WebSocket.
 *   Transcript segments are delivered via Mercure SSE (Python → Mercure → Browser).
 *   PHP does NOT touch the live audio hot path.
 */
class ScribeController extends AbstractController
{
    public function __construct(
        #[Autowire(service: 'strands.client.scribe')]
        private readonly StrandsClient $strandsClient,
        private readonly RoleInferenceService $roleInferenceService,
    ) {
    }

    /**
     * GET /scribe - Render the transcript UI.
     *
     * Generates a fresh session ID and passes WebSocket + Mercure URLs
     * to the Twig template. The browser uses these to connect directly
     * to the Python agent for audio streaming and to Mercure for
     * receiving transcript segments.
     */
    #[Route('/scribe', name: 'scribe_index', methods: ['GET'])]
    public function index(): Response
    {
        $sessionId = Uuid::v4()->toRfc4122();
        $wsUrl = $this->getParameter('nemo_websocket_url');
        $mercureUrl = $this->getParameter('mercure_url');

        return $this->render('scribe/index.html.twig', [
            'session_id' => $sessionId,
            'ws_url' => $wsUrl,
            'mercure_url' => $mercureUrl,
            'mercure_topic_raw' => "scribe/session/{$sessionId}/raw",
            'mercure_topic_roles' => "scribe/session/{$sessionId}/roles",
        ]);
    }

    /**
     * GET /scribe/{sessionId}/history - Fetch the full transcript for a session.
     *
     * Calls the Python agent's /session/{id}/history endpoint via postJson()
     * to retrieve the complete transcript. Uses a short timeout since this
     * is just fetching stored data, not running inference.
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
     * POST /scribe/{sessionId}/roles/stream - Stream progressive role inference.
     *
     * Uses streamSse() to consume the Python agent's role inference endpoint.
     * Returns a streamed JSON response where each line is a role_update event.
     * The browser can read this with fetch() + ReadableStream or EventSource.
     *
     * Per-request timeout: 15s (LLM inference via Bedrock, not instant).
     * Cancellation: consumer returns false to stop the stream early.
     */
    #[Route('/scribe/{sessionId}/roles/stream', name: 'scribe_roles_stream', methods: ['POST'])]
    public function rolesStream(string $sessionId): StreamedJsonResponse
    {
        $events = [];

        $result = $this->roleInferenceService->streamRoleInference(
            $sessionId,
            function (array $event) use (&$events): null {
                $events[] = $event;

                return null;
            },
        );

        return new StreamedJsonResponse([
            'session_id' => $sessionId,
            'events' => $events,
            'result' => $result->toArray(),
        ]);
    }

    /**
     * GET /scribe/{sessionId}/roles - Current role mapping snapshot.
     *
     * Quick lookup using postJson() with a 5s timeout.
     * Returns the latest mapping without streaming.
     */
    #[Route('/scribe/{sessionId}/roles', name: 'scribe_roles', methods: ['GET'])]
    public function roles(string $sessionId): JsonResponse
    {
        $mapping = $this->roleInferenceService->getCurrentMapping($sessionId);

        return $this->json([
            'session_id' => $sessionId,
            ...$mapping,
        ]);
    }

    /**
     * GET / - Redirect to /scribe.
     */
    #[Route('/', name: 'home', methods: ['GET'])]
    public function home(): Response
    {
        return $this->redirectToRoute('scribe_index');
    }
}
