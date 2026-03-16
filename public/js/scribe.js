// =========================================================================
// Ambient Scribe — Core Application
// Globals expected: CONFIG (set inline by Twig), THEME_STORAGE_KEY
// =========================================================================

const THEME_STORAGE_KEY = 'ambient-scribe-theme';

// =========================================================================
// Modes
// =========================================================================
const MODES = {
    medical:   { name: 'Medical',   icon: '\u{1F3E5}', subtitle: 'Consultation transcription with speaker identification', roleLabels: { DOCTOR: 'Doctor', PATIENT: 'Patient' }, startLabel: 'Start Consultation', avatarLabels: { DOCTOR: 'Dr', PATIENT: 'Pt' } },
    meeting:   { name: 'Meeting',   icon: '\u{1F4CB}', subtitle: 'Meeting transcription with participant identification', roleLabels: { DOCTOR: 'Organiser', PATIENT: 'Participant' }, startLabel: 'Start Meeting', avatarLabels: { DOCTOR: 'Org', PATIENT: 'Att' } },
    interview: { name: 'Interview', icon: '\u{1F3A4}', subtitle: 'Interview transcription with role identification', roleLabels: { DOCTOR: 'Interviewer', PATIENT: 'Candidate' }, startLabel: 'Start Interview', avatarLabels: { DOCTOR: 'Int', PATIENT: 'Can' } },
    tv:        { name: 'TV / Media',icon: '\u{1F4FA}', subtitle: 'Broadcast transcription with speaker identification', roleLabels: { DOCTOR: 'Host', PATIENT: 'Guest' }, startLabel: 'Start Broadcast', avatarLabels: { DOCTOR: 'Ho', PATIENT: 'Gu' } },
    lecture:   { name: 'Lecture',   icon: '\u{1F393}', subtitle: 'Lecture transcription with speaker identification', roleLabels: { DOCTOR: 'Lecturer', PATIENT: 'Student' }, startLabel: 'Start Lecture', avatarLabels: { DOCTOR: 'Lc', PATIENT: 'St' } },
    general:   { name: 'General',   icon: '\u{1F399}', subtitle: 'Real-time audio transcription with speaker identification', roleLabels: { DOCTOR: 'Speaker A', PATIENT: 'Speaker B' }, startLabel: 'Start Transcription', avatarLabels: { DOCTOR: 'A', PATIENT: 'B' } },
};

let currentMode = localStorage.getItem('ambient-scribe-mode') || 'medical';

function getMode() { return MODES[currentMode] || MODES.medical; }

function getRoleLabel(backendRole) {
    const mode = getMode();
    return mode.roleLabels[backendRole] || backendRole.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
}

function getAvatarLabel(backendRole) {
    const mode = getMode();
    return mode.avatarLabels[backendRole] || (backendRole === 'UNKNOWN' ? '?' : backendRole.charAt(0));
}

function setMode(modeKey) {
    currentMode = modeKey;
    localStorage.setItem('ambient-scribe-mode', modeKey);
    applyMode();
    closeModeDropdown();
}

function applyMode() {
    const mode = getMode();
    document.getElementById('modeIcon').textContent = mode.icon;
    document.getElementById('modeLabel').textContent = mode.name;
    document.getElementById('appSubtitle').textContent = mode.subtitle;
    document.getElementById('startBtnLabel').textContent = mode.startLabel;
    document.getElementById('emptyStateText').textContent = `Click "${mode.startLabel}" to begin transcription.`;

    // Rebuild dropdown
    const dropdown = document.getElementById('modeDropdown');
    if (dropdown) {
        dropdown.innerHTML = Object.entries(MODES).map(([key, m]) =>
            `<button class="mode-selector__option ${key === currentMode ? 'mode-selector__option--active' : ''}" onclick="setMode('${key}')">` +
            `<span class="mode-selector__icon">${m.icon}</span>${m.name}</button>`
        ).join('');
    }

    // Re-label existing segments
    relabelSegments();
}

function toggleModeDropdown() {
    const d = document.getElementById('modeDropdown');
    d.style.display = d.style.display === 'none' ? '' : 'none';
}

