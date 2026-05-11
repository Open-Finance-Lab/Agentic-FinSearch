// helpers.js
import { clearMessages, getSourceUrls, logQuestion, validateClaims } from './api.js';
import { decorateClaimMarks } from './claimMarks.js';
import { handleChatResponse, handleImageResponse } from './handlers.js';
import { renderMarkdownContent } from './markdownRenderer.js';
import {
    clearCachedSources,
    getCurrentPageUrl,
    getCachedSourceEntries,
    getCurrentPageEntry,
    getLastSearchQuery,
    mergeCachedMetadata,
} from './sourcesCache.js';

// Autoscroll state - enabled by default
let autoScrollEnabled = true;
let scrollListenersInitialized = false;

const SCROLL_BOTTOM_THRESHOLD = 50;

function getScrollElements() {
    const scrollContainer = document.getElementById('content');
    const responseContainer = document.getElementById('respons');
    return {
        scrollContainer,
        responseContainer,
        targets: [scrollContainer, responseContainer].filter(Boolean),
    };
}

function isElementNearBottom(element, threshold = SCROLL_BOTTOM_THRESHOLD) {
    if (!element) {
        return true;
    }

    const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
    return distanceFromBottom <= threshold;
}

// Initialize scroll event listeners to detect user scrolling
function initScrollListeners() {
    if (scrollListenersInitialized) {
        return;
    }

    const { targets } = getScrollElements();
    if (targets.length === 0) {
        return;
    }

    targets.forEach((element) => {
        // Detect wheel events (user scrolling with mouse wheel)
        element.addEventListener('wheel', () => {
            autoScrollEnabled = false;
        }, { passive: true });

        // Disable autoscroll when user manually scrolls away from the bottom.
        // Re-enable when they return to the bottom.
        element.addEventListener('scroll', () => {
            const isAtBottom = isElementNearBottom(element);

            if (autoScrollEnabled) {
                if (!isAtBottom) {
                    autoScrollEnabled = false;
                }
                return;
            }

            if (isAtBottom) {
                autoScrollEnabled = true;
            }
        }, { passive: true });
    });

    scrollListenersInitialized = true;
}

// Function to append chat elements
function appendChatElement(parent, className, text) {
    const element = document.createElement('span');
    element.className = className;
    element.innerText = text;
    parent.appendChild(element);

    scrollChatToBottom();
    return element;
}

// Ensure the chat history stays pinned to the most recent message
function scrollChatToBottom() {
    // Initialize listeners on first call
    if (!scrollListenersInitialized) {
        initScrollListeners();
    }

    // Only autoscroll if enabled
    if (!autoScrollEnabled) {
        return;
    }

    const { scrollContainer, responseContainer, targets } = getScrollElements();
    if (targets.length === 0) {
        return;
    }

    requestAnimationFrame(() => {
        targets.forEach((element) => {
            if (typeof element.scrollTo === 'function') {
                element.scrollTo({ top: element.scrollHeight, behavior: 'auto' });
            } else {
                element.scrollTop = element.scrollHeight;
            }
        });

        if (responseContainer && responseContainer.lastElementChild) {
            responseContainer.lastElementChild.scrollIntoView({ block: 'end', inline: 'nearest' });
        }
    });
}

// Function to clear chat
function clear() {
    const response = document.getElementById('respons');
    const sourceurls = document.getElementById('source_urls');

    // Clear all messages and button rows from the response container
    response.innerHTML = "";

    // Note: We do NOT clear the sources window content because we want to preserve it
    // The sourceurls div will be rebuilt when the user clicks Sources button again

    // Don't clear cached sources - we want to preserve web context
    // clearCachedSources(); // Removed to preserve web context

    clearMessages()
        .then(data => {
            console.log(data);
            const clearMsg = appendChatElement(response, 'system_message', 'FinSearch: Conversation cleared. Web content context preserved.');
            scrollChatToBottom();
        })
        .catch(error => {
            console.error('There was a problem clearing messages:', error);
        });
}

// Ask button click
function get_chat_response() {
    const question = document.getElementById('textbox').value;

    if (question) {
        handleChatResponse(question, false);
        logQuestion(question, 'Ask');
        document.getElementById('textbox').value = '';
    } else {
        alert("Please enter a question.");
    }
}

// Advanced Ask button click
function get_adv_chat_response() {
    const question = document.getElementById('textbox').value.trim();

    if (question === '') {
        alert("Please enter a message.");
        return;
    }

    // Text Processing Mode
    handleChatResponse(question, true);
    logQuestion(question, 'Advanced Ask');

    document.getElementById('textbox').value = '';
}

