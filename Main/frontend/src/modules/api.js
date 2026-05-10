// api.js

import { buildBackendUrl } from './backendConfig.js';

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

// Function to get chat response from server (now supports MCP mode)
function getChatResponse(question, selectedModel, promptMode, useRAG, useMCP) {
    const encodedQuestion = encodeURIComponent(question);
    const currentUrl = window.location.href;
    const encodedCurrentUrl = encodeURIComponent(currentUrl);

    // Get user's timezone and time
    const timeInfo = getUserTimeInfo();
    const encodedTimezone = encodeURIComponent(timeInfo.timezone);
    const encodedCurrentTime = encodeURIComponent(timeInfo.currentTime);

    let endpoint;
    if (useMCP) {
        endpoint = 'get_mcp_response';
    } else {
        endpoint = promptMode ? 'get_adv_response' : 'get_chat_response';
    }

    // Get preferred links from localStorage for advanced mode
    let url = `${buildBackendUrl(`/${endpoint}/`)}?question=${encodedQuestion}` +
        `&models=${selectedModel}` +
        `&is_advanced=${promptMode}` +
        `&use_rag=${useRAG}` +
        `&use_memory=true` +
        `&session_id=${currentSessionId}` +
        `&current_url=${encodedCurrentUrl}` +
        `&user_timezone=${encodedTimezone}` +
        `&user_time=${encodedCurrentTime}`;

    // Add preferred links if in advanced mode
    if (promptMode) {
        try {
            const preferredLinks = JSON.parse(localStorage.getItem('preferredLinks') || '[]');
            if (preferredLinks.length > 0) {
                url += `&preferred_links=${encodeURIComponent(JSON.stringify(preferredLinks))}`;
            }
        } catch (e) {
            console.error('Error getting preferred links:', e);
        }
    }

    return fetch(url, { method: 'GET', credentials: 'include' })
        .then(response => response.json())
        .catch(error => {
            console.error('There was a problem with your fetch operation:', error);
            throw error;
        });
}

// Function to get streaming chat response using EventSource
function getChatResponseStream(question, selectedModel, promptMode, useRAG, useMCP, callbacks = {}) {
    const {
        onChunk,
        onSources,
        onComplete,
        onError,
        onStatus,
    } = callbacks;
    const encodedQuestion = encodeURIComponent(question);
    const currentUrl = window.location.href;
    const encodedCurrentUrl = encodeURIComponent(currentUrl);

    // Get user's timezone and time
    const timeInfo = getUserTimeInfo();
    const encodedTimezone = encodeURIComponent(timeInfo.timezone);
    const encodedCurrentTime = encodeURIComponent(timeInfo.currentTime);

    // MCP mode doesn't support streaming yet
    if (useMCP) {
        return getChatResponse(question, selectedModel, promptMode, useRAG, useMCP)
            .then(data => {
                const modelResponse = data.reply;
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

    // Build SSE URL based on mode
    let url;
    if (promptMode) {
        // Research mode streaming endpoint
        url = `${buildBackendUrl('/get_adv_response_stream/')}` +
            `?question=${encodedQuestion}` +
            `&models=${selectedModel}` +
            `&use_rag=${useRAG}` +
            `&use_memory=true` +
            `&session_id=${currentSessionId}` +
            `&current_url=${encodedCurrentUrl}` +
            `&user_timezone=${encodedTimezone}` +
            `&user_time=${encodedCurrentTime}`;

        // Add preferred links for research mode
        try {
            const preferredLinks = JSON.parse(localStorage.getItem('preferredLinks') || '[]');
            if (preferredLinks.length > 0) {
                url += `&preferred_links=${encodeURIComponent(JSON.stringify(preferredLinks))}`;
            }
        } catch (e) {
            console.error('Error getting preferred links:', e);
        }
    } else {
        // Thinking mode streaming endpoint
        url = `${buildBackendUrl('/get_chat_response_stream/')}` +
            `?question=${encodedQuestion}` +
            `&models=${selectedModel}` +
            `&use_rag=${useRAG}` +
            `&use_memory=true` +
            `&session_id=${currentSessionId}` +
            `&current_url=${encodedCurrentUrl}` +
            `&user_timezone=${encodedTimezone}` +
            `&user_time=${encodedCurrentTime}`;
    }

    // Create EventSource for SSE with credentials support
    const eventSource = new EventSource(url, { withCredentials: true });
    let fullResponse = '';
    let connectionAttempts = 0;
    const maxReconnectAttempts = 3;
    let usedUrls = [];  // Store source URLs for research mode
    let usedSources = [];  // Store detailed source metadata

    // Handle connection event
    eventSource.addEventListener('connected', (event) => {
        console.log(`SSE connection established for ${promptMode ? 'research' : 'thinking'} mode`);
        connectionAttempts = 0; // Reset on successful connection
    });

    // Handle open event (connection established)
    eventSource.onopen = (event) => {
        console.log('EventSource connected successfully');
    };

    // Handle message events
    eventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);

            if (data.error) {
                if (typeof onError === 'function') {
                    onError(new Error(data.error));
                }
                eventSource.close();
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
                eventSource.close();
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
    };

    // Handle errors with reconnection logic
    eventSource.onerror = (error) => {
        // Check readyState to determine the type of error
        if (eventSource.readyState === EventSource.CONNECTING) {
            connectionAttempts++;
            console.log(`SSE reconnecting... Attempt ${connectionAttempts}`);

            if (connectionAttempts > maxReconnectAttempts) {
                console.error('SSE max reconnection attempts reached');
                eventSource.close();
                if (typeof onError === 'function') {
                    onError(new Error('Connection failed after multiple attempts'));
                }
            }
        } else if (eventSource.readyState === EventSource.CLOSED) {
            console.error('SSE connection closed');
            if (typeof onError === 'function') {
                onError(new Error('Connection closed unexpectedly'));
            }
        } else {
            console.error('SSE error:', error);
            eventSource.close();
            if (typeof onError === 'function') {
                onError(error);
            }
        }
    };

    // Return a cleanup function
    return () => {
        if (eventSource.readyState !== EventSource.CLOSED) {
            eventSource.close();
            console.log('EventSource connection closed by client');
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

    const requestUrl = `${buildBackendUrl('/log_question/')}?question=${encodeURIComponent(question)}&button=${encodeURIComponent(button)}&current_url=${encodeURIComponent(currentUrl)}`;

    return fetch(requestUrl, { method: "GET", credentials: "include" })
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
