// =========================================================================
// Ambient Scribe replay and summary output flow.
// Runs after transcript rendering helpers are loaded.
// Owns WAV replay progress and generated summary display.
// Users reach this code by choosing demo audio or requesting a summary.
// =========================================================================

// At most one post-visit summary request may be in flight per visible session.
// Auto-trigger (live stop) and manual click/retry share this guard. Keying by
// session id (not a boolean) lets a reset visit request its own summary while
// the previous visit's request is still running.
let summaryRequestSessionId = null;
// Correction runs once before summary, and its safe outcome explains note provenance.
let correctionRequestPromise = null;
let correctionSessionId = null;
let correctionOutcomeForVisibleSession = null;
// The rendered note payload backs the Copy draft note export and the status
// axes; null means no note artifact exists for the visible session.
let latestRenderedSummaryPayload = null;

/**
 * Decodes a WAV file, plays it locally, and streams its PCM to live transcription.
 * The WAV goes over the same WebSocket as microphone audio, paced by the audible
 * replay clock, because demo replay must preserve the same Stop/finalize behavior
 * clinicians see in a live visit.
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
        // Example: the user selected a corrupt WAV or the transcription socket closed during setup.
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
        // Example: the browser blocked autoplay until the user presses the visible audio control.
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

// Because final NeMo tail flushes are slow, this timeout limits replay Stop without hiding late rows.
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

    // Replay has finalized, so start correction while retained audio is still available.
    requestSummary();
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
 * Runs correction first, then creates the note for Stop, replay, and retry.
 * Reports request errors in the panel and leaves a plain failure message for the clinician.
 */
async function requestSummary() {
    // No transcript text means there is nothing useful to summarize.
    if (segmentIndex === 0) {
        return;
    }

    // No terminal attestation means the backend is still finishing the visit
    // (e.g. the Stop wait timed out first). Requesting a note now is how an
    // emergency plan once went missing - wait and auto-resume instead.
    if (!terminalAttestation) {
        showSummaryWaitingForSource();
        return;
    }

    // One in-flight request per session keeps auto-trigger and retry from overlapping.
    if (summaryRequestSessionId === CONFIG.sessionId) {
        return;
    }
    const requestedSessionId = CONFIG.sessionId;
    summaryRequestSessionId = requestedSessionId;

    const summaryPanel = document.getElementById('summaryPanel');
    const summaryLoading = document.getElementById('summaryLoading');
    const summaryContent = document.getElementById('summaryContent');

    summaryPanel.classList.remove('hidden');
    setSummaryStatus('generating');
    renderNoteStatusAxes({ phase: 'generating' });
    summaryLoading.classList.remove('hidden');
    clearElement(summaryContent);

    try {
        setSummaryLoadingText('Improving transcript...');
        await ensureCorrectedTranscriptReady();

        // A blocked source means no honest note can exist yet (still
        // finalizing, lineage changed, or correction lost rows) - show the
        // specific reason instead of generating from a bad source.
        if (
            correctionOutcomeForVisibleSession?.sessionId === CONFIG.sessionId
            && correctionOutcomeForVisibleSession.status === 'blocked'
        ) {
            setSummaryStatus('failed');
            renderNoteStatusAxes({
                phase: 'blocked',
                blockedReason: correctionOutcomeForVisibleSession.reasonCategory,
            });
            showSummaryMessage(
                blockedSourceMessage(correctionOutcomeForVisibleSession.reasonCategory)
            );
            return;
        }

        setSummaryLoadingText('Generating summary...');
        const response = await fetch(`/session/${requestedSessionId}/summary`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ segments: readVisibleTranscriptSegments() }),
        });

        const summaryPayload = await readJsonResponse(response, { detail: 'Summary generation failed.' });
        // A reset or replay may have started a new session mid-request; the
        // old visit's summary must not render into the new visit's panel.
        if (CONFIG.sessionId !== requestedSessionId) {
            return;
        }
        renderSummaryResponse(response, summaryPayload);
    } catch (summaryError) {
        // Example: the clinician stopped a visit while the summary proxy was temporarily unreachable.
        console.error('Summary request failed:', summaryError);
        if (CONFIG.sessionId === requestedSessionId) {
            showSummaryFailure('Could not reach the summary service.');
        }
    } finally {
        // The shared panel now belongs to the new session; only the owning
        // request may touch its loading state.
        if (CONFIG.sessionId === requestedSessionId) {
            setSummaryLoadingText('Generating summary...');
            summaryLoading.classList.add('hidden');
        }
        if (summaryRequestSessionId === requestedSessionId) {
            summaryRequestSessionId = null;
        }
    }
}

/**
 * Shows that the note is waiting for the backend to finish the transcript.
 * Use when the user's Stop wait ended before the `finalized` event arrived;
 * the note starts automatically once the terminal source exists.
 */
function showSummaryWaitingForSource() {
    const summaryPanel = document.getElementById('summaryPanel');
    const summaryPendingText = document.getElementById('summaryPendingText');

    // Test pages without the panel still capture transcript safely.
    if (!summaryPanel) {
        return;
    }

    summaryPanel.classList.remove('hidden');
    setSummaryStatus('pending');
    renderNoteStatusAxes({ phase: 'waiting' });

    // The pending row explains the wait instead of implying generation began.
    if (summaryPendingText) {
        summaryPendingText.textContent =
            'Waiting for the final transcript — the note will generate automatically.';
    }
}

/**
 * Starts the note after a late `finalized` event ends the source wait.
 * Use from the transcript stream handler when the visit UI already ended on
 * the bounded timeout but the backend has now attested the full transcript.
 */
function resumeSummaryAfterLateFinalize() {
    // Without attestation or visible text there is still nothing to generate.
    if (!terminalAttestation || segmentIndex === 0) {
        return;
    }

    // A request already running for this session must not be duplicated.
    if (summaryRequestSessionId === CONFIG.sessionId) {
        return;
    }

    requestSummary();
}

/**
 * Maps a blocked note source to the sentence the clinician should see.
 * Empty/unknown reasons fall back to a safe generic explanation.
 */
