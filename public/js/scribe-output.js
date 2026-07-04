// =========================================================================
// Ambient Scribe replay, summary, and clinical hint output flow.
// Runs after transcript rendering helpers are loaded.
// Owns WAV replay progress, generated summary display, and clinical hints.
// Users reach this code by choosing demo audio or requesting a summary.
// =========================================================================

// At most one post-visit summary request may be in flight per visible session.
// Auto-trigger (live stop) and manual click/retry share this guard.
let isSummaryRequestInFlight = false;

/**
 * Decodes a WAV file, plays it locally, and streams its PCM to live transcription.
 * The WAV goes over the same WebSocket as microphone audio, paced by the audible
 * replay clock, so transcript rows arrive from Mercure exactly like a live visit.
 * Reports decode/connection errors in status and keeps audio controls usable.
 * Returns whether streaming replay started for the visible session.
 */
async function startReplay(file, options = {}) {
    // No file selected means the user cancelled the picker.
    if (!file) {
        return false;
    }

    // Do not replay/transcribe if roles and the summary would fail - warn and stop.
    if (!(await ensureAiModelAvailable())) {
        return false;
    }

    const demoFileInput = document.getElementById('demoFileInput');
    let wasReplayAudioUrlAdopted = false;

    // Manual upload should be clear after the browser hands us the selected WAV.
    if (demoFileInput) {
        demoFileInput.value = '';
    }

    // Stop live capture before replaying a prerecorded consultation.
    if (isRecording) {
        stopRecording();
    }

    resetSession();
    await new Promise((resolveReplayReset) => setTimeout(resolveReplayReset, 100));
    isReplayActive = true;
    setReplayControlsBusy(true, 'Preparing...');
    setElementHidden('startBtn', true);
    setElementHidden('stopBtn', true);
    setElementHidden('summaryBtn', true);
    setElementHidden('emptyState', true);
    setRecordingStatus('Preparing audio...', 'color:var(--color-speaker-a);font-weight:500;');
    subscribeToMercure();

    try {
        // Decoding locally keeps replay on the same 16 kHz PCM contract as the microphone.
        const decodedReplayAudio = await decodeWavToPcm(file);

        // Silent or empty audio would open a session that can never produce text.
        if (decodedReplayAudio.pcmBytes.byteLength === 0) {
            throw new Error('Audio file contains no samples.');
        }

        replayDuration = decodedReplayAudio.durationSeconds;
        connectWebSocket();
        // Audio must not start until the socket can carry the first heard chunk.
        await waitForTranscriptionSocketOpen();

        wavStreamer = new WavPcmStreamer(
            document.getElementById('replayAudio'),
            decodedReplayAudio.pcmBytes,
            {
                onChunk: (pcmChunk) => {
                    // A closed socket means this audio should not reach a stale visit.
                    if (transcriptionSocket?.readyState === WebSocket.OPEN) {
                        transcriptionSocket.send(pcmChunk);
                    }
                },
            },
        );

        wasReplayAudioUrlAdopted = await startReplayAudioPlayback(file, options.audioUrl ?? null);
        wavStreamer.start();
        startReplayProgress();
        updateReplayProgress();

        // Test-only pages without audio markup should not retain fixture blob URLs.
        if (options.audioUrl && !wasReplayAudioUrlAdopted) {
            URL.revokeObjectURL(options.audioUrl);
        }

        setElementHidden('stopBtn', false);
        setReplayControlsBusy(false, 'Upload WAV');
        return true;
    } catch (replayError) {
        console.error('Replay failed:', replayError);
        setPlainStatus(`Replay error: ${replayError.message}`);
        setReplayControlsBusy(false, 'Upload WAV');
        isReplayActive = false;
        wavStreamer?.stop();
        wavStreamer = null;
        transcriptionSocket?.close();
        setElementHidden('startBtn', false);
        setElementHidden('stopBtn', true);
        disconnectMercureStreams();

        // A fixture-created audio URL must be cleaned up if replay never adopted it.
        if (options.audioUrl && !wasReplayAudioUrlAdopted) {
            URL.revokeObjectURL(options.audioUrl);
        }

        return false;
    }
}

/**
 * Toggles available replay controls while a WAV upload is processing.
 * Use so the user cannot start two audio replays at once from the UI.
 */
