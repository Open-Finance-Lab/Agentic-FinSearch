import { buildBackendUrl, authFetch } from '../backendConfig.js';

export function createLinkManager() {
    // Container for the whole link manager
    const linkList = document.createElement('div');
    linkList.className = 'link-list';

    // Modal for delete confirmation
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.display = 'none';
    modal.innerHTML = `
      <div class="modal-content">
        <p>Are you sure you want to delete this link?</p>
        <div class="modal-buttons">
          <button class="confirm-btn">Delete</button>
          <button class="cancel-btn">Cancel</button>
        </div>
      </div>
    `;

    let linkToDelete = null;

    // Load and save functions for localStorage
    function loadPreferredLinks() {
        try {
            const stored = localStorage.getItem('preferredLinks');
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            console.error('Error loading preferred links:', e);
            return [];
        }
    }

    function savePreferredLinks(links, { sync = true } = {}) {
        try {
            localStorage.setItem('preferredLinks', JSON.stringify(links));
            if (sync) {
                // Sync with backend
                syncWithBackend(links);
            }
        } catch (e) {
            console.error('Error saving preferred links:', e);
        }
    }

    function syncWithBackend(links) {
        // Optional: Sync with backend if available
        if (typeof fetch !== 'undefined') {
            authFetch(buildBackendUrl('/api/sync_preferred_urls/'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ urls: links })
            })
            .then(response => response.json())
            .then(data => {
                console.log('Synced with backend:', data);
            })
            .catch(error => {
                console.error('Error syncing with backend:', error);
            });
        }
    }

    function ensureInputWrapper() {
        let inputWrapper = linkList.querySelector('.link-input:not([data-link-url])');
        if (!inputWrapper) {
            inputWrapper = createLinkInput();
        }
        return inputWrapper;
    }

    function createLinkInput() {
        const wrapper = document.createElement('div');
        wrapper.className = 'link-input';

        const btn = document.createElement('button');
        btn.className = 'plus-btn';
        btn.textContent = '+';

        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = 'Paste link...';

        wrapper.appendChild(btn);
        wrapper.appendChild(input);
        linkList.appendChild(wrapper);

        btn.addEventListener('click', () => {
            wrapper.classList.add('input-visible');
            input.focus();
        });

        input.addEventListener('blur', () => {
            if (input.value.trim() === '') {
                wrapper.classList.remove('input-visible');
            }
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const value = input.value.trim();
                if (value !== '') {
                    addLink(value, wrapper);
                    createLinkInput();
                    // Save to localStorage - getAllLinks() already includes the new link
                    const links = getAllLinks();
                    savePreferredLinks(links);
                }
            }
        });

        return wrapper;
    }

    function addLink(value, wrapper, skipSave = false) {
        // Create DOM elements safely to prevent XSS
        const preview = document.createElement('div');
        preview.className = 'link-preview';

        const span = document.createElement('span');
        span.textContent = value; // Use textContent to prevent XSS

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'delete-btn';
        deleteBtn.title = 'Delete Link';
        deleteBtn.textContent = '×'; // Use textContent for the × character

        preview.appendChild(span);
        preview.appendChild(deleteBtn);

        // Clear and append safely
        while (wrapper.firstChild) {
            wrapper.removeChild(wrapper.firstChild);
        }
        wrapper.appendChild(preview);

        wrapper.dataset.linkUrl = value; // Store URL in dataset
        deleteBtn.addEventListener('click', () => {
            linkToDelete = wrapper;
            modal.style.display = 'block';
        });

        if (!skipSave) {
            // This is for initial load, don't save again
        }
    }

    function getAllLinks() {
        const links = [];
        const linkElements = linkList.querySelectorAll('[data-link-url]');
        linkElements.forEach(el => {
            if (el.dataset.linkUrl) {
                links.push(el.dataset.linkUrl);
            }
        });
        return links;
    }

    function populateLinks(links) {
        const inputWrapper = ensureInputWrapper();
        linkList.querySelectorAll('[data-link-url]').forEach(wrapper => wrapper.remove());
        links.forEach(link => {
            const wrapper = document.createElement('div');
            wrapper.className = 'link-input';
            linkList.insertBefore(wrapper, inputWrapper);
            addLink(link, wrapper, true); // Skip save since we're rendering state
        });
    }

    function hydrateFromBackend() {
        if (typeof fetch === 'undefined') {
            return;
        }

        authFetch(buildBackendUrl('/api/get_preferred_urls/'), {
            method: 'GET',
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Failed to load preferred links: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            const urls = Array.isArray(data.urls) ? data.urls : [];
            if (urls.length === 0) {
                return;
            }

            const storedLinks = loadPreferredLinks();
            if (storedLinks.length > 0) {
                return; // Respect user-customized links in local storage
            }

            savePreferredLinks(urls, { sync: false });
            populateLinks(urls);
        })
        .catch(error => {
            console.error('Error fetching preferred links from backend:', error);
        });
    }

    // Modal logic
    modal.querySelector('.confirm-btn').addEventListener('click', () => {
        if (linkToDelete) {
            linkToDelete.remove();
            linkToDelete = null;
            // Update localStorage after deletion
            const links = getAllLinks();
            savePreferredLinks(links);
        }
        modal.style.display = 'none';
    });

    modal.querySelector('.cancel-btn').addEventListener('click', () => {
        modal.style.display = 'none';
        linkToDelete = null;
    });

    window.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.style.display = 'none';
            linkToDelete = null;
        }
    });

    // Initialize - Load existing links first
    const existingLinks = loadPreferredLinks();
    populateLinks(existingLinks);
    if (existingLinks.length === 0) {
        hydrateFromBackend();
    }

    // Return both the link list and the modal
    const container = document.createElement('div');
    container.appendChild(linkList);
    container.appendChild(modal);

    // Expose a method to get current links
    container.getPreferredLinks = function() {
        return loadPreferredLinks();
    };

    return container;
}
