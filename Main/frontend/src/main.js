// main.js
import { postWebTextToServer, setSessionId, triggerAutoScrape, setAutoScrapePromise } from './modules/api.js';
import { createUI } from './modules/ui.js';
import { fetchAvailableModels } from './modules/config.js';
import { initializeWithCurrentPage } from './modules/sourcesCache.js';
import { getBackendBaseUrl } from './modules/backendConfig.js';

// Generate a cryptographically secure unique session ID for this page load
function generateSecureSessionId() {
    const timestamp = Date.now();
    const randomBytes = new Uint8Array(9);
    crypto.getRandomValues(randomBytes);
    const randomString = Array.from(randomBytes, byte => byte.toString(36)).join('').substr(0, 9);
    return `session_${timestamp}_${randomString}`;
}

const sessionId = generateSecureSessionId();
setSessionId(sessionId);
console.log("Agentic FinSearch Session ID: ", sessionId);

const currentUrl = window.location.href.toString();
console.log("Current page: ", currentUrl);

// Initialize sources cache with current page URL
initializeWithCurrentPage(currentUrl);

// Legacy JS scraping DISABLED - Using Playwright MCP only
// const textContent = document.body.innerText || "";
// const encodedContent = encodeURIComponent(textContent);

// Fetch available models from backend on startup
fetchAvailableModels().then(() => {
    console.log("Models fetched from backend");
}).catch(error => {
    console.error("CRITICAL: Failed to fetch models from backend:", error);
    alert(`Failed to connect to Agentic FinSearch backend: ${error.message}\n\nPlease ensure the backend server at ${getBackendBaseUrl()} is reachable.`);
});


// Initialize UI
const { popup, settings_window, sources_window, searchQuery } = createUI();

// Trigger auto-scraping of the current page
console.log("Triggering auto-scrape for:", currentUrl);

// Show loading indicator
const loadingIndicator = document.getElementById('auto-scrape-loading');
if (loadingIndicator) {
    loadingIndicator.style.display = 'flex';
}

// Store the promise so chat requests can wait for it to complete
setAutoScrapePromise(triggerAutoScrape(currentUrl)).finally(() => {
    // Hide loading indicator
    if (loadingIndicator) {
        loadingIndicator.style.display = 'none';
    }
});