function setReplayControlsBusy(isBusy, label) {
    const uploadButton = document.getElementById('audioSelectUploadBtn');
    const summaryButton = document.getElementById('summaryBtn');

    // Summary should not be requested while replay upload is still being prepared.
    if (summaryButton) {
        summaryButton.disabled = isBusy;
    }

    // Production pages have no demo audio selector, so replay status is enough.
    if (!uploadButton) {
        return;
    }

    uploadButton.disabled = isBusy;
    const uploadTitle = uploadButton.querySelector('.audio-select__option-title');

    // The dropdown row keeps its two-line shape while showing busy state.
    if (uploadTitle) {
        uploadTitle.textContent = isBusy ? label : 'Upload WAV…';
    }
}

/**
 * Shows replay progress and starts its timer.
 * Use once the backend accepts the uploaded WAV file.
 */
function startReplayProgress() {
    setElementHidden('replayProgress', false);
    setElementHidden('timer', false);
    setRecordingStatus('Replaying audio', 'color:var(--color-speaker-a);font-weight:500;');
    replayTimerInterval = setInterval(updateReplayProgress, 200);
}

/**
 * Plays the same WAV the user selected while browser time reveals transcript text.
 * Use after FastAPI accepts the upload so audio and transcript begin together.
 * Reports playback errors in status and leaves the audio controls visible.
 */
async function startReplayAudioPlayback(file, suppliedAudioUrl = null) {
    const replayAudio = document.getElementById('replayAudio');

    // Test-only pages may skip the production audio element.
    if (!replayAudio) {
        return false;
    }

    releaseReplayAudioObjectUrl();
    replayAudioObjectUrl = suppliedAudioUrl ?? URL.createObjectURL(file);
    replayAudio.src = replayAudioObjectUrl;
    replayAudio.currentTime = 0;
    replayAudio.ontimeupdate = updateReplayProgress;
    hasReplayAudioPlaybackStarted = false;
    replayAudio.onplay = () => {
        hasReplayAudioPlaybackStarted = true;
        setRecordingStatus('Replaying audio', 'color:var(--color-speaker-a);font-weight:500;');
        updateReplayProgress();
    };
    replayAudio.onended = () => {
        // Audio ending sends the PCM tail, then waits for the backend to finalize.
        if (isReplayActive) {
            enterReplayDrain('completed');
        }
    };
    replayAudio.classList.remove('hidden');

    try {
        await replayAudio.play();
        hasReplayAudioPlaybackStarted = true;
        updateReplayProgress();
    } catch (playError) {
        console.warn('Browser blocked replay autoplay:', playError);
        setPlainStatus('Audio ready - press play to hear replay');
    }

    return true;
}

/**
 * Pauses or hides replay audio without touching transcript text.
 * Use when replay stops, completes, fails, or the visit resets.
 */
function stopReplayAudioPlayback(shouldHideAudio = false) {
    const replayAudio = document.getElementById('replayAudio');

    // Missing markup means there is no local audio playback to stop.
    if (!replayAudio) {
        releaseReplayAudioObjectUrl();
        return;
    }

    replayAudio.pause();
    hasReplayAudioPlaybackStarted = false;
    replayAudio.onended = null;
    replayAudio.onplay = null;
    replayAudio.ontimeupdate = null;

    // A reset hides the player; a manual stop leaves paused controls visible.
    if (shouldHideAudio) {
        replayAudio.removeAttribute('src');
        replayAudio.load();
        replayAudio.classList.add('hidden');
        releaseReplayAudioObjectUrl();
    }
}

/**
 * Clears replay audio and revokes the browser object URL.
 * Use when the visible visit is reset or replay cannot continue.
 */
function resetReplayAudioPlayback() {
    stopReplayAudioPlayback(true);
}

/**
 * Releases the temporary URL created from the user's WAV file.
 * Use after replay reset so repeated demos do not retain large audio blobs.
 */
function releaseReplayAudioObjectUrl() {
    // Empty URL means the browser has no blob reference to release.
    if (!replayAudioObjectUrl) {
        return;
    }

    URL.revokeObjectURL(replayAudioObjectUrl);
    replayAudioObjectUrl = null;
}

/**
 * Updates the audio replay progress bar.
 * Use while a prerecorded consultation is playing in the browser.
 */
function updateReplayProgress() {
    // Without active replay timing, there is no progress for the user to see.
    if (!isReplayActive || !replayDuration) {
        return;
    }

    // Blocked autoplay means no audio has been heard or streamed yet.
    if (!isReplayAudioClockReady()) {
        return;
    }

    const elapsedSeconds = getReplayAudioCurrentTime();
    const progress = Math.min(elapsedSeconds / replayDuration, 1);
    document.getElementById('replayProgressFill').style.width = `${progress * 100}%`;
    document.getElementById('timer').textContent = formatTime(elapsedSeconds);
}

