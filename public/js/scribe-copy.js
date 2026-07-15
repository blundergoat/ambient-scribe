/**
 * Semantic copy for the transcript and the draft note (M03).
 *
 * Clinicians paste transcript and note text into the clinical record, so what
 * lands on the clipboard must be built from application state - never from
 * container textContent, which harvests control chrome (provenance counts
 * like "11/1/2/6"), drops CSS-only spacing, and loses review meaning. The
 * pure serializers here produce exactly the approved default export: the
 * note always travels with its source/review status lines, and the
 * transcript keeps per-row timestamps, speakers, and exact wording.
 *
 * Loaded as a classic script; the module.exports guard lets node:test pin
 * the leak specimens without a browser DOM.
 */

/* eslint-disable no-unused-vars */

// Contract wording for the lifecycle/source axis (CONTRACTS.md section 2).
// Blocked reasons from the backend map onto these fixed clinician-facing lines.
const SOURCE_AXIS_LINES = {
    generating: 'Generating',
    waiting: 'Source still finalizing — note unavailable',
    corrected: 'Draft generated from corrected transcript',
    live_fallback: 'Complete live fallback — review required',
    incomplete: 'Source incomplete — note unavailable',
    correction_unavailable: 'Correction unavailable — note unavailable',
    over_limit: 'Source exceeds note limit — note unavailable',
};

// Which fixed source line each backend blocked reason means for the user.
const SOURCE_LINE_BY_BLOCKED_REASON = {
    source_not_terminal: SOURCE_AXIS_LINES.waiting,
    stale_lineage: SOURCE_AXIS_LINES.incomplete,
    unaccounted_meaningful_rows: SOURCE_AXIS_LINES.incomplete,
    empty_visit: SOURCE_AXIS_LINES.incomplete,
    correction_pending: SOURCE_AXIS_LINES.correction_unavailable,
    source_exceeds_note_limit: SOURCE_AXIS_LINES.over_limit,
};

// The automated-review axis must never read as clinical approval.
const NO_FLAGS_LIMITATION_TEXT =
    'Automated checks are limited and do not constitute clinical approval.';

// Every 0.4.0 generated artifact carries this fixed clinician-review state.
const NOT_CLINICIAN_REVIEWED_TEXT = 'Not clinician reviewed';

// Lane wording shown beside the transcript and used by the transcript export.
const TRANSCRIPT_LANE_LABELS = {
    corrected: 'Corrected transcript — used for note',
    live: 'Live preview — may change',
    live_fallback: 'Complete live fallback — review required',
};

/**
 * Formats seconds as the [MM:SS] stamp used across the transcript UI.
 * Use in transcript exports so pasted rows keep their spoken moment.
 *
 * @param {number} seconds - spoken start time; missing/invalid renders 00:00.
 * @returns {string} zero-padded MM:SS text.
 */
function formatCopyTimestamp(seconds) {
    const safeSeconds = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
    const wholeMinutes = Math.floor(safeSeconds / 60);
    const remainingSeconds = Math.floor(safeSeconds % 60);
    return `${String(wholeMinutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`;
}

/**
 * Serializes transcript rows into paste-safe plain text.
 * Use for the Copy transcript action: one line per row keeps timestamp,
 * speaker, and the row's exact wording separated - the boundaries a pasted
 * record needs and whole-container copying destroyed.
 *
 * @param {Array<object>} rowModels - [{startSeconds, speakerLabel, text}];
 *   empty produces just the lane header, so the paste is never misleading.
 * @param {string} laneLabel - which lane the user copied; empty omits the header.
 * @returns {string} plain-text transcript, rows in given (spoken) order.
 */
function serializeTranscriptRows(rowModels, laneLabel = '') {
    const transcriptLines = [];

    // The lane header tells the reader whether this text fed the note.
    if (laneLabel) {
        transcriptLines.push(`Transcript — ${laneLabel}`, '');
    }

    // One row per line: exact wording, never re-stitched or re-spaced.
    for (const rowModel of rowModels) {
        transcriptLines.push(
            `[${formatCopyTimestamp(rowModel.startSeconds)}] `
            + `${rowModel.speakerLabel}: ${rowModel.text}`
        );
    }

    return transcriptLines.join('\n');
}

/**
 * Derives the three independent status axes for the note panel and export.
 * Use wherever note state is shown or copied, so screen, clipboard, and
 * keyboard users all read the same three truths: where the text came from,
 * what automated checks flagged, and that no clinician has reviewed it.
 *
 * @param {object} statusModel - current note state:
 *   phase: 'waiting'|'generating'|'blocked'|'failed'|'generated'; unknown acts like 'failed'.
 *   sourceState: M02 source_state; null before a note exists.
 *   blockedReason: backend blocked reason; null unless phase is 'blocked'.
 *   roleSettlement: 'settled'|'failed_frozen'|null; frozen forces a review flag.
 *   unverifiedCount / lowConfidenceCount: automated flags; missing counts as zero.
 * @returns {object} {sourceLine, automatedReviewLine, clinicianReviewLine,
 *   reviewReasons, noteAvailable} - null lines mean that axis is not shown
 *   (e.g. a transient failure keeps the existing failure UI instead).
 */
