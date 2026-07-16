// =========================================================================
// Ambient Scribe transcript and visit-output browser flow.
// Runs after scribe.js once the clinician-facing page state exists.
// Owns transcript cards, manual role relabeling, accessible announcements,
// demo replay, generated summaries, and keyboard-visible transcript state.
// The file keeps model text in textContent so transcript output cannot run HTML.
// =========================================================================

let isReplayActive = false;
let replayDuration = 0;
let replayTimerInterval = null;
let replayAudioObjectUrl = null;
let hasReplayAudioPlaybackStarted = false;
let wavStreamer = null;
let isReplayDraining = false;
let replayDrainReason = null;
let replayDrainTimeout = null;
// Live-recording stop mirrors the replay drain: the stream stays open until
// the backend `finalized` event delivers the held-back tail (or a timeout).
let isLiveDraining = false;
let liveDrainTimeout = null;
const MIXED_ROW_ROLE_LABEL = 'Review labels';
// Terminal source attestation from the backend `finalized` event. Until it
// arrives, the visit's transcript is not complete and no note may be
// requested - a Stop-wait timeout only releases the UI, never the note.
let terminalAttestation = null;

/**
 * Builds summary rows from the transcript rows visible in the browser.
 * Use when stop or summary requests need exactly what the clinician can see.
 * One record per row span (not per coalesced card) keeps server history
 * row-preserving, so per-row corrections survive the summary round-trip.
 */
function readVisibleTranscriptSegments() {
    const visibleSegments = [];

    // Each transcript card can contain several same-speaker row spans.
    for (const segmentElement of document.querySelectorAll('.segment')) {
        const cardSpeakerId = segmentElement.dataset.speakerId;

        // Every row reports its own identity and row-resolved role:
        // clinician correction > automatic row exception > speaker mapping.
        for (const rowSpan of segmentElement.querySelectorAll('.segment__text')) {
            const segmentId = rowSpan.dataset.segmentId ?? '';
            const summaryRow = {
                segment_id: segmentId,
                speaker_id: cardSpeakerId,
                role: resolvedRoleForTranscriptRow(rowSpan, cardSpeakerId),
                text: rowTextFromSpan(rowSpan),
                start: Number.parseFloat(rowSpan.dataset.start) || 0,
                end: Number.parseFloat(rowSpan.dataset.end) || 0,
            };

            // Measured rows echo their heard-confidence back so a server
            // restore keeps it; unmeasured rows stay absent-is-absent.
            const rowConfidence = Number.parseFloat(rowSpan.dataset.confidence);
            if (Number.isFinite(rowConfidence)) {
                summaryRow.confidence = rowConfidence;
            }

            visibleSegments.push(summaryRow);
        }
    }

    return visibleSegments;
}

/**
 * Returns one row's spoken text without its correction chip.
 * The chip ("Dr ✓") is a UI marker and must not leak into summary text.
 */
function rowTextFromSpan(rowSpan) {
    // The row's text node is always first; the optional chip follows it.
    return (rowSpan.childNodes[0]?.textContent ?? '').trim();
}

/**
 * Returns the role one visible transcript row should use.
 * Use when summaries, card headers, or review states need the same row-level
 * truth the clinician sees on screen.
 *
 * @param {HTMLElement} rowSpan - visible transcript row; absent segment ID means no row override exists.
 * @param {string} cardSpeakerId - card speaker ID; empty means the row falls back to Unknown.
 * @returns {string} row-resolved role; `UNKNOWN` means the UI should not claim Doctor or Patient.
 */
function resolvedRoleForTranscriptRow(rowSpan, cardSpeakerId) {
    const segmentId = rowSpan.dataset.segmentId ?? '';

    // Automatic row cues outrank the card's speaker mapping for that line.
    if (autoRowRoles.has(segmentId)) {
        return autoRowRoles.get(segmentId);
    }

    return roleMapping[cardSpeakerId] ?? 'UNKNOWN';
}

/**
 * Chooses the safe card header state from the roles of rows inside the card.
 * Use when a coalesced speaker card may contain row-level corrections, so the
 * header does not confidently say Patient while one row is Doctor.
 *
 * @param {HTMLElement} segmentBlock - transcript card; empty cards fall back to speaker mapping.
 * @returns {object} header text/avatar/style; mixed means the clinician should review row labels.
 */
function visibleCardRoleState(segmentBlock) {
    const speakerId = segmentBlock.dataset.speakerId ?? '';
    const speakerRole = roleMapping[speakerId] ?? 'UNKNOWN';
    const cardRows = segmentBlock.querySelectorAll('.segment__text');

    // Empty cards are defensive; show the speaker mapping instead of inventing a mixed state.
    if (cardRows.length === 0) {
        return displayStateForSingleRole(speakerRole, speakerId, true);
    }

    const rowRoles = new Set();
    // Every row contributes its visible role so mixed cards become reviewable.
    for (const rowSpan of cardRows) {
        rowRoles.add(resolvedRoleForTranscriptRow(rowSpan, speakerId));
    }

    // Mixed row roles mean the card header must not claim a single speaker role.
    if (rowRoles.size > 1) {
        return {
            roleClass: 'UNKNOWN',
            label: MIXED_ROW_ROLE_LABEL,
            isMixed: true,
            showManualConfirmation: false,
        };
    }

    const visibleRole = rowRoles.values().next().value ?? 'UNKNOWN';
    return displayStateForSingleRole(visibleRole, speakerId, visibleRole === speakerRole);
}