/**
 * Checks whether the replay audio clock has started moving.
 * Use before showing progress so blocked autoplay reads as not started.
 */
function isReplayAudioClockReady() {
    const replayAudio = document.getElementById('replayAudio');

    // Test-only pages without an audio element have no local clock to wait for.
    if (!replayAudio) {
        return true;
    }

    return hasReplayAudioPlaybackStarted || replayAudio.currentTime > 0 || replayAudio.ended;
}

/**
 * Reads the current demo audio clock.
 * Use so replay progress follows what the user can hear.
 */
function getReplayAudioCurrentTime() {
    const replayAudio = document.getElementById('replayAudio');

    // Missing audio markup means replay has no browser clock to follow.
    if (!replayAudio) {
        return 0;
    }

    return replayAudio.currentTime || 0;
}

/**
 * Stops whichever active session owns the shared Stop button.
 * Use when the clinician clicks Stop during recording or demo audio replay.
 */
function stopCurrentSession() {
    // Demo replay uses local audio and a backend replay task, not the microphone stream.
    if (isReplayActive) {
        stopReplay();
        return;
    }

    // Live recording still uses the original microphone stop path.
    if (isRecording) {
        stopRecording();
    }
}

/**
 * Stops demo audio before the file ends and drains in-flight transcription.
 * Use when the user wants to summarize only the audio heard so far.
 */
function stopReplay() {
    // Hidden or repeated Stop clicks should not mutate the completed visit twice.
    if (!isReplayActive) {
        return false;
    }

    // Stopping during the finalize wait skips straight to the finished UI.
    if (isReplayDraining) {
        endReplay();
        return false;
    }

    stopReplayAudioPlayback(false);
    enterReplayDrain('stopped');
    announce('Replay stopped');

    return true;
}

// A dead backend must not leave replay stuck waiting for the `finalized` event.
const REPLAY_FINALIZE_TIMEOUT_MS = 15000;

/**
 * Stops sending replay PCM and waits for the backend `finalized` event.
 * The socket close makes the server run its final NeMo pass - the same path a
 * live recording stop uses - and `handleRawSegment` calls `endReplay` when the
 * `finalized` event arrives, so late transcript rows still render meanwhile.
 * Use when replay audio ends, the user stops early, or the socket drops.
 */
function enterReplayDrain(reason) {
    // A second drain request means the finalize wait is already running.
    if (!isReplayActive || isReplayDraining) {
        return;
    }

    isReplayDraining = true;
    replayDrainReason = reason;

    // Completion streams the WAV tail; an early stop sends only heard audio.
    if (reason === 'completed') {
        wavStreamer?.finish();
    } else {
        wavStreamer?.flushHeard();
    }

    wavStreamer?.stop();
    // Closing the socket tells the backend to finalize and publish `finalized`.
    transcriptionSocket?.close();
    setElementHidden('stopBtn', true);
    setRecordingStatus('Finishing transcription...', 'color:var(--color-speaker-a);font-weight:500;');
    replayDrainTimeout = setTimeout(endReplay, REPLAY_FINALIZE_TIMEOUT_MS);
}

/**
 * Finishes audio replay and reveals post-visit actions.
 * Use when the backend publishes `finalized` or the drain timeout fires.
 */
function endReplay() {
    // The finalize event and the drain timeout can race; only one may finish the UI.
    if (!isReplayActive) {
        return;
    }

    clearTimeout(replayDrainTimeout);
    replayDrainTimeout = null;
    isReplayActive = false;
    isReplayDraining = false;
    wavStreamer?.stop();
    wavStreamer = null;
    clearReplayTimer();
    setReplayControlsBusy(false, 'Upload WAV');
    setElementHidden('startBtn', false);
    setElementHidden('stopBtn', true);
    setElementHidden('replayProgress', true);
    setPlainStatus(replayDrainReason === 'stopped' ? 'Replay stopped' : 'Replay complete');
    replayDrainReason = null;
    revealPostVisitActions();
}

/**
 * Clears replay progress timing state.
 * Use when replay stops early, completes, or the visit resets.
 */
function clearReplayTimer() {
    clearInterval(replayTimerInterval);
    replayTimerInterval = null;
}

/**
 * Requests a generated summary for the current session.
 * Reports request errors in the panel and leaves a plain failure message for the clinician.
 */
