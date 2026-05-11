// sourcesCache.js - Manages cached sources from Research mode

let cachedSources = [];
let cachedMetadata = new Map();
let lastSearchQuery = '';
let currentPageUrl = '';

const neutralDomainParts = ['co', 'com', 'org', 'net', 'gov', 'edu'];

function toTitleCase(value) {
    return value.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function getSiteNameFromUrl(url) {
    try {
        const { hostname } = new URL(url);
        const trimmed = hostname.replace(/^www\./i, '');
        const parts = trimmed.split('.');
        let candidate = parts.length >= 2 ? parts[parts.length - 2] : parts[0];
        if (neutralDomainParts.includes(candidate) && parts.length >= 3) {
            candidate = parts[parts.length - 3];
        }
        const cleaned = candidate.replace(/[-_]/g, ' ').trim();
        return cleaned ? toTitleCase(cleaned) : trimmed || 'Source';
    } catch (error) {
        return 'Source';
    }
}

const MAX_DISPLAY_URL_LENGTH = 30;

function formatDisplayUrl(url) {
    if (!url) {
        return '';
    }
    try {
        const parsed = new URL(url);
        let display = `${parsed.hostname}${parsed.pathname || ''}`;
        if (parsed.search) {
            display += parsed.search;
        }
        display = display.replace(/^www\./i, '');
        if (display.length > MAX_DISPLAY_URL_LENGTH) {
            const truncatedLength = Math.max(0, MAX_DISPLAY_URL_LENGTH - 3);
            display = `${display.slice(0, truncatedLength)}...`;
        }
        return display || url;
    } catch (error) {
        return url;
    }
}

function createFallbackMetadata(url) {
    if (!url) {
        return null;
    }
    const displayUrl = formatDisplayUrl(url);
    return {
        url,
        site_name: getSiteNameFromUrl(url),
        display_url: displayUrl,
        title: displayUrl,
        icon: null,
        provisional: false,
        snippet: '',
    };
}

function normalizeMetadata(url, raw = {}) {
    if (!url) {
        return null;
    }
    const fallback = createFallbackMetadata(url);
    if (!fallback) {
        return null;
    }

    const snippetSource = typeof raw.snippet === 'string' ? raw.snippet : fallback.snippet;
    const normalizedSnippet = snippetSource ? snippetSource.replace(/\s+/g, ' ').trim() : '';

    const normalized = {
        url,
        site_name: raw.site_name || fallback.site_name,
        display_url: raw.display_url || fallback.display_url,
        title: raw.title || fallback.title,
        icon: null,
        provisional: raw.provisional ?? fallback.provisional,
        snippet: normalizedSnippet,
        source_type: raw.source_type ?? null,
    };

    return normalized;
}

// Initialize with current page URL
function initializeWithCurrentPage(url) {
    currentPageUrl = url;
    cachedSources = url ? [url] : [];
    const metadata = normalizeMetadata(url);
    cachedMetadata.clear();
    if (url && metadata) {
        cachedMetadata.set(url, metadata);
    }
    console.log('Initialized with current page:', url);
}

// Store sources from Research response
function setCachedSources(urls, searchQuery = '', metadataList = []) {
    console.log('[Sources Debug] setCachedSources called');
    console.log('[Sources Debug] Input URLs:', urls);
    console.log('[Sources Debug] Current page URL:', currentPageUrl);

    const normalizedMetadata = new Map();
    (metadataList || []).forEach((entry) => {
        if (entry && entry.url) {
            const normalized = normalizeMetadata(entry.url, entry);
            if (normalized) {
                normalizedMetadata.set(entry.url, normalized);
            }
        }
    });

    const inputUrls = Array.isArray(urls) ? urls.filter(Boolean) : [];
    const filteredUrls = inputUrls.filter((url) => url !== currentPageUrl);
    const uniqueFilteredUrls = Array.from(new Set(filteredUrls));
    const existingUrls = cachedSources.filter((url) => url !== currentPageUrl);
    const mergedUrls = [
        ...existingUrls,
        ...uniqueFilteredUrls.filter((url) => !existingUrls.includes(url)),
    ];

    cachedSources = currentPageUrl ? [currentPageUrl, ...mergedUrls] : [...mergedUrls];
    if (searchQuery) {
        lastSearchQuery = searchQuery;
    }

    const allowedUrls = new Set(cachedSources);
    Array.from(cachedMetadata.keys()).forEach((url) => {
        if (!allowedUrls.has(url)) {
            cachedMetadata.delete(url);
        }
    });

    cachedSources.forEach((url) => {
        const incoming = normalizedMetadata.get(url) || cachedMetadata.get(url) || {};
        const normalized = normalizeMetadata(url, incoming);
        if (normalized) {
            cachedMetadata.set(url, normalized);
        }
    });

    cachedSources.forEach((url, idx) => {
        if (url.includes('duckduckgo')) {
            console.warn(`[Sources Debug] WARNING: DuckDuckGo URL cached at index ${idx}: ${url}`);
        }
    });

    console.log('[Sources Debug] Final cached sources:', cachedSources);
    console.log('[Sources Debug] Total cached:', cachedSources.length, 'URLs');
}

function mergeCachedMetadata(metadataList = []) {
    (metadataList || []).forEach((entry) => {
        if (!entry || !entry.url) {
            return;
        }
        if (!cachedSources.includes(entry.url)) {
            return;
        }
        const normalized = normalizeMetadata(entry.url, entry);
        if (normalized) {
            cachedMetadata.set(entry.url, normalized);
        }
    });
}

function getCachedSourceEntries(includeCurrentPage = false) {
    return cachedSources
        .filter((url) => includeCurrentPage || url !== currentPageUrl)
        .map((url) => {
            const metadata = cachedMetadata.get(url) || normalizeMetadata(url);
            return metadata;
        })
        .filter(Boolean);
}

function getCurrentPageEntry() {
    if (!currentPageUrl) {
        return null;
    }
    return cachedMetadata.get(currentPageUrl) || normalizeMetadata(currentPageUrl);
}

// Clear cached sources but preserve current page metadata
function clearCachedSources() {
    const currentMetadata = currentPageUrl ? getCurrentPageEntry() : null;
    if (currentPageUrl && currentMetadata) {
        cachedSources = [currentPageUrl];
        cachedMetadata = new Map([[currentPageUrl, currentMetadata]]);
    } else {
        cachedSources = [];
        cachedMetadata.clear();
    }
    lastSearchQuery = '';
}

function getLastSearchQuery() {
    return lastSearchQuery;
}

function getCurrentPageUrl() {
    return currentPageUrl;
}

export {
    initializeWithCurrentPage,
    setCachedSources,
    mergeCachedMetadata,
    getCachedSourceEntries,
    getCurrentPageEntry,
    clearCachedSources,
    getLastSearchQuery,
    getCurrentPageUrl,
};
