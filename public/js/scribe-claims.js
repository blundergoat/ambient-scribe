// =========================================================================
// Ambient Scribe claim and evidence rendering (summary UX).
// Runs before scribe-output.js, which calls createClaimSectionBlocks and
// createClaimKeyPointBlocks while building the note. Owns one claim's text,
// its evidence toggle, and the disclosure naming what was actually checked:
// cited source rows, quote-match state, and wording review. That disclosure
// says plainly that linkage is not clinical approval, because a source link
// is the easiest thing on a note to over-read.
// =========================================================================

// At most one claim disclosure is open; Escape closes it and focus returns
// to its toggle only when the reviewer was inside it.
let openClaimDisclosure = null;
// Fallback for disclosure ids when a claim arrives without its server id.
let claimDisclosureSequence = 0;

// The disclosure's fixed reminder that linkage is not clinical review.
const CLAIM_EVIDENCE_HELP_TEXT =
    'Source links and quote matching are limited automated checks - not clinical review or approval.';

function createClaimSectionBlocks(summaryPayload) {
    const unitsById = summaryUnitsById(summaryPayload);
    const sectionBlocks = [];

    // Each section renders its claims as flowing prose in reading order.
    for (const section of summaryPayload.sections ?? []) {
        const contentBlock = createElement('div', {
            className: 'summary-section__content summary-section__content--claims',
        });

        for (const claim of section.claims ?? []) {
            appendClaimNodes(contentBlock, claim, unitsById);
        }

        sectionBlocks.push(createElement('div', { className: 'summary-section' }, [
            createElement('div', { className: 'summary-section__heading', text: section.heading }),
            contentBlock,
        ]));
    }

    return sectionBlocks;
}

/**
 * Creates the Key Points strip for schema-v2 claim key points.
 * Use from the summary renderer; each point is a claim with its own
 * evidence affordance, exactly like a section claim.
 *
 * @param {object} summaryPayload - v2 payload; no key points hides the strip.
 * @returns {HTMLElement[]} the strip block, or empty when there is none.
 */
function createClaimKeyPointBlocks(summaryPayload) {
    const keyPointClaims = summaryPayload.key_points ?? [];

    // No key points means the sections alone carry the generated note.
    if (keyPointClaims.length === 0) {
        return [];
    }

    const unitsById = summaryUnitsById(summaryPayload);
    const keyPointList = createElement('ul', { className: 'summary-key-points' });

    for (const claim of keyPointClaims) {
        const keyPointItem = createElement('li', {});
        appendClaimNodes(keyPointItem, claim, unitsById);
        keyPointList.appendChild(keyPointItem);
    }

    return [
        createElement('div', { className: 'summary-section' }, [
            createElement('div', { className: 'summary-section__heading', text: 'Key Points' }),
            keyPointList,
        ]),
    ];
}

/**
 * Appends one claim's prose, evidence affordance, and disclosure region.
 * Use for section claims and key points so both share one interaction.
 * The disclosure sits in the document flow directly after its claim - it
 * pushes content down instead of overlaying it, and never traps focus.
 *
 * @param {HTMLElement} claimContainer - section content div or key-point li.
 * @param {object} claim - one v2 claim; flag fields may be absent.
 * @param {Map<string, object>} unitsById - hydrated unit pool.
 * @returns {void} Mutates claimContainer.
 */
function appendClaimNodes(claimContainer, claim, unitsById) {
    // Real spaces keep copied and assistive reading natural between claims.
    if (claimContainer.childNodes.length > 0) {
        claimContainer.appendChild(document.createTextNode(' '));
    }

    claimDisclosureSequence += 1;
    const disclosureId = `claimEvidence-${String(claim.claim_id ?? '').trim() || claimDisclosureSequence}`;
    const citedUnits = resolvedClaimUnits(claim, unitsById);
    const evidenceToggle = createClaimEvidenceToggle(claim, citedUnits, disclosureId);
    const disclosureRegion = createClaimDisclosure(claim, citedUnits, disclosureId);
    // The no-break space glues the chip to the claim's last word so a chip
    // can never wrap onto a line of its own.
    const claimBody = createElement('span', {
        className: 'summary-claim',
        dataset: { claimId: String(claim.claim_id ?? '') },
    }, [createClaimTextSpan(claim), document.createTextNode(' '), evidenceToggle]);

    evidenceToggle.addEventListener('click', () => {
        toggleClaimEvidence(evidenceToggle, disclosureRegion);
    });

    claimContainer.appendChild(claimBody);
    claimContainer.appendChild(disclosureRegion);
}

