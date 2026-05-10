import { describe, test, expect } from 'bun:test';

import {
    escapeCurrencyDollars,
    renderMarkdownContent,
    renderStreamingPreview,
} from './markdownRenderer.js';

describe('escapeCurrencyDollars', () => {
    test('escapes a lone currency dollar followed by digit', () => {
        expect(escapeCurrencyDollars('costs $5 today')).toBe('costs \\$5 today');
    });

    test('escapes every currency dollar in the regression case', () => {
        const input = 'After subtracting total debt ($9.57B) from its cash and short-term investments ($29.09B), the company holds $19.52 billion in net cash.';
        const out = escapeCurrencyDollars(input);
        expect(out).toBe('After subtracting total debt (\\$9.57B) from its cash and short-term investments (\\$29.09B), the company holds \\$19.52 billion in net cash.');
        // No bare `$` left to pair with a sibling currency.
        expect(out.match(/(?<!\\)\$/g)).toBeNull();
    });

    test('leaves $$...$$ display math untouched', () => {
        const input = '$$x = 1 + 2$$';
        expect(escapeCurrencyDollars(input)).toBe(input);
    });

    test('leaves \\(...\\) inline math untouched even when it contains numbers', () => {
        const input = 'where \\(S_0 = 262.76\\) is the spot price';
        expect(escapeCurrencyDollars(input)).toBe(input);
    });

    test('leaves currency inside fenced code blocks alone', () => {
        const input = 'see\n```\nprice = $5.00\n```\nfor details';
        expect(escapeCurrencyDollars(input)).toBe(input);
    });

    test('leaves currency inside inline code alone', () => {
        const input = 'use `print($5)` to log';
        expect(escapeCurrencyDollars(input)).toBe(input);
    });

    test('does not double-escape an already-escaped dollar', () => {
        const input = 'cost is \\$5';
        expect(escapeCurrencyDollars(input)).toBe(input);
    });

    test('leaves a dollar followed by a letter alone', () => {
        // Stays as text. The renderer no longer treats $...$ as math, so this
        // simply renders as literal text (graceful degradation, not a math span).
        const input = 'variable $x is undefined';
        expect(escapeCurrencyDollars(input)).toBe(input);
    });

    test('handles empty and undefined input', () => {
        expect(escapeCurrencyDollars('')).toBe('');
        expect(escapeCurrencyDollars(null)).toBe(null);
        expect(escapeCurrencyDollars(undefined)).toBe(undefined);
    });

    test('escapes bare $ inside $$...$$ math (KaTeX needs \\$ for literal dollar)', () => {
        const input = '$$\nrevenue = $100 + $50\n$$';
        // Boundary `$$` markers preserved; internal bare `$` escaped to `\$`
        // so KaTeX renders literal `$` glyphs instead of erroring.
        expect(escapeCurrencyDollars(input)).toBe('$$\nrevenue = \\$100 + \\$50\n$$');
    });

    test('escapes bare $ inside \\(...\\) inline math', () => {
        const input = '\\(Assets ($106.62B) = Liabilities ($43.01B) + Equity ($63.61B)\\)';
        expect(escapeCurrencyDollars(input)).toBe(
            '\\(Assets (\\$106.62B) = Liabilities (\\$43.01B) + Equity (\\$63.61B)\\)'
        );
    });

    test('escapes bare $ inside \\[...\\] display math', () => {
        const input = '\\[ revenue = $100M \\]';
        expect(escapeCurrencyDollars(input)).toBe('\\[ revenue = \\$100M \\]');
    });

    test('does not double-escape an already-escaped \\$ inside math', () => {
        const input = '\\(x = \\$5\\)';
        expect(escapeCurrencyDollars(input)).toBe(input);
    });
});

