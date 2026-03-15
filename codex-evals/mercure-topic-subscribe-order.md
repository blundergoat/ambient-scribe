# Mercure Topic Subscribe Order

- Origin: real-history
- Source commit: `0125a6b`
- Bug description: The first Mercure topic subscription silently missed events because `StreamOrchestrator.subscribe()` called `_connect()` before setting `_active = true`.
- Replay prompt: Review `templates/scribe/index.html.twig`. A regression causes the first subscribed Mercure topic to miss events until a reconnect. Fix the smallest possible issue and explain how you verified the old behaviour is gone.
- Expected outcome: Update the subscription flow so `_active` is true before `_connect()` runs, keep the fix local to the stream orchestrator, and mention a targeted verification step.
- Failure mode tested: Implement-mode regression fix in a live browser streaming path without unnecessary refactor.
