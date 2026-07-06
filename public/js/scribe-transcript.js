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
            visibleSegments.push({
                segment_id: segmentId,
                speaker_id: cardSpeakerId,
                role: rowRoleOverrides.get(segmentId)
                    ?? autoRowRoles.get(segmentId)
                    ?? roleMapping[cardSpeakerId]
                    ?? 'UNKNOWN',
                text: rowTextFromSpan(rowSpan),
                start: Number.parseFloat(rowSpan.dataset.start) || 0,
                end: Number.parseFloat(rowSpan.dataset.end) || 0,
            });
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
        if (isReplayActive) {
            endReplay();
        } else if (isLiveDraining) {
            endLiveStop();
        } else {
            setPlainStatus('Transcript finalized');
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
        return;
    }

    // A straggler (a late-emitted old row, e.g. a drained dormant tail on the
    // streaming engine) inserts at its chronological row position so the
    // visible transcript and summary body stay in spoken order.
    const lastRow = getLastTranscriptRow(transcriptContainer);
    if (lastRow && segment.start < parseFloat(lastRow.dataset.start)) {
        insertSegmentChronologically(segment, role, transcriptContainer);
        return;
    }

    const segmentBlock = createSegmentBlock(segment, role);
    transcriptContainer.appendChild(segmentBlock);
    trackSpeakerSegment(segment.speaker_id, segmentBlock);
    lastSpeakerId = segment.speaker_id;
    lastSegmentBlock = segmentBlock;
    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
    document.getElementById('segmentCount').textContent = segmentIndex;

}

/**
 * Inserts one late-arriving row where its spoken time belongs.
 * Use when a segment starts earlier than the newest visible card, which the
 * streaming engine's dormant-slot drain can legitimately produce.
 */
