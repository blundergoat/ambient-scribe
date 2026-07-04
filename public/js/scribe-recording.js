// =========================================================================
// Ambient Scribe recording controls and reconnect flow.
// Runs after streaming helpers and before transcript rendering.
// Owns Start/Stop/New Session, WebSocket setup, recording status, and timers.
// Users reach this code from the main consultation controls and shortcuts.
// =========================================================================

/**
 * Requests the browser microphone and starts a live consultation stream.
 * Reports microphone or setup errors in the status region for the clinician.
 */
async function startRecording() {
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia(createMedicalMicrophoneConstraints());
        connectWebSocket();
        subscribeToMercure();
        showRecordingUi();
        startRecordingTimerIfNeeded();
    } catch (recordingError) {
        console.error('Failed to start recording:', recordingError);
        setPlainStatus(recordingError.name === 'NotAllowedError' ? 'Microphone access denied' : `Error: ${recordingError.message}`);
    }
}

/**
 * Builds microphone constraints for clinical speech capture.
 * Use before the browser asks the clinician for microphone access.
 */
function createMedicalMicrophoneConstraints() {
    return {
        audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        },
    };
}

/**
 * Opens the transcription WebSocket for the current session id.
 * Use when a live session starts or reconnects after a dropped socket.
 */
function connectWebSocket() {
    transcriptionSocket = new WebSocket(`${CONFIG.wsUrl}/ws/transcribe/${CONFIG.sessionId}`);
    transcriptionSocket.binaryType = 'arraybuffer';
    transcriptionSocket.onopen = handleTranscriptionSocketOpened;
    transcriptionSocket.onmessage = handleTranscriptionSocketMessage;
    transcriptionSocket.onclose = handleTranscriptionSocketClosed;
    transcriptionSocket.onerror = () => {
        setPlainStatus('Streaming error');
    };
}

/**
 * Starts PCM audio once the WebSocket is ready.
 * Reports audio setup failures, stops streaming, and avoids sending bad chunks.
 */
async function handleTranscriptionSocketOpened() {
    reconnectAttempts = 0;

    // Reuse the current microphone stream so reconnect does not reprompt the user.
    if (!pcmStreamer && mediaStream) {
        try {
            pcmStreamer = createPcmStreamer(mediaStream);
            await pcmStreamer.start();
        } catch (audioError) {
            console.error('PCM stream setup failed:', audioError);
            setPlainStatus(`Audio setup error: ${audioError.message}`);
            stopPcmStreaming();
            transcriptionSocket?.close();
            return;
        }
    }

    setRecordingStatus('Recording', 'color:#ef4444;font-weight:500;');
}

/**
 * Creates the PCM streamer that sends browser audio to NeMo.
 * Use after the WebSocket opens for a live recording.
 */
function createPcmStreamer(activeMediaStream) {
    return new PcmStreamer(activeMediaStream, {
        targetSampleRate: TARGET_AUDIO_SAMPLE_RATE,
        chunkMs: PCM_CHUNK_MS,
        onChunk: (pcmChunk) => {
            // A closed socket means this audio should not reach a stale visit.
            if (transcriptionSocket?.readyState === WebSocket.OPEN) {
                transcriptionSocket.send(pcmChunk);
            }
        },
        onAudioLevel: (rms) => {
            updateAudioLevel(rms);
        },
    });
}

/**
 * Handles text frames from the transcription socket.
 * Reports parse failures to the console while system errors become clinician banners.
 */
function handleTranscriptionSocketMessage(event) {
    try {
        const socketMessage = JSON.parse(event.data);

        // System errors are visible to the clinician as a banner above transcripts.
        if (socketMessage.type === 'system_error') {
            showSystemBanner(socketMessage.message);
        }
    } catch (parseError) {
        console.warn('WebSocket text frame parse error:', parseError);
    }
}

/**
 * Reacts when the transcription socket closes.
 * Use to decide whether the clinician should see reconnect options.
 */
function handleTranscriptionSocketClosed(event) {
    stopPcmStreaming();

    // If the user did not press Stop, preserve transcript state and recover.
    if (isRecording && !didUserStopRecording) {
        handleUnexpectedDisconnect(event.code);
    }
}

/**
 * Stops PCM processing without clearing transcript state.
 * Use when a recording stops, reconnects, or fails audio setup.
 */
function stopPcmStreaming() {
    pcmStreamer?.stop();
    pcmStreamer = null;
}

/**
 * Switches controls into active-recording state.
 * Use after microphone access and socket setup have started successfully.
 */
function showRecordingUi() {
    isRecording = true;
    didUserStopRecording = false;
    reconnectAttempts = 0;
    setElementHidden('startBtn', true);
    setElementHidden('stopBtn', false);
    setElementHidden('reconnectBtn', true);
    setElementHidden('emptyState', true);
    setElementHidden('timer', false);
    setRecordingStatus('Recording', 'color:#ef4444;font-weight:500;');
    announce('Recording started');
    showRoleIdentificationPending();
}