/**
 * Builds a card header state for a card whose rows agree on one role.
 * Use after row-level correction makes all rows agree, even if speaker mapping
 * still carries a stale role.
 *
 * @param {string} visibleRole - resolved row role; empty means the card should appear Unknown.
 * @param {string} speakerId - underlying speaker ID; empty gives the user an Unknown-style header.
 * @param {boolean} followsSpeakerMapping - true when speaker-level manual checks can be shown.
 * @returns {object} card label/avatar/style for one visible role.
 */
function displayStateForSingleRole(visibleRole, speakerId, followsSpeakerMapping) {
    const roleForDisplay = visibleRole === '' ? 'UNKNOWN' : (visibleRole ?? 'UNKNOWN');
    return {
        roleClass: roleForDisplay,
        label: roleForDisplay === 'UNKNOWN' ? speakerId : getRoleLabel(roleForDisplay),
        isMixed: false,
        showManualConfirmation: followsSpeakerMapping && manualOverrides.has(speakerId),
    };
}

/**
 * Refreshes a transcript card header from its current row-level roles.
 * Use after row corrections, automatic row exceptions, speaker relabels, or
 * late row insertion so the card header matches what the clinician can verify.
 *
 * @param {HTMLElement|null} segmentBlock - transcript card; null means the row is not on screen yet.
 * @returns {void} Updates only visible card header/avatar classes.
 */
function refreshSpeakerCardDisplay(segmentBlock) {
    // Rows may receive role state before their card is attached to the DOM.
    if (!segmentBlock) {
        return;
    }

    const displayState = visibleCardRoleState(segmentBlock);
    segmentBlock.className = `segment segment--${displayState.roleClass}`;

    // Mixed cards get an extra hook for tests and future styling.
    if (displayState.isMixed) {
        segmentBlock.classList.add('segment--mixed');
    }

    // Rebuilding the class list above dropped any gap/overlap marker, and a
    // role update only moves a card sideways - its spoken time is unchanged.
    applyFlowClassesFromDataset(segmentBlock);

    const labelElement = segmentBlock.querySelector('.segment__speaker');
    // Partial test DOMs may not render card labels.
    if (labelElement) {
        setSpeakerLabelContent(labelElement, displayState.label, displayState.showManualConfirmation);
    }

}

/**
 * Refreshes the card that owns one transcript row.
 * Use after a row marker changes so the card header stops advertising stale
 * Doctor/Patient confidence.
 *
 * @param {HTMLElement} rowSpan - visible transcript row; detached rows have no card to refresh.
 * @returns {void} Updates the owning card when it is already visible.
 */
function refreshCardDisplayForRow(rowSpan) {
    refreshSpeakerCardDisplay(rowSpan.closest('.segment'));
}

/**
 * Reads one card's spoken-time bounds from the rows it currently holds.
 * Use when flow markers need real intervals after inserts, splits, or merges.
 *
 * @param {HTMLElement} segmentBlock - transcript card; a card without timed
 *   rows falls back to its own dataset bounds, which may be unmeasured.
 * @returns {{start: number, end: number}} bounds; NaN marks unmeasured cards.
 */
function transcriptCardInterval(segmentBlock) {
    let startSeconds = Infinity;
    let endSeconds = -Infinity;

    // Rows are the truth: cards keep min/max as rows are inserted or split off.
    for (const rowSpan of segmentBlock.querySelectorAll('.segment__text')) {
        const rowStart = parseFloat(rowSpan.dataset.start);
        const rowEnd = parseFloat(rowSpan.dataset.end);
        if (Number.isFinite(rowStart)) {
            startSeconds = Math.min(startSeconds, rowStart);
        }
        if (Number.isFinite(rowEnd)) {
            endSeconds = Math.max(endSeconds, rowEnd);
        }
    }

    return {
        start: Number.isFinite(startSeconds) ? startSeconds : parseFloat(segmentBlock.dataset.start),
        end: Number.isFinite(endSeconds) ? endSeconds : parseFloat(segmentBlock.dataset.end),
    };
}

/**
 * Applies the gap/overlap class a card's stored flow marker calls for.
 * Use whenever a card's class list is rebuilt, so role updates and row
 * corrections cannot silently drop the temporal markers.
 *
 * @param {HTMLElement} segmentBlock - transcript card; cards without a stored
 *   marker just lose any stale flow class.
 * @returns {void} Toggles only the two flow classes.
 */
function applyFlowClassesFromDataset(segmentBlock) {
    segmentBlock.classList.toggle('segment--after-gap', segmentBlock.dataset.flowKind === 'gap');
    segmentBlock.classList.toggle('segment--overlap', segmentBlock.dataset.flowKind === 'overlap');
}

/**
 * Recomputes the silence-gap and overlap markers across the whole transcript.
 * Use after any change that adds cards or re-times rows; one pass keeps the
 * markers consistent when late rows land between existing turns.
 *
 * @returns {void} Stores each card's marker in its dataset and class list.
 */
