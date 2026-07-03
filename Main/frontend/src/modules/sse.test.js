import { describe, test, expect } from 'bun:test';
import { createSSEParser } from './sse.js';

// Feed `text` to a fresh parser in chunks of `size` chars, signal
// end-of-stream (as the api.js consumer does), and collect the dispatched
// events.
function parseChunked(text, size) {
    const events = [];
    const parser = createSSEParser((evt) => events.push(evt));
    for (let i = 0; i < text.length; i += size) {
        parser.feed(text.slice(i, i + size));
    }
    parser.end();
    return events;
}

const BACKEND_STREAM =
    'event: connected\ndata: {"status": "connected"}\n\n' +
    'data: {"content": "Hello", "done": false}\n\n' +
    'data: {"content": " world", "done": false}\n\n' +
    'data: {"content": "", "done": true, "used_urls": ["https://a.example"]}\n\n';

describe('createSSEParser', () => {
    test('parses the exact frame shapes the backend emits', () => {
        const events = parseChunked(BACKEND_STREAM, BACKEND_STREAM.length);
        expect(events).toEqual([
            { type: 'connected', data: '{"status": "connected"}' },
            { type: 'message', data: '{"content": "Hello", "done": false}' },
            { type: 'message', data: '{"content": " world", "done": false}' },
            { type: 'message', data: '{"content": "", "done": true, "used_urls": ["https://a.example"]}' },
        ]);
    });

    test('is invariant to chunk boundaries (every chunk size, incl. 1 byte)', () => {
        const reference = parseChunked(BACKEND_STREAM, BACKEND_STREAM.length);
        for (let size = 1; size <= 17; size++) {
            expect(parseChunked(BACKEND_STREAM, size)).toEqual(reference);
        }
    });

    test('joins multi-line data with newlines', () => {
        const events = parseChunked('data: line1\ndata: line2\n\n', 4);
        expect(events).toEqual([{ type: 'message', data: 'line1\nline2' }]);
    });

    test('handles CRLF and lone-CR terminators, including a \\r\\n split across chunks', () => {
        const stream = 'event: connected\r\ndata: x\r\n\r\ndata: y\r\r';
        // Chunk size 17 puts the boundary exactly between '\r' and '\n'
        // of the first line ('event: connected' is 16 chars + '\r' = 17).
        for (const size of [1, 2, 3, 17, stream.length]) {
            expect(parseChunked(stream, size)).toEqual([
                { type: 'connected', data: 'x' },
                { type: 'message', data: 'y' },
            ]);
        }
    });

    test('ignores comment lines and unknown fields, defaults type to message', () => {
        const events = parseChunked(': keep-alive\nid: 7\nretry: 250\ndata: ok\n\n', 5);
        expect(events).toEqual([{ type: 'message', data: 'ok' }]);
    });

    test('strips a single leading space from field values', () => {
        expect(parseChunked('data:  two spaces\n\n', 3)).toEqual([
            { type: 'message', data: ' two spaces' },
        ]);
        expect(parseChunked('data:nospace\n\n', 3)).toEqual([
            { type: 'message', data: 'nospace' },
        ]);
    });

    test('does not dispatch an event without data lines', () => {
        expect(parseChunked('event: ping\n\n', 2)).toEqual([]);
    });

    test('drops an unterminated trailing event (EventSource parity)', () => {
        expect(parseChunked('data: complete\n\ndata: partial', 6)).toEqual([
            { type: 'message', data: 'complete' },
        ]);
    });

    test('passes [DONE]-style sentinel lines through as plain data', () => {
        expect(parseChunked('data: [DONE]\n\n', 5)).toEqual([
            { type: 'message', data: '[DONE]' },
        ]);
    });
});
