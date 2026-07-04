// =========================================================================
// Ambient Scribe replay, summary, and keyboard output flow.
// Runs after transcript rendering helpers are loaded.
// Owns WAV replay progress, generated summary display, and keyboard shortcuts.
// Users reach this code by choosing Demo, stopping a visit, or pressing keys.
// =========================================================================

/**
 * Uploads a WAV file and replays it through the transcript UI.
 * Reports upload/backend errors in status and recovers the Demo button.
 */
async function startReplay(file) {
    // No file selected means the user cancelled the picker.
    if (!file) {
        return;
    }

    document.getElementById('demoFileInput').value = '';

    // Stop live capture before replaying a prerecorded consultation.
    if (isRecording) {
        stopRecording();
    }

    resetSession();
    await new Promise((resolveReplayReset) => setTimeout(resolveReplayReset, 100));
    isReplayActive = true;
    setDemoButtonBusy(true, 'Processing...');
    setElementHidden('startBtn', true);
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

        // Failed uploads should leave the clinician in a recoverable demo state.
        if (!response.ok) {
            const errorPayload = await response.json().catch(() => ({ detail: 'Upload failed' }));
            throw new Error(errorPayload.detail || `HTTP ${response.status}`);
        }

        const replayPayload = await response.json();
        replayDuration = replayPayload.duration_seconds ?? 0;
        startReplayProgress();
        setDemoButtonBusy(false, 'Demo');
    } catch (replayError) {
        console.error('Replay failed:', replayError);
        setPlainStatus(`Demo error: ${replayError.message}`);
        setDemoButtonBusy(false, 'Demo');
        isReplayActive = false;
        disconnectMercureStreams();
    }
}

/**
 * Toggles the replay button while a WAV upload is processing.
 * Use so the user cannot start two demo replays at once.
 */
function setDemoButtonBusy(isBusy, label) {
    const demoButton = document.getElementById('demoBtn');
    demoButton.disabled = isBusy;
    demoButton.textContent = label;
}

/**
 * Shows replay progress and starts its timer.
 * Use once the backend accepts the uploaded WAV file.
 */
function startReplayProgress() {
    setElementHidden('replayProgress', false);
    setElementHidden('timer', false);
    setRecordingStatus('Replaying demo', 'color:var(--color-speaker-a);font-weight:500;');
    replayStartTime = Date.now();
    replayTimerInterval = setInterval(updateReplayProgress, 500);
}

/**
 * Updates the demo replay progress bar.
 * Use while a prerecorded consultation is publishing transcript events.
 */
function updateReplayProgress() {
    // Without active replay timing, there is no progress for the user to see.
    if (!isReplayActive || !replayStartTime || !replayDuration) {
        return;
    }

    const elapsedSeconds = (Date.now() - replayStartTime) / 1000;
    const progress = Math.min(elapsedSeconds / replayDuration, 1);
    document.getElementById('replayProgressFill').style.width = `${progress * 100}%`;
    document.getElementById('timer').textContent = formatTime(elapsedSeconds);

    // Reaching 100% should unlock the same post-visit actions as live stop.
    if (progress >= 1) {
        endReplay();
    }
}

/**
 * Finishes demo replay and reveals post-visit actions.
 * Use when the replay timer completes or the backend sends finalized.
 */
function endReplay() {
    isReplayActive = false;
    clearInterval(replayTimerInterval);
    replayTimerInterval = null;
    replayStartTime = null;
    setElementHidden('replayProgress', true);
    setPlainStatus('Demo complete');

    // Replayed transcript text can be downloaded and summarized like live text.
    if (segmentIndex > 0) {
        setElementHidden('downloadBtn', false);
        setElementHidden('resetBtn', false);
        requestSummary();
    }
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

    const summaryPanel = document.getElementById('summaryPanel');
    const summaryLoading = document.getElementById('summaryLoading');
    const summaryContent = document.getElementById('summaryContent');
    summaryPanel.classList.remove('hidden');
    summaryPanel.classList.add('summary-panel--open');
    summaryLoading.classList.remove('hidden');
    clearElement(summaryContent);

    try {
        const response = await fetch(`/session/${CONFIG.sessionId}/summary`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });

        // Successful responses render sectioned summary content for review.
        if (response.ok) {
            const summaryPayload = await response.json();
            renderSummary(summaryPayload);
            handleClinicalHintsEvent({
                type: 'clinical_hints',
                hints: summaryPayload.clinical_hints ?? [],
            });
        } else {
            showSummaryMessage('Summary generation failed. Try downloading the transcript instead.');
        }
    } catch (summaryError) {
        console.error('Summary request failed:', summaryError);
        showSummaryMessage('Could not reach the summary service.');
    } finally {
        summaryLoading.classList.add('hidden');
    }
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
 * Use when the clinician reviews the post-visit note before download.
 */
function renderSummary(summaryPayload) {
    const summaryContent = document.getElementById('summaryContent');

    // Missing summary content means there is no panel body to render into.
    if (!summaryContent) {
        return;
    }

    const summaryPanel = document.getElementById('summaryPanel');
    summaryPanel.classList.remove('hidden');
    summaryPanel.classList.add('summary-panel--open');
    clearElement(summaryContent);

    // A title from the backend replaces the default Session Summary label.
    if (summaryPayload.title) {
        document.getElementById('summaryTitle').textContent = summaryPayload.title;
    }

    const renderedBlocks = [
        ...createSummarySectionBlocks(summaryPayload.sections ?? []),
        ...createSummaryKeyPointBlocks(summaryPayload.key_points ?? []),
    ];

    // Empty summary payloads should explain that no content is available.
    if (renderedBlocks.length === 0) {
        showSummaryMessage('No summary content available.');
        return;
    }

    summaryContent.replaceChildren(...renderedBlocks);
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
 * Opens or collapses the summary panel body.
 * Use when the clinician clicks the Session Summary header.
 */
function toggleSummary() {
    const summaryPanel = document.getElementById('summaryPanel');
    const summaryToggle = summaryPanel.querySelector('.summary-panel__toggle');
    const isOpen = summaryPanel.classList.toggle('summary-panel--open');
    summaryToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
}

document.addEventListener('keydown', (event) => {
    // Typing in a form field should not start or stop the consultation.
    if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA') {
        return;
    }

    switch (event.key) {
        case ' ':
            event.preventDefault();

            // Space mirrors the main Start/Stop button for keyboard users.
            if (isRecording) {
                stopRecording();
            } else {
                startRecording();
            }
            break;
        case 'Escape':
            // Escape is a quick stop for an active recording.
            if (isRecording) {
                stopRecording();
            }
            break;
        case 'd':
            // Ctrl/Cmd+D belongs to the browser bookmark shortcut.
            if (event.ctrlKey || event.metaKey) {
                return;
            }

            document.getElementById('downloadBtn')?.click();
            break;
    }
});