// Unified submit function for the new mode selector
function submit_question(mode) {
    const question = document.getElementById('textbox').value.trim();

    if (question === '') {
        alert("Please enter a message.");
        return;
    }

    if (mode === 'Thinking') {
        // Thinking mode - equivalent to old "Ask" button
        handleChatResponse(question, false);
        logQuestion(question, 'Thinking');
    } else if (mode === 'Research') {
        // Research mode - equivalent to old "Advanced Ask" button
        handleChatResponse(question, true);
        logQuestion(question, 'Research');
    }

    document.getElementById('textbox').value = '';
}

// Function to get sources
async function get_sources() {
    const sources_window = document.getElementById('sources_window');
    const loadingSpinner = document.getElementById('loading_spinner');
    const sourceContainer = document.getElementById('source_urls');

    sources_window.style.display = 'block';
    loadingSpinner.style.display = 'block';
    sourceContainer.style.display = 'none';
    sourceContainer.innerHTML = '';

    const currentPageUrl = getCurrentPageUrl();
    const searchQuery = getLastSearchQuery();
    const cachedOtherSources = getCachedSourceEntries(false);
    const cachedCurrentSource = getCurrentPageEntry();

    console.log('[Sources Debug] Loading sources for query:', searchQuery);
    console.log('[Sources Debug] Cached sources (excluding current page):', cachedOtherSources);

    const neutralDomainParts = ['co', 'com', 'org', 'net', 'gov', 'edu'];

    const toTitleCase = (value) => value.replace(/\b\w/g, (letter) => letter.toUpperCase());

    const getSiteNameFromUrl = (url) => {
        try {
            const { hostname } = new URL(url);
            const trimmed = hostname.replace(/^www\./i, '');
            const parts = trimmed.split('.');
            let candidate = parts.length >= 2 ? parts[parts.length - 2] : parts[0];
            if (neutralDomainParts.includes(candidate) && parts.length >= 3) {
                candidate = parts[parts.length - 3];
            }
            const cleaned = candidate.replace(/[-_]/g, ' ').trim();
            return cleaned ? toTitleCase(cleaned) : trimmed;
        } catch (error) {
            return 'Source';
        }
    };

    const normalizeEntry = (entry) => {
        if (!entry || !entry.url) {
            return null;
        }
        const fallback = createFallbackMetadata(entry.url);
        if (!fallback) {
            return null;
        }
        const snippetSource = typeof entry.snippet === 'string' ? entry.snippet : fallback.snippet;
        const normalizedSnippet = snippetSource ? snippetSource.replace(/\s+/g, ' ').trim() : '';

        return {
            ...fallback,
            ...entry,
            site_name: entry.site_name || fallback.site_name,
            display_url: entry.display_url || fallback.display_url,
            title: entry.title || fallback.title,
            snippet: normalizedSnippet || fallback.snippet,
            icon: null,
            provisional: entry.provisional ?? fallback.provisional,
        };
    };

    const formatDisplayUrl = (url) => {
        if (!url) {
            return '';
        }

        const MAX_DISPLAY_URL_LENGTH = 30;
        let display = url;
        try {
            const parsed = new URL(url);
            display = `${parsed.hostname}${parsed.pathname}${parsed.search || ''}`;
        } catch (error) {
            // Leave display as url when it cannot be parsed
        }

        display = String(display).replace(/^www\./i, '');

        if (display.length > MAX_DISPLAY_URL_LENGTH) {
            const truncatedLength = Math.max(0, MAX_DISPLAY_URL_LENGTH - 3);
            display = `${display.slice(0, truncatedLength)}...`;
        }

        return display;
    };

    const createFallbackMetadata = (entry) => {
        if (!entry) {
            return null;
        }

        const url = typeof entry === 'string' ? entry : entry.url || '';
        if (!url) {
            return null;
        }

        const displayUrl = formatDisplayUrl(url);
        const siteName = getSiteNameFromUrl(url);
        const title = siteName || displayUrl;

        return {
            url,
            site_name: siteName,
            display_url: displayUrl,
            title,
            icon: null,
            provisional: false,
            snippet: '',
        };
    };

    const buildThumbnail = (wrapper, metadata) => {
        const fallbackInitial = (metadata.site_name || metadata.display_url || '?').charAt(0).toUpperCase();
        wrapper.innerHTML = '';
        wrapper.classList.add('source-card-thumbnail--fallback');
        wrapper.textContent = fallbackInitial || '?';
    };

    const buildXbrlCard = (safeMeta) => {
        const filename = safeMeta.display_url || formatDisplayUrl(safeMeta.url) || 'xbrl-filing';
        const titleText = safeMeta.title && safeMeta.title !== filename
            ? safeMeta.title
            : 'SEC XBRL Filing';

        const cardLink = document.createElement('a');
        cardLink.className = 'source-card source-card--xbrl';
        cardLink.href = safeMeta.url;
        cardLink.target = '_blank';
        cardLink.rel = 'noopener noreferrer';
        cardLink.setAttribute('download', filename);

        const headerWrapper = document.createElement('div');
        headerWrapper.className = 'source-card-header';

        const thumbnailWrapper = document.createElement('div');
        thumbnailWrapper.className = 'source-card-thumbnail source-card-thumbnail--xbrl';
        thumbnailWrapper.textContent = 'X';

        const filenameLabel = document.createElement('span');
        filenameLabel.className = 'source-card-xbrl-filename';
        filenameLabel.textContent = filename;
        filenameLabel.title = filename;

        headerWrapper.appendChild(thumbnailWrapper);
        headerWrapper.appendChild(filenameLabel);

        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'source-card-content';

        const titleLine = document.createElement('span');
        titleLine.className = 'source-card-title';
        titleLine.textContent = titleText;
        contentWrapper.appendChild(titleLine);

        cardLink.appendChild(headerWrapper);
        cardLink.appendChild(contentWrapper);
        cardLink.setAttribute('aria-label', `Download XBRL filing ${filename}`);
        return cardLink;
    };

    const buildSourceCard = (metadata) => {
        const safeMeta = metadata || {};
        if (!safeMeta.url) {
            return null;
        }

        if (safeMeta.source_type === 'xbrl') {
            return buildXbrlCard(safeMeta);
        }

        const cardLink = document.createElement('a');
        cardLink.className = 'source-card';
        cardLink.href = safeMeta.url;
        cardLink.target = '_blank';
        cardLink.rel = 'noopener noreferrer';
        if (safeMeta.provisional) {
            cardLink.classList.add('source-card--provisional');
        }

        const thumbnailWrapper = document.createElement('div');
        thumbnailWrapper.className = 'source-card-thumbnail';
        buildThumbnail(thumbnailWrapper, safeMeta);

        const headerWrapper = document.createElement('div');
        headerWrapper.className = 'source-card-header';

        const siteName = document.createElement('span');
        siteName.className = 'source-card-site';
        siteName.innerText = safeMeta.site_name || getSiteNameFromUrl(safeMeta.url);

        const displayUrl = document.createElement('span');
        displayUrl.className = 'source-card-url';
        const formattedDisplayUrl = formatDisplayUrl(safeMeta.url || safeMeta.display_url);
        displayUrl.innerText = formattedDisplayUrl;
        if (safeMeta.url || safeMeta.display_url) {
            displayUrl.title = safeMeta.url || safeMeta.display_url;
        }

        const metaWrapper = document.createElement('div');
        metaWrapper.className = 'source-card-meta';
        metaWrapper.appendChild(siteName);
        metaWrapper.appendChild(displayUrl);

        headerWrapper.appendChild(thumbnailWrapper);
        headerWrapper.appendChild(metaWrapper);

        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'source-card-content';

        const titleLink = document.createElement('span');
        titleLink.className = 'source-card-title';
        titleLink.innerText = safeMeta.title || siteName.innerText || displayUrl.innerText;

        contentWrapper.appendChild(titleLink);
        if (safeMeta.provisional) {
            const statusBadge = document.createElement('span');
            statusBadge.className = 'source-card-status';
            statusBadge.innerText = 'Preview';
            contentWrapper.appendChild(statusBadge);
        }

        cardLink.appendChild(headerWrapper);
        cardLink.appendChild(contentWrapper);
        cardLink.setAttribute('aria-label', `${siteName.innerText}: ${titleLink.innerText}`);
        return cardLink;
    };

    const appendSection = (title, items, emptyMessage) => {
        const section = document.createElement('div');
        section.className = 'source-section';

        const header = document.createElement('div');
        header.className = 'source-section-header';
        header.innerText = title;
        section.appendChild(header);

        const content = document.createElement('div');
        content.className = 'source-section-content';

        if (items.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'empty-sources';
            empty.innerText = emptyMessage;
            content.appendChild(empty);
        } else {
            items.forEach((metadata) => {
                const card = buildSourceCard(metadata);
                if (card) {
                    content.appendChild(card);
                }
            });
        }

        section.appendChild(content);
        sourceContainer.appendChild(section);
    };

    let currentMetadata = cachedCurrentSource
        ? normalizeEntry(cachedCurrentSource)
        : (currentPageUrl ? createFallbackMetadata(currentPageUrl) : null);
    let sourcesToRender = cachedOtherSources.map((entry) => normalizeEntry(entry)).filter(Boolean);

    try {
        const response = await getSourceUrls(searchQuery || '', currentPageUrl);
        const payload = response?.resp || {};

        const backendSourcesRaw = Array.isArray(payload.sources) ? payload.sources : [];
        const backendCurrentRaw = payload.current_page && payload.current_page.url ? payload.current_page : null;

        mergeCachedMetadata([
            ...backendSourcesRaw,
            ...(backendCurrentRaw ? [backendCurrentRaw] : []),
        ]);

        if (backendCurrentRaw) {
            currentMetadata = normalizeEntry(backendCurrentRaw);
        } else if (currentPageUrl) {
            const refreshedCurrent = getCurrentPageEntry();
            if (refreshedCurrent) {
                currentMetadata = normalizeEntry(refreshedCurrent);
            }
        } else {
            currentMetadata = null;
        }

        const refreshedSources = getCachedSourceEntries(false).map((entry) => normalizeEntry(entry)).filter(Boolean);
        const backendFallback = backendSourcesRaw.map((entry) => normalizeEntry(entry)).filter(Boolean);

        if (refreshedSources.length > 0) {
            sourcesToRender = refreshedSources;
        } else if (backendFallback.length > 0) {
            sourcesToRender = backendFallback;
        }
    } catch (error) {
        console.error('Unable to load source previews:', error);
    } finally {
        loadingSpinner.style.display = 'none';
        sourceContainer.style.display = 'flex';
    }

    appendSection(
        'Current active webpage',
        currentMetadata ? [currentMetadata] : [],
        'No current webpage detected'
    );

    appendSection(
        'Sources used',
        sourcesToRender,
        'Sources used for agent responses when using Research mode will appear here.'
    );
}