function blockedSourceMessage(reason) {
    const messages = {
        source_not_terminal: 'Source still finalizing — note unavailable.',
        stale_lineage: 'The transcript changed after finalization — note unavailable.',
        unaccounted_meaningful_rows: 'Correction lost part of the visit — note unavailable.',
        correction_pending: 'Transcript correction has not completed — note unavailable.',
        source_exceeds_note_limit: 'The visit exceeds the note input limit — the transcript remains available.',
        empty_visit: 'No usable audio was captured — note unavailable.',
    };
    return messages[reason] ?? 'The note source is unavailable for this visit.';
}

/**
 * Gathers the note's current state for the three status axes.
 * Use when a note has just rendered or is being copied, so screen and
 * clipboard describe the same source, flags, and clinician-review truth.
 *
 * @param {object|null} summaryPayload - the rendered note; null means no
 *   artifact exists and callers should use a non-generated phase instead.
 * @returns {object} statusModel accepted by deriveNoteStatusLines.
 */
function collectNoteStatusModel(summaryPayload) {
    // A missing payload still produces a valid zero-flag model.
    const {
        unverified_key_points: unverifiedKeyPoints = [],
        sections = [],
        source_state: sourceState = null,
    } = summaryPayload ?? {};

    // A v2 note counts review flags per claim; the shared payload walk lives
    // beside the copy serializer so screen and clipboard always agree.
    if (summaryPayload?.schema_version === 2 && typeof v2ReviewCountsFrom === 'function') {
        return {
            phase: 'generated',
            sourceState,
            roleSettlement: attestedRoleSettlement(),
            ...v2ReviewCountsFrom(summaryPayload),
        };
    }

    let unverifiedCount = unverifiedKeyPoints.length;
    let lowConfidenceCount = 0;

    // Section-level flags add to the review count the axes announce.
    for (const section of sections) {
        unverifiedCount += (section.unverified ?? []).length;
        lowConfidenceCount += (section.low_confidence ?? []).length;
    }

    return {
        phase: 'generated',
        sourceState,
        roleSettlement: attestedRoleSettlement(),
        unverifiedCount,
        lowConfidenceCount,
    };
}

/**
 * Reads the visit's role-settlement outcome from the held attestation.
 * Use when the axes need to know whether speaker labels finished settling -
 * a frozen settlement becomes a visible review reason on the note.
 *
 * @returns {string|null} 'settled', 'failed_frozen', or null when no
 *   finalized attestation exists yet (test pages, or a visit still running).
 */
function attestedRoleSettlement() {
    // Test pages load without the transcript module's attestation state.
    if (typeof terminalAttestation === 'undefined' || !terminalAttestation) {
        return null;
    }

    return terminalAttestation.roleSettlement ?? null;
}

/**
 * Renders the three status axes and gates the Copy draft note action.
 * Use on every note lifecycle change: the axes are always-visible text
 * (never tooltip-only), and copying stays disabled until an honest note
 * artifact exists - an unavailable state can never reach the clipboard.
 *
 * @param {object} statusModel - see deriveNoteStatusLines; phase decides
 *   which axes render. Null lines hide the strip (transient failure UI).
 * @returns {void} Updates badges, the review-reason list, and the copy button.
 */
function renderNoteStatusAxes(statusModel) {
    const axesStrip = document.getElementById('noteStatusAxes');

    // Test pages without the axes strip or the copy module skip quietly.
    if (!axesStrip || typeof deriveNoteStatusLines !== 'function') {
        return;
    }

    const statusLines = deriveNoteStatusLines(statusModel);
    const reasonsList = document.getElementById('noteReviewReasons');

    // No source line means a transient failure owns the panel; hide the strip.
    if (!statusLines.sourceLine) {
        axesStrip.classList.add('hidden');
        reasonsList?.classList.add('hidden');
    } else {
        axesStrip.classList.remove('hidden');
        renderAxisBadges(statusLines, statusModel);
        renderReviewReasonList(reasonsList, statusLines.reviewReasons);
    }

    setCopyNoteAvailability(statusLines);
}

/**
 * Writes the three axis badges the clinician reads above the note.
 * Use from renderNoteStatusAxes whenever the strip is visible, so source,
 * automated-review, and clinician-review truths stay independently worded.
 *
 * @param {object} statusLines - derived axis wording; null review lines mean
 *   those badges hide (no note artifact exists yet).
 * @param {object} statusModel - the state the wording came from; the fallback
 *   source state styles its badge as caution.
 * @returns {void} Updates badge text, visibility, and caution styling.
 */
function renderAxisBadges(statusLines, statusModel) {
    const sourceBadge = document.getElementById('noteAxisSource');
    const automatedBadge = document.getElementById('noteAxisAutomated');
    const clinicianBadge = document.getElementById('noteAxisClinician');

    sourceBadge.textContent = statusLines.sourceLine;
    // A live-fallback source renders as caution, not plain provenance.
    sourceBadge.dataset.reviewRequired =
        statusModel?.sourceState === 'whole_visit_live_fallback' ? '1' : '0';

    // The automated-review axis only exists once a note artifact exists.
    automatedBadge.classList.toggle('hidden', !statusLines.automatedReviewLine);
    if (statusLines.automatedReviewLine) {
        automatedBadge.textContent = statusLines.automatedReviewLine;
        // Any flagged item styles the badge as a review demand, not decoration.
        automatedBadge.dataset.reviewRequired =
            statusLines.reviewReasons.length > 0 ? '1' : '0';
    }

    // The clinician-review axis likewise appears only with a real note.
    clinicianBadge.classList.toggle('hidden', !statusLines.clinicianReviewLine);
    if (statusLines.clinicianReviewLine) {
        clinicianBadge.textContent = statusLines.clinicianReviewLine;
    }
}

/**
 * Renders the readable list of review reasons under the axis badges.
 * Use with the badges so "Review required (n)" is always explained on
 * screen with the same reasons that copy into the note.
 *
 * @param {HTMLElement|null} reasonsList - the list element; null (test pages)
 *   skips rendering safely.
 * @param {string[]} reviewReasons - reasons to show; empty hides the list.
 * @returns {void} Replaces the list items and toggles visibility.
 */
