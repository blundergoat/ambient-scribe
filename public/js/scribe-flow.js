// =========================================================================
// Ambient Scribe temporal-flow classification for the two-column transcript.
// Vertical spacing between turn cards carries time: silence longer than a
// few seconds becomes a visible labelled gap, and a turn that starts before
// the previous one finished is marked as talking-over. Pure functions only —
// the DOM pass in scribe-transcript.js applies the classes this returns.
// =========================================================================

// Silence between turns must exceed this before the transcript shows a gap.
const FLOW_SILENCE_GAP_SECONDS = 3;
// Consecutive turns within this tolerance count as normal turn-taking, not
// overlap — NeMo row boundaries jitter by roughly a hundred milliseconds.
const FLOW_OVERLAP_TOLERANCE_SECONDS = 0.15;

/**
 * Classifies the time relation of each transcript card to the card above it.
 * Use after any card is added or re-timed; the first card never has a flow
 * marker because there is nothing above it.
 *
 * @param {Array<{start: number, end: number}>} cardIntervals - spoken-time
 *   bounds per card in display order; non-finite bounds mark that pair plain.
 * @returns {Array<{kind: 'gap'|'overlap'|null, seconds: number}>} one entry
 *   per card describing the boundary it shares with the previous card.
 */
function classifyTranscriptFlow(cardIntervals) {
    // Missing input classifies as an empty transcript, never throws.
    if (!Array.isArray(cardIntervals)) {
        return [];
    }

    return cardIntervals.map((interval, cardIndex) => {
        const previousInterval = cardIntervals[cardIndex - 1];

        // The first card, or a pair with unmeasured bounds, stays plain.
        if (
            !previousInterval
            || !Number.isFinite(interval?.start)
            || !Number.isFinite(previousInterval?.end)
        ) {
            return { kind: null, seconds: 0 };
        }

        const silenceSeconds = interval.start - previousInterval.end;

        // Nobody spoke for a noticeable stretch between the two turns.
        if (silenceSeconds > FLOW_SILENCE_GAP_SECONDS) {
            return { kind: 'gap', seconds: silenceSeconds };
        }

        // This turn began while the previous one was still being spoken.
        if (silenceSeconds < -FLOW_OVERLAP_TOLERANCE_SECONDS) {
            // Overlap lasts until the earlier of the two turn ends, so a short
            // interjection inside a long turn reports its own duration.
            const overlapSeconds = Number.isFinite(interval.end)
                ? Math.min(previousInterval.end, interval.end) - interval.start
                : -silenceSeconds;
            return { kind: 'overlap', seconds: Math.max(overlapSeconds, 0) };
        }

        return { kind: null, seconds: 0 };
    });
}

/**
 * Formats one flow entry as the short label the transcript shows on the card.
 * Use with classifyTranscriptFlow output; plain entries return null so the
 * caller removes any stale marker.
 *
 * @param {{kind: 'gap'|'overlap'|null, seconds: number}|null|undefined} flowEntry -
 *   classified boundary; null or plain means the card needs no label.
 * @returns {string|null} readable marker text, or null for no marker.
 */
function transcriptFlowNote(flowEntry) {
    if (!flowEntry || !flowEntry.kind) {
        return null;
    }

    // Silence reads in whole seconds; sub-second precision is jitter here.
    if (flowEntry.kind === 'gap') {
        return `${Math.round(flowEntry.seconds)}s silence`;
    }

    return `overlap · ${flowEntry.seconds.toFixed(1)}s`;
}

// Node tests exercise the classification without creating a browser DOM.
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        FLOW_OVERLAP_TOLERANCE_SECONDS,
        FLOW_SILENCE_GAP_SECONDS,
        classifyTranscriptFlow,
        transcriptFlowNote,
    };
}
