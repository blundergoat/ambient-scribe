// =========================================================================
// Ambient Scribe summary panel tabs (summary UX M4).
// Runs after scribe-output.js; owns the Note/Transcript tab switch and the
// Transcript tab body. Layout only - no provenance interaction yet.
// Transcript rows come from the corrected artifact via the same-origin
// proxy, with the visible live rows as fallback so the tab still shows the
// consultation when correction is unavailable.
// =========================================================================

// The Note tab is active until the clinician switches. Corrected rows are
// cached per session so tab flips do not refetch; a fallback render is never
// cached, so the tab picks up corrected rows once correction completes.
let activeSummaryTabName = 'note';
let correctedTranscriptRowsCache = null;
let correctedTranscriptCacheSessionId = null;
// The deep link (M6) awaits the in-flight tab render before highlighting.
let transcriptRenderPromise = null;
// How long cited blocks stay highlighted before the fade begins.
const CITED_HIGHLIGHT_MILLISECONDS = 3000;
const MISSING_CITATION_NOTICE_MILLISECONDS = 4000;
let citedHighlightTimeout = null;
let missingCitationNoticeTimeout = null;

/**
 * Switches the summary panel between the Note and Transcript views.
 * Use from the tab buttons or arrow-key handling; selection follows focus.
 * Never throws: unknown tab names and test pages without the tabbed markup
 * return without changing the view, and a rejected transcript render is
 * caught and logged as a console warning while the tab stays switched with
 * its previous body - the Note view is never blocked.
 *
 * @param {string} tabName - 'note' or 'transcript'; unknown names keep the current view.
 */
function selectSummaryTab(tabName) {
    // Unknown names would blank both views, so the current tab stays.
    if (tabName !== 'note' && tabName !== 'transcript') {
        return;
    }

    const noteTab = document.getElementById('summaryTabNote');
    const transcriptTab = document.getElementById('summaryTabTranscript');
    const noteView = document.getElementById('summaryNoteView');
    const transcriptView = document.getElementById('summaryTranscriptView');

    // Test pages without the tabbed panel keep the legacy single view.
    if (!noteTab || !transcriptTab || !noteView || !transcriptView) {
        return;
    }

    activeSummaryTabName = tabName;
    const isNoteActive = tabName === 'note';

    noteTab.setAttribute('aria-selected', String(isNoteActive));
    transcriptTab.setAttribute('aria-selected', String(!isNoteActive));
    // Roving tabindex: only the active tab sits in the page tab order.
    noteTab.tabIndex = isNoteActive ? 0 : -1;
    transcriptTab.tabIndex = isNoteActive ? -1 : 0;
    noteView.classList.toggle('hidden', !isNoteActive);
    transcriptView.classList.toggle('hidden', isNoteActive);

    // The transcript body renders lazily on first open and after New Session.
    if (!isNoteActive) {
        transcriptRenderPromise = renderSummaryTranscriptView();
        transcriptRenderPromise.catch((transcriptError) => {
            // Example: the clinician opens Transcript as its corrected-row response becomes invalid.
            console.warn('Transcript tab render failed:', transcriptError);
        });
    }
}

/**
 * Switches to the Transcript tab and highlights the cited utterance blocks.
 * Use from a provenance popover's "Open in transcript" action (M6): the
 * first cited block scrolls into view and every cited block holds a
 * temporary highlight that fades after CITED_HIGHLIGHT_MILLISECONDS.
 * When no cited ID is present in the rendered transcript, a small
 * non-blocking notice appears instead - the tab still opens and never throws.
 */