function renderReviewReasonList(reasonsList, reviewReasons) {
    // Test pages without the list element cannot show reasons.
    if (!reasonsList) {
        return;
    }

    // Each reason is readable text under the badges, and copies with the note.
    reasonsList.replaceChildren(
        ...reviewReasons.map((reviewReason) =>
            createElement('li', { text: reviewReason }))
    );
    reasonsList.classList.toggle('hidden', reviewReasons.length === 0);
}

/**
 * Enables or disables the Copy draft note action to match note availability.
 * Use on every axes render: an unavailable source must never be copyable,
 * and the disabled button explains exactly why.
 *
 * @param {object} statusLines - derived axis state; noteAvailable false
 *   disables the button with the specific reason as its accessible label.
 * @returns {void} Updates the button's disabled state and aria-label.
 */
function setCopyNoteAvailability(statusLines) {
    const copyNoteButton = document.getElementById('copyNoteBtn');

    // Test pages without the copy button have nothing to gate.
    if (!copyNoteButton) {
        return;
    }

    copyNoteButton.disabled = !statusLines.noteAvailable;
    // The label says why copying is unavailable, for keyboard and AT users too.
    copyNoteButton.setAttribute(
        'aria-label',
        statusLines.noteAvailable
            ? 'Copy draft note with its source and review status'
            : `Copy draft note — unavailable: ${statusLines.sourceLine ?? 'note generation failed'}`
    );
}

/**
 * Resets post-visit correction state for the next visible consultation.
 * Use when New Session clears the transcript; otherwise a prior corrected
 * artifact could make the next summary skip correction.
 */
function resetPostVisitCorrectionState() {
    correctionRequestPromise = null;
    correctionSessionId = null;
    correctionOutcomeForVisibleSession = null;
    // A new visit starts with no note artifact: nothing to copy, no axes yet.
    latestRenderedSummaryPayload = null;
    renderNoteStatusAxes({ phase: 'reset' });
    setSummarySourceNotice(null);

    // The Transcript tab caches corrected rows per session; test pages load without it.
    if (typeof resetSummaryTabsState === 'function') {
        resetSummaryTabsState();
    }
}

/**
 * Runs post-stop correction once before generating the summary.
 * Use inside `requestSummary()` so live stop, replay, and panel retry all
 * share the same corrected-transcript-first behavior.
 */
async function ensureCorrectedTranscriptReady() {
    // A corrected artifact already exists for this browser session.
    if (
        correctionOutcomeForVisibleSession?.sessionId === CONFIG.sessionId
        && correctionOutcomeForVisibleSession.status === 'ready'
    ) {
        return;
    }

    // A duplicate retry should wait for the existing correction request.
    if (correctionRequestPromise && correctionSessionId === CONFIG.sessionId) {
        await correctionRequestPromise;
        return;
    }

    const correctionRequestedSessionId = CONFIG.sessionId;
    correctionSessionId = correctionRequestedSessionId;
    const correctionRequestForThisSession = requestTranscriptCorrection(
        correctionRequestedSessionId,
    );
    correctionRequestPromise = correctionRequestForThisSession;

    try {
        const correctionPayload = await correctionRequestForThisSession;

        // A reset can finish while the old visit's correction request is still returning.
        if (CONFIG.sessionId === correctionRequestedSessionId) {
            // Blocked keeps its specific reason so the panel can explain why
            // no note exists; ready/unavailable keep today's meanings.
            const correctionStatus = correctionPayload?.status === 'ready'
                ? 'ready'
                : correctionPayload?.status === 'blocked'
                    ? 'blocked'
                    : 'unavailable';
            correctionOutcomeForVisibleSession = {
                sessionId: correctionRequestedSessionId,
                status: correctionStatus,
                reasonCategory: typeof correctionPayload?.reason === 'string'
                    ? correctionPayload.reason
                    : typeof correctionPayload?.reason_category === 'string'
                        ? correctionPayload.reason_category
                        : null,
                attempted: correctionPayload?.attempted === true,
            };
        }
    } finally {
        // A prior visit finishing late must not clear a newer visit's pending correction.
        if (correctionRequestPromise === correctionRequestForThisSession) {
            correctionRequestPromise = null;
        }
    }
}

/**
 * Calls the same-origin correction proxy with the current visible transcript.
 * Use before summary generation; failures are logged and converted into a live
 * transcript fallback so the user still receives a note.
 *
 * @param {string} requestedSessionId - Visible visit UUID; empty cannot map to retained audio.
 * @returns {Promise<object>} Safe correction outcome; unavailable means use visible live rows.
 */
async function requestTranscriptCorrection(requestedSessionId = CONFIG.sessionId) {
    try {
        const response = await fetch(`/session/${requestedSessionId}/correction`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ segments: readVisibleTranscriptSegments() }),
        });
        const correctionPayload = await readJsonResponse(response, { status: 'unavailable' });

        // Non-OK proxy responses mean the live transcript remains the summary source.
        if (!response.ok || correctionPayload.isFallbackPayload) {
            console.warn('Transcript correction unavailable:', correctionPayload.detail ?? response.status);
            return {
                ...correctionPayload,
                status: 'unavailable',
                attempted: correctionPayload.attempted === true,
                reason_category: typeof correctionPayload.reason_category === 'string'
                    ? correctionPayload.reason_category
                    : 'request_unavailable',
            };
        }

        return correctionPayload;
    } catch (correctionError) {
        // Example: the user stops a visit just as the same-origin correction proxy disconnects.
        console.warn('Transcript correction failed:', correctionError);
        return {
            status: 'unavailable',
            attempted: false,
            reason_category: 'request_failed',
        };
    }
}

/**
 * Updates the summary loading copy without changing the panel layout.
 * Use while the post-stop flow moves from correction to summary generation.
 */
function setSummaryLoadingText(message) {
    const summaryLoadingText = document.getElementById('summaryLoadingText');

    // Test pages without the loading text cannot show progress copy.
    if (!summaryLoadingText) {
        return;
    }

    summaryLoadingText.textContent = message;
}

/**
 * Routes a summary HTTP response to the right panel state.
 * Success renders the note; 404 is a plain "no transcript yet"; other errors
 * (502/503) mean the model or service failed and get an actionable fix note.
 */
