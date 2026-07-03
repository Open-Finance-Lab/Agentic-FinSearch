// api.js

import { buildBackendUrl } from './backendConfig.js';
import { createSSEParser } from './sse.js';

// Session ID management
let currentSessionId = sessionStorage.getItem('fingpt_session_id');

function setSessionId(sessionId) {
    if (sessionId && sessionId !== currentSessionId) {
        currentSessionId = sessionId;
        sessionStorage.setItem('fingpt_session_id', sessionId);
        console.log(`[Session] ID updated to: ${sessionId}`);
    }
}

// Function to POST JSON to the server endpoint
function postWebTextToServer(textContent, currentUrl) {
    return fetch(buildBackendUrl('/input_webtext/'), {
        method: "POST",
        credentials: "include",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            textContent: textContent,
            currentUrl: currentUrl,
            use_memory: true,
            session_id: currentSessionId
        }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Network response was not ok (status: ${response.status})`);
            }
            return response.json();
        })
        .then(data => {
            console.log("Response from server:", data);
            if (data.session_id) setSessionId(data.session_id);
            if (data.context_stats && data.context_stats.session_id) setSessionId(data.context_stats.session_id);
            return data;
        })
        .catch(error => {
            console.error("There was a problem with your fetch operation:", error);
            throw error;
        });
}

// Function to get user's timezone and current time
function getUserTimeInfo() {
    return {
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        currentTime: new Date().toISOString()
    };
}

// Build the flat JSON body for the frozen POST contract shared by the chat
// endpoints. All values are strings and every key is optional; the server
// merges JSON-body values over same-key query-string values (dual-accept
// window). `preferred_links` stays a JSON-array-encoded STRING — the same
// encoding the old GET query param carried.
function buildChatRequestBody(question, selectedModel, promptMode) {
    const timeInfo = getUserTimeInfo();

    const body = {
        question: String(question),
        models: String(selectedModel),
        current_url: window.location.href,
        use_unified: 'true',
        user_timezone: timeInfo.timezone,
        user_time: timeInfo.currentTime,
    };

    // Every key is optional in the contract: omit session_id rather than
    // sending the literal string "null" the old GET template produced.
    if (currentSessionId) {
        body.session_id = String(currentSessionId);
    }

    // Add preferred links if in advanced/research mode
    if (promptMode) {
        try {
            const preferredLinks = JSON.parse(localStorage.getItem('preferredLinks') || '[]');
            if (preferredLinks.length > 0) {
                body.preferred_links = JSON.stringify(preferredLinks);
            }
        } catch (e) {
            console.error('Error getting preferred links:', e);
        }
    }

    return body;
}

// Function to get chat response from server
function getChatResponse(question, selectedModel, promptMode, useRAG, useMCP) {
    // The MCP toggle used to select a dedicated 'get_mcp_response' endpoint
    // that does not exist in the backend (every call 404'd). The regular
    // chat endpoint already runs the LLM with the available MCP tools, so
    // all modes share the two real endpoints now.
    const endpoint = promptMode ? 'get_adv_response' : 'get_chat_response';

    return fetch(buildBackendUrl(`/${endpoint}/`), {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildChatRequestBody(question, selectedModel, promptMode)),
    })
        .then(response => response.json())
        .catch(error => {
            console.error('There was a problem with your fetch operation:', error);
            throw error;
        });
}

// Function to get streaming chat response via POST fetch-streaming SSE.
//
// This replaces the old GET + EventSource transport with the frozen POST
// contract while reproducing the previous observable behavior exactly:
// - same JSON event handling (error/content/status/used_urls/used_sources/
//   done/memory_stats) and the same callback surface;
// - same reconnect semantics: if the connection drops (or the stream ends
//   without a `done` event) the request is retried up to 3 times, and — as
//   with EventSource auto-reconnect before — each retry RE-ISSUES the
//   request server-side (pre-existing semantics);
// - a successful `event: connected` frame resets the retry counter, as the
//   old 'connected' listener did;
// - an HTTP error status is fatal without retry, matching EventSource's
//   readyState CLOSED behavior on non-200 responses;
// - the returned cleanup function cancels the stream (AbortController now,
//   eventSource.close() before).
function getChatResponseStream(question, selectedModel, promptMode, useRAG, useMCP, callbacks = {}) {
    const {
        onChunk,
        onSources,
        onComplete,
        onError,
        onStatus,
    } = callbacks;

    // MCP mode doesn't support streaming yet
    if (useMCP) {
        return getChatResponse(question, selectedModel, promptMode, useRAG, useMCP)
            .then(data => {
                const modelResponse = data.resp ? data.resp[selectedModel] : data.reply;
                if (typeof onComplete === 'function') {
                    onComplete(modelResponse, data);
                }
            })
            .catch(error => {
                if (typeof onError === 'function') {
                    onError(error);
                }
            });
    }

    // Build SSE endpoint based on mode (research vs thinking)
    const url = buildBackendUrl(promptMode ? '/get_adv_response_stream/' : '/get_chat_response_stream/');
    const requestBody = JSON.stringify(buildChatRequestBody(question, selectedModel, promptMode));

    let fullResponse = '';
    let connectionAttempts = 0;
    const maxReconnectAttempts = 3;
    const reconnectDelayMs = 3000; // EventSource's default retry interval
    let usedUrls = [];  // Store source URLs for research mode
    let usedSources = [];  // Store detailed source metadata
    let finished = false;      // done / fatal error / retries exhausted
    let clientClosed = false;  // user-initiated cancel via the cleanup fn
    let abortController = null;

    // Stop all stream activity (the fetch-streaming analogue of
    // eventSource.close()).
    function closeStream() {
        finished = true;
        if (abortController) {
            abortController.abort();
        }
    }

    // Handle one parsed SSE event — the JSON handling is identical to the
    // old eventSource.onmessage / 'connected' listener pair.
    function handleServerEvent(event) {
        if (finished || clientClosed) {
            return;
        }

        // Handle connection event
        if (event.type === 'connected') {
            console.log(`SSE connection established for ${promptMode ? 'research' : 'thinking'} mode`);
            connectionAttempts = 0; // Reset on successful connection
            return;
        }

        if (event.type !== 'message') {
            return; // unknown event types were unlistened before; ignore
        }

        try {
            const data = JSON.parse(event.data);

            if (data.error) {
                if (typeof onError === 'function') {
                    onError(new Error(data.error));
                }
                closeStream();
                return;
            }

            if (data.content) {
                fullResponse += data.content;
                if (typeof onChunk === 'function') {
                    onChunk(data.content, fullResponse);
                }
            }

            if (data.status && typeof onStatus === 'function') {
                onStatus(data.status);
            }

            // Handle source URLs for research mode
            if (data.used_urls && Array.isArray(data.used_urls)) {
                usedUrls = data.used_urls;
                console.log(`[Research Mode] Received ${usedUrls.length} source URLs`);
                console.log('[Research Mode] URLs received:', data.used_urls);
            }

            // Handle detailed source metadata for research mode
            if (data.used_sources && Array.isArray(data.used_sources)) {
                usedSources = data.used_sources;
                console.log(`[Research Mode] Received ${usedSources.length} detailed sources`);
            }

            if (typeof onSources === 'function' && (Array.isArray(data.used_urls) || Array.isArray(data.used_sources))) {
                const urlsForCallback = Array.isArray(data.used_urls) ? data.used_urls : usedUrls;
                const sourcesForCallback = Array.isArray(data.used_sources) ? data.used_sources : usedSources;
                onSources(urlsForCallback, sourcesForCallback);
            }

            if (data.done) {
                closeStream();
                // Debug: Log what we're about to pass to completion
                console.log('[Research Mode] Stream done. Final usedUrls:', usedUrls);
                console.log('[Research Mode] data.used_urls:', data.used_urls);
                console.log('[Research Mode] Final usedSources:', usedSources);

                // Ensure completion callback receives latest source list and metadata
                const completionData = {
                    ...data,
                    used_urls: Array.isArray(data.used_urls) ? data.used_urls : usedUrls,
                    used_sources: Array.isArray(data.used_sources) ? data.used_sources : usedSources
                };

                console.log('[Research Mode] Passing to onComplete with used_urls:', completionData.used_urls);
                console.log('[Research Mode] Passing to onComplete with used_sources:', completionData.used_sources);
                if (typeof onComplete === 'function') {
                    onComplete(fullResponse, completionData);
                }
            }

            // Handle memory stats if present
            if (data.memory_stats) {
                console.log('Memory stats:', data.memory_stats);
            }
        } catch (e) {
            console.error('Error parsing SSE data:', e);
        }
    }

    // Retry logic — mirrors the old onerror readyState===CONNECTING branch.
    // NOTE: like EventSource auto-reconnect, a retry re-sends the POST, which
    // re-issues the question server-side (pre-existing semantics).
    function scheduleReconnect() {
        connectionAttempts++;
        console.log(`SSE reconnecting... Attempt ${connectionAttempts}`);

        if (connectionAttempts > maxReconnectAttempts) {
            console.error('SSE max reconnection attempts reached');
            finished = true;
            if (typeof onError === 'function') {
                onError(new Error('Connection failed after multiple attempts'));
            }
            return;
        }

        setTimeout(() => {
            if (!finished && !clientClosed) {
                connect();
            }
        }, reconnectDelayMs);
    }

    function connect() {
        abortController = new AbortController();
        const parser = createSSEParser(handleServerEvent);
        const decoder = new TextDecoder('utf-8');

        fetch(url, {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'text/event-stream',
            },
            body: requestBody,
            signal: abortController.signal,
        })
            .then(response => {
                if (!response.ok) {
                    // EventSource treated a non-200 as fatal (readyState
                    // CLOSED) — no retry.
                    const err = new Error('Connection closed unexpectedly');
                    err.isFatalSSE = true;
                    throw err;
                }
                console.log('SSE stream connected successfully');

                const reader = response.body.getReader();
                const pump = () => reader.read().then(({ done, value }) => {
                    if (finished || clientClosed) {
                        reader.cancel().catch(() => {});
                        return;
                    }
                    if (done) {
                        parser.feed(decoder.decode()); // flush the decoder
                        parser.end();
                        return;
                    }
                    parser.feed(decoder.decode(value, { stream: true }));
                    return pump();
                });
                return pump();
            })
            .then(() => {
                if (finished || clientClosed) {
                    return;
                }
                // Stream ended without a `done` event: EventSource would
                // auto-reconnect here, so we do too.
                scheduleReconnect();
            })
            .catch(error => {
                if (clientClosed || finished || error.name === 'AbortError') {
                    return;
                }
                if (error.isFatalSSE) {
                    finished = true;
                    console.error('SSE connection closed');
                    if (typeof onError === 'function') {
                        onError(new Error('Connection closed unexpectedly'));
                    }
                    return;
                }
                // Network-level failure: EventSource would retry.
                scheduleReconnect();
            });
    }

    connect();

    // Return a cleanup function (user-initiated cancel)
    return () => {
        if (!finished && !clientClosed) {
            clientClosed = true;
            if (abortController) {
                abortController.abort();
            }
            console.log('SSE stream closed by client');
        }
    };
}

// Function to clear messages
function clearMessages() {
    return fetch(`${buildBackendUrl('/clear_messages/')}?use_memory=true&session_id=${currentSessionId}`, { method: "POST", credentials: "include" })
        .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response.json();
        })
        .catch(error => {
            console.error('There was a problem with your fetch operation:', error);
            throw error;
        });
}

// Function to get sources
function getSourceUrls(searchQuery, currentUrl) {
    const params = new URLSearchParams();
    if (searchQuery) {
        params.append('query', searchQuery);
    }
    if (currentUrl) {
        params.append('current_url', currentUrl);
    }
    if (currentSessionId && currentSessionId !== 'null' && currentSessionId !== 'undefined') {
        params.append('session_id', currentSessionId);
    }

    const queryString = params.toString();
    const baseEndpoint = buildBackendUrl('/get_source_urls/');
    const requestUrl = queryString ? `${baseEndpoint}?${queryString}` : baseEndpoint;

    return fetch(requestUrl, { method: "GET", credentials: "include" })
        .then(response => response.json())
        .then(data => {
            if (data.session_id) setSessionId(data.session_id);
            if (data.resp && data.resp.session_id) setSessionId(data.resp.session_id);
            return data;
        })
        .catch(error => {
            console.error('There was a problem with your fetch operation:', error);
            throw error;
        });
}

// Function to log question
function logQuestion(question, button) {
    const currentUrl = window.location.href;

    return fetch(buildBackendUrl('/log_question/'), {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            question: String(question),
            button: String(button),
            current_url: String(currentUrl),
        }),
    })
        .then(response => response.json())
        .then(data => {
            if (data.status !== 'success') {
                console.error('Failed to log question');
            }
            return data;
        })
        .catch(error => {
            console.error('Error logging question:', error);
            throw error;
        });
}

// Function to sync preferred links with backend
function syncPreferredLinks() {
    try {
        const preferredLinks = JSON.parse(localStorage.getItem('preferredLinks') || '[]');
        if (preferredLinks.length > 0) {
            return fetch(buildBackendUrl('/api/sync_preferred_urls/'), {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ urls: preferredLinks })
            })
                .then(response => response.json())
                .catch(error => {
                    console.error('Error syncing preferred links:', error);
                });
        }
    } catch (e) {
        console.error('Error reading preferred links for sync:', e);
    }
    return Promise.resolve();
}

// Auto-scrape promise tracking for coordination with chat requests
let autoScrapePromise = Promise.resolve({ status: 'not_started' });
let autoScrapeInProgress = false;

function setAutoScrapePromise(promise) {
    autoScrapeInProgress = true;
    autoScrapePromise = promise.finally(() => {
        autoScrapeInProgress = false;
    });
    return autoScrapePromise;
}

function waitForAutoScrape() {
    if (!autoScrapeInProgress) {
        return Promise.resolve({ status: 'already_complete' });
    }
    console.log("[Auto-scrape] Waiting for page scraping to complete...");
    return autoScrapePromise;
}

function isAutoScrapeInProgress() {
    return autoScrapeInProgress;
}

// Function to trigger auto-scraping of the current page
function triggerAutoScrape(currentUrl) {
    if (!currentSessionId) {
        console.warn("Cannot trigger auto-scrape: Session ID not set");
        return Promise.resolve({ status: 'skipped', reason: 'no_session_id' });
    }

    return fetch(buildBackendUrl('/api/auto_scrape/'), {
        method: "POST",
        credentials: "include",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            current_url: currentUrl,
            session_id: currentSessionId
        }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Network response was not ok (status: ${response.status})`);
            }
            return response.json();
        })
        .then(data => {
            console.log("Auto-scrape response:", data);
            return data;
        })
        .catch(error => {
            console.error("Auto-scrape failed:", error);
            // Don't throw, just log, as this is a background process
            return { error: error.message };
        });
}