async function requestSummary() {
    // No transcript text means there is nothing useful to summarize.
    if (segmentIndex === 0) {
        return;
    }

    // One in-flight request per session keeps auto-trigger and retry from overlapping.
    if (isSummaryRequestInFlight) {
        return;
    }
    isSummaryRequestInFlight = true;

    const summaryPanel = document.getElementById('summaryPanel');
    const summaryLoading = document.getElementById('summaryLoading');
    const summaryContent = document.getElementById('summaryContent');
    const summaryButton = document.getElementById('summaryBtn');

    // While the note is generating, the button should not start duplicate requests.
    if (summaryButton) {
        summaryButton.disabled = true;
    }

    summaryPanel.classList.remove('hidden');
    setSummaryStatus('generating');
    summaryLoading.classList.remove('hidden');
    clearElement(summaryContent);

    try {
        const response = await fetch(`/session/${CONFIG.sessionId}/summary`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ segments: readVisibleTranscriptSegments() }),
        });

        const summaryPayload = await readJsonResponse(response, { detail: 'Summary generation failed.' });
        renderSummaryResponse(response, summaryPayload);
    } catch (summaryError) {
        console.error('Summary request failed:', summaryError);
        showSummaryFailure('Could not reach the summary service.');
    } finally {
        summaryLoading.classList.add('hidden');
        isSummaryRequestInFlight = false;

        // A completed request can be rerun if the clinician wants to regenerate the note.
        if (summaryButton) {
            summaryButton.disabled = false;
            setElementHidden('summaryBtn', false);
        }
    }
}

/**
 * Routes a summary HTTP response to the right panel state.
 * Success renders the note; 404 is a plain "no transcript yet"; other errors
 * (502/503) mean the model or service failed and get an actionable fix note.
 */
function renderSummaryResponse(response, summaryPayload) {
    if (response.ok && !summaryPayload.isFallbackPayload) {
        renderSummary(summaryPayload);
        handleClinicalHintsEvent({
            type: 'clinical_hints',
            hints: summaryPayload.clinical_hints ?? [],
        });
        return;
    }

    // No transcript yet is a normal state, not a model/service problem.
    if (response.status === 404) {
        setSummaryStatus('failed');
        showSummaryMessage(summaryPayload.detail || 'No transcript to summarise yet.');
        return;
    }

    showSummaryFailure(summaryPayload.detail || 'Summary generation failed.');
}

/**
 * Handles summary events published over Mercure.
 * Use when the backend finishes a summary after the panel is open.
 */
function handleSummaryEvent(summaryEvent) {
    // Only summary events should replace the clinician's summary panel.
    if (summaryEvent.type === 'summary') {
        document.getElementById('summaryLoading')?.classList.add('hidden');
        renderSummary(summaryEvent);
    }
}

/**
 * Handles clinical hint events from Mercure or the summary HTTP fallback.
 * Use when the completed visit has assistive suggestions for clinician review.
 */
function handleClinicalHintsEvent(hintsEvent) {
    // Non-hint Mercure payloads should not affect the assistive sidebar.
    if (hintsEvent.type !== 'clinical_hints') {
        return;
    }

    renderClinicalHints(hintsEvent.hints ?? []);
}

/**
 * Renders the non-blocking clinical hints sidebar.
 * Empty hints hide the sidebar so the transcript stays the primary workspace.
 */
function renderClinicalHints(hints) {
    const hintsPanel = document.getElementById('clinicalHintsPanel');
    const hintsList = document.getElementById('clinicalHintsList');

    // Missing sidebar markup means older templates still keep summary rendering safe.
    if (!hintsPanel || !hintsList) {
        return;
    }

    clearElement(hintsList);
    hintsPanel.classList.remove('hidden');

    // The section stays visible after a summary; an empty run says so plainly.
    if (hints.length === 0) {
        hintsList.appendChild(createElement('div', {
            className: 'clinical-hints__empty',
            text: 'No suggestions for this consultation.',
        }));
        return;
    }

    // Each hint becomes a dismissible item while preserving transcript focus.
    for (const hint of hints) {
        hintsList.appendChild(createClinicalHintElement(hint));
    }

    hintsPanel.classList.remove('hidden');
}

/**
 * Creates one dismissible hint item.
 * Use when the assistant backend flags a medication, follow-up, or note gap.
 */
