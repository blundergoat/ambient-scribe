// =========================================================================
// Ambient Scribe recording controls and reconnect flow.
// Runs after streaming helpers and before transcript rendering.
// Owns Start/Stop/New Session, WebSocket setup, recording status, and timers.
// Users reach this code from the main consultation controls and shortcuts.
// =========================================================================

/**
 * Requests the browser microphone and starts a live consultation stream.
 * Reports microphone or setup errors in the status region for the clinician.
 * Returns whether capture actually started, so a failed reconnect can retry or give up.
 */
async function startRecording() {
    // Do not capture audio if roles and the summary would fail - warn and stop.
    if (!(await ensureAiModelAvailable())) {
        return false;
    }

    try {
        mediaStream = await navigator.mediaDevices.getUserMedia(createMedicalMicrophoneConstraints());
        connectWebSocket();
        subscribeToMercure();
        showRecordingUi();
        startRecordingTimerIfNeeded();
        return true;
    } catch (recordingError) {
        console.error('Failed to start recording:', recordingError);
        setPlainStatus(recordingError.name === 'NotAllowedError' ? 'Microphone access denied' : `Error: ${recordingError.message}`);
        return false;
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
 * Use when a live session starts, a demo replay starts, or a live socket reconnects.
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
 * Resolves once the current transcription socket is open.
 * Use before demo replay playback so the first heard chunk has a live socket.
 */
function waitForTranscriptionSocketOpen(timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
        const socket = transcriptionSocket;

        // A missing socket means connectWebSocket was not called for this session.
        if (!socket) {
            reject(new Error('Transcription socket is not connected.'));
            return;
        }

        // An already-open socket can carry replay chunks immediately.
        if (socket.readyState === WebSocket.OPEN) {
            resolve();
            return;
        }

        const openTimeout = setTimeout(() => {
            reject(new Error('Transcription service did not respond.'));
        }, timeoutMs);
        socket.addEventListener('open', () => {
            clearTimeout(openTimeout);
            resolve();
        }, { once: true });
        socket.addEventListener('close', () => {
            clearTimeout(openTimeout);
            reject(new Error('Transcription service refused the session.'));
        }, { once: true });
    });
}

/**
 * Starts PCM audio once the WebSocket is ready.
 * Reports audio setup failures, stops streaming, and avoids sending bad chunks.
 */
async function handleTranscriptionSocketOpened() {
    reconnectAttempts = 0;

    // Demo replay streams decoded WAV PCM and owns its own status line.
    if (isReplayActive) {
        return;
    }

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

    // A drop of the replay's own socket cannot be resumed; keep what was heard and
    // finish. A stale close from a previous live socket must not stop the new replay.
    if (isReplayActive && !isReplayDraining && event.target === transcriptionSocket) {
        setPlainStatus('Streaming connection lost - finishing replay');
        stopReplay();
        return;
    }

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
    setElementHidden('pauseBtn', false);
    setElementHidden('reconnectBtn', true);
    setElementHidden('emptyState', true);
    setElementHidden('timer', false);
    setRecordingStatus('Recording', 'color:#ef4444;font-weight:500;');
    announce('Recording started');
    showRoleIdentificationPending();
}

// Pause suspends audio streaming mid-visit without finalizing. The
// session, socket, and transcript all stay live; Stop remains terminal.
let isSessionPaused = false;
let sessionPausedAt = null;

/**
 * Pauses or continues the active session's audio streaming.
 * Use from the Pause/Continue button during live recording or demo replay.
 * A paused microphone drops its audio entirely (never silence-padded), a
 * paused replay simply halts its audio clock, and the elapsed timer freezes
 * so displayed time stays talk-time.
 */