function applyTranscriptFlowMarkers() {
    const transcriptContainer = document.getElementById('transcript');

    // Test pages may load this module without the flow classifier.
    if (!transcriptContainer || typeof classifyTranscriptFlow !== 'function') {
        return;
    }

    const speakerCards = Array.from(transcriptContainer.querySelectorAll('.segment'));
    const flowEntries = classifyTranscriptFlow(speakerCards.map(transcriptCardInterval));

    speakerCards.forEach((segmentBlock, cardIndex) => {
        const flowNote = transcriptFlowNote(flowEntries[cardIndex]);

        // The marker lives in the dataset so class rebuilds can restore it.
        if (flowNote) {
            segmentBlock.dataset.flowKind = flowEntries[cardIndex].kind;
            segmentBlock.dataset.flowNote = flowNote;
        } else {
            delete segmentBlock.dataset.flowKind;
            delete segmentBlock.dataset.flowNote;
        }

        applyFlowClassesFromDataset(segmentBlock);
    });
}

/**
 * Announces important state changes to screen-reader users.
 * Empty follow-up clears the announcement after assistive tech receives it.
 */
function announce(message) {
    const announcementElement = document.getElementById('srAnnounce');

    // Some test pages do not render the live region.
    if (!announcementElement) {
        return;
    }

    announcementElement.textContent = message;
    setTimeout(() => {
        announcementElement.textContent = '';
    }, 1000);
}

/**
 * Handles raw transcript events from Mercure or dev replay.
 * Use whenever the clinician should see new transcript text or final status.
 */
function handleRawSegment(segmentEvent) {
    // Finalized events close replay or mark live transcription complete.
    if (segmentEvent.type === 'finalized') {
        // The backend just attested the terminal transcript; notes are now
        // allowed. Older backends without the fields still count as terminal.
        terminalAttestation = {
            id: segmentEvent.attestation_id ?? null,
            rowCount: segmentEvent.terminal_row_count ?? null,
            roleSettlement: segmentEvent.role_settlement ?? null,
        };
        // Warm the free GPU correction now so however long the clinician reads
        // before clicking Generate, the corrected lane is ready. The summary
        // itself still never starts without their click (M11 gate), and a
        // failed warm-up is retried by the click path.
        if (typeof ensureCorrectedTranscriptReady === 'function') {
            void ensureCorrectedTranscriptReady();
        }
        if (isReplayActive) {
            endReplay();
        } else if (isLiveDraining) {
            endLiveStop();
        } else {
            // The visit UI already ended on the bounded timeout; this late
            // finalize means the complete source finally exists, so the note
            // the user is waiting for can start now.
            setPlainStatus('Transcript finalized');
            if (typeof resumeSummaryAfterLateFinalize === 'function') {
                resumeSummaryAfterLateFinalize();
            }
        }
        return;
    }

    // Backend transcription errors should be visible without adding a card.
    if (segmentEvent.type === 'error') {
        setPlainStatus(`Transcription error: ${segmentEvent.message}`);
        return;
    }

    // Non-segment events are not visible transcript text.
    if (segmentEvent.type !== 'segment') {
        return;
    }

    const role = roleMapping[segmentEvent.speaker_id] ?? 'UNKNOWN';
    appendSegment(segmentEvent, role);
}

/**
 * Stores the completed-session quality record for dev inspection.
 * Use when the backend publishes `session.quality` after final transcription.
 */
function handleQualityRecord(qualityEvent) {
    latestQualityRecord = qualityEvent.quality ?? qualityEvent;
}

/**
 * Adds a transcript card or appends text to the current speaker card.
 * Use when NeMo emits a new segment for the visible consultation.
 */
function appendSegment(segment, role) {
    const transcriptContainer = document.getElementById('transcript');
    segmentIndex++;

    // Any visible transcript text means the start prompt is no longer useful.
    setElementHidden('emptyState', true);

    // The first transcript text makes a summary available, so reveal the pending panel.
    if (segmentIndex === 1) {
        revealSummaryPending();
    }

    // Consecutive text from the same speaker stays in one readable card when
    // the new row belongs at or after that card's first row. Late rows from the
    // same speaker are inserted within the card by row start.
    const lastCardStart = lastSegmentBlock ? parseFloat(lastSegmentBlock.dataset.start) : null;
    if (
        lastSpeakerId === segment.speaker_id
        && lastSegmentBlock
        && (lastCardStart === null || segment.start >= lastCardStart)
    ) {
        appendTextToExistingSegment(segment, transcriptContainer);
        applyTranscriptFlowMarkers();
        return;
    }

    // A straggler (a late-emitted old row, e.g. a drained dormant tail on the
    // streaming engine) inserts at its chronological row position so the
    // visible transcript and summary body stay in spoken order.
    const lastRow = getLastTranscriptRow(transcriptContainer);
    if (lastRow && segment.start < parseFloat(lastRow.dataset.start)) {
        insertSegmentChronologically(segment, role, transcriptContainer);
        applyTranscriptFlowMarkers();
        return;
    }

    const segmentBlock = createSegmentBlock(segment, role);
    transcriptContainer.appendChild(segmentBlock);
    trackSpeakerSegment(segment.speaker_id, segmentBlock);
    lastSpeakerId = segment.speaker_id;
    lastSegmentBlock = segmentBlock;
    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
    document.getElementById('segmentCount').textContent = segmentIndex;
    applyTranscriptFlowMarkers();
}