function deriveNoteStatusLines(statusModel) {
    const phase = statusModel?.phase ?? 'failed';

    // Before any artifact exists only the source axis has something to say.
    if (phase === 'generating') {
        return {
            sourceLine: SOURCE_AXIS_LINES.generating,
            automatedReviewLine: null,
            clinicianReviewLine: null,
            reviewReasons: [],
            noteAvailable: false,
        };
    }
    if (phase === 'waiting') {
        return {
            sourceLine: SOURCE_AXIS_LINES.waiting,
            automatedReviewLine: null,
            clinicianReviewLine: null,
            reviewReasons: [],
            noteAvailable: false,
        };
    }
    if (phase === 'blocked') {
        return {
            // Unknown reasons stay on the safe "incomplete" wording.
            sourceLine: SOURCE_LINE_BY_BLOCKED_REASON[statusModel?.blockedReason]
                ?? SOURCE_AXIS_LINES.incomplete,
            automatedReviewLine: null,
            clinicianReviewLine: null,
            reviewReasons: [],
            noteAvailable: false,
        };
    }
    // Transient failures keep the existing failed badge and retry button.
    if (phase !== 'generated') {
        return {
            sourceLine: null,
            automatedReviewLine: null,
            clinicianReviewLine: null,
            reviewReasons: [],
            noteAvailable: false,
        };
    }

    const sourceLine = statusModel?.sourceState === 'whole_visit_live_fallback'
        ? SOURCE_AXIS_LINES.live_fallback
        : SOURCE_AXIS_LINES.corrected;

    const reviewReasons = [];
    const unverifiedCount = statusModel?.unverifiedCount ?? 0;
    const lowConfidenceCount = statusModel?.lowConfidenceCount ?? 0;
    // The visible (n) counts every flagged item, so it always matches the
    // breakdown beneath it - "(2)" above a 1+3 item list misled the reader.
    let flaggedItemCount = unverifiedCount + lowConfidenceCount;

    // Each reason is user-readable text; the panel and the copy show the same list.
    if (unverifiedCount > 0) {
        reviewReasons.push(
            `${unverifiedCount} statement${unverifiedCount === 1 ? '' : 's'} not supported by the transcript`
        );
    }
    if (lowConfidenceCount > 0) {
        reviewReasons.push(
            `${lowConfidenceCount} sentence${lowConfidenceCount === 1 ? '' : 's'} from low-confidence wording`
        );
    }
    // A frozen role settlement means speaker labels never finished settling.
    if (statusModel?.roleSettlement === 'failed_frozen') {
        reviewReasons.push('speaker labels were frozen before role checks completed');
        flaggedItemCount += 1;
    }
    // A live-fallback source is itself a review reason, even with no text flags.
    if (statusModel?.sourceState === 'whole_visit_live_fallback') {
        reviewReasons.push('note built from the live transcript, not the corrected pass');
        flaggedItemCount += 1;
    }

    const automatedReviewLine = flaggedItemCount > 0
        ? `Review required (${flaggedItemCount})`
        : `No automated review flags. ${NO_FLAGS_LIMITATION_TEXT}`;

    return {
        sourceLine,
        automatedReviewLine,
        clinicianReviewLine: NOT_CLINICIAN_REVIEWED_TEXT,
        reviewReasons,
        noteAvailable: true,
    };
}

/**
 * Appends a visible review marker after each flagged sentence in note prose.
 * Use during note export so review meaning survives as plain text - on
 * screen the same sentences carry styled markers that vanish in a paste.
 *
 * @param {string} sectionContent - the section prose; empty returns empty.
 * @param {string[]} flaggedSentences - exact sentence strings; unmatched
 *   sentences are appended as explicit review lines so meaning is never lost.
 * @param {string} markerText - e.g. '[Unverified]' or '[Low confidence]'.
 * @returns {string} prose with markers, never silently unmarked.
 */