function createClinicalHintElement(hint) {
    const hintElement = createElement('div', { className: 'clinical-hint' });
    const dismissButton = createElement('button', {
        className: 'clinical-hint__dismiss',
        text: 'x',
        attributes: {
            type: 'button',
            'aria-label': 'Dismiss hint',
        },
    });

    dismissButton.addEventListener('click', () => {
        hintElement.remove();
        const hintsList = document.getElementById('clinicalHintsList');

        // The section stays in place; dismissing the last hint says it is done.
        if (hintsList?.children.length === 0) {
            hintsList.appendChild(createElement('div', {
                className: 'clinical-hints__empty',
                text: 'All suggestions dismissed.',
            }));
        }
    });

    const hintHeader = createElement('div', { className: 'clinical-hint__header' }, [
        createElement('div', { className: 'clinical-hint__type', text: formatHintType(hint.type ?? 'suggestion') }),
        dismissButton,
    ]);
    const hintBody = createElement('div', { className: 'clinical-hint__text', text: hint.text ?? '' });
    const hintChildren = [hintHeader, hintBody];

    // Evidence spans are short reminders, not raw transcript dumps.
    if (hint.evidence_span) {
        hintChildren.push(createElement('div', {
            className: 'clinical-hint__evidence',
            text: `Evidence: ${hint.evidence_span}`,
        }));
    }

    hintElement.replaceChildren(...hintChildren);
    return hintElement;
}

/**
 * Converts backend hint codes into compact labels.
 * Use when the sidebar shows the category before the clinician reads the text.
 */
function formatHintType(hintType) {
    return String(hintType).replace(/_/g, ' ');
}

/**
 * Clears the hint sidebar for a fresh session.
 * Use when the clinician starts a new visit or reruns a demo file.
 */
function clearClinicalHints() {
    const hintsPanel = document.getElementById('clinicalHintsPanel');
    const hintsList = document.getElementById('clinicalHintsList');

    // Missing hint elements mean the older page has nothing to reset.
    if (!hintsPanel || !hintsList) {
        return;
    }

    clearElement(hintsList);
    hintsPanel.classList.add('hidden');
}

/**
 * Renders generated summary sections and key points.
 * Use when the clinician reviews the post-visit note.
 */
function renderSummary(summaryPayload) {
    const summaryContent = document.getElementById('summaryContent');

    // Missing summary content means there is no panel body to render into.
    if (!summaryContent) {
        return;
    }

    const summaryPanel = document.getElementById('summaryPanel');
    summaryPanel.classList.remove('hidden');
    clearElement(summaryContent);

    const renderedBlocks = [
        ...createSummarySectionBlocks(summaryPayload.sections ?? []),
        ...createSummaryKeyPointBlocks(summaryPayload.key_points ?? []),
    ];

    // Empty summary payloads should explain that no content is available.
    if (renderedBlocks.length === 0) {
        setSummaryStatus('failed');
        showSummaryMessage('No summary content available.');
        return;
    }

    summaryContent.replaceChildren(...renderedBlocks);
    setSummaryStatus('generated');
    // A rendered summary means the model recovered, so clear any stale warning banner.
    hideSystemBanner();
}

/**
 * Creates DOM blocks for SOAP-style summary sections.
 * Empty sections mean this part of the summary is skipped for the user.
 */
function createSummarySectionBlocks(sections) {
    const sectionBlocks = [];

    // Each backend section becomes one readable block in the summary panel.
    for (const section of sections) {
        sectionBlocks.push(createElement('div', { className: 'summary-section' }, [
            createElement('div', { className: 'summary-section__heading', text: section.heading }),
            createElement('div', { className: 'summary-section__content', text: section.content }),
        ]));
    }

    return sectionBlocks;
}

/**
 * Creates the Key Points list in the summary panel.
 * Empty key points mean no list is shown to the clinician.
 */
function createSummaryKeyPointBlocks(keyPoints) {
    // No key points means the sections alone carry the generated note.
    if (keyPoints.length === 0) {
        return [];
    }

    const keyPointList = createElement('ul', { className: 'summary-key-points' });

    // Every model key point is textContent so it cannot inject markup.
    for (const keyPoint of keyPoints) {
        keyPointList.appendChild(createElement('li', { text: keyPoint }));
    }

    return [
        createElement('div', { className: 'summary-section' }, [
            createElement('div', { className: 'summary-section__heading', text: 'Key Points' }),
            keyPointList,
        ]),
    ];
}

/**
 * Shows a plain summary-panel message.
 * Use for empty, failed, or unreachable summary states.
 */