/**
 * Inserts one late-arriving row where its spoken time belongs.
 * Use when a segment starts earlier than the newest visible card, which the
 * streaming engine's dormant-slot drain can legitimately produce.
 *
 * @param {object} segment - transcript event; missing row ID means the row cannot be corrected.
 * @param {string} role - visible speaker role; `UNKNOWN` means the UI must not guess a label.
 * @param {HTMLElement} transcriptContainer - transcript list; empty means this row starts the visit.
 * @returns {void} Places the row chronologically without changing its speaker identity.
 */
function insertSegmentChronologically(segment, role, transcriptContainer) {
    const segmentBlock = createSegmentBlock(segment, role);
    const nextRow = findFirstTranscriptRowAfter(transcriptContainer, segment.start);

    // With no later wording on screen, this delayed row belongs at the end.
    if (!nextRow) {
        transcriptContainer.appendChild(segmentBlock);
        trackSpeakerSegment(segment.speaker_id, segmentBlock);
        lastSpeakerId = segment.speaker_id;
        lastSegmentBlock = segmentBlock;
        document.getElementById('segmentCount').textContent = segmentIndex;
        return;
    }

    // A visible row should own a card; null remains a safe fallback for malformed DOM state.
    const nextCard = nextRow.closest('.segment');
    const firstRowInCard = nextCard?.querySelector('.segment__text');
    // Only a real preceding transcript card can absorb delayed wording; the empty-state node cannot.
    const precedingElement = nextCard?.previousElementSibling;
    const precedingSpeakerCard = precedingElement?.classList.contains('segment')
        ? precedingElement
        : null;

    // A delayed continuation before the next turn stays in the same speaker card the user was reading.
    if (
        nextRow === firstRowInCard
        && precedingSpeakerCard?.dataset.speakerId === segment.speaker_id
    ) {
        insertRowIntoCard(segment, precedingSpeakerCard);
        trackSpeakerSegment(segment.speaker_id, precedingSpeakerCard);
        document.getElementById('segmentCount').textContent = segmentIndex;
        return;
    }

    // A different speaker at a card boundary still starts a separate visible turn.
    if (!nextCard || nextRow === firstRowInCard) {
        transcriptContainer.insertBefore(segmentBlock, nextCard);
        trackSpeakerSegment(segment.speaker_id, segmentBlock);
        document.getElementById('segmentCount').textContent = segmentIndex;
        return;
    }

    // Delayed wording inside its own existing card joins that card in spoken order.
    if (nextCard.dataset.speakerId === segment.speaker_id) {
        insertRowIntoCard(segment, nextCard, nextRow);
        trackSpeakerSegment(segment.speaker_id, nextCard);
        document.getElementById('segmentCount').textContent = segmentIndex;
        return;
    }

    const tailCard = splitSegmentCardAtRow(nextCard, nextRow);

    transcriptContainer.insertBefore(segmentBlock, tailCard);
    trackSpeakerSegment(segment.speaker_id, segmentBlock);
    document.getElementById('segmentCount').textContent = segmentIndex;
}

/**
 * Finds the newest visible transcript row in the visit.
 * Use when late backend rows need to decide whether they append or insert
 * earlier in the transcript the clinician is reviewing.
 *
 * @param {HTMLElement} transcriptContainer - transcript list; empty means no row is visible yet.
 * @returns {HTMLElement|null} newest visible row, or null when the transcript is empty.
 */
function getLastTranscriptRow(transcriptContainer) {
    const rows = transcriptContainer.querySelectorAll('.segment__text');
    // No rows means the next segment starts the clinician's transcript.
    return rows.length > 0 ? rows[rows.length - 1] : null;
}

/**
 * Finds the first transcript row after a spoken timestamp.
 * Use when a late row arrives and should be placed where the user heard it,
 * not at the bottom of the transcript.
 *
 * @param {HTMLElement} transcriptContainer - transcript list; empty means no insertion point exists.
 * @param {number} startSeconds - spoken start time; zero means the row belongs near the top.
 * @returns {HTMLElement|null} next later row, or null when the new row belongs at the end.
 */
function findFirstTranscriptRowAfter(transcriptContainer, startSeconds) {
    // Rows are checked in visible order to preserve the transcript the user scans.
    for (const row of transcriptContainer.querySelectorAll('.segment__text')) {
        // The first later row becomes the insertion anchor for this late segment.
        if (parseFloat(row.dataset.start) > startSeconds) {
            return row;
        }
    }

    return null;
}

/**
 * Inserts one row into an existing transcript card.
 * Use when adjacent same-speaker rows should stay grouped but still sorted by
 * the time the user heard them.
 *
 * @param {object} segment - transcript event; missing row ID means the row cannot be corrected.
 * @param {HTMLElement} segmentBlock - card receiving the row; empty cards still get a header refresh.
 * @param {HTMLElement|null} nextRow - row to insert before; null appends at the end of the card.
 * @returns {void} Updates the card text, end time, and safe header label.
 */
function insertRowIntoCard(segment, segmentBlock, nextRow = null) {
    const textContainer = segmentBlock.querySelector('.segment__texts');
    const textSpan = createRowTextSpan(segment);

    textContainer.insertBefore(textSpan, nextRow);
    normalizeRowSeparators(textContainer);
    segmentBlock.dataset.end = Math.max(
        parseFloat(segmentBlock.dataset.end) || segment.end,
        segment.end
    );
    refreshSpeakerCardDisplay(segmentBlock);
}

