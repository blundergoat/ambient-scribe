// Unit tests for the display-only stitching transform (summary UX plan).
// Run with: npm run test:js (node --test, no dependencies).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
    STITCH_GAP_SECONDS,
    stitchTranscriptSegments,
} = require('../../public/js/scribe-stitch.js');

// Builds a corrected-transcript row in the storage shape (segment_id,
// speaker_id, role, start, end, text) so tests mirror real payloads.
function row(segmentId, role, start, end, text) {
    return {
        segment_id: segmentId,
        speaker_id: role === 'DOCTOR' ? 'speaker_1' : 'speaker_0',
        role: role,
        start: start,
        end: end,
        text: text,
    };
}

test('empty and missing input produce no blocks', () => {
    assert.deepEqual(stitchTranscriptSegments([]), []);
    assert.deepEqual(stitchTranscriptSegments(null), []);
    assert.deepEqual(stitchTranscriptSegments(undefined), []);
});

test('single segment passes through as one block', () => {
    const blocks = stitchTranscriptSegments([
        row('corrected-0001', 'PATIENT', 3.12, 3.65, 'Okay. Oh, I can do'),
    ]);
    assert.deepEqual(blocks, [
        {
            role: 'PATIENT',
            start: 3.12,
            end: 3.65,
            text: 'Okay. Oh, I can do',
            segmentIds: ['corrected-0001'],
        },
    ]);
});

test('three-way merge keeps IDs in order and spans first start to last end', () => {
    const blocks = stitchTranscriptSegments([
        row('corrected-0001', 'PATIENT', 1.0, 2.0, 'I think it started'),
        row('corrected-0002', 'PATIENT', 3.0, 4.0, 'so my skin’s quite red'),
        row('corrected-0003', 'PATIENT', 5.5, 6.0, 'and itchy at night.'),
    ]);
    assert.equal(blocks.length, 1);
    assert.equal(blocks[0].text, 'I think it started so my skin’s quite red and itchy at night.');
    assert.deepEqual(blocks[0].segmentIds, ['corrected-0001', 'corrected-0002', 'corrected-0003']);
    const firstRowStart = 1.0;
    const lastRowEnd = 6.0;
    assert.equal(blocks[0].start, firstRowStart);
    assert.equal(blocks[0].end, lastRowEnd);
});

test('no merge across speakers even with a tiny gap', () => {
    const blocks = stitchTranscriptSegments([
        row('corrected-0001', 'DOCTOR', 1.0, 2.0, 'How can I help?'),
        row('corrected-0002', 'PATIENT', 2.1, 3.0, 'My skin is itchy.'),
    ]);
    assert.equal(blocks.length, 2);
    assert.deepEqual(blocks.map((b) => b.role), ['DOCTOR', 'PATIENT']);
});

test('no merge across a gap above the threshold', () => {
    const blocks = stitchTranscriptSegments([
        row('corrected-0001', 'PATIENT', 1.0, 2.0, 'It started last week.'),
        row('corrected-0002', 'PATIENT', 2.0 + STITCH_GAP_SECONDS + 0.01, 5.0, 'Then it spread.'),
    ]);
    assert.equal(blocks.length, 2);
});

test('gap exactly at the threshold merges (at or below)', () => {
    const blocks = stitchTranscriptSegments([
        row('corrected-0001', 'PATIENT', 1.0, 2.0, 'It started last week.'),
        row('corrected-0002', 'PATIENT', 2.0 + STITCH_GAP_SECONDS, 5.0, 'Then it spread.'),
    ]);
    assert.equal(blocks.length, 1);
});

test('overlapping rows count as contiguous', () => {
    const blocks = stitchTranscriptSegments([
        row('corrected-0001', 'DOCTOR', 1.0, 3.0, 'And when you say'),
        row('corrected-0002', 'DOCTOR', 2.5, 4.0, 'itchy, is it constant?'),
    ]);
    assert.equal(blocks.length, 1);
    const overlappingRowEnd = 4.0;
    assert.equal(blocks[0].end, overlappingRowEnd);
});

test('missing timestamps prevent merging but rows still render', () => {
    const blocks = stitchTranscriptSegments([
        row('corrected-0001', 'PATIENT', 1.0, null, 'First part'),
        row('corrected-0002', 'PATIENT', 1.5, 2.0, 'second part'),
        row('corrected-0003', 'PATIENT', undefined, 3.0, 'third part'),
    ]);
    assert.equal(blocks.length, 3);
    assert.deepEqual(blocks.map((b) => b.text), ['First part', 'second part', 'third part']);
    assert.equal(blocks[0].end, null);
    assert.equal(blocks[2].start, null);
});

test('blank rows are dropped and do not break a stitch chain decision', () => {
    const blocks = stitchTranscriptSegments([
        row('corrected-0001', 'PATIENT', 1.0, 2.0, 'Before the blank'),
        row('corrected-0002', 'PATIENT', 2.1, 2.2, '   '),
        row('corrected-0003', 'PATIENT', 2.4, 3.0, 'after the blank.'),
    ]);
    assert.equal(blocks.length, 1);
    assert.deepEqual(blocks[0].segmentIds, ['corrected-0001', 'corrected-0003']);
});

test('input rows are never mutated (display-only transform)', () => {
    const first = row('corrected-0001', 'PATIENT', 1.0, 2.0, 'One');
    const second = row('corrected-0002', 'PATIENT', 2.5, 3.0, 'two');
    const snapshot = JSON.stringify([first, second]);
    stitchTranscriptSegments([first, second]);
    assert.equal(JSON.stringify([first, second]), snapshot);
});

test('rows without a role stitch under the UNKNOWN label', () => {
    const blocks = stitchTranscriptSegments([
        { segment_id: 'corrected-0001', start: 1.0, end: 2.0, text: 'Unlabelled' },
        { segment_id: 'corrected-0002', start: 2.5, end: 3.0, text: 'continues', role: '' },
    ]);
    assert.equal(blocks.length, 1);
    assert.equal(blocks[0].role, 'UNKNOWN');
});