async function openTranscriptDeepLink(citedSegmentIds) {
    selectSummaryTab('transcript');

    // The tab render is asynchronous; highlighting needs the blocks in the DOM.
    try {
        await transcriptRenderPromise;
    } catch (renderError) {
        // Example: the clinician opens a source link while corrected-transcript rendering fails.
        console.warn('Transcript render unavailable for deep link:', renderError);
    }

    const citedIds = new Set((citedSegmentIds ?? []).filter(Boolean));
    const citedBlocks = transcriptBlocksForSegmentIds(citedIds);

    // Every cited row missing from this transcript view is the non-blocking miss case.
    if (citedBlocks.length === 0) {
        showMissingCitationNotice();
        return;
    }

    // A successful jump supersedes any lingering miss notice from an earlier one.
    hideMissingCitationNotice();
    clearCitedTranscriptHighlights();
    citedBlocks[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
    for (const citedBlock of citedBlocks) {
        citedBlock.classList.add('summary-transcript__block--cited');
    }
    citedHighlightTimeout = window.setTimeout(
        clearCitedTranscriptHighlights,
        CITED_HIGHLIGHT_MILLISECONDS
    );
}

/**
 * Finds rendered Transcript tab blocks containing any of the cited row IDs.
 * Use for the deep link; stitched blocks carry constituent IDs in
 * data-segment-ids, so a citation resolves even after its row was merged.
 */
function transcriptBlocksForSegmentIds(citedIds) {
    const transcriptContainer = document.getElementById('summaryTranscript');

    // Test pages without the transcript body have nothing to highlight.
    if (!transcriptContainer || citedIds.size === 0) {
        return [];
    }

    return Array.from(
        transcriptContainer.querySelectorAll('.summary-transcript__block')
    ).filter((utteranceBlock) =>
        (utteranceBlock.dataset.segmentIds ?? '')
            .split(' ')
            .some((segmentId) => citedIds.has(segmentId))
    );
}

/**
 * Removes the temporary citation highlight from every transcript block.
 * Use before a new deep link and when the fade timer fires, so repeated
 * jumps never stack stale highlights.
 */
function clearCitedTranscriptHighlights() {
    if (citedHighlightTimeout !== null) {
        window.clearTimeout(citedHighlightTimeout);
        citedHighlightTimeout = null;
    }

    for (const citedBlock of document.querySelectorAll('.summary-transcript__block--cited')) {
        citedBlock.classList.remove('summary-transcript__block--cited');
    }
}

/**
 * Hides the miss notice and cancels its self-hide timer.
 * Use when a later jump succeeds or New Session resets the panel, so a
 * stale "rows not in this view" message never outlives its moment.
 */
function hideMissingCitationNotice() {
    if (missingCitationNoticeTimeout !== null) {
        window.clearTimeout(missingCitationNoticeTimeout);
        missingCitationNoticeTimeout = null;
    }

    document.getElementById('summaryTranscriptNotice')?.classList.add('hidden');
}

/**
 * Shows the temporary "cited rows not in this view" notice (M6 miss case).
 * Use when a deep link resolves zero blocks; the notice self-hides so it
 * never blocks reading the transcript.
 */
function showMissingCitationNotice() {
    const missNotice = document.getElementById('summaryTranscriptNotice');

    // Test pages without the notice element still complete the tab switch.
    if (!missNotice) {
        return;
    }

    missNotice.textContent = 'The cited rows are not in this transcript view.';
    missNotice.classList.remove('hidden');

    if (missingCitationNoticeTimeout !== null) {
        window.clearTimeout(missingCitationNoticeTimeout);
    }
    missingCitationNoticeTimeout = window.setTimeout(() => {
        missNotice.classList.add('hidden');
        missingCitationNoticeTimeout = null;
    }, MISSING_CITATION_NOTICE_MILLISECONDS);
}

/**
 * Moves tab focus and selection with the keyboard (ARIA tabs pattern).
 * Use on the tablist so Left/Right/Home/End work; selection follows focus.
 */
function handleSummaryTabKeydown(keyEvent) {
    const tabOrder = ['note', 'transcript'];
    const activeIndex = tabOrder.indexOf(activeSummaryTabName);
    let nextIndex = null;

    if (keyEvent.key === 'ArrowRight') {
        nextIndex = (activeIndex + 1) % tabOrder.length;
    } else if (keyEvent.key === 'ArrowLeft') {
        nextIndex = (activeIndex - 1 + tabOrder.length) % tabOrder.length;
    } else if (keyEvent.key === 'Home') {
        nextIndex = 0;
    } else if (keyEvent.key === 'End') {
        nextIndex = tabOrder.length - 1;
    }

    // Other keys keep their default behaviour inside the tablist.
    if (nextIndex === null) {
        return;
    }

    keyEvent.preventDefault();
    selectSummaryTab(tabOrder[nextIndex]);
    const focusedTab = document.getElementById(
        tabOrder[nextIndex] === 'note' ? 'summaryTabNote' : 'summaryTabTranscript'
    );
    focusedTab?.focus();
}

/**
 * Renders the Transcript tab body from corrected rows or the live fallback.
 * Use when the tab opens; corrected rows are what the note was built from,
 * so they are preferred whenever the correction artifact exists.
 */
async function renderSummaryTranscriptView() {
    const transcriptContainer = document.getElementById('summaryTranscript');
    const transcriptStatus = document.getElementById('summaryTranscriptStatus');

    // Test pages without the transcript body cannot render rows.
    if (!transcriptContainer || !transcriptStatus) {
        return;
    }

    const correctedRows = await fetchCorrectedTranscriptRows();
    const isCorrectedAvailable = Array.isArray(correctedRows) && correctedRows.length > 0;
    const transcriptRows = isCorrectedAvailable ? correctedRows : readVisibleTranscriptSegments();

    transcriptStatus.classList.toggle('hidden', isCorrectedAvailable && transcriptRows.length > 0);
    if (!isCorrectedAvailable) {
        transcriptStatus.textContent = transcriptRows.length > 0
            ? 'Corrected transcript unavailable - showing the live transcript.'
            : 'No transcript rows yet.';
    }

    const visibleTranscriptLane = isCorrectedAvailable
        ? CORRECTED_TRANSCRIPT_LANE
        : LIVE_TRANSCRIPT_LANE;
    transcriptContainer.replaceChildren(
        ...createSummaryTranscriptBlocks(transcriptRows, visibleTranscriptLane)
    );
}

/**
 * Fetches the corrected transcript rows once per session.
 * Use from the Transcript tab; a missing artifact returns null without
 * caching so a later tab flip picks up rows once correction has run.
 * Network or JSON failures are logged and also return null, which sends
 * the caller to the live-row fallback instead of an error state.
 */
async function fetchCorrectedTranscriptRows() {
    // A cached artifact for the visible session avoids repeat fetches.
    if (correctedTranscriptRowsCache && correctedTranscriptCacheSessionId === CONFIG.sessionId) {
        return correctedTranscriptRowsCache;
    }

    try {
        const response = await fetch(`/session/${CONFIG.sessionId}/corrected-transcript`);

        // Unavailable artifacts fall back to live rows without caching the miss.
        if (!response.ok) {
            return null;
        }

        const transcriptPayload = await response.json();
        const correctedRows = Array.isArray(transcriptPayload.segments) ? transcriptPayload.segments : [];

        // Only a real artifact is cached; empty means correction has not run yet.
        if (correctedRows.length === 0) {
            return null;
        }

        correctedTranscriptRowsCache = correctedRows;
        correctedTranscriptCacheSessionId = CONFIG.sessionId;
        return correctedRows;
    } catch (fetchError) {
        // Example: the clinician opens Transcript while the corrected artifact request disconnects.
        console.warn('Corrected transcript fetch failed:', fetchError);
        return null;
    }
}

/**
 * Builds stitched utterance blocks for the Transcript tab.
 * Use with corrected rows or live fallback rows; blocks keep constituent
 * segment IDs in a data attribute so later milestones can deep-link to them.
 * Individual row spans preserve confidence styling inside a stitched block.
 *
 * @param {Array<object>} transcriptRows - transcript rows in storage shape; empty renders no blocks.
 * @param {string} transcriptLane - corrected or live view; empty/unknown keeps rows unstyled.
 * @returns {HTMLElement[]} one element per stitched utterance block.
 */
function createSummaryTranscriptBlocks(
    transcriptRows,
    transcriptLane = CORRECTED_TRANSCRIPT_LANE,
) {
    // Test pages without the stitching script show nothing rather than throwing.
    if (typeof stitchTranscriptSegments !== 'function') {
        return [];
    }

    const transcriptRowsBySegmentId = new Map();
    // Stable row IDs reconnect each stitched phrase to its local confidence value.
    for (const transcriptRow of transcriptRows ?? []) {
        const segmentId = String(transcriptRow.segment_id ?? '').trim();

        // Rows without identity still render through the stitched-text fallback.
        if (segmentId !== '') {
            transcriptRowsBySegmentId.set(segmentId, transcriptRow);
        }
    }

    // Each stitched utterance keeps the existing block layout and gains row-local text spans.
    return stitchTranscriptSegments(transcriptRows).map((utteranceBlock) => {
        const transcriptWording = createSummaryTranscriptWording(
            utteranceBlock,
            transcriptRowsBySegmentId,
            transcriptLane,
        );

        return createElement('div', {
            className: 'summary-transcript__block',
            dataset: { segmentIds: utteranceBlock.segmentIds.join(' ') },
        }, [
            createElement('span', {
                className: 'summary-transcript__time',
                text: Number.isFinite(utteranceBlock.start) ? formatTime(utteranceBlock.start) : '',
            }),
            createElement('span', {
                className: 'summary-transcript__role',
                text: utteranceBlock.role,
            }),
            transcriptWording,
        ]);
    });
}

/**
 * Builds row-local wording inside one corrected or live-fallback transcript block.
 * Use after stitching so only the uncertain row is marked, not the whole utterance.
 *
 * @param {object} utteranceBlock - stitched display block; empty IDs use its plain text fallback.
 * @param {Map<string, object>} transcriptRowsBySegmentId - source rows; empty leaves the block plain.
 * @param {string} transcriptLane - corrected or live view; unknown leaves every row plain.
 * @returns {HTMLElement} text container with exact row spans or unchanged stitched text.
 */
function createSummaryTranscriptWording(
    utteranceBlock,
    transcriptRowsBySegmentId,
    transcriptLane,
) {
    const transcriptWording = createElement('span', {
        className: 'summary-transcript__text',
    });
    // Every stitched ID must resolve before the UI can safely split the displayed wording.
    const sourceRows = utteranceBlock.segmentIds.map(
        (segmentId) => transcriptRowsBySegmentId.get(segmentId)
    );

    // Missing row identity keeps the established stitched text instead of risking word loss.
    if (sourceRows.length === 0 || sourceRows.some((sourceRow) => !sourceRow)) {
        transcriptWording.textContent = utteranceBlock.text;
        return transcriptWording;
    }

    // One inline span per source row keeps the uncertain wording locally scoped.
    for (const sourceRow of sourceRows) {
        // Real spaces preserve copied and assistive text between rows, not only visual separation.
        if (transcriptWording.childNodes.length > 0) {
            transcriptWording.appendChild(document.createTextNode(' '));
        }

        const sourceRowWording = createElement('span', {
            className: 'summary-transcript__row',
            text: String(sourceRow.text ?? '').trim(),
        });

        // Measured corrected rows keep their value for inspection and browser tests.
        if (Number.isFinite(sourceRow.confidence)) {
            sourceRowWording.dataset.confidence = sourceRow.confidence;

            // The shared classifier applies the lane-specific threshold and accessibility cue.
            if (typeof markTranscriptWordingForReview === 'function') {
                markTranscriptWordingForReview(
                    sourceRowWording,
                    sourceRow.confidence,
                    transcriptLane,
                );
            }
        }

        transcriptWording.appendChild(sourceRowWording);
    }

    return transcriptWording;
}

/**
 * Resets tab state when New Session clears the transcript.
 * Use from resetPostVisitCorrectionState so a prior visit's corrected rows
 * can never appear inside the next visit's Transcript tab.
 */
function resetSummaryTabsState() {
    correctedTranscriptRowsCache = null;
    correctedTranscriptCacheSessionId = null;
    transcriptRenderPromise = null;
    clearCitedTranscriptHighlights();
    hideMissingCitationNotice();
    selectSummaryTab('note');

    const transcriptContainer = document.getElementById('summaryTranscript');
    const transcriptStatus = document.getElementById('summaryTranscriptStatus');
    transcriptContainer?.replaceChildren();
    transcriptStatus?.classList.add('hidden');
}

// The script tag sits after the markup, so the tablist exists at load time.
document.getElementById('summaryTabs')?.addEventListener('keydown', handleSummaryTabKeydown);