/**
 * Keeps exactly one real space between adjacent rows of a transcript card.
 * Visible spacing and copied/searched/screen-reader text must be the same
 * characters - CSS-only spacing silently vanished whenever a clinician
 * copied a card into the clinical record. Safe to call after any row
 * insertion, move, or card split; repeat calls are no-ops.
 *
 * @param {HTMLElement|null} textContainer - a card's row list; null (malformed
 *   DOM fallback) means there is nothing to space.
 * @returns {void} Rewrites only separator text nodes, never row spans.
 */
function normalizeRowSeparators(textContainer) {
    // A card shell that lost its row list has no spacing to maintain.
    if (!textContainer) {
        return;
    }

    let childNode = textContainer.firstChild;
    // Drop every existing separator first: rows moving between cards leave
    // stray leading/trailing spaces behind, and doubles would widen copies.
    while (childNode) {
        const nextNode = childNode.nextSibling;
        if (childNode.nodeType === Node.TEXT_NODE) {
            childNode.remove();
        }
        childNode = nextNode;
    }

    const rowSpans = textContainer.querySelectorAll('.segment__text');
    // Every row after the first gets one real space the clipboard keeps.
    for (let rowIndex = 1; rowIndex < rowSpans.length; rowIndex += 1) {
        textContainer.insertBefore(document.createTextNode(' '), rowSpans[rowIndex]);
    }
}

/**
 * Splits a transcript card before a late row from another speaker is inserted.
 * Use when a user-visible card already contains later rows that must remain in
 * chronological order around the new row.
 *
 * @param {HTMLElement} segmentBlock - existing card; empty cards are not expected here.
 * @param {HTMLElement} firstTailRow - first row moving to the new tail card.
 * @returns {HTMLElement} tail card that should follow the inserted late card.
 */
function splitSegmentCardAtRow(segmentBlock, firstTailRow) {
    const textContainer = segmentBlock.querySelector('.segment__texts');
    const tailRows = [];
    let currentRow = firstTailRow;

    // Move every row from the split point so the user's reading order stays intact.
    while (currentRow) {
        const nextRow = currentRow.nextElementSibling;
        tailRows.push(currentRow);
        currentRow = nextRow;
    }

    const speakerId = segmentBlock.dataset.speakerId;
    const role = roleMapping[speakerId] ?? 'UNKNOWN';
    const tailStart = parseFloat(tailRows[0].dataset.start) || parseFloat(segmentBlock.dataset.start) || 0;
    const tailEnd = parseFloat(tailRows[tailRows.length - 1].dataset.end) || tailStart;
    const tailBlock = createSegmentBlockShell(speakerId, role, tailStart, tailEnd);

    tailBlock.querySelector('.segment__texts').append(...tailRows);
    // Moving rows leaves their old separators behind; respace both cards.
    normalizeRowSeparators(textContainer);
    normalizeRowSeparators(tailBlock.querySelector('.segment__texts'));
    segmentBlock.after(tailBlock);
    trackSpeakerSegment(speakerId, tailBlock);

    const remainingRows = textContainer.querySelectorAll('.segment__text');
    const lastRemainingRow = remainingRows[remainingRows.length - 1];
    segmentBlock.dataset.end = lastRemainingRow?.dataset.end ?? segmentBlock.dataset.start;
    refreshSpeakerCardDisplay(segmentBlock);
    refreshSpeakerCardDisplay(tailBlock);

    // Splitting the latest card changes which card future same-speaker rows should join.
    if (lastSegmentBlock === segmentBlock) {
        lastSpeakerId = speakerId;
        lastSegmentBlock = tailBlock;
    }

    return tailBlock;
}

/**
 * Appends same-speaker text to the current transcript card.
 * Use when diarization keeps the same speaker across adjacent segments.
 */
function appendTextToExistingSegment(segment, transcriptContainer) {
    const nextRow = findFirstTranscriptRowAfter(lastSegmentBlock, segment.start);

    insertRowIntoCard(segment, lastSegmentBlock, nextRow);
    trackSpeakerSegment(segment.speaker_id, lastSegmentBlock);
    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
    document.getElementById('segmentCount').textContent = segmentIndex;
}

/**
 * Builds one transcript card using safe DOM nodes.
 * Use when a new speaker block appears in the consultation.
 */
function createSegmentBlock(segment, role) {
    const segmentBlock = createSegmentBlockShell(segment.speaker_id, role, segment.start, segment.end);
    segmentBlock.id = `segment-${segmentIndex}`;
    segmentBlock.querySelector('.segment__texts').appendChild(createRowTextSpan(segment));
    refreshSpeakerCardDisplay(segmentBlock);
    return segmentBlock;
}

/**
 * Builds the empty shell for one transcript card.
 * Use before the first row is attached; row-level refresh later adjusts mixed
 * or corrected-card labels.
 */
