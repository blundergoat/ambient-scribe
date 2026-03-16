// =========================================================================
// Ambient Scribe — Dev Panel (APP_ENV=dev only, zero prod bytes)
// Globals expected: CONFIG, SCENARIOS (set inline by Twig),
//   handleRawSegment, handleRoleUpdate, startRecording (from scribe.js)
// =========================================================================

let scenarioMode = false;

function toggleScenarioPanel() {
    const panel = document.getElementById('scenarioPanel');
    const backdrop = document.getElementById('scenarioPanelBackdrop');
    if (!panel) return;
    const visible = panel.style.display !== 'none' && panel.style.display !== '';
    panel.style.display = visible ? 'none' : 'flex';
    if (backdrop) backdrop.style.display = visible ? 'none' : 'block';
}

function toggleDevPanelMobile() {
    const panel = document.getElementById('devPanel');
    const backdrop = document.getElementById('devPanelBackdrop');
    if (!panel) return;
    const visible = panel.style.display !== 'none' && panel.style.display !== '';
    panel.style.display = visible ? 'none' : 'flex';
    if (backdrop) backdrop.style.display = visible ? 'none' : 'block';
}

// =========================================================================
// DevPanel Class
// =========================================================================
class DevPanel {
    constructor() {
        this._activeTab = 'segments';
        this._rawBuffer = [];
        this._rawMax = 200;
        this._stateInterval = null;
        this._wsMetrics = { framesSent: 0, framesReceived: 0, bytesSent: 0, bytesReceived: 0 };
        this._mercureMetrics = new Map();
    }

    init() {
        const savedTab = localStorage.getItem('ambient-scribe-devtab');
        if (savedTab) this.switchTab(savedTab);
        this._stateInterval = setInterval(() => this.refreshState(), 500);
        this.renderScenarioList();
    }

    switchTab(tabName) {
        this._activeTab = tabName;
        localStorage.setItem('ambient-scribe-devtab', tabName);
        document.querySelectorAll('.dev-panel__tab').forEach(t => {
            t.classList.toggle('dev-panel__tab--active', t.dataset.tab === tabName);
        });
        document.querySelectorAll('.dev-panel__content').forEach(c => {
            c.style.display = c.dataset.content === tabName ? '' : 'none';
        });
    }

    logSegment(data, role, source) {
        const log = document.getElementById('devSegmentLog');
        if (!log) return;
        const entry = document.createElement('div');
        entry.className = 'dev-panel__entry';
        entry.dataset.speakerId = data.speaker_id || '';
        const raw = data.text || '';
        const text = raw.length > 60 ? raw.substring(0, 57) + '\u2026' : raw;
        const badge = source === 'scenario' ? '<span style="color:#f59e0b">[S]</span> ' : '';
        entry.innerHTML = `${badge}<span data-role-label style="color:${getRoleColor(role)}">${getRoleLabel(role)}</span> <span class="subtle-text">${formatTime(data.start || 0)}</span><br>${escapeHtml(text)}`;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    }

    logRaw(type, data) {
        const entry = { ts: new Date().toISOString(), type, data };
        this._rawBuffer.push(entry);
        if (this._rawBuffer.length > this._rawMax) this._rawBuffer.shift();

        const log = document.getElementById('devRawLog');
        const countEl = document.getElementById('devRawCount');
        if (!log) return;
        if (countEl) countEl.textContent = this._rawBuffer.length;

        const el = document.createElement('div');
        el.className = 'dev-panel__entry';
        const rawJson = JSON.stringify(data);
        const preview = rawJson.length > 80 ? rawJson.substring(0, 77) + '\u2026' : rawJson;
        el.innerHTML = `<span class="subtle-text">${entry.ts.substring(11, 23)}</span> <strong>${type}</strong>${rawJson.length > 80 ? ' <span class="subtle-text">\u25B8</span>' : ''}<br>${escapeHtml(preview)}`;
        el.style.cursor = 'pointer';
        el.onclick = () => {
            const pre = el.querySelector('pre');
            if (pre) { pre.remove(); }
            else { const full = document.createElement('pre'); full.className = 'dev-panel__pre'; full.textContent = JSON.stringify(data, null, 2); el.appendChild(full); }
        };
        log.appendChild(el);
        while (log.children.length > this._rawMax) log.removeChild(log.firstChild);
        log.scrollTop = log.scrollHeight;
    }

