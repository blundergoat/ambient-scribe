// =========================================================================
// Ambient Scribe dev scenario runner and instrumentation wrappers.
// Runs only in APP_ENV=dev after the dev panel class is loaded.
// Owns medical fixture playback, scenario assertions, and dev logging wrappers.
// Users reach this code by clicking scenario controls in the local dev UI.
// =========================================================================

/**
 * Runs medical demo scenarios through the same UI functions as live sessions.
 * Users reach this from the local-only Demo Scenarios panel.
 * It validates final transcript state without requiring microphone or GPU audio.
 */
class ScenarioRunner {
    /**
     * Prepares scenario execution state.
     * Use once when the dev page loads.
     */
    constructor() {
        this._running = false;
        this._stopped = false;
        this._results = new Map();
        this._previousRoleMapping = null;
    }

    /**
     * Runs one selected scenario.
     * Use when a developer clicks a specific medical scenario row.
     */
    async runOne(scenarioId) {
        const scenario = SCENARIOS.find((candidateScenario) => candidateScenario.id === scenarioId);

        // Missing or already-running scenarios should not disturb the visible UI.
        if (!scenario || this._running) {
            return;
        }

        this._running = true;
        this._stopped = false;
        this._updateControls(true);
        this._markScenario(scenarioId, 'running');
        this._showProgress(true);
        this._updateProgress(0, 1);
        await this._resetState();
        const result = await this._executeScenario(scenario);
        this._results.set(scenarioId, result);
        this._markScenario(scenarioId, result.pass ? 'pass' : 'fail', result);
        this._updateProgress(1, 1);
        this._running = false;
        this._updateControls(false);
        this._showPostScenarioButtons();
    }

    /**
     * Runs every medical scenario in order.
     * Use when checking the full local demo corpus.
     */
    async runAll() {
        this._running = true;
        this._stopped = false;
        this._results.clear();
        this._updateControls(true);
        this._showProgress(true);

        // Each scenario resets the visible consultation before it starts.
        for (let scenarioIndex = 0; scenarioIndex < SCENARIOS.length && !this._stopped; scenarioIndex++) {
            const scenario = SCENARIOS[scenarioIndex];
            this._updateProgress(scenarioIndex, SCENARIOS.length);
            this._markScenario(scenario.id, 'running');
            await this._resetState();
            const result = await this._executeScenario(scenario);
            this._results.set(scenario.id, result);
            this._markScenario(scenario.id, result.pass ? 'pass' : 'fail', result);
        }

        this._updateProgress(SCENARIOS.length, SCENARIOS.length);
        this._running = false;
        this._updateControls(false);
        this._showPostScenarioButtons();
    }

    /**
     * Runs one random scenario from the corpus.
     * Use for quick exploratory checks of the demo UI.
     */
    async runRandom() {
        const scenarioIndex = selectRandomScenarioIndex(SCENARIOS.length);
        await this.runOne(SCENARIOS[scenarioIndex].id);
    }

    /**
     * Re-runs only scenarios that failed earlier in this page session.
     * Use after fixing or inspecting a local scenario failure.
     */
    async runFailed() {
        const failedScenarioIds = [];

        // Keep only failed scenario ids so passing scenarios are not repeated.
        for (const [scenarioId, result] of this._results) {
            if (!result.pass) {
                failedScenarioIds.push(scenarioId);
            }
        }

        // No failures means there is nothing useful to re-run.
        if (failedScenarioIds.length === 0) {
            return;
        }

        this._running = true;
        this._stopped = false;
        this._updateControls(true);
        this._showProgress(true);

        // Re-run failed scenarios in the order they failed.
        for (let scenarioIndex = 0; scenarioIndex < failedScenarioIds.length && !this._stopped; scenarioIndex++) {
            const scenario = SCENARIOS.find((candidateScenario) => candidateScenario.id === failedScenarioIds[scenarioIndex]);

            // A removed scenario id should not stop the rest of the re-run set.
            if (!scenario) {
                continue;
            }

            this._updateProgress(scenarioIndex, failedScenarioIds.length);
            this._markScenario(scenario.id, 'running');
            await this._resetState();
            const result = await this._executeScenario(scenario);
            this._results.set(scenario.id, result);
            this._markScenario(scenario.id, result.pass ? 'pass' : 'fail', result);
        }

        this._running = false;
        this._updateControls(false);
        this._showPostScenarioButtons();
    }