function closeModeDropdown() {
    const d = document.getElementById('modeDropdown');
    if (d) d.style.display = 'none';
}

// Close dropdown on outside click
document.addEventListener('click', (e) => {
    const sel = document.getElementById('modeSelector');
    if (sel && !sel.contains(e.target)) closeModeDropdown();
});

// =========================================================================
// Theme
// =========================================================================
function getCurrentTheme() {
    return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

function applyTheme(theme) {
    const resolvedTheme = theme === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = resolvedTheme;

    const toggle = document.getElementById('themeToggle');
    const iconLight = document.getElementById('themeIconLight');
    const iconDark = document.getElementById('themeIconDark');

    if (toggle) toggle.setAttribute('aria-pressed', resolvedTheme === 'dark' ? 'true' : 'false');
    if (iconLight) iconLight.style.display = resolvedTheme === 'dark' ? 'none' : '';
    if (iconDark) iconDark.style.display = resolvedTheme === 'dark' ? '' : 'none';
}

function toggleTheme() {
    const nextTheme = getCurrentTheme() === 'dark' ? 'light' : 'dark';
    localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    applyTheme(nextTheme);
}

applyTheme(localStorage.getItem(THEME_STORAGE_KEY) || getCurrentTheme());

// =========================================================================
// StreamOrchestrator — manages EventSource connections to Mercure
// =========================================================================
class StreamOrchestrator {
    constructor(mercureUrl) {
        this._mercureUrl = mercureUrl;
        this._streams = new Map();
        this._active = false;
        window.addEventListener('beforeunload', () => this.disconnectAll());
    }

    subscribe(topic, handler) {
        if (this._streams.has(topic)) return;
        const state = { source: null, handler, retries: 0, backoffMs: 1000, timer: null, lastEventId: null };
        this._streams.set(topic, state);
        this._active = true;
        this._connect(topic, state);
    }

    unsubscribe(topic) {
        const state = this._streams.get(topic);
        if (!state) return;
        clearTimeout(state.timer);
        state.source?.close();
        this._streams.delete(topic);
    }

    disconnectAll() {
        this._active = false;
        for (const [, state] of this._streams) {
            clearTimeout(state.timer);
            state.source?.close();
        }
        this._streams.clear();
    }

    get isConnected() {
        for (const state of this._streams.values()) {
            if (state.source?.readyState === EventSource.OPEN) return true;
        }
        return false;
    }

    get totalRetries() {
        let total = 0;
        for (const state of this._streams.values()) total += state.retries;
        return total;
    }

    _connect(topic, state) {
        if (!this._active || !this._mercureUrl) return;
        const url = new URL(this._mercureUrl);
        url.searchParams.append('topic', topic);
        // Mercure Last-Event-ID: resume from the last received event
        // so we don't miss segments during brief network interruptions.
        if (state.lastEventId) {
            url.searchParams.append('Last-Event-ID', state.lastEventId);
        }
        const source = new EventSource(url);
        state.source = source;

        source.onmessage = (event) => {
            state.backoffMs = 1000;
            state.retries = 0;
            // Track the last event ID for reconnection
            if (event.lastEventId) {
                state.lastEventId = event.lastEventId;
            }
            try { state.handler(JSON.parse(event.data)); }
            catch (err) { console.error(`StreamOrchestrator: parse error on ${topic}`, err); }
        };

        source.onerror = () => {
            source.close();
            if (!this._active) return;
            state.retries++;
            state.timer = setTimeout(() => this._connect(topic, state), state.backoffMs);
            state.backoffMs = Math.min(state.backoffMs * 2, 30000);
        };

        source.onopen = () => console.log(`StreamOrchestrator: connected to ${topic}`);
    }
}

// =========================================================================
// PCM Streamer
// =========================================================================
class PcmStreamer {
    constructor(stream, options = {}) {
        this._stream = stream;
        this._targetSampleRate = options.targetSampleRate || 16000;
        this._chunkMs = options.chunkMs || 5000;
        this._onChunk = options.onChunk || (() => {});
        this._onAudioLevel = options.onAudioLevel || null;
        this._audioContext = null;
        this._source = null;
        this._processor = null;
        this._silence = null;
        this._buffers = [];
        this._bufferedBytes = 0;
        this._bytesPerChunk = this._targetSampleRate * 2 * (this._chunkMs / 1000);
    }

    async start() {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) throw new Error('Web Audio API is not supported');

        this._audioContext = new AudioContextClass();
        await this._audioContext.resume();

        this._source = this._audioContext.createMediaStreamSource(this._stream);
        this._processor = this._audioContext.createScriptProcessor(4096, 1, 1);
        this._silence = this._audioContext.createGain();
        this._silence.gain.value = 0;

        this._processor.onaudioprocess = (event) => {
            const input = event.inputBuffer.getChannelData(0);
            const downsampled = this._downsampleBuffer(input, this._audioContext.sampleRate, this._targetSampleRate);
            const pcmChunk = this._floatTo16BitPcm(downsampled);
            if (pcmChunk.byteLength === 0) return;

            // Calculate RMS energy and fire callback
            if (this._onAudioLevel) {
                let sumSquares = 0;
                for (let i = 0; i < downsampled.length; i++) {
                    sumSquares += downsampled[i] * downsampled[i];
                }
                const rms = Math.sqrt(sumSquares / downsampled.length);
                this._onAudioLevel(rms);
            }

            this._buffers.push(pcmChunk);
            this._bufferedBytes += pcmChunk.byteLength;
            if (this._bufferedBytes >= this._bytesPerChunk) this.flush();
        };

        this._source.connect(this._processor);
        this._processor.connect(this._silence);
        this._silence.connect(this._audioContext.destination);
    }

    flush() {
        if (this._bufferedBytes === 0) return;
        const merged = new Uint8Array(this._bufferedBytes);
        let offset = 0;
        for (const chunk of this._buffers) { merged.set(chunk, offset); offset += chunk.byteLength; }
        this._buffers = [];
        this._bufferedBytes = 0;
        this._onChunk(merged.buffer);
    }

    stop() {
        this.flush();
        this._processor?.disconnect();
        this._source?.disconnect();
        this._silence?.disconnect();
        this._processor = null;
        this._source = null;
        this._silence = null;
        if (this._audioContext) { this._audioContext.close().catch(() => {}); this._audioContext = null; }
    }

    _downsampleBuffer(buffer, inputSampleRate, outputSampleRate) {
        if (inputSampleRate === outputSampleRate) return new Float32Array(buffer);
        if (inputSampleRate < outputSampleRate) throw new Error(`Cannot upsample audio from ${inputSampleRate}Hz to ${outputSampleRate}Hz`);
        const ratio = inputSampleRate / outputSampleRate;
        const len = Math.round(buffer.length / ratio);
        const out = new Float32Array(len);
        let oi = 0, ii = 0;
        while (oi < len) {
            const ni = Math.round((oi + 1) * ratio);
            let acc = 0, cnt = 0;
            for (let i = ii; i < ni && i < buffer.length; i++) { acc += buffer[i]; cnt++; }
            out[oi] = cnt > 0 ? acc / cnt : 0;
            oi++; ii = ni;
        }
        return out;
    }

    _floatTo16BitPcm(floatBuffer) {
        const pcm = new Int16Array(floatBuffer.length);
        for (let i = 0; i < floatBuffer.length; i++) {
            const s = Math.max(-1, Math.min(1, floatBuffer[i]));
            pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        return new Uint8Array(pcm.buffer);
    }
}

// =========================================================================
// State
// =========================================================================
let ws = null;
let recorder = null;
let mediaStream = null;
let pcmStreamer = null;
let timerInterval = null;
let startTime = null;
let segmentIndex = 0;
let roleMapping = {};
let previousRoleMapping = {};
let confidence = 0;
let streams = null;
let isRecording = false;
let userInitiatedStop = false;
let reconnectAttempts = 0;
let reconnectTimer = null;
const MAX_RECONNECT_ATTEMPTS = 3;
const segmentsBySpeaker = new Map();
const manualOverrides = new Set();
let lowLevelCount = 0;

// State is now declared — safe to apply mode (relabelSegments needs roleMapping)
applyMode();

// =========================================================================
// Audio Level Feedback
// =========================================================================
function updateAudioLevel(rms) {
    const el = document.getElementById('audioLevel');
    if (!el) return;

    if (rms > 0.95) {
        lowLevelCount = 0;
        el.classList.remove('hidden');
        el.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#ef4444;margin-right:4px;"></span>Audio clipping detected';
        el.style.color = '#ef4444';
    } else if (rms < 0.005) {
        lowLevelCount++;
        if (lowLevelCount >= 3) {
            el.classList.remove('hidden');
            el.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#eab308;margin-right:4px;"></span>Low audio level \u2014 move closer to the microphone';
            el.style.color = '#eab308';
        }
    } else {
        lowLevelCount = 0;
        el.classList.remove('hidden');
        el.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;margin-right:4px;"></span>';
        el.style.color = '#22c55e';
    }
}

function hideAudioLevel() {
    lowLevelCount = 0;
    const el = document.getElementById('audioLevel');
    if (el) { el.classList.add('hidden'); el.innerHTML = ''; }
}

function showRoleIdentificationPending() {
    if (!CONFIG.enableRoleUpdates) return;
    const badge = document.getElementById('confidenceBadge');
    badge.classList.remove('hidden');
    badge.textContent = 'Identifying speakers...';
    badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 recording-pulse';
}

// =========================================================================
// Recording Controls
// =========================================================================
async function startRecording() {
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        });

        ws = new WebSocket(`${CONFIG.wsUrl}/ws/transcribe/${CONFIG.sessionId}?mode=${currentMode}`);
        ws.binaryType = 'arraybuffer';

        ws.onopen = async () => {
            try {
                pcmStreamer = new PcmStreamer(mediaStream, {
                    targetSampleRate: 16000, chunkMs: 5000,
                    onChunk: (pcmChunk) => { if (ws?.readyState === WebSocket.OPEN) ws.send(pcmChunk); },
                    onAudioLevel: (rms) => { updateAudioLevel(rms); }
                });
                await pcmStreamer.start();
            } catch (err) {
                console.error('PCM stream setup failed:', err);
                document.getElementById('status').textContent = `Audio setup error: ${err.message}`;
                pcmStreamer?.stop(); pcmStreamer = null; ws?.close();
            }
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === 'system_error') showSystemBanner(msg.message);
            } catch (err) { console.warn('WebSocket text frame parse error:', err); }
        };
        ws.onclose = (event) => {
            pcmStreamer?.stop(); pcmStreamer = null;
            if (isRecording && !userInitiatedStop) handleUnexpectedDisconnect(event.code);
        };
        ws.onerror = () => { document.getElementById('status').textContent = 'Streaming error'; };

        subscribeToMercure();

        isRecording = true;
        userInitiatedStop = false;
        reconnectAttempts = 0;
        document.getElementById('startBtn').classList.add('hidden');
        document.getElementById('stopBtn').classList.remove('hidden');
        document.getElementById('reconnectBtn').classList.add('hidden');
        document.getElementById('status').innerHTML = '<span class="recording-dot recording-pulse"></span><span class="text-red-500 font-medium">Recording</span>';
        document.getElementById('emptyState').classList.add('hidden');
        document.getElementById('timer').classList.remove('hidden');
        announce('Recording started');
        showRoleIdentificationPending();

        if (segmentIndex > 0) document.getElementById('downloadBtn').classList.remove('hidden');

        if (!startTime) {
            startTime = Date.now();
            timerInterval = setInterval(updateTimer, 1000);
        }
    } catch (err) {
        console.error('Failed to start recording:', err);
        document.getElementById('status').textContent = err.name === 'NotAllowedError' ? 'Microphone access denied' : `Error: ${err.message}`;
    }
}