function renderSummaryResponse(response, summaryPayload) {
    // The backend refused to build a note from an unattested source; the
    // transcript stays reviewable and the panel explains the specific reason.
    if (response.ok && summaryPayload.status === 'blocked') {
        setSummaryStatus('failed');
        renderNoteStatusAxes({ phase: 'blocked', blockedReason: summaryPayload.reason });
        showSummaryMessage(blockedSourceMessage(summaryPayload.reason));
        return;
    }

    if (response.ok && !summaryPayload.isFallbackPayload) {
        renderSummary(summaryPayload);
        return;
    }

    // No transcript yet is a normal state, not a model/service problem.
    if (response.status === 404) {
        setSummaryStatus('failed');
        showSummaryMessage(summaryPayload.detail || 'No transcript to summarise yet.');
        return;
    }

    showSummaryFailure(
        summaryPayload.detail || 'Summary generation failed.',
        summaryPayload.reason ?? null,
    );
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
    // The previous note's open evidence disclosure died with its DOM nodes.
    openClaimDisclosure = null;

    // Key points lead as the TL;DR strip, then the SOAP sections (summary UX M4).
    // A v2 payload renders atomic claims with claim-scoped evidence (M06);
    // v1 keeps the section renderer behind its honest source label.
    const isClaimSchema = summaryPayload.schema_version === 2;
    const renderedBlocks = isClaimSchema
        ? [
            ...createClaimKeyPointBlocks(summaryPayload),
            ...createClaimSectionBlocks(summaryPayload),
        ]
        : [
            ...createSummaryKeyPointBlocks(
                summaryPayload.key_points ?? [],
                summaryPayload.unverified_key_points ?? [],
            ),
            ...createSummarySectionBlocks(summaryPayload.sections ?? []),
        ];

    // An older v1 note names its provenance form, so section-level sources
    // can never read as claim-level evidence.
    if (!isClaimSchema && v1SectionsCarryCitations(summaryPayload.sections ?? [])) {
        renderedBlocks.unshift(createV1SectionSourcesNotice());
    }

    // Empty summary payloads should explain that no content is available.
    if (renderedBlocks.length === 0) {
        latestRenderedSummaryPayload = null;
        setSummaryStatus('failed');
        renderNoteStatusAxes({ phase: 'failed' });
        showSummaryMessage('No summary content available.');
        return;
    }

    summaryContent.replaceChildren(...renderedBlocks);
    // The rendered payload becomes the copy source and the axes' input.
    latestRenderedSummaryPayload = summaryPayload;
    setSummaryStatus('generated');
    renderNoteStatusAxes(collectNoteStatusModel(summaryPayload));
    setSummarySourceNotice(summaryPayload);
    setSummaryTruncationNotice(summaryPayload);
    // A rendered summary means the model recovered, so clear any stale warning banner.
    hideSystemBanner();
}

/**
 * Creates DOM blocks for SOAP-style summary sections.
 * Use when the summary panel renders the note after Stop/finalized.
 *
 * @param {Array<object>} sections - SOAP sections from the backend; empty means the panel shows no section cards.
 * @returns {HTMLElement[]} section blocks shown in the panel; empty means the UI shows no-content copy.
 */
function createSummarySectionBlocks(sections) {
    const sectionBlocks = [];

    // Each backend section becomes one readable block in the order the clinician reviews it.
    for (const section of sections) {
        // Missing prose or marker arrays mean the section renders plain rather than failing.
        const sectionContent = section.content ?? '';
        const unverifiedSentences = section.unverified ?? [];
        const lowConfidenceSentences = section.low_confidence ?? [];
        const contentBlock = createElement(
            'div',
            { className: 'summary-section__content' },
            createNoteProseNodes(
                sectionContent,
                unverifiedSentences,
                lowConfidenceSentences,
            ),
        );
        // Cited sections get a superscript provenance affordance after the prose;
        // uncited sections and test pages without the popover script stay plain.
        const provenanceBlock = typeof createSectionProvenanceBlock === 'function'
            ? createSectionProvenanceBlock(section.citations ?? [], section.heading)
            : null;

        if (provenanceBlock) {
            contentBlock.appendChild(provenanceBlock);
        }

        sectionBlocks.push(createElement('div', { className: 'summary-section' }, [
            createElement('div', { className: 'summary-section__heading', text: section.heading }),
            contentBlock,
        ]));
    }

    return sectionBlocks;
}

/**
 * Creates the Key Points list in the summary panel.
 * Empty key points mean no list is shown to the clinician.
 *
 * @param {string[]} keyPoints - TL;DR lines from the backend; empty hides the strip entirely.
 * @param {string[]} unverifiedKeyPoints - lines the fidelity checks could not support; empty
 *   means every key point renders plain, exactly as before M07.
 * @returns {HTMLElement[]} the strip block, or empty when there is nothing to list.
 */
function createSummaryKeyPointBlocks(keyPoints, unverifiedKeyPoints = []) {
    // No key points means the sections alone carry the generated note.
    if (keyPoints.length === 0) {
        return [];
    }

    const keyPointList = createElement('ul', { className: 'summary-key-points' });

    // Every model key point is textContent so it cannot inject markup.
    for (const keyPoint of keyPoints) {
        // A line the transcript could not support carries the visible marker (M07).
        if (unverifiedKeyPoints.includes(keyPoint)) {
            keyPointList.appendChild(createElement('li', {}, [createUnverifiedMarker(keyPoint)]));
        } else {
            keyPointList.appendChild(createElement('li', { text: keyPoint }));
        }
    }

    return [
        createElement('div', { className: 'summary-section' }, [
            createElement('div', { className: 'summary-section__heading', text: 'Key Points' }),
            keyPointList,
        ]),
    ];
}

// =========================================================================
// Claim-level provenance rendering (M06, schema v2).
// Each atomic claim carries its own evidence affordance; activating it opens
// a non-modal in-flow disclosure showing the complete cited source turns,
// labelled display context, and the exact quote/review state. The page
// behind the disclosure stays fully interactive - this is never a dialog.
// =========================================================================

// At most one claim disclosure is open; Escape closes it and focus returns
// to its toggle only when the reviewer was inside it.
let openClaimDisclosure = null;
// Fallback for disclosure ids when a claim arrives without its server id.
let claimDisclosureSequence = 0;

