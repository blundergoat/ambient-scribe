// =========================================================================
// Ambient Scribe dev-panel browser flow for local verification.
// Runs only when APP_ENV=dev renders SCENARIOS beside the consultation UI.
// Lets a developer replay medical scenarios, inspect Mercure/WebSocket state,
// and compare dev logs with the same transcript the clinician would see.
// All model/scenario text is rendered as textContent, not HTML.
// =========================================================================

let isScenarioModeActive = false;

/**
 * Shows or hides the demo-scenario panel on small screens.
 * Use when a developer taps the scenarios icon while testing /scribe.
 */
function toggleScenarioPanel() {
    const scenarioPanel = document.getElementById('scenarioPanel');
    const scenarioBackdrop = document.getElementById('scenarioPanelBackdrop');

    // Production pages and some tests do not render the scenario panel.
    if (!scenarioPanel) {
        return;
    }

    const isVisible = scenarioPanel.style.display !== 'none' && scenarioPanel.style.display !== '';
    scenarioPanel.style.display = isVisible ? 'none' : 'flex';

    // The backdrop follows the panel so mobile users can close it cleanly.
    if (scenarioBackdrop) {
        scenarioBackdrop.style.display = isVisible ? 'none' : 'block';
    }
}

/**
 * Shows or hides the dev-inspector panel on small screens.
 * Use when a developer taps the dev-panel icon while checking a session.
 */
function toggleDevPanelMobile() {
    const devPanelElement = document.getElementById('devPanel');
    const devBackdrop = document.getElementById('devPanelBackdrop');

    // Production pages and some tests do not render the dev panel.
    if (!devPanelElement) {
        return;
    }

    const isVisible = devPanelElement.style.display !== 'none' && devPanelElement.style.display !== '';
    devPanelElement.style.display = isVisible ? 'none' : 'flex';

    // The backdrop follows the panel so mobile users can close it cleanly.
    if (devBackdrop) {
        devBackdrop.style.display = isVisible ? 'none' : 'block';
    }
}

/**
 * Dev inspector for transcript, raw events, Mercure, WebSocket, and state.
 * Users reach this only in local dev beside the normal consultation UI.
 * It mirrors what the clinician sees while exposing enough detail to debug.
 */
class DevPanel {
    /**
     * Prepares dev-only buffers and counters.
     * Use once when the dev page loads.
     */
    constructor() {
        this._activeTab = 'segments';
        this._rawBuffer = [];
        this._rawMax = 200;
        this._stateInterval = null;
        this._wsMetrics = { framesSent: 0, framesReceived: 0, bytesSent: 0, bytesReceived: 0 };
        this._mercureMetrics = new Map();
        this._instrumentedSockets = new WeakSet();
    }

    /**
     * Starts dev-panel state refresh and scenario list rendering.
     * Use on DOMContentLoaded after the production UI is ready.
     */
    init() {
        const savedTab = localStorage.getItem('ambient-scribe-devtab');

        // Restoring the saved tab keeps repeated local checks efficient.
        if (savedTab) {
            this.switchTab(savedTab);
        }

        this._stateInterval = setInterval(() => this.refreshState(), 500);
        this.renderScenarioList();
    }

    /**
     * Switches the active dev-panel tab.
     * Use when a developer clicks Segments, Pipeline, Mercure, WS, State, or Raw.
     */
    switchTab(tabName) {
        this._activeTab = tabName;
        localStorage.setItem('ambient-scribe-devtab', tabName);

        // Highlight the tab the developer is currently inspecting.
        for (const tabElement of document.querySelectorAll('.dev-panel__tab')) {
            tabElement.classList.toggle('dev-panel__tab--active', tabElement.dataset.tab === tabName);
        }

        // Only the active tab's content should be visible.
        for (const contentElement of document.querySelectorAll('.dev-panel__content')) {
            contentElement.style.display = contentElement.dataset.content === tabName ? '' : 'none';
        }
    }

