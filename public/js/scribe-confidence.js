// =========================================================================
// Ambient Scribe wording-confidence presentation for transcripts and notes.
// Clinicians see a quiet review cue only when acoustic evidence falls below
// the corpus-derived lane threshold. This module keeps threshold decisions,
// accessible local row markers, and note review cues consistent across the
// live transcript, corrected transcript, source views, and generated note.
// =========================================================================

// Empirical threshold: `<0.76` marks 18.3% of live rows and limits correct-row cues to 9.9%.
const LIVE_TRANSCRIPT_REVIEW_THRESHOLD = 0.76;
// Empirical threshold: `<0.78` catches both corrected calf/carp rows without a warning wall.
const CORRECTED_TRANSCRIPT_REVIEW_THRESHOLD = 0.78;
const LIVE_TRANSCRIPT_LANE = 'live';
const CORRECTED_TRANSCRIPT_LANE = 'corrected';
const REVIEW_WORDING_CLASS = 'transcript-wording--review';
const REVIEW_WORDING_HELP_ID = 'confidenceWordingHelp';
const REVIEW_WORDING_MESSAGE = 'Lower-confidence transcription — double-check this wording.';

/**
 * Returns the corpus-derived review threshold for the transcript view the clinician opened.
 * Unknown or empty lanes return null so the UI leaves wording unchanged.
 *
 * @param {string} transcriptLane - `live` or `corrected`; empty means no trusted threshold is available.
 * @returns {number|null} lane threshold, or null when the UI should not style the row.
 */
function reviewThresholdForTranscriptLane(transcriptLane) {
    // The live view uses the more conservative threshold measured against live rows.
    if (transcriptLane === LIVE_TRANSCRIPT_LANE) {
        return LIVE_TRANSCRIPT_REVIEW_THRESHOLD;
    }

    // The corrected view uses the threshold that catches the retained calf/carp rows.
    if (transcriptLane === CORRECTED_TRANSCRIPT_LANE) {
        return CORRECTED_TRANSCRIPT_REVIEW_THRESHOLD;
    }

    // An unknown view must stay visually unchanged instead of borrowing the wrong calibration.
    return null;
}

/**
 * Decides whether one displayed transcript row needs the quiet wording-review cue.
 * Use while rendering a row; missing confidence means the UI stays exactly as before.
 *
 * @param {number|null|undefined} rowConfidence - acoustic row confidence; null or absent means unmeasured.
 * @param {string} transcriptLane - visible lane; empty or unknown leaves the row unstyled.
 * @returns {boolean} true only when a measured value is strictly below its lane threshold.
 */
function shouldReviewTranscriptWording(rowConfidence, transcriptLane) {
    const reviewThreshold = reviewThresholdForTranscriptLane(transcriptLane);

    // Unmeasured rows and unknown views carry no honest basis for a warning.
    if (!Number.isFinite(rowConfidence) || reviewThreshold === null) {
        return false;
    }

    return rowConfidence < reviewThreshold;
}

/**
 * Adds the accessible local review cue to one transcript row when its wording is uncertain.
 * Use as live or corrected text enters the page; false means the row stays visually unchanged.
 *
 * @param {HTMLElement|null} rowElement - visible wording span; null means no row reached the UI.
 * @param {number|null|undefined} rowConfidence - stored acoustic confidence; absent means no cue.
 * @param {string} transcriptLane - lane shown to the user; unknown means no cue.
 * @returns {boolean} true when the row was marked, false when it remains unchanged.
 */
function markTranscriptWordingForReview(rowElement, rowConfidence, transcriptLane) {
    // A missing element or an above-threshold row should not gain review semantics.
    if (!rowElement || !shouldReviewTranscriptWording(rowConfidence, transcriptLane)) {
        return false;
    }

    rowElement.classList.add(REVIEW_WORDING_CLASS);
    rowElement.tabIndex = 0;
    rowElement.setAttribute('aria-describedby', REVIEW_WORDING_HELP_ID);
    rowElement.dataset.confidenceTooltip = REVIEW_WORDING_MESSAGE;
    return true;
}

/**
 * Builds note prose with fidelity and transcription-confidence markers in exact reading order.
 * Use when a generated section arrives; empty marker lists preserve one plain text node.
 *
 * @param {string} content - generated section prose; empty renders an empty text node.
 * @param {string[]} unverifiedSentences - unsupported sentences; empty adds no fidelity marker.
 * @param {string[]} lowConfidenceSentences - uncertain-source sentences; empty adds no wording marker.
 * @returns {Array<Node>} safe text and marker nodes; empty content still returns one text node.
 */