// The disclosure's fixed reminder that linkage is not clinical review (M06).
const CLAIM_EVIDENCE_HELP_TEXT =
    'Source links and quote matching are limited automated checks — not clinical review or approval.';

/**
 * Says whether any v1 section carries citations worth labelling.
 * Use before showing the v1 adapter label; an uncited note has no source
 * form to explain, so no label renders.
 *
 * @param {Array<object>} sections - v1 sections; empty means no citations.
 * @returns {boolean} true when at least one section cites rows.
 */
function v1SectionsCarryCitations(sections) {
    return sections.some((section) => (section.citations ?? []).length > 0);
}

/**
 * Builds the honest label for v1 payloads rendered by the adapter path.
 * Use above the note body so section-level counters are never mistaken for
 * claim-level evidence; the wording is fixed by CONTRACTS.md.
 */
function createV1SectionSourcesNotice() {
    return createElement('p', {
        className: 'summary-v1-sources-note',
        text: 'Section sources — not mapped to individual claims',
        attributes: { role: 'note' },
    });
}

/**
 * Indexes the payload's deduplicated source units by their unit id.
 * Use once per render so each claim resolves its citations without rescanning.
 *
 * @param {object} summaryPayload - v2 payload; a missing pool means claims
 *   resolve zero units and render their honest uncited state.
 * @returns {Map<string, object>} unit id → hydrated unit.
 */
function summaryUnitsById(summaryPayload) {
    const unitsById = new Map();

    for (const sourceUnit of summaryPayload.source_units ?? []) {
        const unitId = String(sourceUnit.unit_id ?? '').trim();

        // A unit without identity can never be cited by a claim.
        if (unitId !== '') {
            unitsById.set(unitId, sourceUnit);
        }
    }

    return unitsById;
}

/**
 * Resolves one claim's cited unit ids against the hydrated unit pool.
 * Use when rendering the claim; the server already validated ids, so a
 * missing unit is dropped defensively rather than rendered as a blank.
 *
 * @param {object} claim - v2 claim; empty ids resolve to no units.
 * @param {Map<string, object>} unitsById - hydrated pool for this payload.
 * @returns {Array<object>} the claim's own evidence units, payload order.
 */
function resolvedClaimUnits(claim, unitsById) {
    return (claim.source_unit_ids ?? [])
        .map((unitId) => unitsById.get(String(unitId)))
        .filter((sourceUnit) => sourceUnit !== undefined);
}

/**
 * Creates DOM blocks for schema-v2 SOAP sections of ordered atomic claims.
 * Use from the summary renderer when the payload declares schema v2.
 *
 * @param {object} summaryPayload - v2 payload; empty sections render nothing.
 * @returns {HTMLElement[]} one block per section, claims in reading order.
 */
function createClaimSectionBlocks(summaryPayload) {
    const unitsById = summaryUnitsById(summaryPayload);
    const sectionBlocks = [];

    // Each section renders its claims as flowing prose in reading order.
    for (const section of summaryPayload.sections ?? []) {
        const contentBlock = createElement('div', {
            className: 'summary-section__content summary-section__content--claims',
        });

        for (const claim of section.claims ?? []) {
            appendClaimNodes(contentBlock, claim, unitsById);
        }

        sectionBlocks.push(createElement('div', { className: 'summary-section' }, [
            createElement('div', { className: 'summary-section__heading', text: section.heading }),
            contentBlock,
        ]));
    }

    return sectionBlocks;
}

/**
 * Creates the Key Points strip for schema-v2 claim key points.
 * Use from the summary renderer; each point is a claim with its own
 * evidence affordance, exactly like a section claim.
 *
 * @param {object} summaryPayload - v2 payload; no key points hides the strip.
 * @returns {HTMLElement[]} the strip block, or empty when there is none.
 */
function createClaimKeyPointBlocks(summaryPayload) {
    const keyPointClaims = summaryPayload.key_points ?? [];

    // No key points means the sections alone carry the generated note.
    if (keyPointClaims.length === 0) {
        return [];
    }

    const unitsById = summaryUnitsById(summaryPayload);
    const keyPointList = createElement('ul', { className: 'summary-key-points' });

    for (const claim of keyPointClaims) {
        const keyPointItem = createElement('li', {});
        appendClaimNodes(keyPointItem, claim, unitsById);
        keyPointList.appendChild(keyPointItem);
    }

    return [
        createElement('div', { className: 'summary-section' }, [
            createElement('div', { className: 'summary-section__heading', text: 'Key Points' }),
            keyPointList,
        ]),
    ];
}

/**
 * Appends one claim's prose, evidence affordance, and disclosure region.
 * Use for section claims and key points so both share one interaction.
 * The disclosure sits in the document flow directly after its claim - it
 * pushes content down instead of overlaying it, and never traps focus.
 *
 * @param {HTMLElement} claimContainer - section content div or key-point li.
 * @param {object} claim - one v2 claim; flag fields may be absent.
 * @param {Map<string, object>} unitsById - hydrated unit pool.
 * @returns {void} Mutates claimContainer.
 */
function appendClaimNodes(claimContainer, claim, unitsById) {
    // Real spaces keep copied and assistive reading natural between claims.
    if (claimContainer.childNodes.length > 0) {
        claimContainer.appendChild(document.createTextNode(' '));
    }

    claimDisclosureSequence += 1;
    const disclosureId = `claimEvidence-${String(claim.claim_id ?? '').trim() || claimDisclosureSequence}`;
    const citedUnits = resolvedClaimUnits(claim, unitsById);
    const evidenceToggle = createClaimEvidenceToggle(claim, citedUnits, disclosureId);
    const disclosureRegion = createClaimDisclosure(claim, citedUnits, disclosureId);
    const claimBody = createElement('span', {
        className: 'summary-claim',
        dataset: { claimId: String(claim.claim_id ?? '') },
    }, [createClaimTextSpan(claim), evidenceToggle]);

    evidenceToggle.addEventListener('click', () => {
        toggleClaimEvidence(evidenceToggle, disclosureRegion);
    });

    claimContainer.appendChild(claimBody);
    claimContainer.appendChild(disclosureRegion);
}