describe('renderMarkdownContent end-to-end', () => {
    test('regression: screenshot prose has no math span over currency', () => {
        // The exact prose pattern from the bug report. Before the fix, KaTeX
        // auto-render paired the two `$` in `($9.57B)...($29.09B)` and ate
        // the spaces between them. After the fix, no math element should
        // wrap any of the currency text.
        const div = document.createElement('div');
        const prose = 'After subtracting total debt ($9.57B) from its cash and short-term investments ($29.09B), the company holds $19.52 billion in net cash.';
        renderMarkdownContent(div, prose, { prefixLabel: '' });

        // texmath/KaTeX would emit <eq> / <eqn> / <section class="eqno"> on
        // any recognized math span. None should exist for plain currency prose.
        expect(div.querySelector('eq')).toBeNull();
        expect(div.querySelector('eqn')).toBeNull();
        expect(div.querySelectorAll('section.eqno').length).toBe(0);

        // Currency must round-trip as visible text. After markdown's
        // backslash-escape pass, `\$9.57B` renders as the literal `$9.57B`.
        expect(div.textContent).toContain('$9.57B');
        expect(div.textContent).toContain('$29.09B');
        expect(div.textContent).toContain('$19.52 billion');
    });

    test('legitimate inline math in \\(...\\) still renders as a math span', () => {
        const div = document.createElement('div');
        renderMarkdownContent(div, 'where \\(S_0 = 262.76\\) is the spot price', {
            prefixLabel: '',
        });
        // texmath wraps recognized inline math in an <eq> element; KaTeX
        // (absent in tests) would replace its content with rendered math.
        expect(div.querySelector('eq')).not.toBeNull();
    });

    test('emphasis around currency renders cleanly without math contamination', () => {
        const div = document.createElement('div');
        renderMarkdownContent(div, '**$5 billion** in revenue', { prefixLabel: '' });
        // The `$5` is escaped to `\$5` before parsing, so markdown sees
        // bold-wrapped literal text and emits <strong>$5 billion</strong>.
        const strong = div.querySelector('strong');
        expect(strong).not.toBeNull();
        expect(strong.textContent).toBe('$5 billion');
        expect(div.querySelector('eq')).toBeNull();
    });

    test('regression: equation containing currency does not produce KaTeX error span', () => {
        // The user's reported case: an "Accounting Equation" line wrapped in
        // \(...\) by the LLM. Before this fix the bare `$` inside math mode
        // made KaTeX paint the whole expression in `errorColor: '#cc0000'`.
        // The preprocessor now escapes internal `$` to `\$`, so KaTeX
        // renders literal `$` glyphs and never enters its error path.
        const div = document.createElement('div');
        const prose = '\\(Assets ($106.62B) = Liabilities ($43.01B) + Equity ($63.61B)\\)';
        renderMarkdownContent(div, prose, { prefixLabel: '' });

        const eq = div.querySelector('eq');
        expect(eq).not.toBeNull();
        // The math span exists, but its content has been pre-escaped so
        // KaTeX would not need to fall back to its red error color. We
        // verify by checking the texmath payload that KaTeX consumes.
        expect(eq.textContent).toContain('\\$106.62B');
        expect(eq.textContent).toContain('\\$43.01B');
        expect(eq.textContent).toContain('\\$63.61B');
    });
});

describe('renderStreamingPreview stabilization', () => {
    test('half-open inline math gets closed and does not crash', () => {
        const div = document.createElement('div');
        // Mid-stream snapshot: an open `\(` with no matching `\)` yet.
        renderStreamingPreview(div, 'computing \\(x = 5 + 3', { prefixLabel: '' });
        // Stabilization appends a `\)`, so texmath sees a complete inline
        // math span and emits an <eq> element for it.
        expect(div.querySelector('eq')).not.toBeNull();
    });

    test('half-open display math via $$ gets closed', () => {
        const div = document.createElement('div');
        renderStreamingPreview(div, 'see equation:\n\n$$\nE = mc^2', {
            prefixLabel: '',
        });
        // Block-level $$ stabilization appends `$$`, so texmath emits a
        // <section> with an <eqn> child for the display math.
        expect(div.querySelector('section eqn')).not.toBeNull();
    });

    test('half-open fenced code block gets closed', () => {
        const div = document.createElement('div');
        renderStreamingPreview(div, 'example:\n```\nfn main() {', { prefixLabel: '' });
        // Stabilization appends a closing fence so markdown sees a complete
        // code block and emits <pre><code>...</code></pre>.
        expect(div.querySelector('pre code')).not.toBeNull();
    });

    test('streaming preview escapes currency just like final render', () => {
        const div = document.createElement('div');
        renderStreamingPreview(div, 'spent $5 billion last quarter', { prefixLabel: '' });
        expect(div.querySelector('eq')).toBeNull();
        expect(div.textContent).toContain('$5 billion');
    });
});