function createSegmentBlockShell(speakerId, role, start, end) {
    const segmentBlock = createElement('div', {
        className: `segment segment--${role}`,
        dataset: {
            speakerId,
            start,
            end,
        },
    });
    const speakerLabel = createElement('span', { className: 'segment__speaker' });
    const displayLabel = role === 'UNKNOWN' ? speakerId : getRoleLabel(role);
    setSpeakerLabelContent(speakerLabel, displayLabel, manualOverrides.has(speakerId));
    speakerLabel.style.cursor = 'pointer';
    speakerLabel.title = 'Click to change role';
    speakerLabel.addEventListener('click', () => cycleRole(segmentBlock.dataset.speakerId));

    const header = createElement('div', { className: 'segment__header' }, [
        speakerLabel,
        createElement('span', { className: 'segment__time', text: formatTime(start) }),
    ]);
    const textContainer = createElement('div', { className: 'segment__texts' });
    const body = createElement('div', { className: 'segment__body' }, [header, textContainer]);
    segmentBlock.append(body);
    return segmentBlock;
}

/**
 * Builds one transcript row span for a card.
 * Cards coalesce same-speaker rows, so this is where each row keeps its own
 * server row ID - citations deep-link to it and automatic row exceptions
 * restyle exactly the line they flag.
 */
function createRowTextSpan(segment) {
    const rowSpan = createElement('span', {
        className: 'segment__text',
        text: segment.text,
        dataset: { start: segment.start, end: segment.end },
    });

    // Measured rows keep the same value for summary round-trips.
    if (Number.isFinite(segment.confidence)) {
        rowSpan.dataset.confidence = segment.confidence;
    }

    // Rows without a server row ID (older histories) carry no row identity.
    if (!segment.segment_id) {
        return rowSpan;
    }

    rowSpan.dataset.segmentId = segment.segment_id;

    // A known automatic exception restyles the row as soon as it renders.
    if (autoRowRoles.has(segment.segment_id)) {
        renderAutoRowMarker(rowSpan, autoRowRoles.get(segment.segment_id));
    }

    return rowSpan;
}

/**
 * Tracks which cards belong to each speaker id.
 * Use so role updates can relabel earlier cards after inference improves.
 */
function trackSpeakerSegment(speakerId, segmentBlock) {
    // New speakers need their first list before relabeling can find them.
    if (!segmentsBySpeaker.has(speakerId)) {
        segmentsBySpeaker.set(speakerId, []);
    }

    segmentsBySpeaker.get(speakerId).push(segmentBlock);
}

/**
 * Writes speaker label text plus the manual-confirmed checkmark.
 * Use when cards are created or relabeled after a role change.
 */
function setSpeakerLabelContent(labelElement, labelText, isManuallyConfirmed) {
    labelElement.textContent = labelText;

    // Manual confirmation shows the clinician which labels they overrode.
    if (isManuallyConfirmed) {
        labelElement.appendChild(createElement('span', {
            className: 'segment__override-icon',
            text: '\u2713',
            attributes: { title: 'Manually confirmed' },
        }));
    }
}

/**
 * Cycles one speaker through Doctor, Patient, and Unknown labels.
 * Use when the clinician clicks a speaker label to correct the transcript.
 */
function cycleRole(speakerId) {
    const currentRole = roleMapping[speakerId] ?? 'UNKNOWN';
    const currentRoleIndex = MEDICAL_ROLE_CYCLE.indexOf(currentRole);
    let nextRole;

    // Unknown or unexpected labels move to the first medical role.
    if (currentRoleIndex === -1) {
        nextRole = MEDICAL_ROLE_CYCLE[0];
    } else if (currentRoleIndex === MEDICAL_ROLE_CYCLE.length - 1) {
        // The last medical role wraps to Unknown so users can undo a label.
        nextRole = 'UNKNOWN';
    } else {
        nextRole = MEDICAL_ROLE_CYCLE[currentRoleIndex + 1];
    }

    previousRoleMapping = { ...roleMapping };
    roleMapping[speakerId] = nextRole;
    if (nextRole === 'UNKNOWN') {
        // Unknown is the undo: future agent updates may relabel this speaker.
        manualOverrides.delete(speakerId);
    } else {
        manualOverrides.add(speakerId);
    }
    relabelSegments();
    sendRoleOverride(speakerId, nextRole);
}

/**
 * Sends a manual role correction to the Python role store.
 * Reports save failures as warnings because the visible manual label already changed.
 */
async function sendRoleOverride(speakerId, role) {
    try {
        const response = await fetch(`/scribe/${CONFIG.sessionId}/roles/override`, {
            method: 'POST',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ speaker_id: speakerId, role }),
        });

        // A rejected save leaves the local label visible but not protected server-side.
        if (!response.ok) {
            console.warn('Role override save failed:', response.status);
        }
    } catch (overrideError) {
        // Example: the clinician changes a speaker label while the save request loses connection.
        console.warn('Role override failed:', overrideError);
    }
}

/**
 * Applies role-agent updates in the same priority the clinician sees.
 * Use when mapping, row overrides, and automatic row exceptions arrive together:
 * speaker labels update, then row evidence can make mixed cards reviewable.
 * Runs for every roles-topic event, including post-visit manual corrections
 * that arrive after role-state teardown carrying no mapping at all.
 */
function handleRoleUpdate(roleUpdateEvent) {
    // The roles topic also carries a one-time model-unavailable warning during the visit.
    if (roleUpdateEvent.type === 'system_error') {
        showSystemBanner(roleUpdateEvent.message || 'AI model unavailable.');
        return;
    }

    applyMappingAndConfidenceNews(roleUpdateEvent);
    // A missing stability field means no new identity evidence (older server
    // or post-disconnect drain), so the badge keeps the last known state.
    roleStability = roleUpdateEvent.role_stability ?? roleStability;

    // Automatic row exceptions arrive as the full current set for this
    // mapping, so stale markers from the previous mapping are cleared.
    if (roleUpdateEvent.row_exceptions !== undefined) {
        applyAutoRowExceptions(roleUpdateEvent.row_exceptions);
    }

    updateConfidenceBadge();
    relabelSegments();
}