/**
 * Builds the visible prose span for one claim, review cues attached.
 * Use inside the claim body: flagged claims carry a visible style and an
 * explanatory title, and the exact wording lives in the disclosure - a
 * bare unexplained "Unverified" never renders.
 *
 * @param {object} claim - one v2 claim; no flags renders plain text.
 * @returns {HTMLElement} the claim's text span.
 */
function createClaimTextSpan(claim) {
    // The shared flag reader keeps the panel and the clipboard in agreement.
    const claimFlags = typeof claimReviewFlags === 'function'
        ? claimReviewFlags(claim)
        : { uncited: false, reasonFlagged: false, wordingReview: false };
    const reviewExplanations = [];

    if (claimFlags.uncited) {
        reviewExplanations.push('No cited transcript evidence — review required');
    }
    // Each deterministic reason explains itself in the reviewer's tooltip too.
    if (claimFlags.reasonFlagged) {
        for (const reviewReason of claim.review_reasons ?? []) {
            reviewExplanations.push(String(reviewReason.detail || reviewReason.reason || ''));
        }
    }

    const claimText = createElement('span', {
        className: reviewExplanations.length > 0
            ? 'summary-claim__text summary-claim__text--review'
            : 'summary-claim__text',
        text: String(claim.text ?? ''),
    });

    // The claim-scoped M03 wording cue keeps its established styling and help.
    if (claimFlags.wordingReview) {
        claimText.classList.add('summary-low-confidence');
        claimText.tabIndex = 0;
        claimText.setAttribute('aria-describedby', 'confidenceWordingHelp');
        claimText.dataset.confidenceTooltip = 'Low-confidence transcription';
        reviewExplanations.push('Low-confidence transcription');
    }

    if (reviewExplanations.length > 0) {
        claimText.setAttribute('title', reviewExplanations.filter(Boolean).join('; '));
    }

    return claimText;
}

/**
 * Builds one claim's evidence affordance button.
 * Use beside the claim text: cited claims show their own unit count and
 * uncited/absence claims show an honest wording chip instead - a count of
 * zero or a fabricated link never renders.
 *
 * @param {object} claim - one v2 claim.
 * @param {Array<object>} citedUnits - the claim's resolved units.
 * @param {string} disclosureId - id of the region this button controls.
 * @returns {HTMLElement} toggle button with aria-expanded state.
 */
function createClaimEvidenceToggle(claim, citedUnits, disclosureId) {
    const hasCitedEvidence = claim.evidence_basis === 'source_unit' && citedUnits.length > 0;
    let toggleText;
    let toggleLabel;

    if (hasCitedEvidence) {
        const turnNoun = citedUnits.length === 1 ? 'source turn' : 'source turns';
        toggleText = String(citedUnits.length);
        toggleLabel = `View evidence for this claim, ${citedUnits.length} ${turnNoun}`;
    } else if (claim.evidence_basis === 'transcript_absence') {
        toggleText = 'Absence-based';
        toggleLabel = 'About this statement — based on absence from the transcript';
    } else {
        // Basis none and anything unknown read as uncited, review required.
        toggleText = 'No cited evidence';
        toggleLabel = 'About this statement — no transcript evidence cited, review required';
    }

    return createElement('button', {
        className: hasCitedEvidence
            ? 'summary-claim__toggle'
            : 'summary-claim__toggle summary-claim__toggle--basis',
        attributes: {
            type: 'button',
            'aria-expanded': 'false',
            'aria-controls': disclosureId,
            'aria-label': toggleLabel,
        },
        text: toggleText,
    });
}

/**
 * States the claim's evidence/quote status in the contract's limiting language.
 * Use as the disclosure's first line so automated linkage can never read as
 * clinical verification.
 *
 * @param {object} claim - one v2 claim.
 * @param {boolean} hasCitedEvidence - whether resolved cited units exist.
 * @returns {string} one explanatory sentence.
 */
function claimEvidenceStateText(claim, hasCitedEvidence) {
    // Uncited and absence-based claims explain themselves without a link.
    if (!hasCitedEvidence) {
        return claim.evidence_basis === 'transcript_absence'
            ? 'This statement is based on absence: bounded automated checks found no mention in the selected visit transcript — verify manually.'
            : 'No transcript evidence was cited for this statement — review required.';
    }

    if (claim.quote_state === 'verified') {
        return 'Exact quoted wording matched the cited transcript — an automated check, not clinical approval.';
    }
    if (claim.quote_state === 'not_matched') {
        return 'Quoted wording could not be matched to the cited transcript — verify manually.';
    }
    if (claim.quote_state === 'wrong_role') {
        return 'Quoted wording was found under a different speaker than cited — verify manually.';
    }

    // No quotation marks in the claim: linked wording is a paraphrase.
    return 'Source linked — the claim paraphrases the cited transcript; wording is not a verbatim quote.';
}

/**
 * Builds one claim's non-modal in-flow evidence disclosure.
 * Use once per claim. The region shows the complete cited turns with
 * timestamp and speaker, visibly separated display context, the exact
 * quote/review state, claim-scoped "Open in transcript", an explicit
 * Close, and the fixed automated-check limitation text. It supports
 * Escape and focus return and keeps normal Tab order - never a focus trap.
 *
 * @param {object} claim - one v2 claim.
 * @param {Array<object>} citedUnits - the claim's resolved units.
 * @param {string} disclosureId - id the toggle's aria-controls points at.
 * @returns {HTMLElement} hidden disclosure region, in-flow after the claim.
 */
