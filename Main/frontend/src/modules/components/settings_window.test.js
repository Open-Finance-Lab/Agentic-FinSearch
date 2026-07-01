import { describe, test, expect } from 'bun:test';
import { buildModelListItem } from './settings_window.js';

describe('buildModelListItem', () => {
    test('renders the description as text, never as live HTML', () => {
        const item = buildModelListItem('EvilModel', {
            description: '<img src=x onerror="alert(1)">',
        });
        // The payload must NOT materialize as an element.
        expect(item.querySelector('img')).toBeNull();
        // It must survive verbatim as text inside the <small> caption.
        const small = item.querySelector('small');
        expect(small).not.toBeNull();
        expect(small.textContent).toBe('<img src=x onerror="alert(1)">');
    });

    test('shows the model name in <strong> and description in <small>', () => {
        const item = buildModelListItem('FinGPT', { description: 'Finance model' });
        expect(item.querySelector('strong').textContent).toBe('FinGPT');
        expect(item.querySelector('small').textContent).toBe('Finance model');
    });

    test('falls back to plain text when there is no description', () => {
        const item = buildModelListItem('Plain', null);
        expect(item.textContent).toBe('Plain');
        expect(item.querySelector('strong')).toBeNull();
    });
});