/**
 * Applies the speaker-mapping and confidence news one roles event carries.
 * Split from `handleRoleUpdate` so manual corrections can relabel rows without
 * moving the earned confidence badge: a post-visit override may arrive after
 * server role-state teardown with an empty or missing mapping, and treating
 * that as real model evidence wiped the badge to `Speakers unclear (0%)`.
 *
 * @param {object} roleUpdateEvent - roles-topic event; missing mapping/confidence fields mean no news.
 * @returns {void} Updates `roleMapping`/`confidence` only for genuine model evidence.
 */
function applyMappingAndConfidenceNews(roleUpdateEvent) {
    const mappingUpdate = roleUpdateEvent.mapping ?? {};
    // Speaker labels change only when the event actually carries mapping entries;
    // a post-visit row correction may arrive with no mapping at all.
    if (Object.keys(mappingUpdate).length > 0) {
        previousRoleMapping = { ...roleMapping };
        applySpeakerRoleMapping(mappingUpdate);
    }

    // Manual corrections relabel rows or speakers without new model evidence,
    // so they never move the confidence badge the visit already earned.
    if (!roleUpdateEvent.manual_override) {
        confidence = roleUpdateEvent.confidence ?? 0;
    }
}

/**
 * Applies speaker-level role mapping without overwriting clinician choices.
 * Use when the role agent labels speaker IDs while manual speaker overrides
 * remain the stronger visible decision.
 *
 * @param {object} speakerRoleMapping - speaker-to-role map; empty means no card label changes.
 * @returns {void} Updates `roleMapping` for speakers the clinician has not confirmed.
 */
function applySpeakerRoleMapping(speakerRoleMapping) {
    // Preserve manual corrections while applying model-inferred labels.
    for (const [speakerId, role] of Object.entries(speakerRoleMapping)) {
        // Confirmed speaker labels stay exactly as the clinician left them.
        if (!manualOverrides.has(speakerId)) {
            roleMapping[speakerId] = role;
        }
    }
}

/**
 * Replaces the automatic row-exception markers with the server's latest set.
 * These flag rows whose wording contradicts their card's role (e.g. a doctor
 * question inside a Patient card) - relabeled or shown uncertain per row,
 * without touching rows the clinician corrected personally.
 */
function applyAutoRowExceptions(rowExceptions) {
    // The map must be cleared before any marker removal: removeRowRoleMarker
    // refreshes the owning card, and that refresh reads autoRowRoles - a stale
    // entry would resolve the old auto role and leave the card header wrong.
    const staleSegmentIds = [...autoRowRoles.keys()].filter(
        (segmentId) => rowExceptions[segmentId] === undefined
    );
    autoRowRoles.clear();

    // Markers for rows the new mapping now explains are removed first.
    for (const staleSegmentId of staleSegmentIds) {
        removeRowRoleMarker(staleSegmentId);
    }

    // Each exception restyles exactly one visible row.
    for (const [segmentId, rowRole] of Object.entries(rowExceptions)) {
        autoRowRoles.set(segmentId, rowRole);

        const rowSpan = document.querySelector(
            `.segment__text[data-segment-id="${CSS.escape(segmentId)}"]`
        );
        // The row may not be rendered yet on a freshly reconnected tab.
        if (rowSpan) {
            renderAutoRowMarker(rowSpan, rowRole);
        }
    }
}

/**
 * Removes the automatic row marker from one transcript row.
 * Use when a new mapping explains a row the previous mapping contradicted.
 */
function removeRowRoleMarker(segmentId) {
    const rowSpan = document.querySelector(
        `.segment__text[data-segment-id="${CSS.escape(segmentId)}"]`
    );

    // The row may not be rendered yet on a freshly reconnected tab.
    if (!rowSpan) {
        return;
    }

    rowSpan.querySelector('.segment__row-role')?.remove();
    refreshCardDisplayForRow(rowSpan);
}

/**
 * Shows the automatic row-exception chip ("Dr auto" / "?") on one row.
 * This marks a machine judgment; the speaker label on the card remains the
 * clinician's control for relabeling.
 */
function renderAutoRowMarker(rowSpan, rowRole) {
    rowSpan.querySelector('.segment__row-role')?.remove();

    const isUncertain = rowRole === 'UNKNOWN';
    rowSpan.appendChild(createElement('span', {
        className: 'segment__row-role segment__row-role--auto',
        text: isUncertain ? '?' : `${getAvatarLabel(rowRole)} auto`,
        attributes: {
            title: isUncertain
                ? 'This line\'s speaker is uncertain - click to correct'
                : 'Relabeled from this line\'s wording - click to correct',
            },
    }));
    refreshCardDisplayForRow(rowSpan);
}

/**
 * Reports whether the speaker-identity layer is quiet enough to trust labels.
 * A confident role mapping over churning speaker IDs can still label rows
 * wrongly (the M20 consult-03 failure), so the green badge requires this too.
 */
