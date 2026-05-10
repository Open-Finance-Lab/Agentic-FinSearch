import { describe, test, expect } from 'bun:test';

import { looksLikeValidationRequest } from './intent.js';

describe('looksLikeValidationRequest', () => {
    test('matches direct validate/verify phrasings', () => {
        const positives = [
            'Validate this claim: Apple FY23 gross margin was 38%.',
            'Please verify Tesla’s FY23 current ratio of 1.50.',
            'Can you fact-check that Microsoft FY23 gross margin is 70%?',
            'Fact check this number for me.',
            'Double-check Apple FY23 gross margin = 44.13%',
            'Sanity check: is MSFT FY23 current ratio 1.77?',
            'Cross-check this against the SEC filing please.',
            'Is it true that Tesla FY23 gross margin was 25%?',
            'Is that right — Apple’s current ratio under 1.0?',
            'Is this accurate: TSLA FY23 GM 18%?',
            'Check this claim for me.',
            'Check the number against the 10-K.',
        ];
        for (const q of positives) {
            expect(looksLikeValidationRequest(q)).toBe(true);
        }
    });

    test('does not match plain question phrasings', () => {
        const negatives = [
            'What was Apple’s gross margin in FY2023?',
            'Compare Apple, Microsoft, and Tesla FY23 margins.',
            'Tell me about Tesla’s 2023 financials.',
            'Apple earnings claim hit a record this quarter.',
            'How does the current ratio formula work?',
            '',
            null,
            undefined,
        ];
        for (const q of negatives) {
            expect(looksLikeValidationRequest(q)).toBe(false);
        }
    });

    test('matches "verify" mid-sentence', () => {
        expect(
            looksLikeValidationRequest(
                'Could you please verify the gross margin I read in the press release?'
            )
        ).toBe(true);
    });
});