function markFlaggedSentencesForCopy(sectionContent, flaggedSentences, markerText) {
    let markedContent = sectionContent ?? '';
    const unmatchedSentences = [];

    for (const flaggedSentence of flaggedSentences ?? []) {
        // Exact matches get the marker right where the reader sees the claim.
        if (flaggedSentence && markedContent.includes(flaggedSentence)) {
            markedContent = markedContent.replace(
                flaggedSentence,
                `${flaggedSentence} ${markerText}`
            );
        } else if (flaggedSentence) {
            unmatchedSentences.push(flaggedSentence);
        }
    }

    // Sentences the prose no longer contains still surface their review state.
    for (const unmatchedSentence of unmatchedSentences) {
        markedContent += `\n${markerText} ${unmatchedSentence}`;
    }

    return markedContent;
}

/**
 * Serializes the draft note into the approved default export.
 * Use for the Copy draft note action: title, the three status axes, key
 * points, and SOAP prose with review markers as real text. Provenance
 * counts, buttons, tabs, tooltips, and Dev Panel text can never appear
 * because the serializer reads the note payload, not the DOM.
 *
 * @param {object} noteModel - {title, statusLines, reviewReasons, keyPoints:
 *   [{text, unverified}], sections: [{heading, content, unverifiedSentences,
 *   lowConfidenceSentences}]}; missing pieces are skipped, never guessed.
 * @returns {string} the paste-ready draft note text.
 */
function serializeDraftNote(noteModel) {
    const noteLines = [];

    noteLines.push(noteModel?.title || 'Draft note', '');

    const statusLines = noteModel?.statusLines ?? {};
    // The three axes always travel with the text (approved default export).
    if (statusLines.sourceLine) {
        noteLines.push(`Source: ${statusLines.sourceLine}`);
    }
    if (statusLines.automatedReviewLine) {
        noteLines.push(`Automated review: ${statusLines.automatedReviewLine}`);
        // The reasons make "Review required (n)" actionable in the pasted text.
        for (const reviewReason of noteModel?.reviewReasons ?? []) {
            noteLines.push(`  - ${reviewReason}`);
        }
    }
    if (statusLines.clinicianReviewLine) {
        noteLines.push(`Clinician review: ${statusLines.clinicianReviewLine}`);
    }
    noteLines.push('');

    const keyPoints = noteModel?.keyPoints ?? [];
    // Key points keep their unverified marker exactly where the panel shows it.
    if (keyPoints.length > 0) {
        noteLines.push('Key Points:');
        for (const keyPoint of keyPoints) {
            noteLines.push(
                keyPoint.unverified
                    ? `- [Unverified] ${keyPoint.text}`
                    : `- ${keyPoint.text}`
            );
        }
        noteLines.push('');
    }

    // Sections render as heading + prose with review markers inline.
    for (const section of noteModel?.sections ?? []) {
        noteLines.push(`${section.heading ?? ''}:`);
        let sectionContent = markFlaggedSentencesForCopy(
            section.content,
            section.unverifiedSentences,
            '[Unverified]'
        );
        sectionContent = markFlaggedSentencesForCopy(
            sectionContent,
            section.lowConfidenceSentences,
            '[Low confidence]'
        );
        noteLines.push(sectionContent, '');
    }

    return noteLines.join('\n').trimEnd() + '\n';
}

/**
 * Maps a backend summary payload onto the export's note model.
 * Use before serializing: only clinical text and review meaning survive the
 * mapping - citations, counts, and any UI-only fields are dropped here, so
 * the export cannot leak them no matter how the panel renders.
 *
 * @param {object} summaryPayload - backend note payload; missing arrays act empty.
 * @param {object} statusLines - output of deriveNoteStatusLines for this note.
 * @returns {object} noteModel accepted by serializeDraftNote.
 */
function noteModelFromSummaryPayload(summaryPayload, statusLines) {
    const unverifiedKeyPoints = summaryPayload?.unverified_key_points ?? [];

    return {
        title: summaryPayload?.title || 'Draft note',
        statusLines,
        reviewReasons: statusLines?.reviewReasons ?? [],
        keyPoints: (summaryPayload?.key_points ?? []).map((keyPointText) => ({
            text: keyPointText,
            unverified: unverifiedKeyPoints.includes(keyPointText),
        })),
        sections: (summaryPayload?.sections ?? []).map((section) => ({
            heading: section.heading,
            content: section.content,
            unverifiedSentences: section.unverified ?? [],
            lowConfidenceSentences: section.low_confidence ?? [],
        })),
    };
}

// ---------------------------------------------------------------------------
// Browser adapters - everything below reads live page state and is inert in
// node tests (no DOM access happens until a copy button is clicked).
// ---------------------------------------------------------------------------

/**
 * Resolves one visible transcript row's effective speaker label.
 * Use during transcript export; the pasted label must match what the
 * clinician sees after global role mapping and their own row corrections.
 *
 * @param {object} segment - visible row {segment_id, speaker_id}; missing IDs
 *   fall back to the raw speaker label.
 * @returns {string} Doctor, Patient, Unknown, or the raw speaker id.
 */
