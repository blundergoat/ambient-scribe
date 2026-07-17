// Unit coverage for the corpus-derived transcript wording thresholds.
// These tests protect the live/corrected lane split before the browser adds
// clinician-facing review cues. They run without a DOM via `npm run test:js`.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
    CORRECTED_TRANSCRIPT_REVIEW_THRESHOLD,
    LIVE_TRANSCRIPT_REVIEW_THRESHOLD,
    reviewThresholdForTranscriptLane,
    shouldReviewTranscriptWording,
} = require('../../public/js/scribe-confidence.js');

test('lane thresholds match the approved corpus policy', () => {
    assert.equal(LIVE_TRANSCRIPT_REVIEW_THRESHOLD, 0.76);
    assert.equal(CORRECTED_TRANSCRIPT_REVIEW_THRESHOLD, 0.78);
    assert.equal(reviewThresholdForTranscriptLane('live'), 0.76);
    assert.equal(reviewThresholdForTranscriptLane('corrected'), 0.78);
});

test('strictly lower values receive review styling in their own lane', () => {
    assert.equal(shouldReviewTranscriptWording(0.7599, 'live'), true);
    assert.equal(shouldReviewTranscriptWording(0.7799, 'corrected'), true);
});

test('threshold equality and higher confidence remain visually unchanged', () => {
    assert.equal(shouldReviewTranscriptWording(0.76, 'live'), false);
    assert.equal(shouldReviewTranscriptWording(0.78, 'corrected'), false);
    assert.equal(shouldReviewTranscriptWording(0.91, 'corrected'), false);
});

test('absent invalid or unknown confidence never invents a review cue', () => {
    assert.equal(shouldReviewTranscriptWording(null, 'live'), false);
    assert.equal(shouldReviewTranscriptWording(undefined, 'corrected'), false);
    assert.equal(shouldReviewTranscriptWording(Number.NaN, 'live'), false);
    assert.equal(shouldReviewTranscriptWording(0.2, ''), false);
    assert.equal(shouldReviewTranscriptWording(0.2, 'unknown'), false);
});
