// backendConfig.js
// Central place to resolve the backend base URL used by the extension.

const DEFAULT_BACKEND_BASE_URL = 'https://agenticfinsearch.org';
let cachedBaseUrl = null;

// Hosts the extension is permitted to talk to. Overrides come from
// window.AGENTIC_BACKEND_URL or localStorage['agenticBackendUrl']; because
// requests are sent with credentials:'include', an unconstrained override
// would let an attacker repoint the credentialed session at an exfil host.
const ALLOWED_LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

function isAllowedBackendUrl(parsed) {
    const hostname = parsed.hostname.toLowerCase();
    const isLocal = ALLOWED_LOCAL_HOSTS.has(hostname);

    // Require https in production; permit http only for local dev hosts.
    if (parsed.protocol === 'https:') {
        // allowed
    } else if (parsed.protocol === 'http:' && isLocal) {
        // allowed for local development
    } else {
        return false;
    }

    if (isLocal) {
        return true;
    }

    // Production: the canonical host or any of its subdomains.
    return hostname === 'agenticfinsearch.org' || hostname.endsWith('.agenticfinsearch.org');
}

function normalizeBaseUrl(url) {
    if (!url) {
        return null;
    }

    try {
        const trimmed = url.trim();
        if (!trimmed) {
            return null;
        }

        const parsed = new URL(trimmed);
        if (!isAllowedBackendUrl(parsed)) {
            console.warn('Ignoring backend URL outside the allow-list:', url);
            return null;
        }
        const pathname = parsed.pathname === '/' ? '' : parsed.pathname.replace(/\/$/, '');
        return `${parsed.protocol}//${parsed.host}${pathname}`;
    } catch (error) {
        console.warn('Ignoring invalid backend URL override:', url, error);
        return null;
    }
}

function resolveOverride() {
    if (typeof window === 'undefined') {
        return null;
    }

    if (window.AGENTIC_BACKEND_URL) {
        const override = normalizeBaseUrl(window.AGENTIC_BACKEND_URL);
        if (override) {
            return override;
        }
    }

    try {
        const stored = window.localStorage?.getItem('agenticBackendUrl');
        const override = normalizeBaseUrl(stored);
        if (override) {
            return override;
        }
    } catch (error) {
        console.debug('Unable to read backend URL override from localStorage:', error);
    }

    return null;
}

function getBackendBaseUrl() {
    if (!cachedBaseUrl) {
        cachedBaseUrl = resolveOverride() ?? DEFAULT_BACKEND_BASE_URL;
    }
    return cachedBaseUrl;
}

function buildBackendUrl(path = '/') {
    const baseUrl = getBackendBaseUrl();
    if (!path) {
        return baseUrl;
    }
    const sanitizedPath = path.startsWith('/') ? path : `/${path}`;
    return `${baseUrl}${sanitizedPath}`;
}

export { getBackendBaseUrl, buildBackendUrl, normalizeBaseUrl };