function createNoteProseNodes(
    content,
    unverifiedSentences,
    lowConfidenceSentences = [],
) {
    const unverifiedSentenceSet = new Set(unverifiedSentences);
    const lowConfidenceSentenceSet = new Set(lowConfidenceSentences);

    // A fully supported section renders byte-identical plain text, exactly as before M03.
    if (unverifiedSentenceSet.size === 0 && lowConfidenceSentenceSet.size === 0) {
        return [document.createTextNode(content)];
    }

    // One ordered search list lets a sentence carry either or both review reasons.
    const sentencesForReview = [
        ...new Set([...unverifiedSentenceSet, ...lowConfidenceSentenceSet]),
    ];
    const proseNodes = [];
    let remainingProse = content;

    // Walk the prose start to end so each marker lands where the clinician meets the claim.
    while (remainingProse.length > 0) {
        let earliestSentenceIndex = -1;
        let earliestSentence = '';

        // Every flagged sentence competes by its next exact position in the remaining prose.
        for (const sentenceForReview of sentencesForReview) {
            const sentenceIndex = sentenceForReview
                ? remainingProse.indexOf(sentenceForReview)
                : -1;

            // The next visible marker belongs to the earliest remaining flagged sentence.
            if (
                sentenceIndex !== -1
                && (earliestSentenceIndex === -1 || sentenceIndex < earliestSentenceIndex)
            ) {
                earliestSentenceIndex = sentenceIndex;
                earliestSentence = sentenceForReview;
            }
        }

        // No flagged sentence remains, so the rest of the generated prose stays plain.
        if (earliestSentenceIndex === -1) {
            proseNodes.push(document.createTextNode(remainingProse));
            break;
        }

        // Wording before the marked claim remains an ordinary text node.
        if (earliestSentenceIndex > 0) {
            proseNodes.push(
                document.createTextNode(remainingProse.slice(0, earliestSentenceIndex))
            );
        }

        proseNodes.push(createSummarySentenceReviewMarker(
            earliestSentence,
            unverifiedSentenceSet.has(earliestSentence),
            lowConfidenceSentenceSet.has(earliestSentence),
        ));
        remainingProse = remainingProse.slice(
            earliestSentenceIndex + earliestSentence.length
        );
    }

    return proseNodes;
}

/**
 * Wraps one generated sentence with the review reasons the clinician should see.
 * Use for fidelity, low transcription confidence, or both without rewriting prose.
 *
 * @param {string} sentence - exact generated sentence; empty is not expected by the renderer.
 * @param {boolean} isUnverified - true when deterministic fidelity checks found no support.
 * @param {boolean} hasLowConfidenceSource - true when cited corrected wording is predominantly low.
 * @returns {HTMLElement} focusable sentence span carrying one or both review explanations.
 */
function createSummarySentenceReviewMarker(
    sentence,
    isUnverified,
    hasLowConfidenceSource,
) {
    const markerClasses = [];
    const markerReasons = [];

    // Unsupported clinical claims retain the established M07 marker and wording.
    if (isUnverified) {
        markerClasses.push('summary-unverified');
        markerReasons.push('Unverified against transcript');
    }

    // Predominantly uncertain source rows add the M03 transcription wording cue.
    if (hasLowConfidenceSource) {
        markerClasses.push('summary-low-confidence');
        markerReasons.push('Low-confidence transcription');
    }

    const sentenceMarker = createElement('span', {
        className: markerClasses.join(' '),
        text: sentence,
        attributes: {
            title: markerReasons.join('; '),
        },
    });

    // Low-confidence wording is keyboard-reviewable and shares the transcript help text.
    if (hasLowConfidenceSource) {
        sentenceMarker.tabIndex = 0;
        sentenceMarker.setAttribute('aria-describedby', REVIEW_WORDING_HELP_ID);
        sentenceMarker.dataset.confidenceTooltip = 'Low-confidence transcription';
    }

    return sentenceMarker;
}

/**
 * Preserves the established marker API for unverified key points and older callers.
 * Use when fidelity alone rejected a displayed sentence.
 *
 * @param {string} sentence - exact unsupported sentence; empty renders an empty marked span.
 * @returns {HTMLElement} sentence marked "Unverified against transcript".
 */
function createUnverifiedMarker(sentence) {
    return createSummarySentenceReviewMarker(sentence, true, false);
}

// Node tests exercise threshold behavior without creating a browser DOM.
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        CORRECTED_TRANSCRIPT_REVIEW_THRESHOLD,
        LIVE_TRANSCRIPT_REVIEW_THRESHOLD,
        reviewThresholdForTranscriptLane,
        shouldReviewTranscriptWording,
    };
}