function createClaimDisclosure(claim, citedUnits, disclosureId) {
    const hasCitedEvidence = claim.evidence_basis === 'source_unit' && citedUnits.length > 0;
    const disclosureRegion = createElement('div', {
        className: 'summary-claim__disclosure hidden',
        attributes: {
            id: disclosureId,
            role: 'region',
            'aria-label': `Evidence for claim: ${String(claim.text ?? '').slice(0, 80)}`,
            tabindex: '-1',
        },
    });

    disclosureRegion.appendChild(createElement('p', {
        className: 'summary-claim__state',
        text: claimEvidenceStateText(claim, hasCitedEvidence),
    }));

    // Every deterministic review reason reads in full where the evidence is.
    const reviewReasons = claim.review_reasons ?? [];
    if (reviewReasons.length > 0) {
        disclosureRegion.appendChild(createElement(
            'ul',
            { className: 'summary-claim__reasons' },
            reviewReasons.map((reviewReason) => createElement('li', {
                text: String(reviewReason.detail || reviewReason.reason || ''),
            })),
        ));
    }

    // Cited turns render inside the disclosure's single scroll region.
    if (hasCitedEvidence) {
        disclosureRegion.appendChild(createElement(
            'div',
            { className: 'summary-claim__evidence' },
            citedUnits.map(createEvidenceUnitBlock),
        ));
    }

    const actionsRow = createElement('div', { className: 'summary-claim__actions' });

    // Only real cited evidence earns a transcript jump; uncited and
    // absence-based claims must not expose a misleading deep link.
    if (hasCitedEvidence) {
        const citedSegmentIds = citedUnits.flatMap(
            (sourceUnit) => (sourceUnit.rows ?? []).map((unitRow) => String(unitRow.segment_id))
        ).filter((segmentId) => segmentId !== '');
        const openInTranscriptButton = createElement('button', {
            className: 'summary-claim__open',
            attributes: { type: 'button' },
            text: 'Open in transcript',
        });
        openInTranscriptButton.addEventListener('click', () => {
            // Test pages without the tabs script keep the disclosure-only behaviour.
            if (typeof openTranscriptDeepLink !== 'function') {
                selectSummaryTab('transcript');
                return;
            }

            openTranscriptDeepLink(citedSegmentIds).catch((deepLinkError) => {
                console.warn('Transcript deep link failed:', deepLinkError);
            });
        });
        actionsRow.appendChild(openInTranscriptButton);
    }

    const closeButton = createElement('button', {
        className: 'summary-claim__close',
        attributes: { type: 'button' },
        text: 'Close',
    });
    closeButton.addEventListener('click', () => {
        closeClaimEvidence(disclosureRegion);
    });
    actionsRow.appendChild(closeButton);
    disclosureRegion.appendChild(actionsRow);

    // The fixed reminder that source linkage is never clinical review.
    disclosureRegion.appendChild(createElement('p', {
        className: 'summary-claim__help',
        text: CLAIM_EVIDENCE_HELP_TEXT,
    }));

    return disclosureRegion;
}

/**
 * Builds one complete evidence unit inside a claim disclosure.
 * Use per cited unit: the full turn text derives from its hydrated ordered
 * rows, and neighbour rows render visibly separated as display-only context
 * that never joins the evidence or its count.
 *
 * @param {object} sourceUnit - hydrated unit {unit_id, role, start, end,
 *   rows, context_before, context_after}; missing arrays act empty.
 * @returns {HTMLElement} one unit block.
 */
function createEvidenceUnitBlock(sourceUnit) {
    const unitBlock = createElement('div', {
        className: 'summary-claim__unit',
        dataset: {
            unitId: String(sourceUnit.unit_id ?? ''),
            segmentIds: (sourceUnit.rows ?? [])
                .map((unitRow) => String(unitRow.segment_id ?? ''))
                .filter((segmentId) => segmentId !== '')
                .join(' '),
        },
    });

    // Preceding neighbour rows are reading aid only, and say so.
    for (const contextRow of sourceUnit.context_before ?? []) {
        unitBlock.appendChild(createContextRowLine(contextRow));
    }

    const speakerLabel = typeof getRoleLabel === 'function'
        ? getRoleLabel(sourceUnit.role)
        : String(sourceUnit.role ?? '');
    const unitTimeSpan = Number.isFinite(sourceUnit.start) && Number.isFinite(sourceUnit.end)
        ? `${formatTime(sourceUnit.start)}–${formatTime(sourceUnit.end)}`
        : '';
    unitBlock.appendChild(createElement('div', { className: 'summary-claim__unit-evidence' }, [
        createElement('span', { className: 'summary-claim__unit-meta', text: `[${unitTimeSpan}] ${speakerLabel}` }),
        createElement('span', {
            className: 'summary-claim__unit-text',
            // The complete turn stitches from its ordered rows with real spaces.
            text: (sourceUnit.rows ?? [])
                .map((unitRow) => String(unitRow.text ?? '').trim())
                .filter((rowText) => rowText !== '')
                .join(' '),
        }),
    ]));

    // Following neighbour rows are likewise labelled, never counted.
    for (const contextRow of sourceUnit.context_after ?? []) {
        unitBlock.appendChild(createContextRowLine(contextRow));
    }

    return unitBlock;
}

/**
 * Builds one visibly labelled display-context row line.
 * Use around a unit's evidence text; rows carry no speaker role, so the
 * line never invents one.
 *
 * @param {object} contextRow - {segment_id, start, end, text}.
 * @returns {HTMLElement} labelled context line.
 */
function createContextRowLine(contextRow) {
    const contextTime = Number.isFinite(contextRow.start) ? `[${formatTime(contextRow.start)}] ` : '';

    return createElement('div', {
        className: 'summary-claim__context',
        text: `Context (not evidence) — ${contextTime}${String(contextRow.text ?? '')}`,
    });
}

/**
 * Opens or shuts one claim's evidence disclosure from its toggle.
 * Use from the toggle click; opening one closes any other so Escape and
 * the visible expansion stay unambiguous.
 */
function toggleClaimEvidence(evidenceToggle, disclosureRegion) {
    // A second activation of the open claim's toggle closes it.
    if (evidenceToggle.getAttribute('aria-expanded') === 'true') {
        closeClaimEvidence(disclosureRegion);
        return;
    }

    closeOpenClaimEvidence();
    disclosureRegion.classList.remove('hidden');
    evidenceToggle.setAttribute('aria-expanded', 'true');
    openClaimDisclosure = { evidenceToggle, disclosureRegion };
}

/**
 * Closes one claim disclosure and keeps the reviewer's focus anchored.
 * Use from the Close button, Escape, and toggle re-activation; focus
 * returns to the owning toggle only when it was inside the region, so
 * closing never yanks focus from elsewhere on the page.
 */