function togglePauseSession() {
    // Replay pause rides the audio clock: pausing the element stops chunk
    // pacing without touching the socket or the streamed-bytes ledger.
    if (isReplayActive) {
        const replayAudio = document.getElementById('replayAudio');

        // A replay without local audio markup cannot pause meaningfully.
        if (!replayAudio) {
            return;
        }

        if (!isSessionPaused) {
            replayAudio.pause();
            enterPausedSessionUi('Replay paused - press Continue to keep going');
        } else {
            replayAudio.play().catch((playError) => {
                console.warn('Replay resume blocked:', playError);
            });
            leavePausedSessionUi('Replaying audio');
        }
        return;
    }

    // Live recording: gate the PCM streamer and freeze the elapsed timer.
    if (!isRecording) {
        return;
    }

    if (!isSessionPaused) {
        pcmStreamer?.pause();
        clearInterval(timerInterval);
        timerInterval = null;
        sessionPausedAt = Date.now();
        enterPausedSessionUi('Paused - recording will continue');
    } else {
        pcmStreamer?.resume();

        // Shift the start so elapsed time excludes the pause.
        if (sessionPausedAt && startTime) {
            startTime += Date.now() - sessionPausedAt;
        }

        sessionPausedAt = null;
        timerInterval = setInterval(updateTimer, 1000);
        leavePausedSessionUi('Recording');
    }
}

/**
 * Applies the visible paused state and flips the button to Continue.
 */
function enterPausedSessionUi(statusMessage) {
    isSessionPaused = true;
    const pauseButton = document.getElementById('pauseBtn');

    if (pauseButton) {
        pauseButton.textContent = 'Continue';
        pauseButton.setAttribute('aria-label', 'Continue the paused recording');
    }

    setRecordingStatus(statusMessage, 'color:var(--color-review);font-weight:500;');
    announce('Recording paused');
}

/**
 * Clears the paused state and flips the button back to Pause.
 */
function leavePausedSessionUi(statusMessage) {
    isSessionPaused = false;
    const pauseButton = document.getElementById('pauseBtn');

    if (pauseButton) {
        pauseButton.textContent = 'Pause';
        pauseButton.setAttribute('aria-label', 'Pause the recording without ending the session');
    }

    setRecordingStatus(statusMessage, 'color:var(--color-speaker-a);font-weight:500;');
    announce('Recording resumed');
}

/**
 * Hides the pause control and clears pause state for stop and reset paths.
 * Use whenever a session ends: a terminal visit can no longer be paused.
 */