    logMercure(topic, data) {
        const metrics = this._mercureMetrics.get(topic) || { count: 0 };
        metrics.count++;
        this._mercureMetrics.set(topic, metrics);

        const log = document.getElementById('devMercureLog');
        if (!log) return;
        if (log.children.length === 1 && log.firstChild.textContent.includes('Waiting')) log.innerHTML = '';

        const el = document.createElement('div');
        el.className = 'dev-panel__entry';
        const shortTopic = topic.split('/').slice(-2).join('/');
        el.innerHTML = `<span class="subtle-text">${shortTopic}</span> #${metrics.count} \u2014 ${data.type || 'msg'}`;
        log.appendChild(el);
        log.scrollTop = log.scrollHeight;
    }

    logWs(direction, data, bytes) {
        if (direction === 'send') { this._wsMetrics.framesSent++; this._wsMetrics.bytesSent += bytes || 0; }
        else { this._wsMetrics.framesReceived++; this._wsMetrics.bytesReceived += bytes || 0; }
        this.updateWsStatus();

        const log = document.getElementById('devWsLog');
        if (!log) return;
        const el = document.createElement('div');
        el.className = 'dev-panel__entry';
        const arrow = direction === 'send' ? '\u2191' : '\u2193';
        el.innerHTML = `${arrow} ${direction}${bytes ? ` (${bytes}B)` : ''}`;
        log.appendChild(el);
        if (log.children.length > 100) log.removeChild(log.firstChild);
        log.scrollTop = log.scrollHeight;
    }

    updateWsStatus() {
        const el = document.getElementById('devWsStatus');
        if (!el) return;
        const state = ws ? ['CONNECTING','OPEN','CLOSING','CLOSED'][ws.readyState] : 'None';
        const m = this._wsMetrics;
        el.textContent = `State: ${state}\nSent: ${m.framesSent} frames (${m.bytesSent}B)\nRecv: ${m.framesReceived} frames (${m.bytesReceived}B)`;
    }

    _instrumentWs() {
        if (!ws || ws._devInstrumented) return;
        ws._devInstrumented = true;

        const self = this;
        const origSend = ws.send.bind(ws);
        ws.send = function(data) {
            self._wsMetrics.framesSent++;
            self._wsMetrics.bytesSent += data.byteLength || data.length || 0;
            return origSend(data);
        };

        const origOnMessage = ws.onmessage;
        if (origOnMessage) {
            ws.onmessage = function(event) {
                const bytes = event.data instanceof ArrayBuffer ? event.data.byteLength : (event.data?.length || 0);
                self._wsMetrics.framesReceived++;
                self._wsMetrics.bytesReceived += bytes;
                origOnMessage.call(ws, event);
            };
        }
    }

    refreshState() {
        this._instrumentWs();
        this.updateWsStatus();
        const el = document.getElementById('devStateSnapshot');
        if (!el || this._activeTab !== 'state') return;
        el.textContent = JSON.stringify({
            sessionId: CONFIG.sessionId,
            mode: currentMode,
            isRecording,
            scenarioMode,
            segmentIndex,
            roleMapping,
            confidence,
            reconnectAttempts,
            wsState: ws ? ['CONNECTING','OPEN','CLOSING','CLOSED'][ws.readyState] : null,
            mercureConnected: streams?.isConnected ?? false,
            mercureRetries: streams?.totalRetries ?? 0,
        }, null, 2);
    }

    clearSegmentLog() {
        const log = document.getElementById('devSegmentLog');
        if (log) log.innerHTML = '';
    }