/**
 * Builds the visible prose span for one claim, review cues attached.
 * Use inside the claim body: flagged claims carry a visible style and an
 * explanatory title, and the exact wording lives in the disclosure - a
 * bare unexplained "Unverified" never renders.
 *
 * @param {object} claim - one v2 claim; no flags renders plain text.
 * @returns {HTMLElement} the claim's text span.
 */
function createClaimTextSpan(claim) {
    // The shared flag reader keeps the panel and the clipboard in agreement.
    const claimFlags = typeof claimReviewFlags === 'function'
        ? claimReviewFlags(claim)
        : { uncited: false, reasonFlagged: false, wordingReview: false };
    const reviewExplanations = [];

    if (claimFlags.uncited) {
        reviewExplanations.push('No cited transcript evidence - review required');
    }
    // Each deterministic reason explains itself in the reviewer's tooltip too.
    if (claimFlags.reasonFlagged) {
        for (const reviewReason of claim.review_reasons ?? []) {
            reviewExplanations.push(String(reviewReason.detail || reviewReason.reason || ''));
        }
    }

    const claimText = createElement('span', {
        className: reviewExplanations.length > 0
            ? 'summary-claim__text summary-claim__text--review'
            : 'summary-claim__text',
        text: String(claim.text ?? ''),
    });

    // The claim-scoped wording cue keeps its established styling and help.
    if (claimFlags.wordingReview) {
        claimText.classList.add('summary-low-confidence');
        claimText.tabIndex = 0;
        claimText.setAttribute('aria-describedby', 'confidenceWordingHelp');
        claimText.dataset.confidenceTooltip = 'Low-confidence transcription';
        reviewExplanations.push('Low-confidence transcription');
    }

    if (reviewExplanations.length > 0) {
        claimText.setAttribute('title', reviewExplanations.filter(Boolean).join('; '));
    }

    return claimText;
}

/**
 * Builds one claim's evidence affordance button.
 * Use beside the claim text: cited claims show their own unit count and
 * uncited/absence claims show an honest wording chip instead - a count of
 * zero or a fabricated link never renders.
 *
 * @param {object} claim - one v2 claim.
 * @param {Array<object>} citedUnits - the claim's resolved units.
 * @param {string} disclosureId - id of the region this button controls.
 * @returns {HTMLElement} toggle button with aria-expanded state.
 */
function createClaimEvidenceToggle(claim, citedUnits, disclosureId) {
    const hasCitedEvidence = claim.evidence_basis === 'source_unit' && citedUnits.length > 0;
    let toggleText;
    let toggleLabel;

    if (hasCitedEvidence) {
        const turnNoun = citedUnits.length === 1 ? 'source turn' : 'source turns';
        // Cited chips are a pure disclosure affordance (a CSS chevron); the
        // unit count lives in the accessible label and the opened evidence
        // list, so no number competes with the clinical text.
        toggleText = '';
        toggleLabel = `View evidence for this claim, ${citedUnits.length} ${turnNoun}`;
    } else if (claim.evidence_basis === 'transcript_absence') {
        toggleText = 'Absence-based';
        toggleLabel = 'About this statement - based on absence from the transcript';
    } else {
        // Basis none and anything unknown read as uncited, review required.
        toggleText = 'No cited evidence';
        toggleLabel = 'About this statement - no transcript evidence cited, review required';
    }

    return createElement('button', {
        className: hasCitedEvidence
            ? 'summary-claim__toggle summary-claim__toggle--evidence'
            : 'summary-claim__toggle summary-claim__toggle--basis',
        attributes: {
            type: 'button',
            'aria-expanded': 'false',
            'aria-controls': disclosureId,
            'aria-label': toggleLabel,
        },
        text: toggleText,
    });
}

/**
 * States the claim's evidence/quote status in the contract's limiting language.
 * Use as the disclosure's first line so automated linkage can never read as
 * clinical verification.
 *
 * @param {object} claim - one v2 claim.
 * @param {boolean} hasCitedEvidence - whether resolved cited units exist.
 * @returns {string} one explanatory sentence.
 */
function claimEvidenceStateText(claim, hasCitedEvidence) {
    // Uncited and absence-based claims explain themselves without a link.
    if (!hasCitedEvidence) {
        return claim.evidence_basis === 'transcript_absence'
            ? 'This statement is based on absence: bounded automated checks found no mention in the selected visit transcript - verify manually.'
            : 'No transcript evidence was cited for this statement - review required.';
    }

    if (claim.quote_state === 'verified') {
        return 'Exact quoted wording matched the cited transcript - an automated check, not clinical approval.';
    }
    if (claim.quote_state === 'not_matched') {
        return 'Quoted wording could not be matched to the cited transcript - verify manually.';
    }
    if (claim.quote_state === 'wrong_role') {
        return 'Quoted wording was found under a different speaker than cited - verify manually.';
    }

    // No quotation marks in the claim: linked wording is a paraphrase.
    return 'Source linked - the claim paraphrases the cited transcript; wording is not a verbatim quote.';
}

