/**
 * Browser acceptance coverage for clinician-facing wording-confidence cues.
 * These tests render live and corrected rows through the production page,
 * proving strict thresholds, absent-confidence behavior, accessible wording,
 * quiet review chips, and unchanged role-card geometry.
 */

const { test, expect } = require('@playwright/test');

const APP_PORT = process.env.APP_PORT ?? '48082';
const APP_URL = `http://localhost:${APP_PORT}`;

/**
 * Opens the consultation workspace after all classic scripts are ready for row injection.
 * Use before a user-flow test; the page object is always supplied by Playwright.
 *
 * @param {import('@playwright/test').Page} page - browser page for the visible consultation; never null.
 * @returns {Promise<void>} resolves when transcript controls can accept test rows.
 */
async function openConsultationWorkspace(page) {
    await page.goto(`${APP_URL}/scribe`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#startBtn');
}

test('live rows use the strict live threshold without changing card roles or height', async ({ page }) => {
    await openConsultationWorkspace(page);

    await page.evaluate(() => {
        const visibleRows = [
            {
                speaker_id: 'speaker_low',
                text: 'Please check this wording.',
                segment_id: 'seg-low',
                confidence: 0.7599,
            },
            {
                speaker_id: 'speaker_boundary',
                text: 'Please check this wording.',
                segment_id: 'seg-boundary',
                confidence: 0.76,
            },
            {
                speaker_id: 'speaker_unmeasured',
                text: 'Legacy wording stays plain.',
                segment_id: 'seg-unmeasured',
            },
        ];

        visibleRows.forEach((visibleRow, rowIndex) => {
            handleRawSegment({
                type: 'segment',
                start: rowIndex * 2,
                end: rowIndex * 2 + 1,
                ...visibleRow,
            });
        });
    });

    const liveRows = page.locator('#transcript .segment__text');
    await expect(liveRows).toHaveCount(3);
    await expect(liveRows.nth(0)).toHaveClass(/transcript-wording--review/);
    await expect(liveRows.nth(0)).toHaveAttribute('tabindex', '0');
    await expect(liveRows.nth(0)).toHaveAttribute('aria-describedby', 'confidenceWordingHelp');
    await expect(liveRows.nth(0)).toHaveAttribute(
        'data-confidence-tooltip',
        'Lower-confidence transcription — check wording against audio.',
    );
    await expect(liveRows.nth(1)).not.toHaveClass(/transcript-wording--review/);
    await expect(liveRows.nth(2)).not.toHaveClass(/transcript-wording--review/);
    await expect(liveRows.nth(2)).not.toHaveAttribute('data-confidence');

    const transcriptCards = page.locator('#transcript .segment');
    await expect(transcriptCards).toHaveCount(3);
    await expect(transcriptCards.nth(0).locator('.confidence-review-chip')).toHaveText('Review wording');
    await expect(transcriptCards.nth(1).locator('.confidence-review-chip')).toHaveCount(0);
    await expect(transcriptCards.nth(0)).toHaveClass(/segment--UNKNOWN/);
    await expect(transcriptCards.nth(1)).toHaveClass(/segment--UNKNOWN/);

    const cardHeights = await transcriptCards.evaluateAll((cards) =>
        cards.slice(0, 2).map((card) => card.getBoundingClientRect().height)
    );
    expect(Math.abs(cardHeights[0] - cardHeights[1])).toBeLessThanOrEqual(1);

    const summaryRows = await page.evaluate(() => readVisibleTranscriptSegments());
    expect(summaryRows[0].confidence).toBe(0.7599);
    expect(summaryRows[1].confidence).toBe(0.76);
    expect(summaryRows[2]).not.toHaveProperty('confidence');
});

test('corrected transcript keeps review styling local inside a stitched utterance', async ({ page }) => {
    await page.route('**/session/*/corrected-transcript', async (route) => {
        await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
                segments: [
                    {
                        segment_id: 'corrected-0001',
                        speaker_id: 'speaker_0',
                        role: 'PATIENT',
                        text: 'The rash was on the back of my carp.',
                        start: 10,
                        end: 11,
                        confidence: 0.77,
                    },
                    {
                        segment_id: 'corrected-0002',
                        speaker_id: 'speaker_0',
                        role: 'PATIENT',
                        text: 'My wife noticed it.',
                        start: 11.5,
                        end: 12.5,
                        confidence: 0.78,
                    },
                    {
                        segment_id: 'corrected-0003',
                        speaker_id: 'speaker_0',
                        role: 'PATIENT',
                        text: 'The next line was not measured.',
                        start: 13,
                        end: 14,
                    },
                ],
            }),
        });
    });
    await openConsultationWorkspace(page);

    await page.evaluate(() => {
        document.getElementById('summaryPanel').classList.remove('hidden');
        selectSummaryTab('transcript');
    });

    const correctedBlock = page.locator('#summaryTranscript .summary-transcript__block');
    await expect(correctedBlock).toHaveCount(1);
    const correctedRows = correctedBlock.locator('.summary-transcript__row');
    await expect(correctedRows).toHaveCount(3);
    await expect(correctedRows.nth(0)).toHaveClass(/transcript-wording--review/);
    await expect(correctedRows.nth(0)).toHaveAttribute('data-confidence', '0.77');
    await expect(correctedRows.nth(1)).not.toHaveClass(/transcript-wording--review/);
    await expect(correctedRows.nth(2)).not.toHaveClass(/transcript-wording--review/);
    await expect(correctedBlock.locator('.confidence-review-chip')).toHaveText('Review wording');
    await expect(correctedBlock).toContainText('The rash was on the back of my carp.');
    await expect(correctedBlock).toContainText('My wife noticed it.');
});