    clearRawLog() {
        this._rawBuffer = [];
        const log = document.getElementById('devRawLog');
        if (log) log.innerHTML = '';
        const countEl = document.getElementById('devRawCount');
        if (countEl) countEl.textContent = '0';
    }

    clearMercureLog() {
        this._mercureMetrics.clear();
        const log = document.getElementById('devMercureLog');
        if (log) log.innerHTML = '<div class="dev-panel__entry subtle-text">Waiting for Mercure events...</div>';
    }

    async fetchHealth() {
        const el = document.getElementById('devPipelineInfo');
        if (!el) return;
        el.textContent = 'Fetching...';
        try {
            const wsBaseUrl = CONFIG.wsUrl.replace('ws://', 'http://').replace('wss://', 'https://');
            const resp = await fetch(`${wsBaseUrl}/health`);
            const data = await resp.json();
            el.textContent = JSON.stringify(data, null, 2);
        } catch (err) {
            el.textContent = scenarioMode || !isRecording
                ? 'No backend connected (demo/scenario mode)\n\nThe Pipeline tab requires a running NeMo agent.\nStart the full stack with: docker compose up --build'
                : `Connection error: ${err.message}`;
        }
    }

    copyAllRaw() {
        const text = this._rawBuffer.map(e => JSON.stringify(e)).join('\n');
        navigator.clipboard.writeText(text).catch(() => {});
    }

    renderScenarioList() {
        const list = document.getElementById('scenarioList');
        if (!list) return;
        list.innerHTML = SCENARIOS.map(s => `
            <div class="scenario-item" data-scenario-id="${s.id}" onclick="scenarioRunner.runOne('${s.id}')">
                <div class="scenario-item__name">${escapeHtml(s.name)}</div>
                <div class="scenario-item__meta">${s.events.length} events \u2014 ${escapeHtml(s.description)}</div>
                <div id="scenarioResult-${s.id}" class="scenario-item__result" style="display:none;"></div>
            </div>
        `).join('');
        // Initialize the progress counter directly (not on ScenarioRunner)
        const progressText = document.getElementById('scenarioProgressText');
        if (progressText) progressText.textContent = `0/${SCENARIOS.length}`;
    }
}

// =========================================================================
// ScenarioRunner Class
// =========================================================================
class ScenarioRunner {
    constructor() {
        this._running = false;
        this._stopped = false;
        this._results = new Map();
        this._previousRoleMapping = null;
    }

    async runOne(id) {
        const scenario = SCENARIOS.find(s => s.id === id);
        if (!scenario || this._running) return;
        this._running = true;
        this._stopped = false;
        this._updateControls(true);
        this._markScenario(id, 'running');
        this._showProgress(true);
        this._updateProgress(0, 1);

        await this._resetState();
        const result = await this._executeScenario(scenario);
        this._results.set(id, result);
        this._markScenario(id, result.pass ? 'pass' : 'fail', result);
        this._updateProgress(1, 1);

        this._running = false;
        this._updateControls(false);
        this._showPostScenarioButtons();
    }