/**
 * Builds one claim's non-modal in-flow evidence disclosure.
 * Use once per claim. The region shows the complete cited turns with
 * timestamp and speaker, visibly separated display context, the exact
 * quote/review state, claim-scoped "Open in transcript", an explicit
 * Close, and the fixed automated-check limitation text. It supports
 * Escape and focus return and keeps normal Tab order - never a focus trap.
 *
 * @param {object} claim - one v2 claim.
 * @param {Array<object>} citedUnits - the claim's resolved units.
 * @param {string} disclosureId - id the toggle's aria-controls points at.
 * @returns {HTMLElement} hidden disclosure region, in-flow after the claim.
 */
function createClaimDisclosure(claim, citedUnits, disclosureId) {
    const hasCitedEvidence = claim.evidence_basis === 'source_unit' && citedUnits.length > 0;
    const disclosureRegion = createElement('div', {
        className: 'summary-claim__disclosure hidden',
        attributes: {
            id: disclosureId,
            role: 'region',
            'aria-label': `Evidence for claim: ${String(claim.text ?? '').slice(0, 80)}`,
            tabindex: '-1',
        },
    });

    disclosureRegion.appendChild(createElement('p', {
        className: 'summary-claim__state',
        text: claimEvidenceStateText(claim, hasCitedEvidence),
    }));

    // Every deterministic review reason reads in full where the evidence is.
    const reviewReasons = claim.review_reasons ?? [];
    if (reviewReasons.length > 0) {
        disclosureRegion.appendChild(createElement(
            'ul',
            { className: 'summary-claim__reasons' },
            reviewReasons.map((reviewReason) => createElement('li', {
                text: String(reviewReason.detail || reviewReason.reason || ''),
            })),
        ));
    }

    // Cited turns render inside the disclosure's single scroll region.
    if (hasCitedEvidence) {
        disclosureRegion.appendChild(createElement(
            'div',
            { className: 'summary-claim__evidence' },
            citedUnits.map(createEvidenceUnitBlock),
        ));
    }

    const actionsRow = createElement('div', { className: 'summary-claim__actions' });

    // Only real cited evidence earns a transcript jump; uncited and
    // absence-based claims must not expose a misleading deep link.
    if (hasCitedEvidence) {
        const citedSegmentIds = citedUnits.flatMap(
            (sourceUnit) => (sourceUnit.rows ?? []).map((unitRow) => String(unitRow.segment_id))
        ).filter((segmentId) => segmentId !== '');
        const openInTranscriptButton = createElement('button', {
            className: 'summary-claim__open',
            attributes: { type: 'button' },
            text: 'Open in transcript',
        });
        openInTranscriptButton.addEventListener('click', () => {
            // Test pages without the tabs script keep the disclosure-only behaviour.
            if (typeof openTranscriptDeepLink !== 'function') {
                selectSummaryTab('transcript');
                return;
            }

            openTranscriptDeepLink(citedSegmentIds).catch((deepLinkError) => {
                console.warn('Transcript deep link failed:', deepLinkError);
            });
        });
        actionsRow.appendChild(openInTranscriptButton);
    }

    const closeButton = createElement('button', {
        className: 'summary-claim__close',
        attributes: { type: 'button' },
        text: 'Close',
    });
    closeButton.addEventListener('click', () => {
        closeClaimEvidence(disclosureRegion);
    });
    actionsRow.appendChild(closeButton);
    disclosureRegion.appendChild(actionsRow);

    // The fixed reminder that source linkage is never clinical review.
    disclosureRegion.appendChild(createElement('p', {
        className: 'summary-claim__help',
        text: CLAIM_EVIDENCE_HELP_TEXT,
    }));

    return disclosureRegion;
}

/**
 * Builds one complete evidence unit inside a claim disclosure.
 * Use per cited unit: the full turn text derives from its hydrated ordered
 * rows, and neighbour rows render visibly separated as display-only context
 * that never joins the evidence or its count.
 *
 * @param {object} sourceUnit - hydrated unit {unit_id, role, start, end,
 *   rows, context_before, context_after}; missing arrays act empty.
 * @returns {HTMLElement} one unit block.
 */