/**
 * Starts the elapsed-time counter once per consultation.
 * Use when recording begins or resumes after a reconnect.
 */
function startRecordingTimerIfNeeded() {
    // Keep the original start time so reconnects do not reset the visible timer.
    if (!startTime) {
        startTime = Date.now();
        timerInterval = setInterval(updateTimer, 1000);
    }
}

/**
 * Stops the live consultation and requests a summary when text exists.
 * Use when the clinician clicks Stop or presses Space/Escape.
 */
function stopRecording() {
    didUserStopRecording = true;
    isRecording = false;
    clearTimeout(reconnectTimer);
    stopPcmStreaming();
    transcriptionSocket?.close();
    mediaStream?.getTracks().forEach((track) => track.stop());
    disconnectMercureStreams();
    clearInterval(timerInterval);

    setElementHidden('startBtn', false);
    setElementHidden('stopBtn', true);
    setElementHidden('reconnectBtn', true);
    setPlainStatus('Session ended');
    hideAudioLevel();

    const confidenceBadge = document.getElementById('confidenceBadge');

    // The badge stops pulsing once the clinician has ended the visit.
    if (confidenceBadge) {
        confidenceBadge.classList.remove('recording-pulse');
    }

    announce('Recording stopped');

    // Only visits with transcript text need post-visit actions and a summary request.
    if (segmentIndex > 0) {
        revealPostVisitActions();
        requestSummary();
    }
}

/**
 * Clears the visible visit and prepares a fresh session id.
 * Use when the clinician clicks New Session after finishing a consultation.
 */
function resetSession() {
    // Demo replay owns a server task and browser audio that must stop before reset.
    if (isReplayActive) {
        stopReplay();
    }

    // Stop live capture before clearing UI so no late audio mutates the new visit.
    if (isRecording) {
        stopRecording();
    } else {
        disconnectMercureStreams();
    }

    CONFIG.sessionId = crypto.randomUUID();
    CONFIG.topicRaw = `scribe/session/${CONFIG.sessionId}/raw`;
    CONFIG.topicRoles = `scribe/session/${CONFIG.sessionId}/roles`;
    CONFIG.topicSummary = `scribe/session/${CONFIG.sessionId}/summary`;
    CONFIG.topicHints = `scribe/session/${CONFIG.sessionId}/hints`;

    const transcript = document.getElementById('transcript');

    // Remove prior transcript cards while leaving the empty-state element intact.
    for (const segmentElement of transcript.querySelectorAll('.segment')) {
        segmentElement.remove();
    }

    resetVisitState();
    resetVisitUi();

    // The dev panel mirrors the cleared visit if it is loaded in local dev.
    if (typeof devPanel !== 'undefined') {
        devPanel.clearSegmentLog();
        devPanel.clearRawLog();
        devPanel.clearMercureLog();
    }

    announce('Session reset');
}

/**
 * Resets shared in-memory state for the next visible visit.
 * Use after the clinician starts a new session or a demo replay resets.
 */
function resetVisitState() {
    segmentIndex = 0;
    roleMapping = {};
    previousRoleMapping = {};
    confidence = 0;
    startTime = null;
    manualOverrides.clear();
    segmentsBySpeaker.clear();
    lastSpeakerId = null;
    lastSegmentBlock = null;
    isReplayActive = false;
    clearInterval(replayTimerInterval);
    replayTimerInterval = null;
    replayDuration = 0;
    replayTranscriptSegments = [];
    replayNextSegmentIndex = 0;
    isBrowserClockReplaySession = false;
    hasReplayAudioPlaybackStarted = false;
    releaseReplayAudioObjectUrl();
    clearInterval(timerInterval);
    timerInterval = null;
}

/**
 * Resets visible controls for a fresh consultation.
 * Use after state has been cleared for a new session.
 */
function resetVisitUi() {
    document.getElementById('segmentCount').textContent = '0';
    document.getElementById('timer').textContent = '00:00';
    setElementHidden('timer', true);
    setElementHidden('resetBtn', true);
    setElementHidden('summaryBtn', true);
    setElementHidden('startBtn', false);
    setElementHidden('stopBtn', true);
    setElementHidden('reconnectBtn', true);
    setPlainStatus('Ready');
    setElementHidden('emptyState', false);
    setElementHidden('replayProgress', true);
    document.getElementById('replayProgressFill').style.width = '0%';
    resetReplayAudioPlayback();

    const summaryContent = document.getElementById('summaryContent');
    clearElement(summaryContent);
    // Idle sessions hide the panel; it reveals in pending state when transcript text returns.
    document.getElementById('summaryPanel').classList.add('hidden');
    document.getElementById('summaryLoading').classList.add('hidden');
    document.getElementById('summaryTitle').textContent = 'Session Summary';
    setSummaryStatus('pending');
    clearClinicalHints();

    const confidenceBadge = document.getElementById('confidenceBadge');

    // A new visit has no speaker confidence until transcript text arrives.
    if (confidenceBadge) {
        confidenceBadge.classList.add('hidden');
        confidenceBadge.textContent = '';
    }

    const systemBanner = document.getElementById('systemBanner');
    systemBanner.classList.add('hidden');
    systemBanner.textContent = '';
}