function stopRecording() {
    userInitiatedStop = true;
    isRecording = false;
    clearTimeout(reconnectTimer);
    pcmStreamer?.stop(); pcmStreamer = null;
    ws?.close();
    mediaStream?.getTracks().forEach(t => t.stop());
    streams?.disconnectAll();
    clearInterval(timerInterval);

    document.getElementById('startBtn').classList.remove('hidden');
    document.getElementById('stopBtn').classList.add('hidden');
    document.getElementById('reconnectBtn').classList.add('hidden');
    document.getElementById('status').textContent = 'Session ended';
    hideAudioLevel();
    const badge = document.getElementById('confidenceBadge');
    if (badge) badge.classList.remove('recording-pulse');
    announce('Recording stopped');

    if (segmentIndex > 0) {
        document.getElementById('downloadBtn').classList.remove('hidden');
        document.getElementById('resetBtn').classList.remove('hidden');
    }
}

function resetSession() {
    // Stop anything still running
    if (isRecording) stopRecording();

    // Generate a new session ID
    CONFIG.sessionId = crypto.randomUUID();

    // Update Mercure topics for the new session
    CONFIG.topicRaw = `scribe/session/${CONFIG.sessionId}/raw`;
    CONFIG.topicRoles = `scribe/session/${CONFIG.sessionId}/roles`;

    // Clear transcript
    const transcript = document.getElementById('transcript');
    transcript.querySelectorAll('.segment').forEach(el => el.remove());

    // Reset all state
    segmentIndex = 0;
    roleMapping = {};
    confidence = 0;
    startTime = null;
    manualOverrides.clear();
    clearInterval(timerInterval);
    timerInterval = null;

    // Reset UI
    document.getElementById('segmentCount').textContent = '0';
    document.getElementById('timer').textContent = '00:00';
    document.getElementById('timer').classList.add('hidden');
    document.getElementById('downloadBtn').classList.add('hidden');
    document.getElementById('resetBtn').classList.add('hidden');
    document.getElementById('startBtn').classList.remove('hidden');
    document.getElementById('stopBtn').classList.add('hidden');
    document.getElementById('reconnectBtn').classList.add('hidden');
    document.getElementById('status').textContent = 'Ready';

    const emptyState = document.getElementById('emptyState');
    if (emptyState) emptyState.classList.remove('hidden');

    const badge2 = document.getElementById('confidenceBadge');
    badge2.classList.add('hidden');
    badge2.textContent = '';

    const banner = document.getElementById('systemBanner');
    banner.classList.add('hidden');
    banner.textContent = '';

    // Clear dev panel tabs (match scenario _resetState behaviour)
    if (typeof devPanel !== 'undefined') {
        devPanel.clearSegmentLog();
        devPanel.clearRawLog();
        devPanel.clearMercureLog();
    }
    segmentsBySpeaker.clear();
    manualOverrides.clear();

    announce('Session reset');
}