// Layer 1 Validate: POST the current session to /api/axioms/validate/ and
// return the per-claim verdicts for inline rendering.
function validateClaims() {
    return fetch(buildBackendUrl('/api/axioms/validate/'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ session_id: currentSessionId }),
    })
        .then((response) => {
            if (!response.ok) {
                throw new Error(`validate_claims HTTP ${response.status}`);
            }
            return response.json();
        });
}

// Lightweight check: did the just-completed response record any ratio claims?
// Used to decide whether to show the Validate button on a response bubble.
function hasAxiomClaims() {
    const params = currentSessionId ? `?session_id=${encodeURIComponent(currentSessionId)}` : '';
    return fetch(buildBackendUrl(`/api/axioms/has_claims/${params}`), {
        method: 'GET',
        credentials: 'include',
    })
        .then((response) => (response.ok ? response.json() : { has_claims: false }))
        .catch(() => ({ has_claims: false }));
}

export {
    postWebTextToServer,
    getChatResponse,
    getChatResponseStream,
    clearMessages,
    getSourceUrls,
    logQuestion,
    setSessionId,
    syncPreferredLinks,
    triggerAutoScrape,
    setAutoScrapePromise,
    waitForAutoScrape,
    isAutoScrapeInProgress,
    validateClaims,
    hasAxiomClaims
};
