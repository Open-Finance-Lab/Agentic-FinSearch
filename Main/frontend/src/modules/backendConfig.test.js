import { describe, test, expect } from 'bun:test';
import { normalizeBaseUrl } from './backendConfig.js';

describe('normalizeBaseUrl allow-list', () => {
    test('accepts the canonical production host', () => {
        expect(normalizeBaseUrl('https://agenticfinsearch.org')).toBe('https://agenticfinsearch.org');
        expect(normalizeBaseUrl('https://agenticfinsearch.org/')).toBe('https://agenticfinsearch.org');
    });

    test('accepts subdomains of the production host', () => {
        expect(normalizeBaseUrl('https://api.agenticfinsearch.org')).toBe('https://api.agenticfinsearch.org');
    });

    test('accepts http only for local dev hosts', () => {
        expect(normalizeBaseUrl('http://localhost:8000')).toBe('http://localhost:8000');
        expect(normalizeBaseUrl('http://127.0.0.1:8000')).toBe('http://127.0.0.1:8000');
    });

    test('rejects an unrelated host', () => {
        expect(normalizeBaseUrl('https://evil.com')).toBeNull();
    });

    test('rejects look-alike suffix hosts', () => {
        expect(normalizeBaseUrl('https://agenticfinsearch.org.evil.com')).toBeNull();
        expect(normalizeBaseUrl('https://evilagenticfinsearch.org')).toBeNull();
    });

    test('rejects http for non-local hosts', () => {
        expect(normalizeBaseUrl('http://agenticfinsearch.org')).toBeNull();
    });

    test('rejects dangerous schemes', () => {
        expect(normalizeBaseUrl('javascript:alert(1)')).toBeNull();
        expect(normalizeBaseUrl('data:text/html,<script>1</script>')).toBeNull();
    });

    test('returns null for empty / nullish input', () => {
        expect(normalizeBaseUrl('')).toBeNull();
        expect(normalizeBaseUrl(null)).toBeNull();
        expect(normalizeBaseUrl(undefined)).toBeNull();
    });
});