// =========================================================================
// Mercure SSE Subscription
// =========================================================================
function subscribeToMercure() {
    if (!CONFIG.mercureUrl) return;
    streams = new StreamOrchestrator(CONFIG.mercureUrl);
    streams.subscribe(CONFIG.topicRaw, handleRawSegment);
    if (CONFIG.enableRoleUpdates) streams.subscribe(CONFIG.topicRoles, handleRoleUpdate);
}

// =========================================================================
// Reconnect Logic
// =========================================================================
function handleUnexpectedDisconnect(code) {
    if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
        autoReconnect();
    } else {
        isRecording = false;
        document.getElementById('stopBtn').classList.add('hidden');
        document.getElementById('reconnectBtn').classList.remove('hidden');
        const msg = `Connection failed \u2014 ${segmentIndex} segments preserved`;
        document.getElementById('status').textContent = msg;
        announce(msg);
        if (segmentIndex > 0) document.getElementById('downloadBtn').classList.remove('hidden');
    }
}

function autoReconnect() {
    reconnectAttempts++;
    const backoffMs = 1000 * Math.pow(2, reconnectAttempts - 1);
    const seconds = Math.round(backoffMs / 1000);
    document.getElementById('status').textContent = `Reconnecting in ${seconds}s...`;
    announce(`Connection lost. Reconnecting in ${seconds} seconds.`);

    reconnectTimer = setTimeout(() => {
        document.getElementById('status').textContent = `Reconnecting (${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`;
        if (!mediaStream || mediaStream.getTracks().every(t => t.readyState === 'ended')) {
            startRecording();
        } else {
            connectWebSocket();
        }
    }, backoffMs);
}