    /**
     * Requests that the current scenario batch stop after the active event.
     * Use when a developer clicks Stop in the scenario panel.
     */
    stop() {
        this._stopped = true;
    }

    /**
     * Downloads accumulated scenario results as JSON.
     * Use after running one or more scenarios.
     */
    exportResults() {
        const resultsPayload = {};

        // Preserve scenario ids as keys so failures are easy to find later.
        for (const [scenarioId, result] of this._results) {
            resultsPayload[scenarioId] = result;
        }

        const timestamp = new Date().toISOString().slice(0, 19).replace(/[:.]/g, '-');
        const resultsBlob = new Blob([JSON.stringify(resultsPayload, null, 2)], { type: 'application/json' });
        triggerDownload(resultsBlob, `scenario-results-${timestamp}.json`);
    }

    /**
     * Clears transcript state before a scenario run.
     * Use so fixture events start from the same UI state as a fresh visit.
     */
    async _resetState() {
        this._previousRoleMapping = { ...roleMapping };
        const transcript = document.getElementById('transcript');

        // Remove prior transcript cards before replaying fixture events.
        for (const segmentElement of transcript.querySelectorAll('.segment')) {
            segmentElement.remove();
        }

        const emptyState = document.getElementById('emptyState');

        // Scenario runs hide the empty state because fixture text will appear.
        if (emptyState) {
            emptyState.classList.add('hidden');
        }

        segmentIndex = 0;
        roleMapping = {};
        previousRoleMapping = {};
        confidence = 0;
        segmentsBySpeaker.clear();
        manualOverrides.clear();
        document.getElementById('segmentCount').textContent = '0';
        devPanel.clearSegmentLog();
        devPanel.clearRawLog();
        devPanel.clearMercureLog();
        isScenarioModeActive = true;
    }

    /**
     * Executes scenario events and validates the final visible state.
     * Use as the core local demo verification path.
     */
    async _executeScenario(scenario) {
        let didDetectFlip = false;
        const startedAt = Date.now();

        // Feed each scenario event into the same handlers used by live sessions.
        for (const scenarioEvent of scenario.events) {
            // Stop leaves the current visible state available for inspection.
            if (this._stopped) {
                break;
            }

            // Fixture delays simulate realistic pacing between transcript events.
            if (scenarioEvent.delay > 0) {
                await new Promise((resolveDelay) => setTimeout(resolveDelay, scenarioEvent.delay));
            }

            didDetectFlip = this._applyScenarioEvent(scenarioEvent, didDetectFlip);
        }

        isScenarioModeActive = false;
        return this._validateScenarioResult(scenario, startedAt, didDetectFlip);
    }

    /**
     * Applies one fixture event to the visible transcript UI.
     * Use during scenario execution to mimic live backend messages.
     */
    _applyScenarioEvent(scenarioEvent, didDetectFlip) {
        switch (scenarioEvent.type) {
            case 'segment':
                handleRawSegment({ ...scenarioEvent.data, __scenario: true });
                return didDetectFlip;
            case 'role_update':
                return this._applyRoleUpdateScenarioEvent(scenarioEvent, didDetectFlip);
            case 'ws_disconnect':
                devPanel.logRaw('ws_disconnect', scenarioEvent.data);
                setPlainStatus('Connection lost (scenario)');
                return didDetectFlip;
            case 'ws_reconnect':
                devPanel.logRaw('ws_reconnect', scenarioEvent.data);
                setRecordingStatus('Recording', 'color:#ef4444;font-weight:500;');
                return didDetectFlip;
            case 'control':
                devPanel.logRaw('control', scenarioEvent.data);
                this._applyControlScenarioEvent(scenarioEvent.data);
                return didDetectFlip;
            case 'finalized':
                handleRawSegment({ type: 'finalized' });
                return didDetectFlip;
            default:
                return didDetectFlip;
        }
    }

