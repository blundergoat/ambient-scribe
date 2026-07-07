// =========================================================================
// Ambient Scribe shared output actions for the consultation screen.
// Runs after replay, transcript, and summary helpers are loaded.
// Owns post-visit action visibility, safe JSON response parsing,
// summary-panel toggling, and keyboard shortcuts for the visible visit.
// Users reach this code by stopping a recording, retrying a summary, or pressing keys.
// =========================================================================

/**
 * Reveals post-visit actions once transcript text exists.
 * Use after live stop, replay stop, or replay completion.
 */
function revealPostVisitActions() {
    // Replayed transcript text can be summarized like live text.
    if (segmentIndex > 0) {
        setElementHidden('resetBtn', false);
        setSummaryPendingText('Consultation ended - generating summary now.');
    }
}

/**
 * Updates the summary panel's waiting message.
 * Use when the visit moves between recording and summary-generation states.
 */
function setSummaryPendingText(message) {
    const pendingText = document.getElementById('summaryPendingText');

    // Isolated test pages may not render the summary panel.
    if (pendingText) {
        pendingText.textContent = message;
    }
}

/**
 * Reads a backend JSON payload without exposing HTML parser errors to users.
 * Invalid or empty bodies return the fallback so the UI can show a plain error.
 * Use after replay or summary calls that may pass through Symfony and FastAPI.
 */
async function readJsonResponse(response, fallbackPayload) {
    const responseText = await response.text();

    // Empty responses give the caller a known fallback detail instead of a parse error.
    if (!responseText) {
        return { ...fallbackPayload, isFallbackPayload: true };
    }

    try {
        return JSON.parse(responseText);
    } catch (parseError) {
        console.warn('Backend returned non-JSON response:', parseError);
        return { ...fallbackPayload, isFallbackPayload: true };
    }
}

document.addEventListener('keydown', (event) => {
    // Interacting with a focused control (typing, activating a button or
    // link, opening a select) should not start or stop the consultation.
    if (event.target.closest?.('input, textarea, select, button, a, [contenteditable="true"]')) {
        return;
    }

    switch (event.key) {
        case ' ':
            event.preventDefault();

            // Space mirrors the main Start/Stop button for keyboard users.
            if (isReplayActive) {
                stopReplay();
            } else if (isRecording) {
                stopRecording();
            } else {
                startRecording();
            }
            break;
        case 'Escape':
            // Escape is a quick stop for an active recording or replay.
            if (isReplayActive) {
                stopReplay();
            } else if (isRecording) {
                stopRecording();
            }
            break;
    }
});