/**
 * Subscribes the browser to raw transcript, role, and summary events.
 * Use when live recording or demo replay needs visible updates.
 */
function subscribeToMercure() {
    disconnectMercureStreams();

    // Without a Mercure hub URL, the browser cannot receive live UI updates.
    if (!CONFIG.mercureUrl) {
        return;
    }

    streams = new StreamOrchestrator(CONFIG.mercureUrl);
    streams.subscribe(CONFIG.topicRaw, handleRawSegment);

    // Role updates are optional in degraded/local environments.
    if (CONFIG.enableRoleUpdates) {
        streams.subscribe(CONFIG.topicRoles, handleRoleUpdate);
    }

    // Summary updates appear after the visit finishes.
    if (CONFIG.topicSummary) {
        streams.subscribe(CONFIG.topicSummary, handleSummaryEvent);
    }

    // Hints are assistive and should not block transcript or summary updates.
    if (CONFIG.topicHints) {
        streams.subscribe(CONFIG.topicHints, handleClinicalHintsEvent);
    }
}

/**
 * Preserves transcript state after an unexpected WebSocket close.
 * Use when the clinician's network drops during recording.
 */
function handleUnexpectedDisconnect(closeCode) {
    // Retry first so the clinician can keep speaking without manual action.
    if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
        autoReconnect();
        return;
    }

    isRecording = false;
    setElementHidden('stopBtn', true);
    setElementHidden('reconnectBtn', false);
    const message = `Connection failed - ${segmentIndex} segments preserved`;
    setPlainStatus(message);
    announce(message);
    console.warn(`Transcription socket closed after retries with code ${closeCode}`);
}

/**
 * Schedules the next automatic reconnect attempt.
 * Use when the socket drops but the clinician did not stop recording.
 */
function autoReconnect() {
    reconnectAttempts++;
    const backoffMs = 1000 * Math.pow(2, reconnectAttempts - 1);
    const seconds = Math.round(backoffMs / 1000);
    setPlainStatus(`Reconnecting in ${seconds}s...`);
    announce(`Connection lost. Reconnecting in ${seconds} seconds.`);

    reconnectTimer = setTimeout(() => {
        setPlainStatus(`Reconnecting (${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`);

        // If the mic stream ended, ask the browser to start a fresh live session.
        if (!mediaStream || mediaStream.getTracks().every((track) => track.readyState === 'ended')) {
            startRecording();
            return;
        }

        connectWebSocket();
    }, backoffMs);
}

/**
 * Restarts a failed live connection after automatic retries are exhausted.
 * Use when the clinician clicks Reconnect.
 */
function reconnect() {
    reconnectAttempts = 0;
    setElementHidden('reconnectBtn', true);
    startRecording();
}

/**
 * Formats seconds as the mm:ss timer shown in the visit UI.
 * Use in transcript cards, replay progress, exports, and the header timer.
 */
function formatTime(seconds) {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = Math.floor(seconds % 60);
    return `${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`;
}

/**
 * Updates the visible elapsed-time counter.
 * Use once per second while recording or replaying a consultation.
 */
function updateTimer() {
    const elapsedSeconds = Math.floor((Date.now() - startTime) / 1000);
    document.getElementById('timer').textContent = formatTime(elapsedSeconds);
}

/**
 * Maps a medical role to the CSS color used by cards and dev logs.
 * Use when relabeling visible speakers after inference or manual override.
 */
function getRoleColor(role) {
    switch (role) {
        case 'DOCTOR':
            return 'var(--color-speaker-a)';
        case 'PATIENT':
            return 'var(--color-speaker-b)';
        default:
            return 'var(--color-unknown)';
    }
}

/**
 * Displays a system warning above the transcript.
 * Use when the backend reports a non-transcript stream error.
 */
function showSystemBanner(message) {
    const systemBanner = document.getElementById('systemBanner');
    systemBanner.textContent = message;
    systemBanner.classList.remove('hidden');
}

/**
 * Hides the system warning banner.
 * Use when the condition that raised it (e.g. an unavailable model) has cleared.
 */
function hideSystemBanner() {
    const systemBanner = document.getElementById('systemBanner');

    // Isolated templates may not render the banner element.
    if (!systemBanner) {
        return;
    }

    systemBanner.classList.add('hidden');
    systemBanner.textContent = '';
}