    /**
     * Applies a fixture role update and records whether labels flipped.
     * Use so scenario assertions can prove correction behavior happened.
     */
    _applyRoleUpdateScenarioEvent(scenarioEvent, didDetectFlip) {
        const previousMapping = { ...roleMapping };
        handleRoleUpdate(scenarioEvent.data);

        // Compare previous labels with the fixture update to detect visible flips.
        for (const [speakerId, role] of Object.entries(previousMapping)) {
            if (scenarioEvent.data.mapping[speakerId] && scenarioEvent.data.mapping[speakerId] !== role) {
                return true;
            }
        }

        return didDetectFlip;
    }

    /**
     * Applies fixture start/stop controls to the visible status.
     * Use when scenarios need to simulate a user's recording actions.
     */
    _applyControlScenarioEvent(controlEvent) {
        // Start events make the fixture look like an active consultation.
        if (controlEvent.action === 'start') {
            setRecordingStatus('Recording', 'color:#ef4444;font-weight:500;');
            return;
        }

        // Stop events make the fixture look like a finished consultation.
        if (controlEvent.action === 'stop') {
            setPlainStatus('Session ended');
        }
    }

    /**
     * Validates scenario state by composing small UI-facing assertions.
     * This stays flat so each failure maps to one visible transcript outcome.
     */
    _validateScenarioResult(scenario, startedAt, didDetectFlip) {
        const expected = scenario.expectedEndState;
        const durationMs = Date.now() - startedAt;
        const errors = [
            ...this._validateSegmentCount(expected),
            ...this._validateRoleMapping(expected),
            ...this._validateConfidence(expected),
            ...this._validateFlip(expected, didDetectFlip),
            ...this._validateDuration(expected, durationMs),
        ];

        this._validateScenarioContent(expected, errors);
        return {
            pass: errors.length === 0,
            errors,
            segmentCount: segmentIndex,
            roleMapping: { ...roleMapping },
            confidence,
            flipDetected: didDetectFlip,
            durationMs,
        };
    }

    /**
     * Validates the visible number of transcript cards.
     * Use to catch scenario events that failed to render.
     */
    _validateSegmentCount(expected) {
        // Missing expectation means the scenario does not care about count.
        if (expected.segmentCount === undefined || segmentIndex === expected.segmentCount) {
            return [];
        }

        return [`segments: ${segmentIndex} (expected ${expected.segmentCount})`];
    }

    /**
     * Validates final Doctor/Patient labels for expected speakers.
     * Use to prove role inference updates reached the visible transcript.
     */
    _validateRoleMapping(expected) {
        const roleErrors = [];

        // Empty role expectations mean this scenario checks other UI behavior.
        if (!expected.roleMapping || Object.keys(expected.roleMapping).length === 0) {
            return roleErrors;
        }

        // Every expected speaker should finish with the fixture's medical role.
        for (const [speakerId, expectedRole] of Object.entries(expected.roleMapping)) {
            if (roleMapping[speakerId] !== expectedRole) {
                roleErrors.push(`role ${speakerId}: ${roleMapping[speakerId] || 'none'} (expected ${expectedRole})`);
            }
        }

        return roleErrors;
    }

    /**
     * Validates the role-confidence badge state.
     * Use to catch scenarios where confidence never reached the visible threshold.
     */
    _validateConfidence(expected) {
        // Missing or zero confidence requirements mean no badge threshold is asserted.
        if (expected.confidenceMin === undefined || expected.confidenceMin <= 0 || confidence >= expected.confidenceMin) {
            return [];
        }

        return [`confidence: ${confidence} (min ${expected.confidenceMin})`];
    }

    /**
     * Validates whether a visible role correction happened.
     * Use when a scenario expects labels to flip after more context.
     */
    _validateFlip(expected, didDetectFlip) {
        // Missing flip expectation means the scenario does not assert corrections.
        if (expected.flipDetected === undefined || didDetectFlip === expected.flipDetected) {
            return [];
        }

        return [`flip: ${didDetectFlip} (expected ${expected.flipDetected})`];
    }

    /**
     * Validates that the scenario completed within its UI budget.
     * Use to catch event loops that stall visible feedback.
     */
    _validateDuration(expected, durationMs) {
        // Missing duration budget means runtime is not part of this scenario's contract.
        if (expected.maxDurationMs === undefined || durationMs <= expected.maxDurationMs) {
            return [];
        }

        return [`duration: ${durationMs}ms (max ${expected.maxDurationMs}ms)`];
    }

