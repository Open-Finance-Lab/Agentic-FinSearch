// sse.js — incremental Server-Sent Events parser for fetch()-based streaming.
//
// The extension used to consume SSE with the browser's EventSource, which
// only supports GET. The chat endpoints moved to POST (frozen contract), so
// the stream is now read via fetch() + ReadableStream and this parser
// reassembles SSE frames from arbitrarily-chunked network reads.
//
// Spec-relevant behavior (mirrors what EventSource did for our streams):
// - Lines end with \n, \r\n, or a lone \r; a trailing \r at the end of a
//   chunk is held back until the next read in case it is half of a \r\n.
// - `data:` lines accumulate; multi-line data is joined with '\n'.
// - `event:` sets the event type (default 'message').
// - Lines starting with ':' are comments and are ignored.
// - `id:` and `retry:` fields are parsed but unused — the previous
//   EventSource consumer never used them either (the backend sends neither).
// - An event is dispatched only on a blank line; an unterminated final event
//   at end-of-stream is dropped, exactly like EventSource.

function createSSEParser(onEvent) {
    let buffer = '';
    let dataLines = [];
    let eventType = '';

    function dispatch() {
        if (dataLines.length > 0) {
            onEvent({ type: eventType || 'message', data: dataLines.join('\n') });
        }
        dataLines = [];
        eventType = '';
    }

    function processLine(line) {
        if (line === '') {
            dispatch();
            return;
        }
        if (line.charAt(0) === ':') {
            return; // comment line
        }

        let field = line;
        let value = '';
        const colonIdx = line.indexOf(':');
        if (colonIdx !== -1) {
            field = line.slice(0, colonIdx);
            value = line.slice(colonIdx + 1);
            if (value.charAt(0) === ' ') {
                value = value.slice(1);
            }
        }

        if (field === 'data') {
            dataLines.push(value);
        } else if (field === 'event') {
            eventType = value;
        }
        // 'id' / 'retry' / unknown fields: accepted and ignored.
    }

    return {
        // Feed a decoded text chunk. Partial lines (and a possible trailing
        // '\r' of a split '\r\n') are buffered until the next feed().
        feed(text) {
            buffer += text;
            let start = 0;
            let i = 0;
            while (i < buffer.length) {
                const ch = buffer[i];
                if (ch === '\r') {
                    if (i === buffer.length - 1) {
                        // Might be the first half of a \r\n split across
                        // chunks — wait for the next read.
                        break;
                    }
                    processLine(buffer.slice(start, i));
                    i += buffer[i + 1] === '\n' ? 2 : 1;
                    start = i;
                } else if (ch === '\n') {
                    processLine(buffer.slice(start, i));
                    i += 1;
                    start = i;
                } else {
                    i += 1;
                }
            }
            buffer = buffer.slice(start);
        },

        // Signal end-of-stream. A held trailing '\r' is now known to be a
        // lone-CR line terminator (not the first half of a split '\r\n'),
        // so its line is processed; any other unterminated partial line is
        // dropped, exactly like EventSource at EOF.
        end() {
            if (buffer.charAt(buffer.length - 1) === '\r') {
                processLine(buffer.slice(0, -1));
            }
            buffer = '';
        },
    };
}

export { createSSEParser };