function closeClaimEvidence(disclosureRegion) {
    const owningToggle = openClaimDisclosure?.disclosureRegion === disclosureRegion
        ? openClaimDisclosure.evidenceToggle
        : document.querySelector(`[aria-controls="${disclosureRegion.id}"]`);
    const hadFocusInside = disclosureRegion.contains(document.activeElement)
        || document.activeElement === owningToggle;

    disclosureRegion.classList.add('hidden');
    owningToggle?.setAttribute('aria-expanded', 'false');

    if (openClaimDisclosure?.disclosureRegion === disclosureRegion) {
        openClaimDisclosure = null;
    }

    // Escape and Close keep keyboard users anchored on the claim's toggle.
    if (hadFocusInside) {
        owningToggle?.focus();
    }
}

/**
 * Closes whichever claim disclosure is open, if any.
 * Use before opening another claim's evidence.
 */
function closeOpenClaimEvidence() {
    // Nothing is open, so there is nothing to close.
    if (!openClaimDisclosure) {
        return;
    }

    closeClaimEvidence(openClaimDisclosure.disclosureRegion);
}

// Escape closes the open claim disclosure no matter where focus sits.
document.addEventListener('keydown', (keyEvent) => {
    if (keyEvent.key === 'Escape' && openClaimDisclosure) {
        keyEvent.stopPropagation();
        closeClaimEvidence(openClaimDisclosure.disclosureRegion);
    }
});

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
 * Renders a summary failure with guidance matched to its actual cause.
 * A named reason from the backend gets honest, specific copy; everything
 * else keeps the provider-unavailable guidance (Ollama or Bedrock
 * unreachable, 502/503, network) so the clinician sees the likely cause
 * and how to recover while the transcript stays visible.
 *
 * @param {string} detail - clinician-facing failure sentence from the backend.
 * @param {string|null} failureReason - machine reason from the 502 body;
 *   null/unknown means the generic provider guidance applies.
 */
function showSummaryFailure(detail, failureReason = null) {
    // A failure replaces any rendered note, so copying must disable with it.
    latestRenderedSummaryPayload = null;
    setSummaryStatus('failed');
    renderNoteStatusAxes({ phase: 'failed' });

    const summaryContent = document.getElementById('summaryContent');
    const failureMessage = createElement('p', {
        className: 'text-sm',
        text: detail,
        style: 'color:var(--text-strong); margin:0 0 0.5rem',
    });

    // An output-limit failure is not a provider outage: the model was up
    // and generating. Telling the operator to restart it wastes their time,
    // so this path gets its own copy and no page-level model warning.
    if (failureReason === 'note_output_limit') {
        const limitNote = createElement('p', {
            className: 'text-xs',
            text: 'This visit’s note is longer than the current generation limit, so retrying is unlikely to help. The transcript remains available for review.',
            style: 'color:var(--text-subtle); margin:0; line-height:1.5',
        });
        summaryContent.replaceChildren(failureMessage, limitNote);
        // A stale model-unavailable banner from an earlier failure would
        // contradict this message; the model demonstrably responded.
        hideSystemBanner();
        return;
    }

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
 * caller aborts. Fetch failures show a visible fallback banner instead of starting
 * a visit that cannot produce roles or a summary.
 */
async function ensureAiModelAvailable() {
    let isModelAvailable = false;
    let detail = 'agent unreachable';
    try {
        const response = await fetch('/agent/model-health', { headers: { Accept: 'application/json' } });
        const payload = await readJsonResponse(response, { available: false, detail: 'model health check failed' });
        isModelAvailable = payload.available === true;
        detail = payload.detail || detail;
    } catch (modelHealthError) {
        // Example: the clinician opens the page while the off-GPU note provider is restarting.
        console.warn('Model health check failed:', modelHealthError);
    }

    // The user cannot get roles or a note, so starting a visit would create unusable output.
    if (!isModelAvailable) {
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
    setSummarySourceNotice(null);
    setSummaryTruncationNotice(null);

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
 * Shows which transcript actually built the note without inventing a failure.
 * Use after HTTP or Mercure summary delivery; null clears prior-visit wording.
 *
 * @param {object|null} summaryPayload - Summary metadata; null or missing source hides the notice.
 * @returns {void} Updates persistent source wording beside the generated status.
 */
function setSummarySourceNotice(summaryPayload) {
    const summarySourceNotice = document.getElementById('summarySourceNotice');

    // Older templates have no source row, so generated notes still render normally.
    if (!summarySourceNotice) {
        return;
    }

    const actualTranscriptSource = summaryPayload?.transcript_source;
    const isCorrectedTranscriptSource = [
        'corrected_segments',
        'corrected',
    ].includes(actualTranscriptSource);
    const isLiveTranscriptSource = [
        'browser_visible_segments',
        'session_store',
        'live_segments',
        'browser',
    ].includes(actualTranscriptSource);

    // Corrected or unknown sources must not carry a stale live-fallback notice.
    if (isCorrectedTranscriptSource || !isLiveTranscriptSource) {
        summarySourceNotice.textContent = '';
        summarySourceNotice.classList.add('hidden');
        return;
    }

    const didThisBrowserObserveUnavailableCorrection = (
        correctionOutcomeForVisibleSession?.sessionId === CONFIG.sessionId
        && correctionOutcomeForVisibleSession.status === 'unavailable'
    );
    summarySourceNotice.textContent = didThisBrowserObserveUnavailableCorrection
        ? 'Built from the live transcript; post-visit correction was unavailable.'
        : 'Built from the live transcript.';
    summarySourceNotice.classList.remove('hidden');
}

/**
 * Keeps transcript-input elision visible beside a successfully generated note.
 * The notice describes input selection only; it does not imply correction failure.
 */
function setSummaryTruncationNotice(summaryPayload) {
    const summaryTruncationNotice = document.getElementById('summaryTruncationNotice');

    // Older templates and non-truncated notes need no additional provenance notice.
    if (!summaryTruncationNotice) {
        return;
    }

    if (summaryPayload?.transcript_truncated !== true) {
        summaryTruncationNotice.textContent = '';
        summaryTruncationNotice.classList.add('hidden');
        return;
    }

    summaryTruncationNotice.textContent = 'Selected opening and closing transcript rows were used for this note because the full transcript exceeded the summary input limit. Review the transcript for omitted middle content.';
    summaryTruncationNotice.classList.remove('hidden');
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