// Removed old preferred links functions - now handled by link_manager.js component

// Function to make an element draggable and resizable
function makeDraggableAndResizable(element, sourceWindowOffsetX = 10, onLayoutChange = null) {
    const MIN_WIDTH = 360;
    const MIN_HEIGHT = 420;

    let isDragging = false;
    let offsetX = 0;
    let offsetY = 0;

    let isResizing = false;
    let resizeDirections = { left: false, right: false, top: false, bottom: false };
    let startX = 0;
    let startY = 0;
    let startWidth = 0;
    let startHeight = 0;
    let startLeft = 0;
    let startTop = 0;
    let layoutChanged = false;

    const dragHandle = element.querySelector('.draggable');
    if (dragHandle) {
        dragHandle.addEventListener('mousedown', (event) => {
            if (shouldIgnoreTarget(event)) {
                return;
            }

            event.preventDefault();
            isDragging = true;

            const rect = element.getBoundingClientRect();
            offsetX = event.clientX - rect.left;
            offsetY = event.clientY - rect.top;

            document.addEventListener('mousemove', dragElement);
            document.addEventListener('mouseup', stopInteraction);
        });
    }

    const resizeHandles = element.querySelectorAll('[data-resize-handle]');
    resizeHandles.forEach((handle) => {
        handle.addEventListener('mousedown', (event) => {
            event.preventDefault();
            event.stopPropagation();
            beginResize(event, handle.dataset.resizeHandle || '');
        });
    });

    function shouldIgnoreTarget(event) {
        const tagName = event.target.tagName;
        return ['INPUT', 'TEXTAREA', 'BUTTON', 'A'].includes(tagName) || event.target.id === 'position-mode-icon';
    }

    function dragElement(event) {
        if (!isDragging) {
            return;
        }

        event.preventDefault();
        const positionMode = window.getComputedStyle(element).position;
        const isFixed = positionMode === 'fixed';

        const newLeft = isFixed
            ? event.clientX - offsetX
            : event.clientX - offsetX + window.scrollX;
        const newTop = isFixed
            ? event.clientY - offsetY
            : event.clientY - offsetY + window.scrollY;

        element.style.left = `${newLeft}px`;
        element.style.top = `${newTop}px`;
        element.style.right = 'auto';
        layoutChanged = true;

        updateSourcesWindowPosition();
    }

    function beginResize(event, direction) {
        isResizing = true;

        const rect = element.getBoundingClientRect();
        const positionMode = window.getComputedStyle(element).position;
        const isFixed = positionMode === 'fixed';

        startX = event.clientX;
        startY = event.clientY;
        startWidth = rect.width;
        startHeight = rect.height;
        startLeft = isFixed ? rect.left : rect.left + window.scrollX;
        startTop = isFixed ? rect.top : rect.top + window.scrollY;

        resizeDirections = {
            left: direction.includes('left'),
            right: direction.includes('right'),
            top: direction.includes('top'),
            bottom: direction.includes('bottom'),
        };

        if (resizeDirections.left) {
            element.style.left = `${startLeft}px`;
            element.style.right = 'auto';
        }

        document.addEventListener('mousemove', resizeElement);
        document.addEventListener('mouseup', stopInteraction);
    }

    function resizeElement(event) {
        if (!isResizing) {
            return;
        }

        event.preventDefault();

        const deltaX = event.clientX - startX;
        const deltaY = event.clientY - startY;

        let newWidth = startWidth;
        if (resizeDirections.right) {
            newWidth = Math.max(MIN_WIDTH, startWidth + deltaX);
            layoutChanged = true;
        }
        if (resizeDirections.left) {
            const widthFromLeft = startWidth - deltaX;
            newWidth = Math.max(MIN_WIDTH, widthFromLeft);
            const appliedLeft = startLeft + (startWidth - newWidth);
            element.style.left = `${appliedLeft}px`;
            element.style.right = 'auto';
            layoutChanged = true;
        }
        element.style.width = `${newWidth}px`;

        if (resizeDirections.bottom) {
            const nextHeight = Math.max(MIN_HEIGHT, startHeight + deltaY);
            element.style.height = `${nextHeight}px`;
            layoutChanged = true;
        }

        if (resizeDirections.top) {
            const heightFromTop = startHeight - deltaY;
            const constrainedHeight = Math.max(MIN_HEIGHT, heightFromTop);
            const appliedTop = startTop + (startHeight - constrainedHeight);
            element.style.top = `${appliedTop}px`;
            element.style.height = `${constrainedHeight}px`;
            layoutChanged = true;
        }

        updateSourcesWindowPosition();
    }

    function updateSourcesWindowPosition() {
        const sourcesWindow = document.getElementById('sources_window');
        if (!sourcesWindow) {
            return;
        }

        const measuredWidth = sourcesWindow.offsetWidth || 360;
        const popupRect = element.getBoundingClientRect();
        const sourcesPosition = window.getComputedStyle(sourcesWindow).position;
        const isFixedSources = sourcesPosition === 'fixed';

        const baseLeft = isFixedSources ? popupRect.left : element.offsetLeft;
        const baseTop = isFixedSources ? popupRect.top : element.offsetTop;

        const sourcesLeft = Math.max(0, baseLeft - (measuredWidth + sourceWindowOffsetX));
        sourcesWindow.style.left = `${sourcesLeft}px`;
        sourcesWindow.style.top = `${baseTop}px`;
    }

    function stopInteraction() {
        document.removeEventListener('mousemove', dragElement);
        document.removeEventListener('mousemove', resizeElement);
        document.removeEventListener('mouseup', stopInteraction);
        isDragging = false;
        isResizing = false;

        if (layoutChanged && typeof onLayoutChange === 'function') {
            onLayoutChange();
        }
        layoutChanged = false;
    }
}