test('review wording remains non-colour-only and keyboard visible in both themes', async ({ page }) => {
    await openConsultationWorkspace(page);
    await page.evaluate(() => {
        handleRawSegment({
            type: 'segment',
            speaker_id: 'speaker_0',
            text: 'Focus this wording to see why it needs review.',
            segment_id: 'seg-focus',
            start: 0,
            end: 1,
            confidence: 0.7,
        });
    });

    const reviewWording = page.locator('#transcript .transcript-wording--review');
    const reviewChip = page.locator('#transcript .confidence-review-chip');
    await reviewWording.focus();
    await expect(reviewWording).toBeFocused();
    await expect(reviewChip).toHaveText('Review wording');

    const lightThemeStyles = await reviewWording.evaluate((wording) => ({
        background: getComputedStyle(wording).backgroundColor,
        decoration: getComputedStyle(wording).textDecorationColor,
        outline: getComputedStyle(wording).outlineStyle,
        tooltipOpacity: getComputedStyle(wording, '::after').opacity,
    }));
    expect(lightThemeStyles.background).not.toBe('rgba(0, 0, 0, 0)');
    expect(lightThemeStyles.decoration).not.toBe('currentcolor');
    expect(lightThemeStyles.outline).toBe('solid');
    await expect.poll(() => reviewWording.evaluate(
        (wording) => getComputedStyle(wording, '::after').opacity
    )).toBe('1');

    await page.evaluate(() => applyTheme('dark'));
    const darkThemeStyles = await reviewWording.evaluate((wording) => ({
        background: getComputedStyle(wording).backgroundColor,
        decoration: getComputedStyle(wording).textDecorationColor,
        tooltipOpacity: getComputedStyle(wording, '::after').opacity,
    }));
    expect(darkThemeStyles.background).not.toBe(lightThemeStyles.background);
    expect(darkThemeStyles.decoration).not.toBe(lightThemeStyles.decoration);
    await expect.poll(() => reviewWording.evaluate(
        (wording) => getComputedStyle(wording, '::after').opacity
    )).toBe('1');
});

test('day5 note sentence shows low-confidence transcription without changing prose', async ({ page }) => {
    await openConsultationWorkspace(page);
    const day5Sentence = (
        'Patient reports a rash on the back of his carp, noticed at the end of February.'
    );
    const plainSentence = 'Blood tests were arranged for the following week.';

    await page.evaluate(({ flaggedSentence, unchangedSentence }) => {
        renderSummary({
            title: 'Skin review',
            sections: [
                {
                    heading: 'Subjective',
                    content: `${flaggedSentence} ${unchangedSentence}`,
                    low_confidence: [flaggedSentence],
                    citations: [],
                },
            ],
            key_points: [],
        });
    }, { flaggedSentence: day5Sentence, unchangedSentence: plainSentence });

    const noteContent = page.locator('#summaryContent .summary-section__content');
    await expect(noteContent).toHaveText(`${day5Sentence} ${plainSentence}`);
    const confidenceMarker = noteContent.locator('.summary-low-confidence');
    await expect(confidenceMarker).toHaveCount(1);
    await expect(confidenceMarker).toHaveText(day5Sentence);
    await expect(confidenceMarker).toHaveAttribute('title', 'Low-confidence transcription');
    await expect(confidenceMarker).toHaveAttribute('tabindex', '0');
    await expect(confidenceMarker).toHaveAttribute('aria-describedby', 'confidenceWordingHelp');

    await confidenceMarker.focus();
    await expect(confidenceMarker).toBeFocused();
    await expect.poll(() => confidenceMarker.evaluate(
        (marker) => getComputedStyle(marker, '::after').opacity
    )).toBe('1');
});

test('one note sentence can carry fidelity and low-confidence reasons together', async ({ page }) => {
    await openConsultationWorkspace(page);
    const reviewedSentence = 'Patient denies dyspnea.';

    await page.evaluate((sentence) => {
        renderSummary({
            title: 'Combined review',
            sections: [
                {
                    heading: 'Subjective',
                    content: sentence,
                    unverified: [sentence],
                    low_confidence: [sentence],
                    citations: [],
                },
            ],
            key_points: [],
        });
    }, reviewedSentence);

    const combinedMarker = page.locator(
        '#summaryContent .summary-unverified.summary-low-confidence'
    );
    await expect(combinedMarker).toHaveCount(1);
    await expect(combinedMarker).toHaveText(reviewedSentence);
    await expect(combinedMarker).toHaveAttribute(
        'title',
        'Unverified against transcript; Low-confidence transcription',
    );
});
