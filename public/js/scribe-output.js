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
 * Uploads a WAV file, plays it locally, and replays it through the transcript UI.
 * Reports upload/backend errors in status and keeps audio controls usable.
 * Returns whether the backend accepted replay for the visible session.
 */
async function startReplay(file, options = {}) {
    // No file selected means the user cancelled the picker.
    if (!file) {
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
    setReplayControlsBusy(true, 'Processing...');
    setElementHidden('startBtn', true);
    setElementHidden('stopBtn', true);
    setElementHidden('summaryBtn', true);
    setElementHidden('emptyState', true);
    setRecordingStatus('Processing WAV...', 'color:var(--color-speaker-a);font-weight:500;');
    subscribeToMercure();

    const replayFormData = new FormData();
    replayFormData.append('file', file);

    try {
        const response = await fetch(`/session/${CONFIG.sessionId}/replay?speed=1.0`, {
            method: 'POST',
            body: replayFormData,
        });

        const replayPayload = await readJsonResponse(response, {
            detail: 'Replay service returned an unreadable response.',
        });

        const replayMetadata = requireAcceptedReplayPayload(response, replayPayload);
        replayDuration = replayMetadata.durationSeconds;
        replayTranscriptSegments = replayMetadata.transcriptSegments;
        replayNextSegmentIndex = 0;
        isBrowserClockReplaySession = true;
        wasReplayAudioUrlAdopted = await startReplayAudioPlayback(file, options.audioUrl ?? null);
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
 * Validates the replay response before the UI shows progress.
 * Use after a WAV upload so parser warnings or empty transcripts stay visible as errors.
 * Throws when the backend response would leave the replay UI stuck or misleading.
 */
function requireAcceptedReplayPayload(response, replayPayload) {
    // Failed uploads should leave the clinician in a recoverable replay state.
    if (!response.ok || replayPayload.isFallbackPayload) {
        throw new Error(replayPayload.detail || `HTTP ${response.status}`);
    }

    const replaySegmentCount = Number(replayPayload.segments ?? 0);
    const replayDurationSeconds = Number(replayPayload.duration_seconds ?? 0);
    const transcriptSegments = Array.isArray(replayPayload.transcript_segments)
        ? replayPayload.transcript_segments
        : [];

    // A replay with no transcript text would leave the progress bar stuck.
    if (replaySegmentCount <= 0 || replayDurationSeconds <= 0 || transcriptSegments.length === 0) {
        throw new Error(replayPayload.detail || 'Replay did not return transcript segments.');
    }

    return { durationSeconds: replayDurationSeconds, transcriptSegments };
}

/**
 * Toggles available replay controls while a WAV upload is processing.
 * Use so the user cannot start two audio replays at once from the UI.
 */
function setReplayControlsBusy(isBusy, label) {
    const uploadButton = document.querySelector('.audio-fixture-panel__footer button');
    const summaryButton = document.getElementById('summaryBtn');

    // Summary should not be requested while replay upload is still being prepared.
    if (summaryButton) {
        summaryButton.disabled = isBusy;
    }

    // Production pages have no demo audio panel, so replay status is enough.
    if (!uploadButton) {
        return;
    }

    uploadButton.disabled = isBusy;
    uploadButton.textContent = label;
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
        // Audio ending should reveal the final transcript rows before post-visit actions.
        if (isReplayActive) {
            endReplay();
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

    // Blocked autoplay should not reveal transcript before the user hears audio.
    if (!isReplayAudioClockReady()) {
        return;
    }

    const elapsedSeconds = getReplayAudioCurrentTime();
    revealReplaySegmentsUpToAudioTime(elapsedSeconds);
    const progress = Math.min(elapsedSeconds / replayDuration, 1);
    document.getElementById('replayProgressFill').style.width = `${progress * 100}%`;
    document.getElementById('timer').textContent = formatTime(elapsedSeconds);

    // Reaching the audio end unlocks the same post-visit actions as live stop.
    if (progress >= 1 && replayNextSegmentIndex >= replayTranscriptSegments.length) {
        endReplay();
    }
}

/**
 * Checks whether the replay audio clock has started moving.
 * Use before revealing text so blocked autoplay does not show unheard speech.
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
 * Use so transcript reveal follows what the user can hear, not backend processing speed.
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
 * Reveals replay transcript rows up to the browser audio time.
 * Use while audio plays, pauses, or stops so visible text matches heard speech.
 */
function revealReplaySegmentsUpToAudioTime(audioTimeSeconds) {
    // Tolerance threshold: 0.2s because sub-second ASR timestamps can feel late.
    const revealToleranceSeconds = 0.2;

    // Empty replay metadata means there are no prepared rows to reveal.
    if (replayTranscriptSegments.length === 0) {
        return;
    }

    // Each due segment is appended once as the audible WAV reaches its timestamp.
    while (replayNextSegmentIndex < replayTranscriptSegments.length) {
        const replaySegment = replayTranscriptSegments[replayNextSegmentIndex];
        const segmentStartSeconds = Number(replaySegment.start ?? 0);

        // Future segments remain hidden until the user hears that part of the WAV.
        if (segmentStartSeconds > audioTimeSeconds + revealToleranceSeconds) {
            break;
        }

        handleRawSegment({ type: 'segment', ...replaySegment });
        replayNextSegmentIndex++;
    }
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
 * Stops demo audio before the file ends and reveals post-visit actions.
 * Use when the user wants to summarize only the transcript captured so far.
 */
function stopReplay() {
    // Hidden or repeated Stop clicks should not mutate the completed visit twice.
    if (!isReplayActive) {
        return false;
    }

    const stoppedAtSeconds = getReplayAudioCurrentTime();
    revealReplaySegmentsUpToAudioTime(stoppedAtSeconds);
    isReplayActive = false;
    clearReplayTimer();
    stopReplayAudioPlayback(false);
    disconnectMercureStreams();
    setReplayControlsBusy(false, 'Upload WAV');
    setElementHidden('startBtn', false);
    setElementHidden('stopBtn', true);
    setElementHidden('replayProgress', true);
    setPlainStatus('Replay stopped');
    revealPostVisitActions();
    const summaryButton = document.getElementById('summaryBtn');

    // Summary waits for the stop request so late replay text is less likely to leak in.
    if (summaryButton) {
        summaryButton.disabled = true;
    }

    announce('Replay stopped');
    syncStoppedReplayOnServer(readVisibleTranscriptSegments(), {
        audioTimeSeconds: stoppedAtSeconds,
        wasCompleted: false,
    }).finally(() => {
        // Once replay stop has synced, the captured transcript can be summarized.
        if (summaryButton) {
            summaryButton.disabled = false;
        }
    });

    return true;
}

/**
 * Syncs stopped replay state for the current browser session.
 * Reports network failures to the console and does not roll back the stopped UI.
 * Use after stop/end so summary uses the visible transcript snapshot.
 */
async function syncStoppedReplayOnServer(visibleSegments = [], options = {}) {
    try {
        await fetch(`/session/${CONFIG.sessionId}/replay/stop`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                visible_segments: visibleSegments,
                audio_time_seconds: options.audioTimeSeconds ?? null,
                was_completed: options.wasCompleted ?? false,
            }),
        });
    } catch (stopSyncError) {
        console.warn('Replay stop sync failed:', stopSyncError);
    }
}

/**
 * Finishes audio replay and reveals post-visit actions.
 * Use when the local WAV reaches the end or the visible transcript is complete.
 */
function endReplay() {
    revealReplaySegmentsUpToAudioTime(replayDuration + 0.2);
    isReplayActive = false;
    clearReplayTimer();
    setReplayControlsBusy(false, 'Upload WAV');
    setElementHidden('startBtn', false);
    setElementHidden('stopBtn', true);
    setElementHidden('replayProgress', true);
    setPlainStatus('Replay complete');
    revealPostVisitActions();
    syncStoppedReplayOnServer(readVisibleTranscriptSegments(), {
        audioTimeSeconds: replayDuration,
        wasCompleted: true,
    });
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

    // No suggestions means there is nothing for the clinician to review.
    if (hints.length === 0) {
        hintsPanel.classList.add('hidden');
        setClinicalHintsLayoutVisible(false);
        return;
    }

    // Each hint becomes a dismissible item while preserving transcript focus.
    for (const hint of hints) {
        hintsList.appendChild(createClinicalHintElement(hint));
    }

    hintsPanel.classList.remove('hidden');
    setClinicalHintsLayoutVisible(true);
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
        const hintsPanel = document.getElementById('clinicalHintsPanel');
        const hintsList = document.getElementById('clinicalHintsList');

        // The sidebar closes once the clinician dismisses the final suggestion.
        if (hintsList?.children.length === 0) {
            hintsPanel?.classList.add('hidden');
            setClinicalHintsLayoutVisible(false);
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
    setClinicalHintsLayoutVisible(false);
}

/**
 * Adds or removes the desktop hints column.
 * Use when the sidebar opens or closes so hidden hints do not leave blank space.
 */
function setClinicalHintsLayoutVisible(shouldShowHintsColumn) {
    const appLayout = document.getElementById('appLayout');

    // Isolated tests may omit the full app shell while still exercising rendering.
    if (!appLayout) {
        return;
    }

    appLayout.classList.toggle('app-layout--with-hints', shouldShowHintsColumn);
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
        text: 'The summary model looks unavailable. Check that Ollama (or Bedrock) is running and the agent container can reach it, then use Retry Summary. See README_STACK.md.',
        style: 'color:var(--text-subtle); margin:0; line-height:1.5',
    });
    summaryContent.replaceChildren(failureMessage, fixNote);

    // A page-level warning keeps the cause visible even when the summary panel is scrolled away.
    showSystemBanner('AI model unavailable — summaries and speaker roles need Ollama or Bedrock reachable from the agent container. See README_STACK.md.');
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
