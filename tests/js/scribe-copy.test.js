// Unit coverage for the M03 semantic copy serializers and status axes.
// These pin the two observed paste-leak specimens (consult 1.2's "11/1/2/6"
// and consult 5.3's "14/1/1/7" provenance counts) and the contract wording
// for the three status axes. They run without a DOM via `npm run test:js`.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
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
} = require('../../public/js/scribe-copy.js');

// Consult 1.2's shape: four cited sections whose count buttons pasted as 11/1/2/6.
function consult12StylePayload() {
    return {
        title: 'Session Summary',
        key_points: ['Sore red skin on both forearms.'],
        unverified_key_points: [],
        sections: [
            {
                heading: 'Subjective',
                content: 'Patient reports sore, red skin for two weeks.',
                citations: new Array(11).fill({ segment_id: 'seg-x', text: 'row' }),
            },
            {
                heading: 'Objective',
                content: 'Erythema noted on both forearms.',
                citations: new Array(1).fill({ segment_id: 'seg-y', text: 'row' }),
            },
            {
                heading: 'Assessment',
                content: 'Likely contact dermatitis.',
                citations: new Array(2).fill({ segment_id: 'seg-z', text: 'row' }),
            },
            {
                heading: 'Plan',
                content: 'Emollients and follow-up in two weeks.',
                citations: new Array(6).fill({ segment_id: 'seg-w', text: 'row' }),
            },
        ],
    };
}

test('transcript export keeps timestamp, speaker, and exact row boundaries', () => {
    const exportText = serializeTranscriptRows(
        [
            { startSeconds: 0, speakerLabel: 'Doctor', text: 'How long have the headaches lasted?' },
            { startSeconds: 62.4, speakerLabel: 'Doctor', text: 'About two weeks now, mostly mornings.' },
            { startSeconds: 65.1, speakerLabel: 'Patient', text: 'Any visual changes with them?' },
        ],
        TRANSCRIPT_LANE_LABELS.corrected
    );

    assert.ok(exportText.startsWith('Transcript — Corrected transcript — used for note\n'));
    assert.ok(exportText.includes('[00:00] Doctor: How long have the headaches lasted?'));
    assert.ok(exportText.includes('[01:02] Doctor: About two weeks now, mostly mornings.'));
    assert.ok(exportText.includes('[01:05] Patient: Any visual changes with them?'));
    // Adjacent same-speaker rows stay on separate lines, never re-stitched.
    assert.equal(exportText.split('\n').filter((line) => line.startsWith('[')).length, 3);
});

test('timestamps degrade safely when a row has no usable start time', () => {
    assert.equal(formatCopyTimestamp(undefined), '00:00');
    assert.equal(formatCopyTimestamp(Number.NaN), '00:00');
    assert.equal(formatCopyTimestamp(605), '10:05');
});

test('consult 1.2 note export contains no provenance count sequence', () => {
    const statusLines = deriveNoteStatusLines({
        phase: 'generated',
        sourceState: 'whole_visit_corrected',
    });
    const exportText = serializeDraftNote(
        noteModelFromSummaryPayload(consult12StylePayload(), statusLines)
    );

    // The pasted note once ended in "11/1/2/6" harvested from count buttons.
    assert.ok(!exportText.includes('11'));
    assert.ok(!exportText.includes('View source'));
    assert.ok(!exportText.includes('utterance'));
    assert.ok(exportText.includes('Plan:\nEmollients and follow-up in two weeks.'));
    assert.ok(exportText.includes(`Source: ${SOURCE_AXIS_LINES.corrected}`));
    assert.ok(exportText.includes(NO_FLAGS_LIMITATION_TEXT));
    assert.ok(exportText.includes(`Clinician review: ${NOT_CLINICIAN_REVIEWED_TEXT}`));
});