function createEvidenceUnitBlock(sourceUnit) {
    const unitBlock = createElement('div', {
        className: 'summary-claim__unit',
        dataset: {
            unitId: String(sourceUnit.unit_id ?? ''),
            segmentIds: (sourceUnit.rows ?? [])
                .map((unitRow) => String(unitRow.segment_id ?? ''))
                .filter((segmentId) => segmentId !== '')
                .join(' '),
        },
    });

    // Preceding neighbour rows are reading aid only, and say so.
    for (const contextRow of sourceUnit.context_before ?? []) {
        unitBlock.appendChild(createContextRowLine(contextRow));
    }

    const speakerLabel = typeof getRoleLabel === 'function'
        ? getRoleLabel(sourceUnit.role)
        : String(sourceUnit.role ?? '');
    const unitTimeSpan = Number.isFinite(sourceUnit.start) && Number.isFinite(sourceUnit.end)
        ? `${formatTime(sourceUnit.start)}–${formatTime(sourceUnit.end)}`
        : '';
    unitBlock.appendChild(createElement('div', { className: 'summary-claim__unit-evidence' }, [
        createElement('span', { className: 'summary-claim__unit-meta', text: `[${unitTimeSpan}] ${speakerLabel}` }),
        createElement('span', {
            className: 'summary-claim__unit-text',
            // The complete turn stitches from its ordered rows with real spaces.
            text: (sourceUnit.rows ?? [])
                .map((unitRow) => String(unitRow.text ?? '').trim())
                .filter((rowText) => rowText !== '')
                .join(' '),
        }),
    ]));

    // Following neighbour rows are likewise labelled, never counted.
    for (const contextRow of sourceUnit.context_after ?? []) {
        unitBlock.appendChild(createContextRowLine(contextRow));
    }

    return unitBlock;
}

/**
 * Builds one visibly labelled display-context row line.
 * Use around a unit's evidence text; rows carry no speaker role, so the
 * line never invents one.
 *
 * @param {object} contextRow - {segment_id, start, end, text}.
 * @returns {HTMLElement} labelled context line.
 */
function createContextRowLine(contextRow) {
    const contextTime = Number.isFinite(contextRow.start) ? `[${formatTime(contextRow.start)}] ` : '';

    return createElement('div', {
        className: 'summary-claim__context',
        text: `Context (not evidence) - ${contextTime}${String(contextRow.text ?? '')}`,
    });
}

/**
 * Opens or shuts one claim's evidence disclosure from its toggle.
 * Use from the toggle click; opening one closes any other so Escape and
 * the visible expansion stay unambiguous.
 */
function toggleClaimEvidence(evidenceToggle, disclosureRegion) {
    // A second activation of the open claim's toggle closes it.
    if (evidenceToggle.getAttribute('aria-expanded') === 'true') {
        closeClaimEvidence(disclosureRegion);
        return;
    }

    closeOpenClaimEvidence();
    disclosureRegion.classList.remove('hidden');
    evidenceToggle.setAttribute('aria-expanded', 'true');
    openClaimDisclosure = { evidenceToggle, disclosureRegion };
}

/**
 * Closes one claim disclosure and keeps the reviewer's focus anchored.
 * Use from the Close button, Escape, and toggle re-activation; focus
 * returns to the owning toggle only when it was inside the region, so
 * closing never yanks focus from elsewhere on the page.
 */
function closeClaimEvidence(disclosureRegion) {
    const owningToggle = openClaimDisclosure?.disclosureRegion === disclosureRegion
        ? openClaimDisclosure.evidenceToggle
        : document.querySelector(`[aria-controls="${disclosureRegion.id}"]`);
    const hadFocusInside = disclosureRegion.contains(document.activeElement)
        || document.activeElement === owningToggle;

    disclosureRegion.classList.add('hidden');
    owningToggle?.setAttribute('aria-expanded', 'false');

    if (openClaimDisclosure?.disclosureRegion === disclosureRegion) {
        openClaimDisclosure = null;
    }

    // Escape and Close keep keyboard users anchored on the claim's toggle.
    if (hadFocusInside) {
        owningToggle?.focus();
    }
}

/**
 * Closes whichever claim disclosure is open, if any.
 * Use before opening another claim's evidence.
 */
function closeOpenClaimEvidence() {
    // Nothing is open, so there is nothing to close.
    if (!openClaimDisclosure) {
        return;
    }

    closeClaimEvidence(openClaimDisclosure.disclosureRegion);
}

// Escape closes the open claim disclosure no matter where focus sits.
document.addEventListener('keydown', (keyEvent) => {
    if (keyEvent.key === 'Escape' && openClaimDisclosure) {
        keyEvent.stopPropagation();
        closeClaimEvidence(openClaimDisclosure.disclosureRegion);
    }
});