    /**
     * Adds one visible transcript event to the dev segment log.
     * Use when a scenario or live Mercure event reaches handleRawSegment.
     */
    logSegment(segmentEvent, role, source) {
        const segmentLog = document.getElementById('devSegmentLog');

        // If the segment log is hidden or absent, normal transcript rendering continues.
        if (!segmentLog) {
            return;
        }

        const entry = createElement('div', {
            className: 'dev-panel__entry',
            dataset: { speakerId: segmentEvent.speaker_id ?? '' },
        });

        // Scenario rows get a short badge so developers can separate fixture text.
        if (source === 'scenario') {
            entry.appendChild(createElement('span', { text: '[S] ', style: 'color:#f59e0b' }));
        }

        const rawText = segmentEvent.text ?? '';
        const previewText = rawText.length > 60 ? `${rawText.substring(0, 57)}\u2026` : rawText;
        entry.append(
            createElement('span', {
                text: getRoleLabel(role),
                style: `color:${getRoleColor(role)}`,
                attributes: { 'data-role-label': '' },
            }),
            document.createTextNode(' '),
            createElement('span', {
                className: 'subtle-text',
                text: formatTime(segmentEvent.start ?? 0),
            }),
            createElement('br'),
            document.createTextNode(previewText)
        );
        segmentLog.appendChild(entry);
        segmentLog.scrollTop = segmentLog.scrollHeight;
    }

    /**
     * Adds raw event JSON to the dev log with an expandable payload.
     * Use when checking the exact event that drove the visible transcript.
     */
    logRaw(eventType, eventPayload) {
        const rawEntry = { ts: new Date().toISOString(), type: eventType, payload: eventPayload };
        this._rawBuffer.push(rawEntry);

        // Keep the raw log bounded so long scenario runs stay responsive.
        if (this._rawBuffer.length > this._rawMax) {
            this._rawBuffer.shift();
        }

        const rawLog = document.getElementById('devRawLog');
        const rawCountElement = document.getElementById('devRawCount');

        // Without the raw log tab, there is nothing visible to append.
        if (!rawLog) {
            return;
        }

        // The raw count mirrors the buffer length shown in the tab header.
        if (rawCountElement) {
            rawCountElement.textContent = this._rawBuffer.length;
        }

        const rawJson = JSON.stringify(eventPayload);
        const previewText = rawJson.length > 80 ? `${rawJson.substring(0, 77)}\u2026` : rawJson;
        const entryElement = createElement('div', { className: 'dev-panel__entry' }, [
            createElement('span', { className: 'subtle-text', text: rawEntry.ts.substring(11, 23) }),
            document.createTextNode(' '),
            createElement('strong', { text: eventType }),
            document.createTextNode(rawJson.length > 80 ? ' \u25B8' : ''),
            createElement('br'),
            document.createTextNode(previewText),
        ]);
        entryElement.style.cursor = 'pointer';
        entryElement.addEventListener('click', () => {
            const existingPayload = entryElement.querySelector('pre');

            // Clicking an expanded row collapses it for easier scanning.
            if (existingPayload) {
                existingPayload.remove();
                return;
            }

            const fullPayload = createElement('pre', {
                className: 'dev-panel__pre',
                text: JSON.stringify(eventPayload, null, 2),
            });
            entryElement.appendChild(fullPayload);
        });

        rawLog.appendChild(entryElement);

        // Remove oldest visible rows to match the bounded raw buffer.
        while (rawLog.children.length > this._rawMax) {
            rawLog.removeChild(rawLog.firstChild);
        }

        rawLog.scrollTop = rawLog.scrollHeight;
    }

    /**
     * Adds one Mercure event row and updates per-topic counts.
     * Use when transcript, role, or summary events arrive in dev mode.
     */
    logMercure(topic, eventPayload) {
        const metrics = this._mercureMetrics.get(topic) ?? { count: 0 };
        metrics.count++;
        this._mercureMetrics.set(topic, metrics);

        const mercureLog = document.getElementById('devMercureLog');

        // Production pages do not render the Mercure inspector.
        if (!mercureLog) {
            return;
        }

        // Replace the waiting placeholder once real events arrive.
        if (mercureLog.children.length === 1 && mercureLog.firstChild.textContent.includes('Waiting')) {
            clearElement(mercureLog);
        }

        const shortTopic = topic.split('/').slice(-2).join('/');
        const entryElement = createElement('div', {
            className: 'dev-panel__entry',
            text: `${shortTopic} #${metrics.count} - ${eventPayload.type ?? 'msg'}`,
        });
        mercureLog.appendChild(entryElement);
        mercureLog.scrollTop = mercureLog.scrollHeight;
    }

