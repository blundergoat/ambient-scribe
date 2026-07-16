/**
 * Semantic copy for the transcript and the draft note (M03).
 *
 * Clinicians paste transcript and note text into the clinical record, so what
 * lands on the clipboard must be built from application state - never from
 * container textContent, which harvests control chrome (provenance counts
 * like "11/1/2/6"), drops CSS-only spacing, and loses review meaning. The
 * pure serializers here produce the approved default export: the note always
 * travels with its source/review status lines, and the transcript keeps
 * per-row timestamps, speakers, and exact wording.
 */

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
 * @param {number} seconds - spoken row start; missing or invalid means the row
 *   had no usable timing and pastes as 00:00 rather than breaking the line.
 * @returns {string} zero-padded MM:SS text.
 */
function formatCopyTimestamp(seconds) {
    // A row without usable timing still needs a readable stamp in the paste.
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
 * Builds the axes shape for every state where no honest note exists yet.
 * Use for waiting/generating/blocked states: only the source axis speaks,
 * and the Copy summary action stays disabled.
 *
 * @param {string|null} sourceLine - lifecycle wording to show; null hides the
 *   whole strip (a transient failure keeps the existing failure UI instead).
 * @returns {object} axes with no review lines and noteAvailable false.
 */
function noteUnavailableAxes(sourceLine) {
    return {
        sourceLine,
        automatedReviewLine: null,
        clinicianReviewLine: null,
        reviewReasons: [],
        noteAvailable: false,
    };
}

/**
 * Reads which review flags one schema-v2 claim carries.
 * Use for the axes counts and the export markers so both always agree.
 *
 * @param {object} claim - v2 claim; missing fields mean that flag is absent.
 * @returns {object} {uncited, reasonFlagged, wordingReview} booleans.
 */
function claimReviewFlags(claim) {
    return {
        // An uncited claim has no transcript evidence at all - review required.
        uncited: (claim?.evidence_basis ?? 'none') === 'none',
        // Deterministic review reasons (M05 lanes, quote mismatch) travel per claim.
        reasonFlagged: (claim?.review_reasons ?? []).length > 0,
        // The M03 threshold cue: cited wording is predominantly low-confidence.
        wordingReview: claim?.wording_review === true,
    };
}

/**
 * Walks every claim of a schema-v2 note and tallies its review flags.
 * Use to build the status model for the axes and the copied header: the
 * renderer and the clipboard must count from the same payload walk.
 *
 * @param {object} summaryPayload - v2 note payload; missing arrays act empty.
 * @returns {object} additive per-flag claim counts plus the note-level
 *   review reason details; all zero/empty means no automated flags.
 */
function v2ReviewCountsFrom(summaryPayload) {
    const allClaims = [
        ...(summaryPayload?.sections ?? []).flatMap((section) => section.claims ?? []),
        ...(summaryPayload?.key_points ?? []),
    ];
    let uncitedClaimCount = 0;
    let reasonFlaggedClaimCount = 0;
    let wordingReviewClaimCount = 0;

    // Counts are additive per flag (a claim can carry more than one), so the
    // "(n)" total always equals the breakdown lines beneath it.
    for (const claim of allClaims) {
        const claimFlags = claimReviewFlags(claim);
        uncitedClaimCount += claimFlags.uncited ? 1 : 0;
        reasonFlaggedClaimCount += claimFlags.reasonFlagged ? 1 : 0;
        wordingReviewClaimCount += claimFlags.wordingReview ? 1 : 0;
    }

    return {
        uncitedClaimCount,
        reasonFlaggedClaimCount,
        wordingReviewClaimCount,
        // Note-level findings (e.g. an unrepresented emergency-plan step) are
        // readable sentences the reviewer sees verbatim.
        noteReviewReasons: (summaryPayload?.note_review_reasons ?? []).map(
            (noteReason) => String(noteReason.detail || noteReason.reason || '')
        ).filter((reasonText) => reasonText !== ''),
    };
}

/**
 * Collects the automated-review reasons and flagged-item total for one note.
 * Use when a note has rendered: the visible "(n)" must always equal the
 * breakdown beneath it - "(2)" above a 1+3 item list misled the reader.
 *
 * @param {object} statusModel - see deriveNoteStatusLines; missing counts act as zero.
 *   v1 notes fill unverifiedCount/lowConfidenceCount; v2 notes fill the
 *   claim-scoped counts from v2ReviewCountsFrom. Absent fields act as zero.
 * @returns {object} {reviewReasons, flaggedItemCount}; both empty/zero means
 *   the note carries no automated flags at all.
 */
function automatedReviewSummaryFor(statusModel) {
    // A missing model means a note somehow rendered without state; zero flags.
    const {
        unverifiedCount = 0,
        lowConfidenceCount = 0,
        uncitedClaimCount = 0,
        reasonFlaggedClaimCount = 0,
        wordingReviewClaimCount = 0,
        noteReviewReasons = [],
        roleSettlement,
        sourceState,
    } = statusModel ?? {};
    const reviewReasons = [];
    let flaggedItemCount = unverifiedCount + lowConfidenceCount
        + uncitedClaimCount + reasonFlaggedClaimCount + wordingReviewClaimCount
        + noteReviewReasons.length;

    // Unsupported statements are the first thing a reviewer should check.
    if (unverifiedCount > 0) {
        reviewReasons.push(
            `${countedNoun(unverifiedCount, 'statement')} not supported by the transcript`
        );
    }
    // Low-confidence wording flags sentences built on uncertain audio.
    if (lowConfidenceCount > 0) {
        reviewReasons.push(
            `${countedNoun(lowConfidenceCount, 'sentence')} from low-confidence wording`
        );
    }
    // Claims that cite nothing carry no evidence a reviewer could open.
    if (uncitedClaimCount > 0) {
        reviewReasons.push(
            `${countedNoun(uncitedClaimCount, 'claim')} without cited transcript evidence`
        );
    }
    // Claims with deterministic review reasons carry their exact wording in place.
    if (reasonFlaggedClaimCount > 0) {
        reviewReasons.push(
            `${countedNoun(reasonFlaggedClaimCount, 'claim')} flagged by automated review checks`
        );
    }
    // The claim-scoped M03 cue: cited wording came from uncertain audio.
    if (wordingReviewClaimCount > 0) {
        reviewReasons.push(
            `${countedNoun(wordingReviewClaimCount, 'claim')} from low-confidence wording`
        );
    }
    // Note-level findings read verbatim so the reviewer knows what to check.
    reviewReasons.push(...noteReviewReasons);
    // A frozen role settlement means speaker labels never finished settling.
    if (roleSettlement === 'failed_frozen') {
        reviewReasons.push('speaker labels were frozen before role checks completed');
        flaggedItemCount += 1;
    }
    // A live-fallback source is itself a review reason, even with no text flags.
    if (sourceState === 'whole_visit_live_fallback') {
        reviewReasons.push('note built from the live transcript, not the corrected pass');
        flaggedItemCount += 1;
    }

    return { reviewReasons, flaggedItemCount };
}

/**
 * Formats a count with its correctly pluralized noun for review wording.
 * Use in the review-reason lines so "1 statement" and "3 sentences" both
 * read naturally in the panel and the pasted note.
 *
 * @param {number} itemCount - flagged-item count; zero never reaches here
 *   because zero-count reasons are not emitted.
 * @param {string} noun - singular noun, e.g. 'statement'.
 * @returns {string} "<count> <noun>" with an s appended when plural.
 */
function countedNoun(itemCount, noun) {
    // One item keeps the singular noun; anything else pluralizes.
    return `${itemCount} ${noun}${itemCount === 1 ? '' : 's'}`;
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
 *   (a transient failure keeps the existing failure UI instead).
 */
function deriveNoteStatusLines(statusModel) {
    // A missing phase is treated as failure so nothing unavailable looks copyable.
    const phase = statusModel?.phase ?? 'failed';

    // The note request is in flight; the panel shows progress, nothing copyable.
    if (phase === 'generating') {
        return noteUnavailableAxes(SOURCE_AXIS_LINES.generating);
    }
    // The Stop wait released the UI but the backend has not attested the visit.
    if (phase === 'waiting') {
        return noteUnavailableAxes(SOURCE_AXIS_LINES.waiting);
    }
    // The backend refused the source; the user sees the specific reason.
    if (phase === 'blocked') {
        // Unknown blocked reasons stay on the safe "incomplete" wording.
        return noteUnavailableAxes(
            SOURCE_LINE_BY_BLOCKED_REASON[statusModel?.blockedReason]
                ?? SOURCE_AXIS_LINES.incomplete
        );
    }
    // Transient failures keep the existing failed badge and retry button.
    if (phase !== 'generated') {
        return noteUnavailableAxes(null);
    }

    // A fallback-sourced note announces itself; everything else is corrected.
    const sourceLine = statusModel?.sourceState === 'whole_visit_live_fallback'
        ? SOURCE_AXIS_LINES.live_fallback
        : SOURCE_AXIS_LINES.corrected;

    const { reviewReasons, flaggedItemCount } = automatedReviewSummaryFor(statusModel);
    // Any flagged item turns the axis into an explicit review demand.
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
    // Absent prose means the section has nothing the reader could mark.
    let markedContent = sectionContent ?? '';
    const unmatchedSentences = [];

    // Each flagged sentence is marked exactly where the reader sees the claim.
    for (const flaggedSentence of flaggedSentences ?? []) {
        // An exact match gets the marker inline, beside the claim itself.
        if (flaggedSentence && markedContent.includes(flaggedSentence)) {
            markedContent = markedContent.replace(
                flaggedSentence,
                `${flaggedSentence} ${markerText}`
            );
        } else if (flaggedSentence) {
            // The prose no longer contains this sentence; remember it so its
            // review state still reaches the pasted text.
            unmatchedSentences.push(flaggedSentence);
        }
    }

    // Sentences the prose lost still surface their review state at the end.
    for (const unmatchedSentence of unmatchedSentences) {
        markedContent += `\n${markerText} ${unmatchedSentence}`;
    }

    return markedContent;
}

/**
 * Builds the status header lines of the note export.
 * Use at the top of Copy summary output: the three axes always travel
 * with the text (the approved default export has no status-free variant).
 *
 * @param {object} noteModel - export model; missing status lines are skipped.
 * @returns {string[]} header lines ending with one blank spacer line.
 */
function noteStatusHeaderLines(noteModel) {
    const headerLines = [];
    const statusLines = noteModel?.statusLines ?? {};

    // Where the note's text came from (corrected transcript or fallback).
    if (statusLines.sourceLine) {
        headerLines.push(`Source: ${statusLines.sourceLine}`);
    }
    // What the automated checks flagged, with the actionable breakdown.
    if (statusLines.automatedReviewLine) {
        headerLines.push(`Automated review: ${statusLines.automatedReviewLine}`);
        // The reasons make "Review required (n)" actionable in the pasted text.
        for (const reviewReason of noteModel?.reviewReasons ?? []) {
            headerLines.push(`  - ${reviewReason}`);
        }
    }
    // The fixed reminder that no clinician has signed this text off.
    if (statusLines.clinicianReviewLine) {
        headerLines.push(`Clinician review: ${statusLines.clinicianReviewLine}`);
    }
    headerLines.push('');

    return headerLines;
}

/**
 * Builds the Key Points block of the note export.
 * Use after the status header: unverified points keep their marker exactly
 * where the panel shows it.
 *
 * @param {Array<object>} keyPoints - [{text, unverified}]; empty means the
 *   sections alone carry the note and no block is emitted.
 * @returns {string[]} block lines, or empty when there are no key points.
 */
function keyPointLines(keyPoints) {
    // No key points means the sections alone carry the generated note.
    if (keyPoints.length === 0) {
        return [];
    }

    const blockLines = ['Key Points:'];
    // Every point keeps its review marker in front of the claim it flags.
    for (const keyPoint of keyPoints) {
        // An unverified point is marked so the paste cannot read as checked fact.
        blockLines.push(
            keyPoint.unverified
                ? `- [Unverified] ${keyPoint.text}`
                : `- ${keyPoint.text}`
        );
    }
    blockLines.push('');

    return blockLines;
}

/**
 * Serializes the draft note into the approved default export.
 * Use for the Copy summary action: title, the three status axes, key
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

    // A missing title still gives the paste a recognizable heading.
    noteLines.push(noteModel?.title || 'Draft note', '');
    noteLines.push(...noteStatusHeaderLines(noteModel));
    noteLines.push(...keyPointLines(noteModel?.keyPoints ?? []));

    // Each SOAP section renders as heading + prose with review markers inline.
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
 * Maps one backend key point onto the export's marker-aware shape.
 * Use while building the note model so unverified points stay marked in copies.
 *
 * @param {string} keyPointText - one TL;DR line from the note payload.
 * @param {string[]} unverifiedKeyPoints - lines the checks could not support;
 *   empty means every point copies plain.
 * @returns {object} {text, unverified} consumed by keyPointLines.
 */
function keyPointModelFrom(keyPointText, unverifiedKeyPoints) {
    return {
        text: keyPointText,
        unverified: unverifiedKeyPoints.includes(keyPointText),
    };
}

/**
 * Maps one backend note section onto the export's section shape.
 * Use while building the note model: citations and any UI-only fields are
 * dropped here, so counts can never leak into the pasted text.
 *
 * @param {object} section - backend SOAP section; missing flag arrays mean
 *   the prose copies unmarked.
 * @returns {object} section model consumed by serializeDraftNote.
 */
function sectionModelFrom(section) {
    return {
        heading: section.heading,
        content: section.content,
        unverifiedSentences: section.unverified ?? [],
        lowConfidenceSentences: section.low_confidence ?? [],
    };
}

/**
 * Serializes one schema-v2 claim for the export, review meaning inline.
 * Use while building the v2 note model: markers are real text beside the
 * claim they flag, exactly where the panel shows them, and citation counts
 * or disclosure text can never appear because only claim state is read.
 *
 * @param {object} claim - v2 claim; missing flag fields copy the text plain.
 * @returns {string} claim text followed by its plain-text review markers.
 */
function v2ClaimExportText(claim) {
    const exportParts = [String(claim?.text ?? '')];
    const claimFlags = claimReviewFlags(claim);

    // An absence-based statement says so, or the paste reads as observed fact.
    if (claim?.evidence_basis === 'transcript_absence') {
        exportParts.push('[Based on transcript absence]');
    }
    // An uncited claim is marked with why it needs review, not a bare word.
    if (claimFlags.uncited) {
        exportParts.push('[No cited evidence — review]');
    }
    // Each deterministic reason travels with its exact explanatory wording.
    for (const reviewReason of claim?.review_reasons ?? []) {
        const reasonText = String(reviewReason.detail || reviewReason.reason || '');

        // A reason without wording still may not vanish from the paste.
        exportParts.push(`[Review: ${reasonText || 'automated review flag'}]`);
    }
    // The claim-scoped low-confidence cue keeps the established M03 marker.
    if (claimFlags.wordingReview) {
        exportParts.push('[Low confidence]');
    }

    return exportParts.join(' ');
}

/**
 * Maps a backend summary payload onto the export's note model.
 * Use before serializing: only clinical text and review meaning survive the
 * mapping, so the export cannot leak counts no matter how the panel renders.
 *
 * @param {object} summaryPayload - backend note payload (v1 or v2); missing arrays act empty.
 * @param {object} statusLines - output of deriveNoteStatusLines for this note.
 * @returns {object} noteModel accepted by serializeDraftNote.
 */
function noteModelFromSummaryPayload(summaryPayload, statusLines) {
    // A missing payload still yields a valid, clearly empty note model.
    const {
        title,
        unverified_key_points: unverifiedKeyPoints = [],
        key_points: keyPoints = [],
        sections = [],
    } = summaryPayload ?? {};

    // A v2 note copies as claim prose: ordered claims joined per section,
    // review markers already inline, so the shared serializer adds none.
    if (summaryPayload?.schema_version === 2) {
        return {
            title: title || 'Draft note',
            statusLines,
            reviewReasons: statusLines?.reviewReasons ?? [],
            keyPoints: keyPoints.map((claim) => ({
                text: v2ClaimExportText(claim),
                unverified: false,
            })),
            sections: sections.map((section) => ({
                heading: section.heading,
                content: (section.claims ?? []).map(v2ClaimExportText).join(' '),
                unverifiedSentences: [],
                lowConfidenceSentences: [],
            })),
        };
    }

    return {
        title: title || 'Draft note',
        statusLines,
        reviewReasons: statusLines?.reviewReasons ?? [],
        // Key points and sections are reduced to clinical text + review flags.
        keyPoints: keyPoints.map(
            (keyPointText) => keyPointModelFrom(keyPointText, unverifiedKeyPoints)
        ),
        sections: sections.map(sectionModelFrom),
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
 * @param {object} segment - visible row {segment_id, speaker_id}; a missing
 *   row ID means no per-row correction can apply and the card mapping decides.
 * @returns {string} Doctor, Patient, Unknown, or the raw speaker id when no
 *   role was ever mapped.
 */
function effectiveSpeakerLabelForCopy(segment) {
    // The clinician's own row correction wins, then automatic exceptions,
    // then the visit-wide speaker mapping.
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
 *
 * @param {string} copyText - the finished export text.
 * @param {HTMLElement|null} copyButton - button that shows "Copied"; null
 *   (test pages) skips the confirmation but still writes the clipboard.
 * @returns {Promise<void>} resolves once the text is on the clipboard.
 */
async function copyTextToClipboard(copyText, copyButton) {
    try {
        // Example: the clinician clicks Copy summary after reviewing the panel.
        await navigator.clipboard.writeText(copyText);
    } catch (clipboardError) {
        // Example: a non-secure context or denied permission rejects the API;
        // the hidden-textarea path still gets the text onto the clipboard.
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
        // The resting label is kept so repeated copies always restore it.
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
 *
 * @param {HTMLElement|null} copyButton - the clicked button; null skips the
 *   visible "Copied" confirmation.
 * @returns {Promise<void>} resolves once the lane text is on the clipboard.
 */
async function copyActiveTranscriptLane(copyButton) {
    // The tab's own selection logic decides corrected-vs-live; reuse it.
    const correctedRows = await fetchCorrectedTranscriptRows();
    const isCorrectedLane = Array.isArray(correctedRows) && correctedRows.length > 0;
    // Without a corrected artifact the user is reading the live rows.
    const laneRows = isCorrectedLane ? correctedRows : readVisibleTranscriptSegments();

    // The header names the lane so the paste says whether it fed the note.
    const laneLabel = isCorrectedLane
        ? TRANSCRIPT_LANE_LABELS.corrected
        : transcriptLaneLabelForLiveRows();

    // Each stored row becomes one export line with its effective speaker.
    const rowModels = laneRows.map((laneRow) => ({
        startSeconds: parseFloat(laneRow.start) || 0,
        // Corrected rows carry their own role; live rows resolve overrides.
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
 *
 * @returns {string} the lane wording shown beside and copied with live rows.
 */
function transcriptLaneLabelForLiveRows() {
    // A live-fallback note means these live rows ARE the note's source.
    if (latestRenderedSummaryPayload?.source_state === 'whole_visit_live_fallback') {
        return TRANSCRIPT_LANE_LABELS.live_fallback;
    }
    return TRANSCRIPT_LANE_LABELS.live;
}

/**
 * Copies the rendered summary using the approved default export.
 * Use from the Copy summary button; disabled states never reach here
 * because the button is only enabled once a note artifact exists.
 *
 * @param {HTMLElement|null} copyButton - the clicked button; null skips the
 *   visible "Copied" confirmation.
 * @returns {Promise<void>} resolves once the note text is on the clipboard.
 */
async function copySummary(copyButton) {
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
        claimReviewFlags,
        deriveNoteStatusLines,
        formatCopyTimestamp,
        markFlaggedSentencesForCopy,
        noteModelFromSummaryPayload,
        serializeDraftNote,
        serializeTranscriptRows,
        v2ClaimExportText,
        v2ReviewCountsFrom,
    };
}
