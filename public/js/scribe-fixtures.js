// =========================================================================
// Ambient Scribe dev audio fixture playback.
// Runs only in APP_ENV=dev after the dev panel class is loaded.
// Owns the left-side Demo Audio picker and dev logging wrappers.
// Users reach this by clicking a generated WAV under tests/fixtures/audio/.
// =========================================================================

/**
 * Plays generated consultation WAV files through the real replay pipeline.
 * Users click one row in the Demo Audio panel, then the existing replay route
 * transcribes the file with NeMo and the browser reveals rows from audio time.
 */
class AudioFixtureRunner {
    /**
     * Stores the generated WAV choices rendered by Symfony.
     * Use once when the dev page loads.
     */
    constructor(audioFixtures) {
        this._audioFixtures = audioFixtures;
        this._activeFilename = null;
    }

    /**
     * Fetches one fixture WAV and starts replay transcription.
     * Use when a developer clicks a Demo Audio row.
     * Reports fetch/upload errors in the row and keeps the transcript recoverable.
     */
    async play(filename) {
        const audioFixture = this._audioFixtures.find((candidateFixture) => candidateFixture.filename === filename);

        // Unknown rows can happen after stale HTML; leave the current visit unchanged.
        if (!audioFixture) {
            return;
        }

        // A new fixture selection replaces the previous row's active marker.
        if (this._activeFilename) {
            this._markAudioFixture(this._activeFilename, 'idle', '');
        }

        this._activeFilename = filename;
        this._markAudioFixture(filename, 'running', 'Loading audio...');

        try {
            const response = await fetch(audioFixture.url);

            // Missing fixture responses should show a recoverable row error.
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const audioContentType = response.headers.get('Content-Type') ?? '';
            // HTML here means the fixture route failed before the replay upload can start.
            if (!audioContentType.includes('audio/wav')) {
                throw new Error('Demo audio route returned non-WAV content');
            }

            const audioBlob = await response.blob();
            const replayFile = new File([audioBlob], audioFixture.filename, { type: 'audio/wav' });
            const replayAudioUrl = URL.createObjectURL(audioBlob);
            const didStart = await startReplay(replayFile, { audioUrl: replayAudioUrl });

            // If the replay upload failed, startReplay already showed the main status error.
            if (!didStart) {
                this._markAudioFixture(filename, 'error', 'Replay failed');
                this._activeFilename = null;
                return;
            }

            this._markAudioFixture(filename, 'running', 'Replaying');
        } catch (fixtureError) {
            console.error('Demo audio replay failed:', fixtureError);
            setPlainStatus(`Demo audio error: ${fixtureError.message}`);
            this._markAudioFixture(filename, 'error', fixtureError.message);
            this._activeFilename = null;
        }
    }

    /**
     * Marks the active fixture complete after replay reaches the end.
     * Use when the shared replay timer finishes and summary generation starts.
     */
    completeActive() {
        // Manual uploads do not have a left-panel row to complete.
        if (!this._activeFilename) {
            return;
        }

        this._markAudioFixture(this._activeFilename, 'complete', 'Complete');
        this._activeFilename = null;
    }

    /**
     * Marks the active audio row as stopped after an early replay stop.
     * Use when the tester pauses the demo before requesting a summary.
     */
    stopActive() {
        // Manual uploads do not have a left-panel row to stop.
        if (!this._activeFilename) {
            return;
        }

        this._markAudioFixture(this._activeFilename, 'complete', 'Stopped');
        this._activeFilename = null;
    }

    /**
     * Updates one fixture row with replay status.
     * Use while loading, replaying, completing, or reporting a fixture error.
     */
    _markAudioFixture(filename, status, message) {
        const fixtureItems = document.querySelectorAll('[data-audio-fixture-filename]');

        // Each row is checked because filenames contain dots that are awkward in selectors.
        for (const fixtureItem of fixtureItems) {
            // Other fixture rows keep their current status.
            if (fixtureItem.dataset.audioFixtureFilename !== filename) {
                continue;
            }

            fixtureItem.classList.remove(
                'audio-fixture-item--running',
                'audio-fixture-item--complete',
                'audio-fixture-item--error',
            );

            // Idle status clears badges when a different audio file starts.
            if (status !== 'idle') {
                fixtureItem.classList.add(`audio-fixture-item--${status}`);
            }

            const statusElement = fixtureItem.querySelector('.audio-fixture-item__status');

            // Rows without a status element still show the border state.
            if (!statusElement) {
                return;
            }

            statusElement.style.display = message ? '' : 'none';
            statusElement.textContent = message;
            break;
        }
    }
}

const devPanel = new DevPanel();
const audioFixtureRunner = new AudioFixtureRunner(AUDIO_FIXTURES);

const originalHandleRawSegment = handleRawSegment;
/**
 * Wraps transcript events with dev logging before normal rendering.
 * Use so replay/live events appear in the dev panel and clinician transcript.
 */
handleRawSegment = function handleRawSegmentWithDevLogging(segmentEvent) {
    const source = isReplayActive ? 'fixture' : 'live';
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

const originalEndReplay = endReplay;
/**
 * Completes the active audio row after replay finishes.
 * Use so the Demo Audio panel mirrors the transcript lifecycle.
 */
endReplay = function endReplayWithAudioFixtureStatus() {
    originalEndReplay();
    audioFixtureRunner.completeActive();
};

const originalStopReplay = stopReplay;
/**
 * Marks the active audio row when replay stops before the WAV ends.
 * Use so Demo Audio status matches the visible paused transcript.
 */
stopReplay = function stopReplayWithAudioFixtureStatus() {
    const didStopReplay = originalStopReplay();

    // Only an actual replay stop should update the active Demo Audio row.
    if (didStopReplay) {
        audioFixtureRunner.stopActive();
    }

    return didStopReplay;
};

document.addEventListener('DOMContentLoaded', () => {
    devPanel.init();

    // Mobile dev panels need backdrop divs for tap-out dismissal.
    if (window.innerWidth < 1280) {
        // Both side panels need tap-out dismissal on smaller screens.
        for (const panelId of ['audioFixturePanel', 'devPanel']) {
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