    /**
     * Updates the WebSocket status panel from the current recording socket.
     * Use while diagnosing live audio streaming or reconnect behavior.
     */
    updateWsStatus() {
        const wsStatusElement = document.getElementById('devWsStatus');

        // Without the WS panel, there is no visible status to update.
        if (!wsStatusElement) {
            return;
        }

        const socketState = transcriptionSocket ? SOCKET_STATE_LABELS[transcriptionSocket.readyState] : 'None';
        const metrics = this._wsMetrics;
        wsStatusElement.textContent = `State: ${socketState}\nSent: ${metrics.framesSent} frames (${metrics.bytesSent}B)\nRecv: ${metrics.framesReceived} frames (${metrics.bytesReceived}B)`;
    }

    /**
     * Instruments the active WebSocket once for dev metrics.
     * Use when a live socket exists and the WS tab needs byte counts.
     */
    _instrumentWs() {
        // No socket or already-instrumented socket means no metrics wrapper is needed.
        if (!transcriptionSocket || this._instrumentedSockets.has(transcriptionSocket)) {
            return;
        }

        this._instrumentedSockets.add(transcriptionSocket);
        const originalSend = transcriptionSocket.send.bind(transcriptionSocket);
        transcriptionSocket.send = (outboundFrame) => {
            this._wsMetrics.framesSent++;
            this._wsMetrics.bytesSent += outboundFrame.byteLength ?? outboundFrame.length ?? 0;
            return originalSend(outboundFrame);
        };

        const originalOnMessage = transcriptionSocket.onmessage;

        // If production code has a message handler, wrap it after counting bytes.
        if (originalOnMessage) {
            transcriptionSocket.onmessage = (event) => {
                const byteCount = event.data instanceof ArrayBuffer ? event.data.byteLength : event.data?.length ?? 0;
                this._wsMetrics.framesReceived++;
                this._wsMetrics.bytesReceived += byteCount;
                originalOnMessage.call(transcriptionSocket, event);
            };
        }
    }

    /**
     * Refreshes the JSON state snapshot in the dev panel.
     * Use when checking what the visible consultation state currently holds.
     */
    refreshState() {
        this._instrumentWs();
        this.updateWsStatus();
        const stateElement = document.getElementById('devStateSnapshot');

        // Only update the state tab while it is visible to reduce DOM churn.
        if (!stateElement || this._activeTab !== 'state') {
            return;
        }

        stateElement.textContent = JSON.stringify({
            sessionId: CONFIG.sessionId,
            isRecording,
            scenarioMode: isScenarioModeActive,
            segmentIndex,
            roleMapping,
            confidence,
            reconnectAttempts,
            wsState: transcriptionSocket ? SOCKET_STATE_LABELS[transcriptionSocket.readyState] : null,
            mercureConnected: streams?.isConnected ?? false,
            mercureRetries: streams?.totalRetries ?? 0,
        }, null, 2);
    }

    /**
     * Clears the dev segment log.
     * Use when starting a new scenario or when the developer clicks Clear.
     */
    clearSegmentLog() {
        clearElement(document.getElementById('devSegmentLog'));
    }

    /**
     * Clears raw event history and count.
     * Use between scenarios so the developer sees only current-run events.
     */
    clearRawLog() {
        this._rawBuffer = [];
        clearElement(document.getElementById('devRawLog'));
        const rawCountElement = document.getElementById('devRawCount');

        // Reset the visible raw count when the tab exists.
        if (rawCountElement) {
            rawCountElement.textContent = '0';
        }
    }

    /**
     * Clears Mercure metrics and restores the waiting placeholder.
     * Use between scenario runs or fresh local sessions.
     */
    clearMercureLog() {
        this._mercureMetrics.clear();
        const mercureLog = document.getElementById('devMercureLog');

        // Production pages do not render the Mercure log.
        if (mercureLog) {
            mercureLog.replaceChildren(createElement('div', {
                className: 'dev-panel__entry subtle-text',
                text: 'Waiting for Mercure events...',
            }));
        }
    }