function resetPauseState() {
    isSessionPaused = false;
    sessionPausedAt = null;
    const pauseButton = document.getElementById('pauseBtn');

    if (pauseButton) {
        pauseButton.textContent = 'Pause';
        pauseButton.setAttribute('aria-label', 'Pause the recording without ending the session');
    }

    setElementHidden('pauseBtn', true);
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

// Because final NeMo tail flushes are slow, this timeout limits live Stop without hiding late rows.
// Because replay uses the same wait, both Stop flows return control consistently for the user.
const LIVE_FINALIZE_TIMEOUT_MS = 15000;

/**
 * Stops the live consultation and waits for the backend `finalized` event.
 * The socket close makes the server run its final NeMo pass; the Mercure
 * stream stays open so the held-back tail still renders, then `endLiveStop`
 * reveals post-visit actions and requests the summary (same drain the demo
 * replay uses). Use when the clinician clicks Stop or presses Space/Escape.
 */
function stopRecording() {
    didUserStopRecording = true;
    isRecording = false;
    clearTimeout(reconnectTimer);
    clearTimeout(liveDrainTimeout);
    stopPcmStreaming();

    isLiveDraining = true;
    setRecordingStatus('Finishing transcription...', 'color:var(--color-speaker-a);font-weight:500;');
    liveDrainTimeout = setTimeout(endLiveStop, LIVE_FINALIZE_TIMEOUT_MS);

    // Closing the socket tells the backend to finalize and publish `finalized`.
    transcriptionSocket?.close();
    mediaStream?.getTracks().forEach((track) => track.stop());
    clearInterval(timerInterval);

    setElementHidden('stopBtn', true);
    setElementHidden('reconnectBtn', true);
    // Stop is terminal, so the pause control leaves with it.
    resetPauseState();
    hideAudioLevel();

    const confidenceBadge = document.getElementById('confidenceBadge');

    // The badge stops pulsing once the clinician has ended the visit.
    if (confidenceBadge) {
        confidenceBadge.classList.remove('recording-pulse');
    }

    announce('Recording stopped');

    // Even if no transcript is visible yet, finalize may flush the first rows.
    // `requestSummary` still no-ops if the backend truly finalizes empty.
}

/**
 * Finishes a stopped live visit and reveals post-visit actions.
 * Use when the backend publishes `finalized` or the drain timeout fires.
 */
function endLiveStop() {
    // The finalize event and the drain timeout can race; only one may finish the UI.
    if (!isLiveDraining) {
        return;
    }

    clearTimeout(liveDrainTimeout);
    liveDrainTimeout = null;
    isLiveDraining = false;
    setElementHidden('startBtn', false);
    setPlainStatus('Session ended');
    revealPostVisitActions();
    // The note is generated on demand: finalizing only unlocks the
    // Generate summary button; the clinician decides when to run it.
    updateGenerateSummaryAvailability();
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
    }

    // Reset discards the visit: cancel any finalize wait and close the stream
    // now, so no stale events bleed into the fresh session's UI.
    clearTimeout(liveDrainTimeout);
    liveDrainTimeout = null;
    isLiveDraining = false;
    disconnectMercureStreams();

    CONFIG.sessionId = crypto.randomUUID();
    CONFIG.topicRaw = `scribe/session/${CONFIG.sessionId}/raw`;
    CONFIG.topicRoles = `scribe/session/${CONFIG.sessionId}/roles`;
    CONFIG.topicSummary = `scribe/session/${CONFIG.sessionId}/summary`;

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
    // Stability evidence belongs to the previous visit's speaker identities.
    roleStability = null;
    // A fresh visit has no finalized transcript yet, so no note is allowed.
    terminalAttestation = null;
    // Corrected transcript state belongs to the previous visit's audio.
    if (typeof resetPostVisitCorrectionState === 'function') {
        resetPostVisitCorrectionState();
    }
    latestQualityRecord = null;
    startTime = null;
    manualOverrides.clear();
    autoRowRoles.clear();
    segmentsBySpeaker.clear();
    lastSpeakerId = null;
    lastSegmentBlock = null;
    isReplayActive = false;
    clearInterval(replayTimerInterval);
    replayTimerInterval = null;
    replayDuration = 0;
    wavStreamer?.stop();
    wavStreamer = null;
    isReplayDraining = false;
    replayDrainReason = null;
    clearTimeout(replayDrainTimeout);
    replayDrainTimeout = null;
    isLiveDraining = false;
    clearTimeout(liveDrainTimeout);
    liveDrainTimeout = null;
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
    setSummaryPendingText('Recording in progress - press Generate summary when the consultation ends.');

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

    const topics = [CONFIG.topicRaw];

    // Role updates are optional in degraded/local environments.
    if (CONFIG.enableRoleUpdates) {
        topics.push(CONFIG.topicRoles);
    }

    // Summary updates appear after the visit finishes.
    if (CONFIG.topicSummary) {
        topics.push(CONFIG.topicSummary);
    }

    // One EventSource carries every topic; events route to handlers by type.
    streams = new StreamOrchestrator(CONFIG.mercureUrl);
    streams.connect(topics, {
        segment: handleRawSegment,
        finalized: handleRawSegment,
        error: handleRawSegment,
        quality: handleQualityRecord,
        role_update: handleRoleUpdate,
        system_error: handleRoleUpdate,
        summary: handleSummaryEvent,
    });
}

/**
 * Preserves transcript state after an unexpected WebSocket close.
 * Use when the clinician's network drops during recording.
 */
function handleUnexpectedDisconnect(closeCode) {
    // Retry first so the clinician can keep speaking without manual action.
    if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
        autoReconnect(closeCode);
        return;
    }

    // Retries are exhausted: release the microphone now. resetSession() takes
    // the non-recording branch after this point and would never stop these
    // tracks, leaving the mic capturing while the UI says the connection failed.
    mediaStream?.getTracks().forEach((track) => track.stop());
    mediaStream = null;
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
function autoReconnect(closeCode) {
    reconnectAttempts++;
    const backoffMs = 1000 * Math.pow(2, reconnectAttempts - 1);
    const seconds = Math.round(backoffMs / 1000);
    setPlainStatus(`Reconnecting in ${seconds}s...`);
    announce(`Connection lost. Reconnecting in ${seconds} seconds.`);

    reconnectTimer = setTimeout(async () => {
        setPlainStatus(`Reconnecting (${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`);

        // If the mic stream ended, ask the browser to start a fresh live session.
        if (!mediaStream || mediaStream.getTracks().every((track) => track.readyState === 'ended')) {
            // A refused restart opens no socket, so no close event would ever arrive to retry or to
            // release the microphone. Hand back to the handler that owns both endings.
            if (!(await startRecording())) {
                handleUnexpectedDisconnect(closeCode);
            }
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
