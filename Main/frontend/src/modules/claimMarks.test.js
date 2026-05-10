import { describe, test, expect, beforeEach, mock } from 'bun:test';

import { decorateClaimMarks } from './claimMarks.js';

function makeSpan(claimId, text) {
    const s = document.createElement('span');
    s.setAttribute('data-claim-id', claimId);
    s.textContent = text;
    return s;
}

function makeBubble(spans) {
    const bubble = document.createElement('div');
    bubble.className = 'agent_response';
    for (const s of spans) bubble.appendChild(s);
    return bubble;
}

describe('decorateClaimMarks', () => {
    let warnSpy;
    let realWarn;

    beforeEach(() => {
        realWarn = console.warn;
        warnSpy = mock(() => {});
        console.warn = warnSpy;
    });

    test('applies the correct class per status', () => {
        const bubble = makeBubble([
            makeSpan('gm-0', '44.13%'),
            makeSpan('cr-0', '0.99'),
            makeSpan('ae-0', '$352.8 billion'),
        ]);
        decorateClaimMarks(bubble, [
            { claim_id: 'gm-0', status: 'VERIFIED' },
            { claim_id: 'cr-0', status: 'FAILED' },
            { claim_id: 'ae-0', status: 'NOT_APPLICABLE' },
        ]);
        expect(
            bubble.querySelector('[data-claim-id="gm-0"]').classList.contains('claim-mark-verified')
        ).toBe(true);
        expect(
            bubble.querySelector('[data-claim-id="cr-0"]').classList.contains('claim-mark-failed')
        ).toBe(true);
        expect(
            bubble.querySelector('[data-claim-id="ae-0"]').classList.contains('claim-mark-na')
        ).toBe(true);
        console.warn = realWarn;
    });

    test('aggregates missing spans into a single warn', () => {
        const bubble = makeBubble([]);
        decorateClaimMarks(bubble, [
            { claim_id: 'missing-1', status: 'FAILED' },
            { claim_id: 'missing-2', status: 'VERIFIED' },
        ]);
        expect(warnSpy).toHaveBeenCalledTimes(1);
        const args = warnSpy.mock.calls[0];
        expect(args[1]).toEqual(['missing-1', 'missing-2']);
        console.warn = realWarn;
    });

    test('skips SKIPPED and unknown statuses', () => {
        const bubble = makeBubble([makeSpan('x-0', '5')]);
        decorateClaimMarks(bubble, [{ claim_id: 'x-0', status: 'SKIPPED' }]);
        const span = bubble.querySelector('[data-claim-id="x-0"]');
        expect(span.classList.length).toBe(0);
        console.warn = realWarn;
    });

    test('ignores null bubble / non-array claims', () => {
        expect(() => decorateClaimMarks(null, [])).not.toThrow();
        expect(() => decorateClaimMarks(document.createElement('div'), null)).not.toThrow();
        console.warn = realWarn;
    });

    test('ignores claims without a claim_id', () => {
        const bubble = makeBubble([makeSpan('x-0', '5')]);
        decorateClaimMarks(bubble, [{ status: 'FAILED' }]);
        expect(bubble.querySelector('[data-claim-id="x-0"]').classList.length).toBe(0);
        console.warn = realWarn;
    });

    test('joins by id even when non-matching spans share the container', () => {
        const bubble = makeBubble([
            makeSpan('a-0', 'one'),
            makeSpan('b-0', 'two'),
        ]);
        decorateClaimMarks(bubble, [{ claim_id: 'b-0', status: 'FAILED' }]);
        expect(bubble.querySelector('[data-claim-id="a-0"]').classList.length).toBe(0);
        expect(
            bubble.querySelector('[data-claim-id="b-0"]').classList.contains('claim-mark-failed')
        ).toBe(true);
        console.warn = realWarn;
    });
});