function insertSegmentChronologically(segment, role, transcriptContainer) {
    const segmentBlock = createSegmentBlock(segment, role);
    const nextRow = findFirstTranscriptRowAfter(transcriptContainer, segment.start);

    if (!nextRow) {
        transcriptContainer.appendChild(segmentBlock);
        trackSpeakerSegment(segment.speaker_id, segmentBlock);
        lastSpeakerId = segment.speaker_id;
        lastSegmentBlock = segmentBlock;
        document.getElementById('segmentCount').textContent = segmentIndex;
        return;
    }

    const nextCard = nextRow.closest('.segment');
    const firstRowInCard = nextCard?.querySelector('.segment__text');

    if (!nextCard || nextRow === firstRowInCard) {
        transcriptContainer.insertBefore(segmentBlock, nextCard);
        trackSpeakerSegment(segment.speaker_id, segmentBlock);
        document.getElementById('segmentCount').textContent = segmentIndex;
        return;
    }

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

function getLastTranscriptRow(transcriptContainer) {
    const rows = transcriptContainer.querySelectorAll('.segment__text');
    return rows.length > 0 ? rows[rows.length - 1] : null;
}

function findFirstTranscriptRowAfter(transcriptContainer, startSeconds) {
    for (const row of transcriptContainer.querySelectorAll('.segment__text')) {
        if (parseFloat(row.dataset.start) > startSeconds) {
            return row;
        }
    }

    return null;
}

function insertRowIntoCard(segment, segmentBlock, nextRow = null) {
    const textContainer = segmentBlock.querySelector('.segment__texts');
    const textSpan = createRowTextSpan(segment);

    textContainer.insertBefore(textSpan, nextRow);
    segmentBlock.dataset.end = Math.max(
        parseFloat(segmentBlock.dataset.end) || segment.end,
        segment.end
    );
}

function splitSegmentCardAtRow(segmentBlock, firstTailRow) {
    const textContainer = segmentBlock.querySelector('.segment__texts');
    const tailRows = [];
    let currentRow = firstTailRow;

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
    segmentBlock.after(tailBlock);
    trackSpeakerSegment(speakerId, tailBlock);

    const remainingRows = textContainer.querySelectorAll('.segment__text');
    const lastRemainingRow = remainingRows[remainingRows.length - 1];
    segmentBlock.dataset.end = lastRemainingRow?.dataset.end ?? segmentBlock.dataset.start;

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
    return segmentBlock;
}

function createSegmentBlockShell(speakerId, role, start, end) {
    const segmentBlock = createElement('div', {
        className: `segment segment--${role}`,
        dataset: {
            speakerId,
            start,
            end,
        },
    });
    const avatar = createElement('div', {
        className: 'segment__avatar',
        text: role === 'UNKNOWN' ? '?' : getAvatarLabel(role),
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
    segmentBlock.append(avatar, body);
    return segmentBlock;
}

/**
 * Builds one correctable transcript row span for a card.
 * Cards coalesce same-speaker rows, so this is where each row keeps its own
 * server row ID - letting the clinician fix exactly the line that is wrong
 * (e.g. one doctor question rendered inside a Patient card) without
 * relabeling the whole speaker.
 */
function createRowTextSpan(segment) {
    const rowSpan = createElement('span', {
        className: 'segment__text',
        text: segment.text,
        dataset: { start: segment.start, end: segment.end },
    });

    // Rows without a server row ID (older histories) cannot be corrected individually.
    if (!segment.segment_id) {
        return rowSpan;
    }

    rowSpan.dataset.segmentId = segment.segment_id;
    rowSpan.title = 'Click to correct who said this line';
    rowSpan.addEventListener('click', (clickEvent) => {
        // The card's speaker label has its own click behavior; keep them separate.
        clickEvent.stopPropagation();
        cycleRowRole(rowSpan);
    });

    // A correction may already exist when history rows re-render after replay.
    const existingRowRole = rowRoleOverrides.get(segment.segment_id);
    if (existingRowRole !== undefined) {
        renderRowRoleMarker(rowSpan, existingRowRole);
    } else if (autoRowRoles.has(segment.segment_id)) {
        // A known automatic exception restyles the row as soon as it renders.
        renderAutoRowMarker(rowSpan, autoRowRoles.get(segment.segment_id));
    }

    return rowSpan;
}

/**
 * Cycles one transcript row through Doctor, Patient, and Unknown labels.
 * Use when the clinician clicks a row whose speaker is wrong; Unknown marks
 * the row explicitly uncertain instead of guessing.
 */
function cycleRowRole(rowSpan) {
    const segmentId = rowSpan.dataset.segmentId;
    const cardSpeakerId = rowSpan.closest('.segment')?.dataset.speakerId ?? '';
    const currentRowRole = rowRoleOverrides.get(segmentId)
        ?? autoRowRoles.get(segmentId)
        ?? roleMapping[cardSpeakerId]
        ?? 'UNKNOWN';
    const currentRoleIndex = MEDICAL_ROLE_CYCLE.indexOf(currentRowRole);
    let nextRole;

    // Unknown or unexpected labels move to the first medical role.
    if (currentRoleIndex === -1) {
        nextRole = MEDICAL_ROLE_CYCLE[0];
    } else if (currentRoleIndex === MEDICAL_ROLE_CYCLE.length - 1) {
        // The last medical role wraps to Unknown so users can mark a row uncertain.
        nextRole = 'UNKNOWN';
    } else {
        nextRole = MEDICAL_ROLE_CYCLE[currentRoleIndex + 1];
    }

    rowRoleOverrides.set(segmentId, nextRole);
    renderRowRoleMarker(rowSpan, nextRole);
    announce(`Row corrected to ${nextRole === 'UNKNOWN' ? 'unknown speaker' : getRoleLabel(nextRole)}`);
    sendRowRoleOverride(segmentId, nextRole);
}

/**
 * Shows the corrected-row chip ("Dr ✓") on one transcript row.
 * The chip survives later speaker-level relabeling, so the clinician can see
 * which rows they personally pinned.
 */
function renderRowRoleMarker(rowSpan, role) {
    rowSpan.classList.add('segment__text--corrected');
    rowSpan.querySelector('.segment__row-role')?.remove();

    const chipLabel = role === 'UNKNOWN' ? '?' : getAvatarLabel(role);
    rowSpan.appendChild(createElement('span', {
        className: 'segment__row-role',
        text: `${chipLabel} ✓`,
        attributes: { title: 'Corrected by you' },
    }));
}

/**
 * Sends a per-row role correction to the Python row-correction store.
 * Reports save failures as warnings because the visible row label already changed.
 */
async function sendRowRoleOverride(segmentId, role) {
    try {
        const response = await fetch(`/scribe/${CONFIG.sessionId}/roles/override`, {
            method: 'POST',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ segment_id: segmentId, role }),
        });

        // A rejected save leaves the local row label visible but not protected server-side.
        if (!response.ok) {
            console.warn('Row role override save failed:', response.status);
        }
    } catch (overrideError) {
        console.warn('Row role override failed:', overrideError);
    }
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
    manualOverrides.add(speakerId);
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
        console.warn('Role override failed:', overrideError);
    }
}

/**
 * Applies inferred role mapping updates to visible transcript cards.
 * Use when the role agent publishes Doctor/Patient confidence.
 */
