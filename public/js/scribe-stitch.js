// =========================================================================
// Ambient Scribe transcript stitching (display transform, summary UX).
// Merges adjacent same-speaker corrected rows into utterance blocks for the
// Transcript tab and provenance popovers. Display-only: input rows are never
// mutated, and every block keeps its constituent segment IDs so section
// citations still resolve against stitched output.
// Loaded as a classic script; the module.exports guard exists for node:test.
// =========================================================================

// Rows merge when the same speaker continues within this many seconds.
// 2.0 s is the chosen default; tests/js/scribe-stitch.test.js pins the behaviour.
const STITCH_GAP_SECONDS = 2.0;

/**
 * Returns a finite number, or null when storage had no usable timestamp.
 * A row without timing can still render, but it never merges - the gap to
 * its neighbours is unknowable.
 */
function stitchTimeOrNull(rowTime) {
    return typeof rowTime === 'number' && Number.isFinite(rowTime) ? rowTime : null;
}

/**
 * Decides whether a row continues the previous display block.
 * The stitch key is the displayed speaker label (role): the Transcript tab
 * and popovers label blocks by role, so role equality is what "same
 * speaker" means on screen. Missing timestamps or a gap above
 * STITCH_GAP_SECONDS keep rows apart; overlapping rows count as contiguous.
 */
function continuesBlock(block, segment) {
    if ((segment.role || 'UNKNOWN') !== block.role) {
        return false;
    }
    const start = stitchTimeOrNull(segment.start);
    if (block.end === null || start === null) {
        return false;
    }
    return start - block.end <= STITCH_GAP_SECONDS;
}

/**
 * Merges adjacent same-speaker transcript rows into display blocks.
 * Use on corrected transcript rows before rendering the Transcript tab or a
 * provenance popover. Rows without text are dropped - they cannot help the
 * clinician and would render as blank blocks.
 */
function stitchTranscriptSegments(segments) {
    const blocks = [];
    for (const segment of segments || []) {
        const text = String(segment.text || '').trim();
        if (text === '') {
            continue;
        }
        const previous = blocks[blocks.length - 1];
        if (previous && continuesBlock(previous, segment)) {
            previous.text += ' ' + text;
            const end = stitchTimeOrNull(segment.end);
            if (end !== null) {
                previous.end = end;
            }
            previous.segmentIds.push(String(segment.segment_id || ''));
            continue;
        }
        blocks.push({
            role: segment.role || 'UNKNOWN',
            start: stitchTimeOrNull(segment.start),
            end: stitchTimeOrNull(segment.end),
            text: text,
            segmentIds: [String(segment.segment_id || '')],
        });
    }
    return blocks;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { STITCH_GAP_SECONDS, stitchTranscriptSegments };
}