test('consult 5.3 note export keeps unresolved-review meaning without counts', () => {
    const summaryPayload = {
        title: 'Session Summary',
        key_points: ['Feeling very anxious for several months.'],
        unverified_key_points: ['Feeling very anxious for several months.'],
        sections: [
            {
                heading: 'Assessment',
                content: 'Palpitations are most likely associated with anxiety. Sleep is irregular.',
                unverified: ['Palpitations are most likely associated with anxiety.'],
                low_confidence: ['Sleep is irregular.'],
                citations: new Array(14).fill({ segment_id: 'seg-a', text: 'row' }),
            },
        ],
    };
    const statusLines = deriveNoteStatusLines({
        phase: 'generated',
        sourceState: 'whole_visit_corrected',
        unverifiedCount: 2,
        lowConfidenceCount: 1,
    });
    const exportText = serializeDraftNote(
        noteModelFromSummaryPayload(summaryPayload, statusLines)
    );

    // The observed "14/1/1/7"-style trailing counts must be impossible.
    assert.ok(!exportText.includes('14'));
    // Review meaning survives as plain text where the reader needs it.
    assert.ok(exportText.includes('- [Unverified] Feeling very anxious for several months.'));
    assert.ok(exportText.includes(
        'Palpitations are most likely associated with anxiety. [Unverified]'
    ));
    assert.ok(exportText.includes('Sleep is irregular. [Low confidence]'));
    // The count matches the flagged ITEMS (2 unverified + 1 low-confidence),
    // never the number of reason categories.
    assert.ok(exportText.includes('Automated review: Review required (3)'));
});

test('flagged sentences the prose no longer contains still surface review state', () => {
    const markedContent = markFlaggedSentencesForCopy(
        'Current prose without the flagged claim.',
        ['A claim that was edited away.'],
        '[Unverified]'
    );
    assert.ok(markedContent.includes('[Unverified] A claim that was edited away.'));
});

test('status axes speak the contract wording for every lifecycle state', () => {
    assert.equal(
        deriveNoteStatusLines({ phase: 'generating' }).sourceLine,
        'Generating'
    );
    assert.equal(
        deriveNoteStatusLines({ phase: 'waiting' }).sourceLine,
        'Source still finalizing — note unavailable'
    );
    assert.equal(
        deriveNoteStatusLines({ phase: 'blocked', blockedReason: 'source_not_terminal' }).sourceLine,
        'Source still finalizing — note unavailable'
    );
    assert.equal(
        deriveNoteStatusLines({ phase: 'blocked', blockedReason: 'stale_lineage' }).sourceLine,
        'Source incomplete — note unavailable'
    );
    assert.equal(
        deriveNoteStatusLines({ phase: 'blocked', blockedReason: 'correction_pending' }).sourceLine,
        'Correction unavailable — note unavailable'
    );
    assert.equal(
        deriveNoteStatusLines({ phase: 'blocked', blockedReason: 'source_exceeds_note_limit' }).sourceLine,
        'Source exceeds note limit — note unavailable'
    );
    // Unknown blocked reasons stay on the safe incomplete wording.
    assert.equal(
        deriveNoteStatusLines({ phase: 'blocked', blockedReason: 'later_reason' }).sourceLine,
        'Source incomplete — note unavailable'
    );
    // No unavailable state may expose a copyable note.
    for (const phase of ['generating', 'waiting', 'blocked', 'failed']) {
        assert.equal(deriveNoteStatusLines({ phase }).noteAvailable, false);
    }
});

test('generated notes always carry all three axes and fallback is review-required', () => {
    const corrected = deriveNoteStatusLines({
        phase: 'generated',
        sourceState: 'whole_visit_corrected',
    });
    assert.equal(corrected.sourceLine, 'Draft generated from corrected transcript');
    assert.ok(corrected.automatedReviewLine.startsWith('No automated review flags.'));
    assert.equal(corrected.clinicianReviewLine, NOT_CLINICIAN_REVIEWED_TEXT);
    assert.equal(corrected.noteAvailable, true);

    const fallback = deriveNoteStatusLines({
        phase: 'generated',
        sourceState: 'whole_visit_live_fallback',
    });
    assert.equal(fallback.sourceLine, 'Complete live fallback — review required');
    // A fallback source is itself an automated review reason.
    assert.equal(fallback.automatedReviewLine, 'Review required (1)');

    const frozen = deriveNoteStatusLines({
        phase: 'generated',
        sourceState: 'whole_visit_corrected',
        roleSettlement: 'failed_frozen',
    });
    assert.equal(frozen.automatedReviewLine, 'Review required (1)');
    assert.ok(frozen.reviewReasons[0].includes('frozen'));

    // The visible count totals flagged items so it matches its own breakdown:
    // "(2)" above a 1+3 reason list is exactly the confusion this prevents.
    const mixed = deriveNoteStatusLines({
        phase: 'generated',
        sourceState: 'whole_visit_corrected',
        unverifiedCount: 1,
        lowConfidenceCount: 3,
    });
    assert.equal(mixed.automatedReviewLine, 'Review required (4)');
    assert.equal(mixed.reviewReasons.length, 2);
});