function effectiveSpeakerLabelForCopy(segment) {
    const rowRole = rowRoleOverrides.get(segment.segment_id)
        ?? autoRowRoles.get(segment.segment_id)
        ?? roleMapping[segment.speaker_id];

    // Unmapped speakers keep their raw id so the paste never invents a role.
    if (!rowRole || rowRole === 'UNKNOWN') {
        return rowRole === 'UNKNOWN' ? 'Unknown' : (segment.speaker_id ?? 'Unknown');
    }

    return getRoleLabel(rowRole);
}

/**
 * Copies text to the clipboard with a visible confirmation on the button.
 * Use from both copy actions; the textarea fallback covers browsers or
 * test pages where the async clipboard API is unavailable.
 */
async function copyTextToClipboard(copyText, copyButton) {
    try {
        // Example: the clinician clicks Copy draft note after reviewing the panel.
        await navigator.clipboard.writeText(copyText);
    } catch (clipboardError) {
        // Example: a non-secure context or denied permission rejects the API.
        const fallbackArea = document.createElement('textarea');
        fallbackArea.value = copyText;
        fallbackArea.setAttribute('readonly', '');
        fallbackArea.style.position = 'fixed';
        fallbackArea.style.opacity = '0';
        document.body.appendChild(fallbackArea);
        fallbackArea.select();
        document.execCommand('copy');
        fallbackArea.remove();
    }

    // The button itself confirms, so no toast can cover clinical text.
    if (copyButton) {
        const restingLabel = copyButton.dataset.restingLabel ?? copyButton.textContent;
        copyButton.dataset.restingLabel = restingLabel;
        copyButton.textContent = 'Copied';
        window.setTimeout(() => {
            copyButton.textContent = restingLabel;
        }, 1600);
    }
}

/**
 * Copies the summary panel's Transcript tab lane exactly as displayed.
 * Use from the Copy transcript button; the export lane label states whether
 * this text fed the note or is the changeable live preview.
 */
async function copyActiveTranscriptLane(copyButton) {
    // The tab's own selection logic decides corrected-vs-live; reuse it.
    const correctedRows = await fetchCorrectedTranscriptRows();
    const isCorrectedLane = Array.isArray(correctedRows) && correctedRows.length > 0;
    const laneRows = isCorrectedLane ? correctedRows : readVisibleTranscriptSegments();

    const laneLabel = isCorrectedLane
        ? TRANSCRIPT_LANE_LABELS.corrected
        : transcriptLaneLabelForLiveRows();

    const rowModels = laneRows.map((laneRow) => ({
        startSeconds: parseFloat(laneRow.start) || 0,
        speakerLabel: isCorrectedLane && laneRow.role
            ? getRoleLabel(laneRow.role)
            : effectiveSpeakerLabelForCopy(laneRow),
        text: String(laneRow.text ?? ''),
    }));

    await copyTextToClipboard(serializeTranscriptRows(rowModels, laneLabel), copyButton);
}

/**
 * Chooses the live lane's label from the last note's source state.
 * Use for lane headers: a live-fallback note makes the live lane the note
 * source (review required); otherwise it is just the changeable preview.
 */
function transcriptLaneLabelForLiveRows() {
    // A live-fallback note means these live rows ARE the note's source.
    if (latestRenderedSummaryPayload?.source_state === 'whole_visit_live_fallback') {
        return TRANSCRIPT_LANE_LABELS.live_fallback;
    }
    return TRANSCRIPT_LANE_LABELS.live;
}

/**
 * Copies the rendered draft note using the approved default export.
 * Use from the Copy draft note button; disabled states never reach here
 * because the button is only enabled once a note artifact exists.
 */
async function copyDraftNote(copyButton) {
    // No rendered note means nothing honest to copy; the button should be disabled.
    if (!latestRenderedSummaryPayload) {
        return;
    }

    const summaryPayload = latestRenderedSummaryPayload;
    const statusLines = deriveNoteStatusLines(collectNoteStatusModel(summaryPayload));
    const noteModel = noteModelFromSummaryPayload(summaryPayload, statusLines);

    await copyTextToClipboard(serializeDraftNote(noteModel), copyButton);
}

// Node tests pin the serializers and axis wording without a browser DOM.
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        NO_FLAGS_LIMITATION_TEXT,
        NOT_CLINICIAN_REVIEWED_TEXT,
        SOURCE_AXIS_LINES,
        TRANSCRIPT_LANE_LABELS,
        deriveNoteStatusLines,
        formatCopyTimestamp,
        markFlaggedSentencesForCopy,
        noteModelFromSummaryPayload,
        serializeDraftNote,
        serializeTranscriptRows,
    };
}