    /**
     * Validates expected text snippets in visible transcript cards.
     * Use after event assertions to catch rendering regressions.
     */
    _validateScenarioContent(expected, errors) {
        // Scenarios without content checks rely on state assertions only.
        if (!expected.contentCheck) {
            return;
        }

        const allSegments = document.querySelectorAll('#transcript .segment');

        // First segment text proves the beginning of the fixture rendered.
        if (expected.contentCheck.firstSegmentText && allSegments.length > 0) {
            const firstText = [...allSegments[0].querySelectorAll('.segment__text')]
                .map((textElement) => textElement.textContent.trim())
                .join(' ');

            if (!firstText.includes(expected.contentCheck.firstSegmentText)) {
                errors.push(`first segment text mismatch: "${firstText.substring(0, 60)}..."`);
            }
        }

        // Last segment text proves later fixture events were not dropped.
        if (expected.contentCheck.lastSegmentText && allSegments.length > 0) {
            const lastSegment = allSegments[allSegments.length - 1];
            const lastText = [...lastSegment.querySelectorAll('.segment__text')]
                .map((textElement) => textElement.textContent.trim())
                .join(' ');

            if (!lastText.includes(expected.contentCheck.lastSegmentText)) {
                errors.push(`last segment text mismatch: "${lastText.substring(0, 60)}..."`);
            }
        }
    }

    /**
     * Marks a scenario row as running, passing, or failing.
     * Use while scenario execution updates the left panel.
     */
    _markScenario(scenarioId, status, result = null) {
        const scenarioItem = document.querySelector(`[data-scenario-id="${scenarioId}"]`);

        // A missing row means there is no visible scenario status to update.
        if (!scenarioItem) {
            return;
        }

        scenarioItem.className = `scenario-item scenario-item--${status}`;
        const resultElement = document.getElementById(`scenarioResult-${scenarioId}`);

        // Missing result element leaves the row status class as the only signal.
        if (!resultElement) {
            return;
        }

        resultElement.style.display = '';
        clearElement(resultElement);

        // Running status tells the developer which scenario currently owns the UI.
        if (status === 'running') {
            resultElement.appendChild(createElement('span', {
                text: 'Running...',
                style: 'color:var(--accent)',
            }));
            return;
        }

        // Completed rows need a result payload to show pass/fail details.
        if (!result) {
            return;
        }

        if (result.pass) {
            resultElement.append(
                createElement('span', { text: 'PASS', style: 'color:var(--color-success)' }),
                document.createTextNode(` - ${result.durationMs}ms, ${result.segmentCount} segments`)
            );
            return;
        }

        resultElement.append(
            createElement('span', { text: 'FAIL', style: 'color:var(--color-danger)' }),
            document.createTextNode(` - ${result.errors.join(', ')}`)
        );
    }

    /**
     * Enables or disables scenario control buttons.
     * Use while a scenario run is active or completed.
     */
    _updateControls(isRunning) {
        const runAllButton = document.getElementById('scenarioRunAllBtn');
        const stopButton = document.getElementById('scenarioStopBtn');
        const failedButton = document.getElementById('scenarioRunFailedBtn');
        const exportButton = document.getElementById('scenarioExportBtn');

        // Disable run-all during active runs to avoid interleaved fixture events.
        if (runAllButton) {
            runAllButton.disabled = isRunning;
        }

        // Stop is useful only while a scenario is running.
        if (stopButton) {
            stopButton.disabled = !isRunning;
        }

        // Post-run controls depend on accumulated results.
        if (!isRunning) {
            const hasFailedScenario = [...this._results.values()].some((result) => !result.pass);

            if (failedButton) {
                failedButton.disabled = !hasFailedScenario;
            }

            if (exportButton) {
                exportButton.style.display = this._results.size > 0 ? '' : 'none';
            }
        }
    }

    /**
     * Shows or hides the scenario progress bar.
     * Use while running one or more scenarios.
     */
    _showProgress(shouldShow) {
        const progressElement = document.getElementById('scenarioProgress');

        // Without a progress element, scenario execution can still run.
        if (progressElement) {
            progressElement.style.display = shouldShow ? 'flex' : 'none';
        }
    }