function isSpeakerIdentityStable() {
    // No stability report yet (older server or none received) keeps the
    // pre-M20 behavior where confidence alone drives the badge.
    if (roleStability === null) {
        return true;
    }

    return roleStability.level !== 'unstable';
}

/**
 * Updates the role-confidence badge beside the recording status.
 * Use when inference confidence changes during a consultation. Green now
 * requires BOTH high mapping confidence and stable speaker identity; a
 * confident mapping over unstable identities renders as an amber warning so
 * the clinician verifies labels instead of trusting them.
 */
function updateConfidenceBadge() {
    const badge = document.getElementById('confidenceBadge');

    // The transcript still works if the badge is not rendered.
    if (!badge) {
        return;
    }

    badge.classList.remove('hidden');

    // High confidence AND quiet speaker identity: labels are likely stable.
    if (confidence >= 0.8 && isSpeakerIdentityStable()) {
        badge.textContent = `Roles identified (${Math.round(confidence * 100)}%)`;
        badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-800';
        // The percentage grades the doctor/patient mapping, not each row's label.
        badge.title = 'Confidence in the doctor/patient mapping overall - individual rows can still be mislabeled. Click a row to correct it.';
        return;
    }

    // Confident mapping over unstable speaker IDs: the consult-03 trap.
    // Some rows may show the wrong person even though the mapping looks sure.
    if (confidence >= 0.8) {
        badge.textContent = `Roles assigned - verify labels (${Math.round(confidence * 100)}%)`;
        badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800';
        badge.title = 'Speaker identities changed during this visit; some rows may be mislabeled. Click a speaker label to correct it.';
        return;
    }

    // Mid confidence warns that speaker labels may still change.
    if (confidence >= 0.5) {
        badge.textContent = `Low confidence (${Math.round(confidence * 100)}%)`;
        badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800';
        badge.title = '';
        return;
    }

    // Inference has run but could not confidently label speakers - a settled state,
    // not the pulsing "identifying" placeholder (which implies work still in progress).
    badge.textContent = `Speakers unclear (${Math.round(confidence * 100)}%)`;
    badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600';
    badge.title = '';
}

/**
 * Relabels transcript cards affected by changed speaker roles.
 * Use after role inference or a manual speaker override.
 */
function relabelSegments() {
    const changedSpeakers = collectChangedSpeakers();

    // No changed speakers means the visible transcript already matches state.
    if (changedSpeakers.length === 0) {
        return;
    }

    for (const speakerId of changedSpeakers) {
        relabelSpeakerBlocks(speakerId, roleMapping[speakerId] ?? 'UNKNOWN');
    }

    updateDevSegmentLogRoles(changedSpeakers);
}

/**
 * Determines which speakers need visible relabeling.
 * Empty previous state means all known speakers need their first label pass.
 */
function collectChangedSpeakers() {
    const changedSpeakers = [];

    // Compare mapping state so unchanged cards do not reanimate.
    for (const [speakerId, role] of Object.entries(roleMapping)) {
        if (previousRoleMapping[speakerId] !== role) {
            changedSpeakers.push(speakerId);
        }
    }

    // First inference pass should label every speaker already on screen.
    if (Object.keys(previousRoleMapping).length === 0) {
        for (const speakerId of segmentsBySpeaker.keys()) {
            if (!changedSpeakers.includes(speakerId)) {
                changedSpeakers.push(speakerId);
            }
        }
    }

    return changedSpeakers;
}

/**
 * Applies one speaker's new role to all visible cards.
 * Use so earlier transcript text matches the latest inferred label.
 */
function relabelSpeakerBlocks(speakerId, newRole) {
    const segmentBlocks = [...new Set(segmentsBySpeaker.get(speakerId) ?? [])];

    // Each visible card for this speaker gets the same role styling and avatar.
    for (const segmentBlock of segmentBlocks) {
        const previousClass = segmentBlock.className;
        refreshSpeakerCardDisplay(segmentBlock);

        // Unknown-to-known transitions draw attention to a newly identified speaker.
        if (previousClass.includes('segment--UNKNOWN') && newRole !== 'UNKNOWN') {
            segmentBlock.classList.add('segment--relabeled');
            segmentBlock.addEventListener('animationend', () => {
                segmentBlock.classList.remove('segment--relabeled');
            }, { once: true });
        }

    }
}

/**
 * Mirrors role changes into the dev segment log.
 * Use in local dev so replay/debug panels match the clinician transcript.
 */
function updateDevSegmentLogRoles(changedSpeakers) {
    const devLog = document.getElementById('devSegmentLog');

    // Production pages do not render the dev segment log.
    if (!devLog) {
        return;
    }

    const changedSpeakerSet = new Set(changedSpeakers);

    // Every logged segment row gets the same updated role label as the transcript.
    for (const entry of devLog.querySelectorAll('.dev-panel__entry')) {
        const speakerId = entry.dataset.speakerId;

        // Rows without this speaker are not affected by the role update.
        if (!speakerId || !changedSpeakerSet.has(speakerId)) {
            continue;
        }

        const newRole = roleMapping[speakerId] ?? 'UNKNOWN';
        const roleSpan = entry.querySelector('[data-role-label]');

        // Older or partial rows may not have the role label span.
        if (roleSpan) {
            roleSpan.textContent = getRoleLabel(newRole);
            roleSpan.style.color = getRoleColor(newRole);
        }
    }
}