// ── Layer 1 Validate ────────────────────────────────────────────────
// Click handler for the Validate button. POSTs session_id to the backend,
// renders the XBRL validation report as a fresh conversation turn (user
// request + agent reply), and best-effort highlights claimed numbers that
// failed verification in the preceding agent response.

function _statusGlyph(status) {
    if (status === 'VERIFIED')       return 'VERIFIED';
    if (status === 'FAILED')         return 'FAILED';
    if (status === 'NOT_APPLICABLE') return 'N/A';
    return 'SKIPPED';
}

function _formatNumber(n) {
    if (n === null || n === undefined) return '—';
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(3) + 'B';
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(3) + 'M';
    return Number.isInteger(n) ? n.toString() : n.toFixed(4);
}

function _buildValidateMarkdown(data) {
    const claims = data.claims || [];
    const summary = data.summary || {};
    const total = summary.total || claims.length;
    const verified = summary.VERIFIED || 0;
    const failed = summary.FAILED || 0;
    const na = summary.NOT_APPLICABLE || 0;

    if (total === 0) {
        return '**Validation Report**\n\nNo ratio claims were recorded for the previous response, so there is nothing to validate.';
    }

    const headline = failed > 0
        ? `**${failed} of ${total} claim${total === 1 ? '' : 's'} failed verification** against SEC XBRL filings.`
        : na === total
            ? `All ${total} claim${total === 1 ? '' : 's'} marked not applicable for this filer's reporting structure.`
            : `**All ${verified} of ${total} claim${total === 1 ? '' : 's'} verified** against SEC XBRL filings.`;

    const lines = [
        '**Validation Report**',
        '',
        headline,
        '',
        '| Ratio | Ticker | Period | Your Claim | XBRL Ground Truth | Variance | Status |',
        '|---|---|---|---:|---:|---:|:---:|',
    ];

    for (const c of claims) {
        const ratio = c.ratio ? c.ratio.replace(/_/g, ' ') : '—';
        const claimStr = c.actual !== undefined && c.actual !== null
            ? _formatNumber(c.actual)
            : (c.claimed_value !== undefined ? _formatNumber(c.claimed_value) : '—');
        const expectedStr = c.expected !== undefined && c.expected !== null
            ? _formatNumber(c.expected)
            : '—';
        const varStr = c.variance_pct !== undefined && c.variance_pct !== null
            ? `${c.variance_pct.toFixed(3)}%`
            : '—';
        lines.push(
            `| ${ratio} | ${c.ticker || '—'} | ${c.period || '—'} | ${claimStr} | ${expectedStr} | ${varStr} | ${_statusGlyph(c.status)} |`
        );
    }

    const notes = claims.filter(c => c.message).map(c =>
        `- **${c.ticker} · ${c.ratio ? c.ratio.replace(/_/g, ' ') : ''}**: ${c.message}`
    );
    if (notes.length) {
        lines.push('', ...notes);
    }

    const sources = Array.from(new Set(claims.map(c => c.xbrl_source).filter(Boolean)));
    if (sources.length) {
        lines.push('', `*Ground truth: ${sources.map(s => `\`${s}\``).join(', ')}*`);
    }

    return lines.join('\n');
}

function _renderValidateResults(data) {
    const responseContainer = document.getElementById('respons');
    if (!responseContainer) return;

    // Capture the preceding agent response bubble BEFORE appending new
    // content — that's the bubble whose numbers should be highlighted if any
    // claim failed.
    const priorBubbles = responseContainer.querySelectorAll('.agent_response');
    const priorAgentBubble = priorBubbles.length ? priorBubbles[priorBubbles.length - 1] : null;

    // Append user-side prompt + agent-side reply to mimic a conversation turn.
    appendChatElement(responseContainer, 'your_question', 'Validate the numerical claims in your last response against SEC XBRL filings.');

    const replyElement = appendChatElement(responseContainer, 'agent_response', '');
    renderMarkdownContent(replyElement, _buildValidateMarkdown(data));

    scrollChatToBottom();

    // Decorate each pre-wrapped number in the prior agent bubble with
    // its Validate status (VERIFIED / FAILED / NOT_APPLICABLE). The
    // spans were wrapped server-side during post-processing; this just
    // joins status by claim_id.
    if (priorAgentBubble) {
        decorateClaimMarks(priorAgentBubble, data.claims || []);
    }
}

async function validate_response() {
    const responseContainer = document.getElementById('respons');
    try {
        const data = await validateClaims();
        _renderValidateResults(data);
    } catch (err) {
        console.error('Validate failed:', err);
        if (responseContainer) {
            appendChatElement(responseContainer, 'your_question', 'Validate the numerical claims in your last response against SEC XBRL filings.');
            const replyElement = appendChatElement(responseContainer, 'agent_response', '');
            renderMarkdownContent(replyElement, '**Validation request failed.** Unable to reach the validation service. Check the backend logs and try again.');
            scrollChatToBottom();
        }
    }
}

export {
    appendChatElement,
    clear,
    get_chat_response,
    get_adv_chat_response,
    submit_question,
    get_sources,
    validate_response,
    makeDraggableAndResizable,
    scrollChatToBottom,
};