    /**
     * Updates scenario progress text and fill width.
     * Use after each scenario completes in a batch.
     */
    _updateProgress(current, total) {
        const progressFill = document.getElementById('scenarioProgressFill');
        const progressText = document.getElementById('scenarioProgressText');

        // The fill shows rough batch completion at a glance.
        if (progressFill) {
            progressFill.style.width = `${(current / total) * 100}%`;
        }

        // The text gives exact current/total progress.
        if (progressText) {
            progressText.textContent = `${current}/${total}`;
        }
    }

    /**
     * Reveals post-scenario actions after a run.
     * Use so the developer can download or reset the scenario transcript.
     */
    _showPostScenarioButtons() {
        // A scenario that produced text can be downloaded like a real visit.
        if (segmentIndex > 0) {
            setElementHidden('downloadBtn', false);
        }

        setElementHidden('resetBtn', false);
        setPlainStatus('Scenario complete');
        const confidenceBadge = document.getElementById('confidenceBadge');

        // Stop role badge pulsing when a scenario completes.
        if (confidenceBadge) {
            confidenceBadge.classList.remove('recording-pulse');
        }
    }
}

/**
 * Picks a scenario index with browser crypto instead of non-crypto randomness.
 * Empty scenario lists return zero, which callers only use when fixtures exist.
 */
function selectRandomScenarioIndex(totalScenarios) {
    // No scenarios means there is no valid random choice.
    if (totalScenarios <= 0) {
        return 0;
    }

    const randomValues = new Uint32Array(1);
    crypto.getRandomValues(randomValues);
    return randomValues[0] % totalScenarios;
}

const devPanel = new DevPanel();
const scenarioRunner = new ScenarioRunner();

const originalHandleRawSegment = handleRawSegment;
/**
 * Wraps transcript events with dev logging before normal rendering.
 * Use so scenario/live events appear in the dev panel and clinician transcript.
 */
handleRawSegment = function handleRawSegmentWithDevLogging(segmentEvent) {
    const source = segmentEvent.__scenario ? 'scenario' : 'live';
    devPanel.logRaw('segment', segmentEvent);
    const role = roleMapping[segmentEvent.speaker_id] ?? 'UNKNOWN';

    // Segment events add a row to the dev segment log.
    if (segmentEvent.type === 'segment') {
        devPanel.logSegment(segmentEvent, role, source);
    }

    devPanel.logMercure(CONFIG.topicRaw, segmentEvent);
    originalHandleRawSegment(segmentEvent);
};

const originalHandleRoleUpdate = handleRoleUpdate;
/**
 * Wraps role updates with dev logging before normal relabeling.
 * Use so the dev panel can explain why labels changed on screen.
 */
handleRoleUpdate = function handleRoleUpdateWithDevLogging(roleUpdateEvent) {
    devPanel.logRaw('role_update', roleUpdateEvent);
    devPanel.logMercure(CONFIG.topicRoles, roleUpdateEvent);
    originalHandleRoleUpdate(roleUpdateEvent);
};

const originalStartRecording = startRecording;
/**
 * Replaces Start with a scenario-safe path while fixture playback is active.
 * Use when a demo scenario simulates the user clicking Start.
 */
startRecording = async function startRecordingWithScenarioMode() {
    // Scenario mode skips microphone access and drives transcript events directly.
    if (isScenarioModeActive) {
        isRecording = true;
        didUserStopRecording = false;
        setElementHidden('startBtn', true);
        setElementHidden('stopBtn', false);
        setRecordingStatus('Scenario', 'color:#ef4444;font-weight:500;');
        setElementHidden('emptyState', true);
        return;
    }

    return originalStartRecording();
};

document.addEventListener('DOMContentLoaded', () => {
    devPanel.init();

    // Mobile dev panels need backdrop divs for tap-out dismissal.
    if (window.innerWidth < 1280) {
        for (const panelId of ['scenarioPanel', 'devPanel']) {
            const backdrop = createElement('div', {
                className: 'panel-backdrop',
                attributes: { id: `${panelId}Backdrop` },
                style: 'display:none',
            });
            backdrop.addEventListener('click', () => {
                document.getElementById(panelId).style.display = 'none';
                backdrop.style.display = 'none';
            });
            document.body.appendChild(backdrop);
        }
    }
});
