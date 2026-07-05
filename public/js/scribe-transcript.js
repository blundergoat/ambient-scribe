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

/**
 * Builds summary rows from the transcript cards visible in the browser.
 * Use when stop or summary requests need exactly what the clinician can see.
 */
function readVisibleTranscriptSegments() {
    const visibleSegments = [];

    // Each transcript card can contain several same-speaker text spans.
    for (const segmentElement of document.querySelectorAll('.segment')) {
        visibleSegments.push({
            speaker_id: segmentElement.dataset.speakerId,
            role: roleMapping[segmentElement.dataset.speakerId] ?? 'UNKNOWN',
            text: [...segmentElement.querySelectorAll('.segment__text')]
                .map((textElement) => textElement.textContent.trim())
                .join(' '),
            start: Number.parseFloat(segmentElement.dataset.start) || 0,
            end: Number.parseFloat(segmentElement.dataset.end) || 0,
        });
    }

    return visibleSegments;
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

    // Consecutive text from the same speaker stays in one readable card.
    if (lastSpeakerId === segment.speaker_id && lastSegmentBlock) {
        appendTextToExistingSegment(segment, transcriptContainer);
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
 * Appends same-speaker text to the current transcript card.
 * Use when diarization keeps the same speaker across adjacent segments.
 */
function appendTextToExistingSegment(segment, transcriptContainer) {
    const textContainer = lastSegmentBlock.querySelector('.segment__texts');
    const textSpan = createElement('span', {
        className: 'segment__text',
        text: segment.text,
        dataset: { start: segment.start, end: segment.end },
    });

    textContainer.appendChild(textSpan);
    lastSegmentBlock.dataset.end = segment.end;
    trackSpeakerSegment(segment.speaker_id, lastSegmentBlock);
    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
    document.getElementById('segmentCount').textContent = segmentIndex;
}

/**
 * Builds one transcript card using safe DOM nodes.
 * Use when a new speaker block appears in the consultation.
 */
function createSegmentBlock(segment, role) {
    const segmentBlock = createElement('div', {
        className: `segment segment--${role}`,
        dataset: {
            speakerId: segment.speaker_id,
            start: segment.start,
            end: segment.end,
        },
        attributes: { id: `segment-${segmentIndex}` },
    });
    const avatar = createElement('div', {
        className: 'segment__avatar',
        text: role === 'UNKNOWN' ? '?' : getAvatarLabel(role),
    });
    const speakerLabel = createElement('span', { className: 'segment__speaker' });
    const displayLabel = role === 'UNKNOWN' ? segment.speaker_id : getRoleLabel(role);
    setSpeakerLabelContent(speakerLabel, displayLabel, manualOverrides.has(segment.speaker_id));
    speakerLabel.style.cursor = 'pointer';
    speakerLabel.title = 'Click to change role';
    speakerLabel.addEventListener('click', () => cycleRole(segmentBlock.dataset.speakerId));

    const header = createElement('div', { className: 'segment__header' }, [
        speakerLabel,
        createElement('span', { className: 'segment__time', text: formatTime(segment.start) }),
    ]);
    const textContainer = createElement('div', { className: 'segment__texts' }, [
        createElement('span', {
            className: 'segment__text',
            text: segment.text,
            dataset: { start: segment.start, end: segment.end },
        }),
    ]);
    const body = createElement('div', { className: 'segment__body' }, [header, textContainer]);
    segmentBlock.append(avatar, body);
    return segmentBlock;
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
    updateConfidenceBadge();
    relabelSegments();
}

/**
 * Updates the role-confidence badge beside the recording status.
 * Use when inference confidence changes during a consultation.
 */
function updateConfidenceBadge() {
    const badge = document.getElementById('confidenceBadge');

    // The transcript still works if the badge is not rendered.
    if (!badge) {
        return;
    }

    badge.classList.remove('hidden');

    // High confidence tells the clinician labels are likely stable.
    if (confidence >= 0.8) {
        badge.textContent = `Roles identified (${Math.round(confidence * 100)}%)`;
        badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-800';
        return;
    }

    // Mid confidence warns that speaker labels may still change.
    if (confidence >= 0.5) {
        badge.textContent = `Low confidence (${Math.round(confidence * 100)}%)`;
        badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800';
        return;
    }

    // Inference has run but could not confidently label speakers - a settled state,
    // not the pulsing "identifying" placeholder (which implies work still in progress).
    badge.textContent = `Speakers unclear (${Math.round(confidence * 100)}%)`;
    badge.className = 'confidence-badge text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600';
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