function showSummaryMessage(message) {
    const summaryContent = document.getElementById('summaryContent');
    const messageElement = createElement('p', {
        className: 'text-sm',
        text: message,
        style: 'color:var(--text-subtle)',
    });
    summaryContent.replaceChildren(messageElement);
}

/**
 * Renders a summary failure with an actionable fix note and a page-level warning.
 * Use for provider/service errors (Ollama or Bedrock unreachable, 502/503, network) so the
 * clinician sees the likely cause and how to recover while the transcript stays visible.
 */
function showSummaryFailure(detail) {
    setSummaryStatus('failed');

    const summaryContent = document.getElementById('summaryContent');
    const failureMessage = createElement('p', {
        className: 'text-sm',
        text: detail,
        style: 'color:var(--text-strong); margin:0 0 0.5rem',
    });
    const fixNote = createElement('p', {
        className: 'text-xs',
        text: 'The AI model is unavailable. Run  ./scripts/check-ai-model.sh  to start it, then use Retry Summary.',
        style: 'color:var(--text-subtle); margin:0; line-height:1.5',
    });
    summaryContent.replaceChildren(failureMessage, fixNote);

    // A page-level warning keeps the cause visible even when the summary panel is scrolled away.
    showSystemBanner('AI model unavailable - run  ./scripts/check-ai-model.sh  to start it (or set ROLE_AGENT_MODEL_PROVIDER=bedrock).');
}

/**
 * Pre-flight check that the off-GPU role/summary model is reachable.
 * Returns true when a consultation may start; otherwise warns and returns false so the
 * caller aborts, since transcribing without the model yields no roles and no summary.
 */
async function ensureAiModelAvailable() {
    let available = false;
    let detail = 'agent unreachable';
    try {
        const response = await fetch('/agent/model-health', { headers: { Accept: 'application/json' } });
        const payload = await readJsonResponse(response, { available: false, detail: 'model health check failed' });
        available = payload.available === true;
        detail = payload.detail || detail;
    } catch (modelHealthError) {
        console.warn('Model health check failed:', modelHealthError);
    }

    if (!available) {
        showSystemBanner('AI model unavailable - run  ./scripts/check-ai-model.sh  to start it (or set ROLE_AGENT_MODEL_PROVIDER=bedrock).');
        setPlainStatus('AI model unavailable - consultation not started');
        return false;
    }

    hideSystemBanner();
    return true;
}

/**
 * Reflects summary progress in the panel.
 * States: 'pending' (placeholder before the visit ends), 'generating' (loading row
 * shows progress), 'generated' (green confirmation), 'failed' (retry offered while
 * the transcript stays visible for review).
 */
function setSummaryStatus(summaryState) {
    const summaryStatus = document.getElementById('summaryStatus');
    const summaryStatusBadge = document.getElementById('summaryStatusBadge');
    const summaryRetryButton = document.getElementById('summaryRetryBtn');
    const summaryPending = document.getElementById('summaryPending');

    // Older templates without the status row still render summary content safely.
    if (!summaryStatus || !summaryStatusBadge) {
        return;
    }

    // The pending placeholder only shows before a summary has been requested.
    summaryPending?.classList.toggle('hidden', summaryState !== 'pending');
    summaryStatusBadge.classList.remove('summary-status__badge--generated', 'summary-status__badge--failed');

    if (summaryState === 'generated') {
        summaryStatusBadge.textContent = '✓ Generated';
        summaryStatusBadge.classList.add('summary-status__badge--generated');
        summaryStatus.classList.remove('hidden');
        summaryRetryButton?.classList.add('hidden');
        return;
    }

    if (summaryState === 'failed') {
        summaryStatusBadge.textContent = 'Summary unavailable';
        summaryStatusBadge.classList.add('summary-status__badge--failed');
        summaryStatus.classList.remove('hidden');
        summaryRetryButton?.classList.remove('hidden');
        return;
    }

    // Pending and generating both hide the badge; the pending row or loading row speaks instead.
    summaryStatus.classList.add('hidden');
    summaryRetryButton?.classList.add('hidden');
}

/**
 * Reveals the summary panel in its pending state once transcript text exists.
 * Use when the first segment arrives, so the panel appears only when a summary can be generated.
 */
function revealSummaryPending() {
    const summaryPanel = document.getElementById('summaryPanel');

    // Older templates without the panel still capture transcript safely.
    if (!summaryPanel) {
        return;
    }

    summaryPanel.classList.remove('hidden');
    setSummaryStatus('pending');
}