function reconnect() {
    reconnectAttempts = 0;
    document.getElementById('reconnectBtn').classList.add('hidden');
    startRecording();
}

function connectWebSocket() {
    ws = new WebSocket(`${CONFIG.wsUrl}/ws/transcribe/${CONFIG.sessionId}?mode=${currentMode}`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = async () => {
        reconnectAttempts = 0;
        if (!pcmStreamer && mediaStream) {
            pcmStreamer = new PcmStreamer(mediaStream, {
                targetSampleRate: 16000, chunkMs: 5000,
                onChunk: (pcmChunk) => { if (ws?.readyState === WebSocket.OPEN) ws.send(pcmChunk); },
                onAudioLevel: (rms) => { updateAudioLevel(rms); }
            });
            await pcmStreamer.start();
        }
        document.getElementById('status').innerHTML = '<span class="recording-dot recording-pulse"></span><span class="text-red-500 font-medium">Recording</span>';
        announce('Reconnected successfully');
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'system_error') showSystemBanner(msg.message);
        } catch (err) {}
    };
    ws.onclose = (event) => {
        pcmStreamer?.stop(); pcmStreamer = null;
        if (isRecording && !userInitiatedStop) handleUnexpectedDisconnect(event.code);
    };
    ws.onerror = () => {};
}