    /**
     * Fetches the agent health payload into the Pipeline tab.
     * Reports backend errors as visible guidance instead of breaking the dev panel.
     */
    async fetchHealth() {
        const pipelineElement = document.getElementById('devPipelineInfo');

        // Without the Pipeline tab, there is nowhere to show health data.
        if (!pipelineElement) {
            return;
        }

        pipelineElement.textContent = 'Fetching...';

        try {
            const httpBaseUrl = CONFIG.wsUrl.replace('ws://', 'http://').replace('wss://', 'https://');
            const response = await fetch(`${httpBaseUrl}/health`);
            const healthPayload = await response.json();
            pipelineElement.textContent = JSON.stringify(healthPayload, null, 2);
        } catch (healthError) {
            pipelineElement.textContent = isScenarioModeActive || !isRecording
                ? 'No backend connected (demo/scenario mode)\n\nThe Pipeline tab requires a running NeMo agent.\nStart the full stack with: docker compose up --build'
                : `Connection error: ${healthError.message}`;
        }
    }

    /**
     * Copies raw events to the clipboard for debugging.
     * Reports clipboard failures in the console because the visible raw log remains available.
     */
    copyAllRaw() {
        const rawText = this._rawBuffer.map((rawEntry) => JSON.stringify(rawEntry)).join('\n');
        navigator.clipboard.writeText(rawText).catch((clipboardError) => {
            console.warn('Could not copy raw dev events:', clipboardError);
        });
    }

    /**
     * Renders clickable scenario rows.
     * Use after the dev panel loads or scenarios are refreshed.
     */
    renderScenarioList() {
        const scenarioList = document.getElementById('scenarioList');

        // Without scenario fixtures, there is no local demo list to render.
        if (!scenarioList) {
            return;
        }

        clearElement(scenarioList);

        // Each scenario row starts a self-contained transcript simulation.
        for (const scenario of SCENARIOS) {
            const scenarioItem = createScenarioItem(scenario);
            scenarioList.appendChild(scenarioItem);
        }

        const progressText = document.getElementById('scenarioProgressText');

        // The initial progress value tells the developer no scenario has run yet.
        if (progressText) {
            progressText.textContent = `0/${SCENARIOS.length}`;
        }
    }

    /**
     * Logs a WebSocket frame to the WS tab.
     * Use for explicit dev-panel frame events when needed during debugging.
     */
    logWs(direction, byteCount) {
        const normalizedBytes = byteCount ?? 0;

        // Direction determines which aggregate the developer sees increment.
        if (direction === 'send') {
            this._wsMetrics.framesSent++;
            this._wsMetrics.bytesSent += normalizedBytes;
        } else {
            this._wsMetrics.framesReceived++;
            this._wsMetrics.bytesReceived += normalizedBytes;
        }

        this.updateWsStatus();
        const wsLog = document.getElementById('devWsLog');

        // Without the WS log tab, there is no visible row to append.
        if (!wsLog) {
            return;
        }

        const arrow = direction === 'send' ? '\u2191' : '\u2193';
        const byteSuffix = normalizedBytes ? ` (${normalizedBytes}B)` : '';
        wsLog.appendChild(createElement('div', {
            className: 'dev-panel__entry',
            text: `${arrow} ${direction}${byteSuffix}`,
        }));

        // Keep the visible WebSocket log bounded during long checks.
        if (wsLog.children.length > 100) {
            wsLog.removeChild(wsLog.firstChild);
        }

        wsLog.scrollTop = wsLog.scrollHeight;
    }
}

/**
 * Creates one scenario row for the left dev panel.
 * Use when rendering the scenario chooser.
 */
function createScenarioItem(scenario) {
    const scenarioItem = createElement('div', {
        className: 'scenario-item',
        dataset: { scenarioId: scenario.id },
    }, [
        createElement('div', { className: 'scenario-item__name', text: scenario.name }),
        createElement('div', {
            className: 'scenario-item__meta',
            text: `${scenario.events.length} events - ${scenario.description}`,
        }),
        createElement('div', {
            className: 'scenario-item__result',
            attributes: { id: `scenarioResult-${scenario.id}` },
            style: 'display:none;',
        }),
    ]);
    scenarioItem.addEventListener('click', () => scenarioRunner.runOne(scenario.id));
    return scenarioItem;
}
