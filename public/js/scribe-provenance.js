// =========================================================================
// Ambient Scribe per-section provenance popovers (summary UX M5).
// Runs after scribe-summary-tabs.js. Each cited SOAP section gets one
// superscript affordance that opens a popover of the cited utterances in
// stitched form - the same block shape as the Transcript tab - plus an
// "Open in transcript" action. Sections without citations get no affordance.
// =========================================================================

// At most one popover is open; document-level handlers close it on Escape
// or an outside click and return focus to its toggle.
let openProvenanceElement = null;

/**
 * Builds the superscript provenance affordance for one summary section.
 * Use from the section renderer; the returned span sits inline after the
 * section prose while its popover overlays the full section width.
 * Contract: returns null (no affordance, no empty popover) unless at least
 * one citation has both an ID and text and the stitching scripts are loaded;
 * otherwise returns a span whose toggle and popover manage their own
 * aria-expanded/hidden state. Never throws.
 *
 * @param {Array<object>} citations - validated storage-truth rows; empty means no affordance.
 * @param {string} sectionHeading - heading used in the popover's accessible name.
 * @returns {HTMLElement|null} inline provenance element, or null when there is nothing to cite.
 */
function createSectionProvenanceBlock(citations, sectionHeading) {
    // Test pages without the stitching or tabs scripts cannot show stitched utterances.
    if (typeof stitchTranscriptSegments !== 'function' || typeof createSummaryTranscriptBlocks !== 'function') {
        return null;
    }

    // Only rows a clinician can trace (ID and text) count as provenance.
    const citedRows = (citations ?? []).filter((citation) =>
        citation
        && String(citation.segment_id ?? '').trim() !== ''
        && String(citation.text ?? '').trim() !== ''
    );

    // No provenance means no affordance - empty popovers never render.
    if (citedRows.length === 0) {
        return null;
    }

    const spokenOrderRows = sortRowsBySpokenOrder(citedRows);
    const utteranceBlocks = createSummaryTranscriptBlocks(spokenOrderRows);
    const citedSegmentIds = spokenOrderRows.map((citedRow) => String(citedRow.segment_id));
    const popover = buildProvenancePopover(utteranceBlocks, sectionHeading, citedSegmentIds);
    const toggleButton = buildProvenanceToggle(utteranceBlocks.length);
    const provenanceElement = createElement('span', {
        className: 'summary-provenance',
    }, [toggleButton, popover]);

    toggleButton.addEventListener('click', () => {
        // Reopening the same affordance toggles it shut instead of flickering.
        if (openProvenanceElement === provenanceElement) {
            closeProvenancePopover();
            return;
        }
        openProvenancePopover(provenanceElement);
    });

    return provenanceElement;
}

/**
 * Orders citation rows by spoken time for honest stitching.
 * Use before building popover blocks; citations arrive in model order, and
 * rows without a start time sort last because their gap is unknowable.
 * Contract: pure - returns a new array, never mutates the caller's rows,
 * and keeps the original order for rows whose start times tie.
 */
function sortRowsBySpokenOrder(citationRows) {
    return [...citationRows].sort((firstRow, secondRow) => {
        const firstStart = Number.isFinite(firstRow.start) ? firstRow.start : Number.POSITIVE_INFINITY;
        const secondStart = Number.isFinite(secondRow.start) ? secondRow.start : Number.POSITIVE_INFINITY;
        return firstStart - secondStart;
    });
}

/**
 * Builds the hidden popover dialog for one section's cited utterances.
 * Use once per cited section. "Open in transcript" deep-links: the tab
 * switches, the first cited block scrolls into view, and cited blocks
 * carry a temporary highlight; a missing citation shows a non-blocking
 * notice instead (M6). A rejected deep link is caught and reported as a
 * console warning, and pages without the tabs script fall back to a plain
 * tab switch - the action never throws.
 */
function buildProvenancePopover(utteranceBlocks, sectionHeading, citedSegmentIds) {
    const popover = createElement('div', {
        className: 'summary-provenance__popover hidden',
        attributes: {
            role: 'dialog',
            'aria-label': `Sources for ${sectionHeading}`,
            tabindex: '-1',
        },
    }, utteranceBlocks);

    const openInTranscriptButton = createElement('button', {
        className: 'summary-provenance__open',
        attributes: { type: 'button' },
        text: 'Open in transcript',
    });
    openInTranscriptButton.addEventListener('click', () => {
        closeProvenancePopover();

        // Test pages without the tabs script keep the popover-only behaviour.
        if (typeof openTranscriptDeepLink !== 'function') {
            selectSummaryTab('transcript');
            return;
        }

        openTranscriptDeepLink(citedSegmentIds).catch((deepLinkError) => {
            console.warn('Transcript deep link failed:', deepLinkError);
        });
    });
    popover.appendChild(openInTranscriptButton);

    return popover;
}

/**
 * Builds the superscript toggle button with its screen-reader label.
 * Use once per cited section; the visible text is the utterance count.
 */
function buildProvenanceToggle(utteranceCount) {
    const utteranceNoun = utteranceCount === 1 ? 'utterance' : 'utterances';

    return createElement('button', {
        className: 'summary-provenance__toggle',
        attributes: {
            type: 'button',
            'aria-haspopup': 'dialog',
            'aria-expanded': 'false',
            'aria-label': `View source, ${utteranceCount} ${utteranceNoun}`,
        },
        text: String(utteranceCount),
    });
}

/**
 * Opens one section's provenance popover and closes any other.
 * Use from the toggle click; focus moves into the dialog so Escape and
 * screen readers behave like a standard popup.
 */
function openProvenancePopover(provenanceElement) {
    closeProvenancePopover();

    const popover = provenanceElement.querySelector('.summary-provenance__popover');
    const toggleButton = provenanceElement.querySelector('.summary-provenance__toggle');
    popover.classList.remove('hidden');
    toggleButton.setAttribute('aria-expanded', 'true');
    openProvenanceElement = provenanceElement;
    popover.focus();
}

/**
 * Closes the open provenance popover, if any.
 * Use on Escape, outside clicks, tab switches, and before opening another.
 * Focus returns to the owning toggle only when it was inside the popover,
 * so outside clicks do not yank the clinician's focus around.
 */
function closeProvenancePopover() {
    // Nothing is open, so there is nothing to close.
    if (!openProvenanceElement) {
        return;
    }

    const popover = openProvenanceElement.querySelector('.summary-provenance__popover');
    const toggleButton = openProvenanceElement.querySelector('.summary-provenance__toggle');
    const hadFocusInside = popover.contains(document.activeElement);
    popover.classList.add('hidden');
    toggleButton.setAttribute('aria-expanded', 'false');
    openProvenanceElement = null;

    // Escape and action clicks keep keyboard users anchored on the toggle.
    if (hadFocusInside) {
        toggleButton.focus();
    }
}

// Escape closes the open popover no matter where focus sits.
document.addEventListener('keydown', (keyEvent) => {
    if (keyEvent.key === 'Escape' && openProvenanceElement) {
        keyEvent.stopPropagation();
        closeProvenancePopover();
    }
});

// A click outside the open affordance dismisses the popover.
document.addEventListener('click', (clickEvent) => {
    if (openProvenanceElement && !openProvenanceElement.contains(clickEvent.target)) {
        closeProvenancePopover();
    }
});