// =========================================================================
// Transcript Download
// =========================================================================
async function downloadTranscript() {
    let segments;
    try {
        const response = await fetch(`/scribe/${CONFIG.sessionId}/history`);
        if (response.ok) { const data = await response.json(); segments = data.segments || []; }
    } catch (err) { console.warn('Could not fetch from server, falling back to DOM:', err); }

    if (!segments || segments.length === 0) {
        segments = [];
        document.querySelectorAll('.segment').forEach(el => {
            segments.push({
                speaker_id: el.dataset.speakerId,
                role: roleMapping[el.dataset.speakerId] || 'UNKNOWN',
                text: el.querySelector('.segment__text')?.textContent?.trim() || '',
                start: parseFloat(el.dataset.start) || 0,
                end: parseFloat(el.dataset.end) || 0,
            });
        });
    }

    const exportData = { session_id: CONFIG.sessionId, exported_at: new Date().toISOString(), mode: currentMode, segments };
    const jsonBlob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });

    const textLines = segments.map(seg => {
        const role = getRoleLabel(seg.role || roleMapping[seg.speaker_id] || 'UNKNOWN');
        return `[${formatTime(seg.start || 0)} - ${formatTime(seg.end || 0)}] ${role}: ${seg.text || ''}`;
    });
    const textBlob = new Blob([textLines.join('\n')], { type: 'text/plain' });

    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    triggerDownload(jsonBlob, `transcript-${CONFIG.sessionId}-${timestamp}.json`);
    triggerDownload(textBlob, `transcript-${CONFIG.sessionId}-${timestamp}.txt`);
}

function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url);
}

// =========================================================================
// Accessibility
// =========================================================================
function announce(message) {
    const el = document.getElementById('srAnnounce');
    if (el) { el.textContent = message; setTimeout(() => { el.textContent = ''; }, 1000); }
}

// =========================================================================
// Segment Rendering
// =========================================================================
function handleRawSegment(data) {
    if (data.type === 'finalized') { document.getElementById('status').textContent = 'Transcript finalized'; return; }
    if (data.type === 'error') { document.getElementById('status').textContent = `Transcription error: ${data.message}`; return; }
    if (data.type !== 'segment') return;
    const role = roleMapping[data.speaker_id] || 'UNKNOWN';
    appendSegment(data, role);
}

