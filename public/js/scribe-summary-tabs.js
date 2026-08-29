// =========================================================================
// Ambient Scribe Note and Transcript tabs for post-visit review.
// Runs after scribe-output.js and owns tab selection, transcript rendering, and source-link highlights.
//
// Reviewers see corrected rows when available and an explicitly labelled live fallback otherwise.
// Source links keep cited rows highlighted until the reviewer returns to the Note tab.
// =========================================================================

// The Note tab is active until the clinician switches. Corrected rows are
// cached per session so tab flips do not refetch; a fallback render is never
// cached, so the tab picks up corrected rows once correction completes.
let activeSummaryTabName = 'note';
let correctedTranscriptRowsCache = null;
let correctedTranscriptCacheSessionId = null;
// The deep link awaits the in-flight tab render before highlighting.
let transcriptRenderPromise = null;
const MISSING_CITATION_NOTICE_MILLISECONDS = 4000;
let missingCitationNoticeTimeout = null;

/**
 * Switches the summary panel between the Note and Transcript views.
 *
 * Use from tab buttons or keyboard handling; invalid markup leaves the current view unchanged.
 * It reports render failures in the browser console without blocking the Note view.
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

    // Returning to the note ends the clinician's transcript evidence focus.
    if (isNoteActive) {
        clearCitedTranscriptHighlights();
    }

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
 *
 * Use from "Open in transcript"; cited rows stay highlighted until Note is selected, while missing rows show a non-blocking notice.
 * It reports rejected transcript rendering in the browser console before the source link continues with any available rows.
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
}

/**
 * Finds rendered Transcript tab blocks containing any of the cited row IDs.
 * Use for source links; stitched blocks retain row IDs so a citation still resolves after display merging.
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
 * Removes the citation highlight from every transcript block.
 * Use before a new source link, on Note selection, and on New Session so later review targets never retain stale highlights.
 */
function clearCitedTranscriptHighlights() {
    for (const citedBlock of document.querySelectorAll('.summary-transcript__block--cited')) {
        citedBlock.classList.remove('summary-transcript__block--cited');
    }
}

/**
 * Hides the miss notice and cancels its self-hide timer.
 * Use when a later source link succeeds or New Session resets the panel so stale guidance does not remain visible.
 */
function hideMissingCitationNotice() {
    if (missingCitationNoticeTimeout !== null) {
        window.clearTimeout(missingCitationNoticeTimeout);
        missingCitationNoticeTimeout = null;
    }

    document.getElementById('summaryTranscriptNotice')?.classList.add('hidden');
}

/**
 * Shows the temporary "cited rows not in this view" notice (deep-link miss case).
 * Use when a source link resolves no blocks; the notice self-hides without blocking transcript review.
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
 * Use when Transcript opens; corrected rows are preferred because they are the note source, with visible live rows as fallback.
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

    // The lane label always states whether this text fed the note or may
    // still change - the note's actual source stays explicit while reading.
    transcriptStatus.classList.remove('hidden');
    if (transcriptRows.length === 0) {
        transcriptStatus.textContent = 'No transcript rows yet.';
    } else if (isCorrectedAvailable) {
        transcriptStatus.textContent = typeof TRANSCRIPT_LANE_LABELS !== 'undefined'
            ? TRANSCRIPT_LANE_LABELS.corrected
            : 'Corrected transcript - used for note';
    } else {
        const liveLaneLabel = typeof transcriptLaneLabelForLiveRows === 'function'
            ? transcriptLaneLabelForLiveRows()
            : 'Live preview - may change';
        transcriptStatus.textContent =
            `${liveLaneLabel} - corrected transcript unavailable.`;
    }

    transcriptContainer.replaceChildren(
        ...createSummaryTranscriptBlocks(transcriptRows)
    );
}

/**
 * Fetches the corrected transcript rows once per session.
 *
 * Use from Transcript; a missing, empty, invalid, or unreachable artifact returns null so the view uses live rows and can retry later.
 * It reports network and JSON failures in the browser console rather than replacing the transcript with an error screen.
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
 * Use with corrected or live-fallback rows; each block retains row IDs and confidence values for source review.
 *
 * @param {Array<object>} transcriptRows - rows in storage shape; missing or empty input renders no blocks.
 * @returns {HTMLElement[]} one element per stitched utterance block, or an empty list when rows cannot be stitched.
 */
function createSummaryTranscriptBlocks(transcriptRows) {
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
 * Use after stitching so exact row boundaries and measured confidence remain available inside the combined phrase.
 *
 * @param {object} utteranceBlock - stitched display block; empty IDs use its plain text fallback.
 * @param {Map<string, object>} transcriptRowsBySegmentId - source rows; missing entries leave the block as stitched plain text.
 * @returns {HTMLElement} text container with exact row spans or unchanged stitched text.
 */
function createSummaryTranscriptWording(
    utteranceBlock,
    transcriptRowsBySegmentId,
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
        }

        transcriptWording.appendChild(sourceRowWording);
    }

    return transcriptWording;
}

/**
 * Resets tab state when New Session clears the transcript.
 * Use during New Session so corrected rows and source highlights from the prior visit cannot appear in the next one.
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