    async runAll() {
        this._running = true;
        this._stopped = false;
        this._results.clear();
        this._updateControls(true);
        this._showProgress(true);

        for (let i = 0; i < SCENARIOS.length && !this._stopped; i++) {
            const scenario = SCENARIOS[i];
            this._updateProgress(i, SCENARIOS.length);
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

    async runRandom() {
        const idx = Math.floor(Math.random() * SCENARIOS.length);
        await this.runOne(SCENARIOS[idx].id);
    }

    async runFailed() {
        const failedIds = [];
        this._results.forEach((r, id) => { if (!r.pass) failedIds.push(id); });
        if (failedIds.length === 0) return;

        this._running = true;
        this._stopped = false;
        this._updateControls(true);
        this._showProgress(true);

        for (let i = 0; i < failedIds.length && !this._stopped; i++) {
            const scenario = SCENARIOS.find(s => s.id === failedIds[i]);
            if (!scenario) continue;
            this._updateProgress(i, failedIds.length);
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

    stop() { this._stopped = true; }

    exportResults() {
        const data = {};
        this._results.forEach((r, id) => { data[id] = r; });
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        triggerDownload(blob, `scenario-results-${new Date().toISOString().slice(0,19).replace(/[:.]/g,'-')}.json`);
    }

    async _resetState() {
        this._previousRoleMapping = { ...roleMapping };
        const transcript = document.getElementById('transcript');
        transcript.querySelectorAll('.segment').forEach(el => el.remove());
        const empty = document.getElementById('emptyState');
        if (empty) empty.classList.add('hidden');
        segmentIndex = 0;
        roleMapping = {};
        previousRoleMapping = {};
        confidence = 0;
        segmentsBySpeaker.clear();
        manualOverrides.clear();
        document.getElementById('segmentCount').textContent = '0';

        // Clear inspector panels between runs
        devPanel.clearSegmentLog();
        devPanel.clearRawLog();
        devPanel.clearMercureLog();

        scenarioMode = true;
    }

    async _executeScenario(scenario) {
        let flipDetected = false;
        const startedAt = Date.now();

        for (const event of scenario.events) {
            if (this._stopped) break;
            if (event.delay > 0) await new Promise(r => setTimeout(r, event.delay));

            switch (event.type) {
                case 'segment':
                    handleRawSegment({ ...event.data, __scenario: true });
                    break;
                case 'role_update':
                    const prevMapping = { ...roleMapping };
                    handleRoleUpdate(event.data);
                    for (const [spk, role] of Object.entries(prevMapping)) {
                        if (event.data.mapping[spk] && event.data.mapping[spk] !== role) flipDetected = true;
                    }
                    break;
                case 'ws_disconnect':
                    devPanel.logRaw('ws_disconnect', event.data);
                    document.getElementById('status').textContent = 'Connection lost (scenario)';
                    break;
                case 'ws_reconnect':
                    devPanel.logRaw('ws_reconnect', event.data);
                    document.getElementById('status').innerHTML = '<span class="recording-dot recording-pulse"></span><span class="text-red-500 font-medium">Recording</span>';
                    break;
                case 'control':
                    devPanel.logRaw('control', event.data);
                    if (event.data.action === 'start') document.getElementById('status').innerHTML = '<span class="recording-dot recording-pulse"></span><span class="text-red-500 font-medium">Recording</span>';
                    else if (event.data.action === 'stop') document.getElementById('status').textContent = 'Session ended';
                    break;
                case 'finalized':
                    handleRawSegment({ type: 'finalized' });
                    break;
            }
        }

        scenarioMode = false;
        const expected = scenario.expectedEndState;
        const errors = [];

        if (expected.segmentCount !== undefined && segmentIndex !== expected.segmentCount) errors.push(`segments: ${segmentIndex} (expected ${expected.segmentCount})`);
        if (expected.roleMapping && Object.keys(expected.roleMapping).length > 0) {
            for (const [spk, role] of Object.entries(expected.roleMapping)) {
                if (roleMapping[spk] !== role) errors.push(`role ${spk}: ${roleMapping[spk] || 'none'} (expected ${role})`);
            }
        }
        if (expected.confidenceMin !== undefined && expected.confidenceMin > 0 && confidence < expected.confidenceMin) errors.push(`confidence: ${confidence} (min ${expected.confidenceMin})`);
        if (expected.flipDetected !== undefined && flipDetected !== expected.flipDetected) errors.push(`flip: ${flipDetected} (expected ${expected.flipDetected})`);

        return { pass: errors.length === 0, errors, segmentCount: segmentIndex, roleMapping: { ...roleMapping }, confidence, flipDetected, durationMs: Date.now() - startedAt };
    }

    _markScenario(id, status, result) {
        const item = document.querySelector(`[data-scenario-id="${id}"]`);
        if (!item) return;
        item.className = `scenario-item scenario-item--${status}`;
        const resultEl = document.getElementById(`scenarioResult-${id}`);
        if (!resultEl) return;

        if (status === 'running') {
            resultEl.style.display = '';
            resultEl.innerHTML = '<span style="color:var(--accent)">Running...</span>';
        } else if (result) {
            resultEl.style.display = '';
            resultEl.innerHTML = result.pass
                ? `<span style="color:var(--color-success)">PASS</span> \u2014 ${result.durationMs}ms, ${result.segmentCount} segments`
                : `<span style="color:var(--color-danger)">FAIL</span> \u2014 ${escapeHtml(result.errors.join(', '))}`;
        }
    }

    _updateControls(running) {
        const runAll = document.getElementById('scenarioRunAllBtn');
        const stopBtn = document.getElementById('scenarioStopBtn');
        const failedBtn = document.getElementById('scenarioRunFailedBtn');
        const exportBtn = document.getElementById('scenarioExportBtn');

        if (runAll) runAll.disabled = running;
        if (stopBtn) stopBtn.disabled = !running;

        if (!running) {
            let hasFailed = false;
            this._results.forEach(r => { if (!r.pass) hasFailed = true; });
            if (failedBtn) failedBtn.disabled = !hasFailed;
            if (exportBtn) exportBtn.style.display = this._results.size > 0 ? '' : 'none';
        }
    }

    _showProgress(show) {
        const el = document.getElementById('scenarioProgress');
        if (el) el.style.display = show ? 'flex' : 'none';
    }

    _updateProgress(current, total) {
        const fill = document.getElementById('scenarioProgressFill');
        const text = document.getElementById('scenarioProgressText');
        if (fill) fill.style.width = `${(current / total) * 100}%`;
        if (text) text.textContent = `${current}/${total}`;
    }

    _showPostScenarioButtons() {
        if (segmentIndex > 0) {
            document.getElementById('downloadBtn').classList.remove('hidden');
        }
        document.getElementById('resetBtn').classList.remove('hidden');
        document.getElementById('status').textContent = 'Scenario complete';
        const badge = document.getElementById('confidenceBadge');
        if (badge) badge.classList.remove('recording-pulse');
    }
}

// =========================================================================
// Dev Panel Instances & Instrumentation
// =========================================================================
const devPanel = new DevPanel();
const scenarioRunner = new ScenarioRunner();

const _origHandleRawSegment = handleRawSegment;
handleRawSegment = function(data) {
    const source = data.__scenario ? 'scenario' : 'live';
    devPanel.logRaw('segment', data);
    const role = roleMapping[data.speaker_id] || 'UNKNOWN';
    if (data.type === 'segment') devPanel.logSegment(data, role, source);
    devPanel.logMercure(CONFIG.topicRaw, data);
    _origHandleRawSegment(data);
};

const _origHandleRoleUpdate = handleRoleUpdate;
handleRoleUpdate = function(data) {
    devPanel.logRaw('role_update', data);
    devPanel.logMercure(CONFIG.topicRoles, data);
    _origHandleRoleUpdate(data);
};

const _origStartRecording = startRecording;
startRecording = async function() {
    if (scenarioMode) {
        isRecording = true;
        userInitiatedStop = false;
        document.getElementById('startBtn').classList.add('hidden');
        document.getElementById('stopBtn').classList.remove('hidden');
        document.getElementById('status').innerHTML = '<span class="recording-dot recording-pulse"></span><span class="text-red-500 font-medium">Scenario</span>';
        document.getElementById('emptyState')?.classList.add('hidden');
        return;
    }
    return _origStartRecording();
};

document.addEventListener('DOMContentLoaded', () => {
    devPanel.init();

    // Add mobile backdrops
    if (window.innerWidth < 1280) {
        ['scenarioPanel', 'devPanel'].forEach(id => {
            const backdrop = document.createElement('div');
            backdrop.id = id + 'Backdrop';
            backdrop.className = 'panel-backdrop';
            backdrop.style.display = 'none';
            backdrop.onclick = () => {
                document.getElementById(id).style.display = 'none';
                backdrop.style.display = 'none';
            };
            document.body.appendChild(backdrop);
        });
    }
});