function appendSegment(segment, role) {
    const container = document.getElementById('transcript');
    const el = document.createElement('div');
    const id = `segment-${segmentIndex++}`;

    el.id = id;
    el.className = `segment segment--${role}`;
    el.dataset.speakerId = segment.speaker_id;
    el.dataset.start = segment.start;
    el.dataset.end = segment.end;

    const displayLabel = getRoleLabel(role === 'UNKNOWN' ? 'UNKNOWN' : role);
    const speakerLabel = role === 'UNKNOWN' ? segment.speaker_id : displayLabel;
    const avatarText = role === 'UNKNOWN' ? '?' : getAvatarLabel(role);

    const isOverridden = manualOverrides.has(segment.speaker_id);
    const overrideIcon = isOverridden ? ' <span class="segment__override-icon" title="Manually confirmed">&#x2713;</span>' : '';

    el.innerHTML = `
        <div class="segment__avatar">${avatarText}</div>
        <div class="segment__body">
            <div class="segment__header">
                <span class="segment__speaker">${escapeHtml(speakerLabel)}${overrideIcon}</span>
                <span class="segment__time">${formatTime(segment.start)}</span>
            </div>
            <span class="segment__text">${escapeHtml(segment.text)}</span>
        </div>
    `;

    // Make the speaker label clickable for role override
    const label = el.querySelector('.segment__speaker');
    if (label) {
        label.style.cursor = 'pointer';
        label.title = 'Click to change role';
        label.addEventListener('click', () => {
            const speakerId = el.dataset.speakerId;
            cycleRole(speakerId);
        });
    }

    container.appendChild(el);

    if (!segmentsBySpeaker.has(segment.speaker_id)) segmentsBySpeaker.set(segment.speaker_id, []);
    segmentsBySpeaker.get(segment.speaker_id).push(el);

    container.scrollTop = container.scrollHeight;

    document.getElementById('segmentCount').textContent = segmentIndex;
    if (segmentIndex === 1) document.getElementById('downloadBtn').classList.remove('hidden');
}

// =========================================================================
// Manual Speaker Role Override
// =========================================================================
function cycleRole(speakerId) {
    const mode = getMode();
    const roles = Object.keys(mode.roleLabels);
    const currentRole = roleMapping[speakerId] || 'UNKNOWN';

    let nextRole;
    const idx = roles.indexOf(currentRole);
    if (idx === -1) {
        // Currently UNKNOWN or unrecognised — cycle to first role
        nextRole = roles[0];
    } else if (idx === roles.length - 1) {
        // At end of role list — wrap to UNKNOWN, then back to first
        nextRole = 'UNKNOWN';
    } else {
        nextRole = roles[idx + 1];
    }

    previousRoleMapping = { ...roleMapping };
    roleMapping[speakerId] = nextRole;
    manualOverrides.add(speakerId);
    relabelSegments();
    sendRoleOverride(speakerId, nextRole);
}

async function sendRoleOverride(speakerId, role) {
    const wsBaseUrl = CONFIG.wsUrl.replace('ws://', 'http://').replace('wss://', 'https://');
    try {
        await fetch(`${wsBaseUrl}/session/${CONFIG.sessionId}/roles/override`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ speaker_id: speakerId, role: role }),
        });
    } catch (e) {
        console.warn('Role override failed:', e);
    }
}

// =========================================================================
// Toast Notifications
// =========================================================================
function showToast(message, duration = 3000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.textContent = message;
    toast.style.cssText = 'padding:0.5rem 1rem; margin-top:0.5rem; border-radius:0.5rem; font-size:0.875rem; font-weight:500; background:#fef3c7; color:#92400e; box-shadow:0 4px 6px -1px rgba(0,0,0,0.1); opacity:1; transition:opacity 0.3s ease;';
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.addEventListener('transitionend', () => toast.remove(), { once: true });
    }, duration);
}

// =========================================================================
// Role Updates
// =========================================================================
function handleRoleUpdate(data) {
    if (!data.mapping) return;
    previousRoleMapping = { ...roleMapping };

    // Apply the mapping but preserve manually-overridden speakers
    for (const [spk, role] of Object.entries(data.mapping)) {
        if (!manualOverrides.has(spk)) {
            roleMapping[spk] = role;
        }
    }
    confidence = data.confidence || 0;

    // Detect role flips client-side (works for both live and scenario modes)
    const hadPreviousRoles = Object.keys(previousRoleMapping).length > 0;
    if (hadPreviousRoles) {
        const flipped = Object.keys(data.mapping).some(spk =>
            previousRoleMapping[spk] && previousRoleMapping[spk] !== data.mapping[spk]
        );
        if (flipped) showToast('Speaker labels corrected');
    }

    const badge = document.getElementById('confidenceBadge');
    badge.classList.remove('hidden');
    if (confidence >= 0.8) {
        badge.textContent = `Roles identified (${Math.round(confidence * 100)}%)`;
        badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-800';
    } else if (confidence >= 0.5) {
        badge.textContent = `Low confidence (${Math.round(confidence * 100)}%)`;
        badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800';
    } else {
        badge.textContent = 'Identifying speakers...';
        badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 recording-pulse';
    }
    relabelSegments();
}