function handleRoleUpdate(roleUpdateEvent) {
    // The roles topic also carries a one-time model-unavailable warning during the visit.
    if (roleUpdateEvent.type === 'system_error') {
        showSystemBanner(roleUpdateEvent.message || 'AI model unavailable.');
        return;
    }

    // Empty mapping means role inference has nothing new to show the clinician.
    if (!roleUpdateEvent.mapping) {
        return;
    }

    previousRoleMapping = { ...roleMapping };

    // Preserve manual corrections while applying model-inferred labels.
    for (const [speakerId, role] of Object.entries(roleUpdateEvent.mapping)) {
        if (!manualOverrides.has(speakerId)) {
            roleMapping[speakerId] = role;
        }
    }

    confidence = roleUpdateEvent.confidence ?? 0;
    // A missing stability field means no new identity evidence (older server
    // or post-disconnect drain), so the badge keeps the last known state.
    roleStability = roleUpdateEvent.role_stability ?? roleStability;

    // Row-scoped corrections (from this tab's save or another tab) pin exactly
    // one row; they never relabel the rest of the speaker's rows.
    for (const [segmentId, rowRole] of Object.entries(roleUpdateEvent.row_overrides ?? {})) {
        rowRoleOverrides.set(segmentId, rowRole);
        const rowSpan = document.querySelector(
            `.segment__text[data-segment-id="${CSS.escape(segmentId)}"]`
        );

        // The row may not be rendered yet on a freshly reconnected tab.
        if (rowSpan) {
            renderRowRoleMarker(rowSpan, rowRole);
        }
    }

    // Automatic row exceptions arrive as the full current set for this
    // mapping, so stale markers from the previous mapping are cleared.
    if (roleUpdateEvent.row_exceptions !== undefined) {
        applyAutoRowExceptions(roleUpdateEvent.row_exceptions);
    }

    updateConfidenceBadge();
    relabelSegments();
}

/**
 * Replaces the automatic row-exception markers with the server's latest set.
 * These flag rows whose wording contradicts their card's role (e.g. a doctor
 * question inside a Patient card) - relabeled or shown uncertain per row,
 * without touching rows the clinician corrected personally.
 */
function applyAutoRowExceptions(rowExceptions) {
    // Markers for rows the new mapping now explains are removed first.
    for (const staleSegmentId of autoRowRoles.keys()) {
        if (rowExceptions[staleSegmentId] === undefined) {
            removeRowRoleMarker(staleSegmentId);
        }
    }

    autoRowRoles.clear();

    // Each exception restyles exactly one visible row.
    for (const [segmentId, rowRole] of Object.entries(rowExceptions)) {
        autoRowRoles.set(segmentId, rowRole);

        // The clinician's own correction chip stays authoritative on screen.
        if (rowRoleOverrides.has(segmentId)) {
            continue;
        }

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
 * Removes any row marker (auto or corrected) from one transcript row.
 * Use when a new mapping explains a row the previous mapping contradicted.
 */
function removeRowRoleMarker(segmentId) {
    const rowSpan = document.querySelector(
        `.segment__text[data-segment-id="${CSS.escape(segmentId)}"]`
    );

    // Rows corrected by the clinician keep their own chip.
    if (!rowSpan || rowRoleOverrides.has(segmentId)) {
        return;
    }

    rowSpan.classList.remove('segment__text--corrected');
    rowSpan.querySelector('.segment__row-role')?.remove();
}

/**
 * Shows the automatic row-exception chip ("Dr auto" / "?") on one row.
 * Unlike the clinician's "✓" chip, this marks a machine judgment the
 * clinician can still override by clicking the row.
 */
function renderAutoRowMarker(rowSpan, rowRole) {
    rowSpan.querySelector('.segment__row-role')?.remove();
    rowSpan.classList.remove('segment__text--corrected');

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
        badge.title = '';
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
        segmentBlock.className = `segment segment--${newRole}`;

        // Unknown-to-known transitions draw attention to a newly identified speaker.
        if (previousClass.includes('segment--UNKNOWN') && newRole !== 'UNKNOWN') {
            segmentBlock.classList.add('segment--relabeled');
            segmentBlock.addEventListener('animationend', () => {
                segmentBlock.classList.remove('segment--relabeled');
            }, { once: true });
        }

        updateSpeakerCardLabel(segmentBlock, speakerId, newRole);
        updateSpeakerCardAvatar(segmentBlock, newRole);
    }
}

/**
 * Updates the role label inside one transcript card.
 * Use when inference or manual override changes what the clinician sees.
 */
function updateSpeakerCardLabel(segmentBlock, speakerId, newRole) {
    const labelElement = segmentBlock.querySelector('.segment__speaker');

    // Missing label markup means this card has nothing visible to relabel.
    if (!labelElement) {
        return;
    }

    const displayLabel = newRole === 'UNKNOWN' ? speakerId : getRoleLabel(newRole);
    setSpeakerLabelContent(labelElement, displayLabel, manualOverrides.has(speakerId));
}

/**
 * Updates the compact avatar text in one transcript card.
 * Use when a speaker changes between Unknown, Doctor, and Patient.
 */
function updateSpeakerCardAvatar(segmentBlock, newRole) {
    const avatarElement = segmentBlock.querySelector('.segment__avatar');

    // Missing avatar markup means there is no visible initial to update.
    if (!avatarElement) {
        return;
    }

    avatarElement.textContent = newRole === 'UNKNOWN' ? '?' : getAvatarLabel(newRole);
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
