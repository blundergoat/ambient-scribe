// Unit tests for the temporal-flow classification behind the two-column
// transcript layout (on-demand-note rework). They pin the silence threshold, the
// overlap tolerance, and the visible marker wording without a browser DOM.
// Run with: npm run test:js (node --test, no dependencies).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
    FLOW_OVERLAP_TOLERANCE_SECONDS,
    FLOW_SILENCE_GAP_SECONDS,
    classifyTranscriptFlow,
    transcriptFlowNote,
} = require('../../public/js/scribe-flow.js');

test('empty and missing input classify as an empty transcript', () => {
    assert.deepEqual(classifyTranscriptFlow([]), []);
    assert.deepEqual(classifyTranscriptFlow(null), []);
    assert.deepEqual(classifyTranscriptFlow(undefined), []);
});

test('a single card and normal turn-taking stay plain', () => {
    const flows = classifyTranscriptFlow([
        { start: 0.0, end: 1.5 },
        { start: 2.0, end: 3.5 },
    ]);

    assert.equal(flows.length, 2);
    assert.equal(flows[0].kind, null);
    assert.equal(flows[1].kind, null);
});

test('silence at exactly the threshold is still normal turn-taking', () => {
    const flows = classifyTranscriptFlow([
        { start: 0.0, end: 2.0 },
        { start: 2.0 + FLOW_SILENCE_GAP_SECONDS, end: 6.5 },
    ]);

    assert.equal(flows[1].kind, null);
});

test('silence past the threshold classifies as a gap with its duration', () => {
    const flows = classifyTranscriptFlow([
        { start: 0.0, end: 1.4 },
        { start: 7.6, end: 9.0 },
    ]);

    assert.equal(flows[1].kind, 'gap');
    assert.ok(Math.abs(flows[1].seconds - 6.2) < 0.001);
    assert.equal(transcriptFlowNote(flows[1]), '6s silence');
});

test('a turn starting before the previous one ends is an overlap', () => {
    const flows = classifyTranscriptFlow([
        { start: 0.0, end: 4.0 },
        { start: 2.8, end: 5.5 },
    ]);

    assert.equal(flows[1].kind, 'overlap');
    assert.ok(Math.abs(flows[1].seconds - 1.2) < 0.001);
    assert.equal(transcriptFlowNote(flows[1]), 'overlap · 1.2s');
});

test('a short interjection inside a long turn reports its own duration', () => {
    const flows = classifyTranscriptFlow([
        { start: 0.0, end: 30.0 },
        { start: 10.0, end: 12.0 },
    ]);

    assert.equal(flows[1].kind, 'overlap');
    assert.ok(Math.abs(flows[1].seconds - 2.0) < 0.001);
});

test('row-boundary jitter inside the tolerance is not overlap', () => {
    const flows = classifyTranscriptFlow([
        { start: 0.0, end: 4.0 },
        { start: 4.0 - FLOW_OVERLAP_TOLERANCE_SECONDS, end: 6.0 },
    ]);

    assert.equal(flows[1].kind, null);
});

test('unmeasured bounds mark the pair plain instead of guessing', () => {
    const flows = classifyTranscriptFlow([
        { start: 0.0, end: Number.NaN },
        { start: 8.0, end: 9.0 },
        { start: Number.NaN, end: 12.0 },
    ]);

    assert.equal(flows[1].kind, null);
    assert.equal(flows[2].kind, null);
});

test('an overlap with no usable end still reports how early it started', () => {
    const flows = classifyTranscriptFlow([
        { start: 0.0, end: 4.0 },
        { start: 2.5, end: Number.NaN },
    ]);

    assert.equal(flows[1].kind, 'overlap');
    assert.ok(Math.abs(flows[1].seconds - 1.5) < 0.001);
});

test('plain and missing entries produce no marker text', () => {
    assert.equal(transcriptFlowNote({ kind: null, seconds: 0 }), null);
    assert.equal(transcriptFlowNote(null), null);
    assert.equal(transcriptFlowNote(undefined), null);
});