function relabelSegments() {
    // Only update segments for speakers whose role actually changed
    const changedSpeakers = [];
    for (const [speakerId, role] of Object.entries(roleMapping)) {
        if (previousRoleMapping[speakerId] !== role) {
            changedSpeakers.push(speakerId);
        }
    }

    // If no previous mapping existed, update all tracked speakers
    if (Object.keys(previousRoleMapping).length === 0) {
        for (const speakerId of segmentsBySpeaker.keys()) {
            if (!changedSpeakers.includes(speakerId)) changedSpeakers.push(speakerId);
        }
    }

    for (const speakerId of changedSpeakers) {
        const newRole = roleMapping[speakerId] || 'UNKNOWN';
        const elements = segmentsBySpeaker.get(speakerId) || [];

        for (const el of elements) {
            const previousClass = el.className;
            el.className = `segment segment--${newRole}`;

            // Animate segments that transition from UNKNOWN to a known role
            if (previousClass.includes('segment--UNKNOWN') && newRole !== 'UNKNOWN') {
                el.classList.add('segment--relabeled');
                el.addEventListener('animationend', () => {
                    el.classList.remove('segment--relabeled');
                }, { once: true });
            }

            const label = el.querySelector('.segment__speaker');
            if (label) {
                const displayLabel = getRoleLabel(newRole === 'UNKNOWN' ? 'UNKNOWN' : newRole);
                const labelText = newRole === 'UNKNOWN' ? speakerId : displayLabel;
                const overrideIcon = manualOverrides.has(speakerId) ? ' <span class="segment__override-icon" title="Manually confirmed">&#x2713;</span>' : '';
                label.innerHTML = escapeHtml(labelText) + overrideIcon;
            }

            const avatar = el.querySelector('.segment__avatar');
            if (avatar) {
                avatar.textContent = newRole === 'UNKNOWN' ? '?' : getAvatarLabel(newRole);
            }
        }
    }

    // Update inspector segment log entries retroactively (only changed speakers)
    const log = document.getElementById('devSegmentLog');
    if (log && changedSpeakers.length > 0) {
        const changedSet = new Set(changedSpeakers);
        log.querySelectorAll('.dev-panel__entry').forEach(entry => {
            const speakerId = entry.dataset.speakerId;
            if (!speakerId || !changedSet.has(speakerId)) return;
            const newRole = roleMapping[speakerId] || 'UNKNOWN';
            const roleSpan = entry.querySelector('[data-role-label]');
            if (roleSpan) {
                roleSpan.textContent = getRoleLabel(newRole);
                roleSpan.style.color = getRoleColor(newRole);
            }
        });
    }
}

// =========================================================================
// Utilities
// =========================================================================
function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function updateTimer() {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    document.getElementById('timer').textContent = formatTime(elapsed);
}

function getRoleColor(role) {
    switch (role) {
        case 'DOCTOR': return 'var(--color-speaker-a)';
        case 'PATIENT': return 'var(--color-speaker-b)';
        default: return 'var(--color-unknown)';
    }
}

function showSystemBanner(message) {
    const banner = document.getElementById('systemBanner');
    banner.textContent = message;
    banner.classList.remove('hidden');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// =========================================================================
// Keyboard Shortcuts
// =========================================================================
document.addEventListener('keydown', (e) => {
    // Don't capture when typing in an input/textarea
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    switch(e.key) {
        case ' ':  // Space = toggle recording
            e.preventDefault();
            if (isRecording) stopRecording();
            else startRecording();
            break;
        case 'Escape':  // Esc = stop recording
            if (isRecording) stopRecording();
            break;
        case 'd':  // D = download
            if (e.ctrlKey || e.metaKey) return; // don't capture Ctrl+D
            document.getElementById('downloadBtn')?.click();
            break;
    }
});
