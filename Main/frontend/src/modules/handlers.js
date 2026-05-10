// handlers.js
import { appendChatElement, scrollChatToBottom, validate_response } from './helpers.js';
import { hasAxiomClaims } from './api.js';
import { looksLikeValidationRequest } from './intent.js';
import { getChatResponse, getChatResponseStream, waitForAutoScrape, isAutoScrapeInProgress } from './api.js';
import { getSelectedModel, selectedModel } from './config.js';
import { setCachedSources } from './sourcesCache.js';
import { renderMarkdownContent, renderStreamingPreview } from './markdownRenderer.js';

const NAVIGATION_STATUS_LABELS = new Set(['navigating site', 'navigating current page']);
const STATUS_LABEL_REMAPPINGS = {
  'preparing context': 'Processing',
  'drafting answer': 'Drafting response',
  'finalizing response': 'Finalizing response',
  'analyzing query': 'Analyzing your question',
  'planning research': 'Planning research strategy',
  'researching': 'Researching',
  'evaluating results': 'Checking completeness',
  'follow-up research': 'Digging deeper',
  'synthesizing findings': 'Putting it all together',
};
const SEARCH_STATUS_LABEL = 'searching the web';
const READING_STATUS_LABEL = 'reading source';

function normalizeStatusLabel(label) {
  return (label || '').trim().toLowerCase();
}

function getCurrentPageHostname() {
  try {
    if (typeof window !== 'undefined' && window.location) {
      return window.location.hostname || '';
    }
  } catch (_error) {
    // Ignore – fallback below
  }
  return '';
}

function extractHostname(value) {
  if (!value) {
    return '';
  }

  const trimmed = value.trim();
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
    try {
      const parsed = new URL(trimmed);
      return parsed.hostname || '';
    } catch (_error) {
      // Fall through to manual parsing
    }
  }

  const withoutProtocol = trimmed.replace(/^https?:\/\//i, '');
  const stopChars = ['/', '?', '#'];
  let endIndex = withoutProtocol.length;
  stopChars.forEach((char) => {
    const idx = withoutProtocol.indexOf(char);
    if (idx >= 0 && idx < endIndex) {
      endIndex = idx;
    }
  });
  const hostWithPort = withoutProtocol.slice(0, endIndex);
  return hostWithPort.replace(/:\d+$/, '');
}

function deriveLocation(detail, url, { allowDetail = true } = {}) {
  const trimmedDetail = (detail || '').trim();
  const lower = trimmedDetail.toLowerCase();
  if (
    allowDetail &&
    trimmedDetail &&
    lower !== 'current site' &&
    lower !== 'current page'
  ) {
    return trimmedDetail;
  }

  const fromUrl = extractHostname(url);
  if (fromUrl) {
    return fromUrl;
  }

  return getCurrentPageHostname();
}

function formatNavigationDetail(detail, url) {
  const target = deriveLocation(detail, url, { allowDetail: true });
  if (!target) {
    return 'Exploring this page';
  }
  return /^(visiting|navigating)/i.test(target) ? target : `Visiting ${target}`;
}

function formatReadingDetail(detail, url) {
  if (detail && detail.trim()) {
    return detail;
  }
  const target = deriveLocation(detail, url, { allowDetail: false });
  if (!target) {
    return 'Reviewing source';
  }
  return /^reviewing/i.test(target) ? target : `Reviewing ${target}`;
}

function describeAgentStatus(status, options = {}) {
  const fallbackDetail = options.fallbackDetail || '';
  if (!status || typeof status !== 'object') {
    return { label: 'Processing', detail: fallbackDetail };
  }

  const normalizedLabel = normalizeStatusLabel(status.label);
  let label =
    STATUS_LABEL_REMAPPINGS[normalizedLabel] || status.label || 'Processing';
  let detail = (status.detail || '').trim();
  const url = status.url || '';

  if (NAVIGATION_STATUS_LABELS.has(normalizedLabel)) {
    label = 'Navigating site';
    detail = formatNavigationDetail(detail, url);
  } else if (normalizedLabel === SEARCH_STATUS_LABEL) {
    label = 'Searching the web';
    if (!detail) {
      detail = 'Looking for relevant information';
    }
  } else if (normalizedLabel === READING_STATUS_LABEL) {
    label = 'Reading source';
    detail = formatReadingDetail(detail, url);
  }

  if (!detail && label.toLowerCase() === 'processing') {
    detail = fallbackDetail || 'Working on it';
  }

  return { label, detail };
}

function createLoadingCard(responseContainer) {
  const card = document.createElement('div');
  card.className = 'agent_response agent-loading-card';

  const title = document.createElement('div');
  title.className = 'loading-card-title';
  title.innerText = 'FinSearch is working';

  const currentWrapper = document.createElement('div');
  currentWrapper.className = 'loading-card-current';

  const currentLabel = document.createElement('div');
  currentLabel.className = 'loading-card-current-label';
  currentWrapper.appendChild(currentLabel);

  const currentDetail = document.createElement('div');
  currentDetail.className = 'loading-card-current-detail';
  currentWrapper.appendChild(currentDetail);

  const historyList = document.createElement('ul');
  historyList.className = 'loading-card-history';

  card.appendChild(title);
  card.appendChild(currentWrapper);
  card.appendChild(historyList);
  responseContainer.appendChild(card);

  const MAX_HISTORY = 4;
  let currentStatus = null;
  let isActive = true;

  const normalize = (status = {}) => ({
    label: status.label || '',
    detail: status.detail || '',
    url: status.url || '',
  });

  const appendHistory = (status) => {
    if (!status || !status.label) {
      return;
    }
    const item = document.createElement('li');
    item.className = 'loading-card-history-item';

    const labelSpan = document.createElement('span');
    labelSpan.className = 'history-label';
    labelSpan.innerText = status.label;
    item.appendChild(labelSpan);

    if (status.detail) {
      const detailSpan = document.createElement('span');
      detailSpan.className = 'history-detail';
      detailSpan.innerText = status.detail;
      item.appendChild(detailSpan);
    }

    historyList.insertBefore(item, historyList.firstChild);
    while (historyList.children.length > MAX_HISTORY) {
      historyList.removeChild(historyList.lastChild);
    }
  };

  const updateStatus = (status) => {
    if (!isActive) {
      return;
    }
    const next = normalize(status);
    if (!next.label) {
      return;
    }
    if (
      currentStatus &&
      currentStatus.label === next.label &&
      currentStatus.detail === next.detail
    ) {
      return;
    }
    if (currentStatus) {
      appendHistory(currentStatus);
    }
    currentStatus = next;
    currentLabel.innerText = next.label;
    if (next.detail) {
      currentDetail.innerText = next.detail;
      currentDetail.style.display = 'block';
    } else {
      currentDetail.innerText = '';
      currentDetail.style.display = 'none';
    }
  };

  const complete = () => {
    if (!isActive) {
      return;
    }
    currentStatus = null;
    isActive = false;
    if (card.parentNode) {
      card.parentNode.removeChild(card);
    }
  };

  const fail = (message) => {
    if (!isActive) {
      return;
    }
    card.classList.add('agent-loading-card--error');
    currentLabel.innerText = 'Issue detected';
    currentDetail.innerText = message || 'Unable to finish this request.';
    currentDetail.style.display = 'block';
    historyList.innerHTML = '';
  };

  return {
    element: card,
    updateStatus,
    complete,
    fail,
  };
}

// Function to create action buttons (copy and retry)
function createActionButtons(
  responseText,
  userQuestion,
  promptMode,
  useRAG,
  useMCP,
  selectedModel
) {
  const buttonContainer = document.createElement('div');
  buttonContainer.className = 'action-buttons';

  // Copy button
  const copyButton = document.createElement('button');
  copyButton.className = 'action-button copy-button';
  copyButton.title = 'Copy response';

  const copyIcon = document.createElement('img');
  copyIcon.src = chrome.runtime.getURL('assets/copy.png');
  copyIcon.alt = 'Copy';
  copyIcon.className = 'action-icon';
  copyButton.appendChild(copyIcon);

  copyButton.onclick = () => {
    // Remove "FinSearch: " prefix before copying
    const textToCopy = responseText.replace(/^FinSearch:\s*/, '');
    navigator.clipboard
      .writeText(textToCopy)
      .then(() => {
        // Visual feedback
        copyButton.classList.add('action-button-clicked');
        setTimeout(() => {
          copyButton.classList.remove('action-button-clicked');
        }, 200);
      })
      .catch((err) => {
        console.error('Failed to copy text:', err);
      });
  };

  // Retry button
  const retryButton = document.createElement('button');
  retryButton.className = 'action-button retry-button';
  retryButton.title = 'Retry';

  const retryIcon = document.createElement('img');
  retryIcon.src = chrome.runtime.getURL('assets/retry-1.png');
  retryIcon.alt = 'Retry';
  retryIcon.className = 'action-icon';
  retryButton.appendChild(retryIcon);

  retryButton.onclick = () => {
    // Visual feedback
    retryButton.classList.add('action-button-clicked');
    setTimeout(() => {
      retryButton.classList.remove('action-button-clicked');
    }, 200);

    // Get current mode from DOM (don't use captured promptMode)
    const selectedModeElement = document.querySelector('.mode-option-selected');
    const currentMode = selectedModeElement
      ? selectedModeElement.dataset.mode
      : 'Normal';
    const currentPromptMode = currentMode === 'Extensive';

    // Resend the question with current settings
    handleChatResponse(userQuestion, currentPromptMode);
  };

  buttonContainer.appendChild(copyButton);
  buttonContainer.appendChild(retryButton);

  return buttonContainer;
}

// Create a per-response Validate button. Shown only when the backend
// reports that this response recorded one or more ratio claims (i.e.,
// the response is about XBRL-validatable numbers).
function _createValidateButton() {
  const btn = document.createElement('button');
  btn.className = 'action-button validate-button';
  btn.title = 'Validate against SEC XBRL filing';
  btn.textContent = 'Validate';
  btn.onclick = () => {
    btn.classList.add('action-button-clicked');
    setTimeout(() => btn.classList.remove('action-button-clicked'), 200);
    validate_response();
  };
  return btn;
}

function _attachAndMaybeAutoFire(container, autoFire) {
  container.appendChild(_createValidateButton());
  if (autoFire) validate_response();
}

function attachValidateButtonIfClaims(actionButtonsContainer, { knownFlag, autoFire = false } = {}) {
  // Fast path: if the response payload already told us whether claims
  // exist (has_axiom_claims from chat_response), skip the network call.
  if (knownFlag === true) {
    _attachAndMaybeAutoFire(actionButtonsContainer, autoFire);
    return;
  }
  if (knownFlag === false) return;

  // Streaming responses don't carry the flag yet — fall back to a quick GET.
  hasAxiomClaims()
    .then((data) => {
      if (data && data.has_claims) {
        _attachAndMaybeAutoFire(actionButtonsContainer, autoFire);
      }
    })
    .catch((err) => {
      console.debug('hasAxiomClaims check failed:', err);
    });
}

// Function to create rating element
function createRatingElement() {
  const ratingContainer = document.createElement('div');
  ratingContainer.className = 'rating-container';

  const ratingStars = document.createElement('div');
  ratingStars.className = 'rating-stars';

  // Create 5 stars using UTF-8 characters
  const stars = [];
  for (let i = 0; i < 5; i++) {
    const star = document.createElement('span');
    star.className = 'rating-star';
    star.innerText = '★';
    star.dataset.rating = i + 1;
    stars.push(star);
    ratingStars.appendChild(star);
  }

  // Hover effect - fill stars up to the hovered star
  stars.forEach((star, index) => {
    star.addEventListener('mouseenter', () => {
      // Fill stars from 0 to current index
      for (let i = 0; i <= index; i++) {
        stars[i].classList.add('filled');
      }
      // Empty stars after current index
      for (let i = index + 1; i < stars.length; i++) {
        stars[i].classList.remove('filled');
      }
    });

    // Click handler - switch to thanks message
    star.addEventListener('click', () => {
      const rating = parseInt(star.dataset.rating);
      console.log(`User rated response: ${rating} stars`);

      // Clear container and show thanks message
      ratingContainer.innerHTML = '';
      const thanksText = document.createElement('span');
      thanksText.className = 'rating-thanks';
      thanksText.innerText = 'Thanks for the feedback!';
      ratingContainer.appendChild(thanksText);
    });
  });

  // Reset stars to empty when mouse leaves the stars container
  ratingStars.addEventListener('mouseleave', () => {
    stars.forEach((star) => {
      star.classList.remove('filled');
    });
  });

  ratingContainer.appendChild(ratingStars);

  return ratingContainer;
}

// Function to handle chat responses (single model)
async function handleChatResponse(question, promptMode = false, useStreaming = true) {
  const startTime = performance.now();
  const responseContainer = document.getElementById('respons');

  // Show the user's question
  appendChatElement(responseContainer, 'your_question', question);

  // Scroll to show the new question immediately
  scrollChatToBottom();

  const loadingCard = createLoadingCard(responseContainer);
  const defaultStatusDetail = promptMode ? 'Research mode' : 'Thinking mode';
  const pushAgentStatus = (status) => {
    const formatted = describeAgentStatus(status, {
      fallbackDetail: defaultStatusDetail,
    });
    loadingCard.updateStatus(formatted);
  };

  // Wait for auto-scrape to complete if in progress
  if (isAutoScrapeInProgress()) {
    pushAgentStatus({
      label: 'Preparing',
      detail: 'Loading page context...',
    });
    await waitForAutoScrape();
    console.log("[Chat] Auto-scrape complete, proceeding with request");
  }

  pushAgentStatus({
    label: 'Processing',
    detail: defaultStatusDetail,
  });

  const responseElement = appendChatElement(responseContainer, 'agent_response', '');
  responseElement.style.display = 'none';

  // Read the RAG checkbox state
  const ragSwitchEl = document.getElementById('ragSwitch');
  const useRAG = ragSwitchEl ? !!ragSwitchEl.checked : false;

  // Read the MCP mode toggle
  const mcpSwitchEl = document.getElementById('mcpModeSwitch');
  const useMCP = mcpSwitchEl ? !!mcpSwitchEl.checked : false;

  const selectedModel = getSelectedModel();

  // Check if streaming is available (not for MCP or RAG modes)
  const canStream = useStreaming && !useMCP && !useRAG;
  if (!canStream) {
    pushAgentStatus({
      label: 'Processing',
      detail: useMCP ? 'MCP mode' : useRAG ? 'RAG mode' : 'Standard pipeline',
    });
  }

  if (canStream) {
    // Use streaming response
    let isFirstChunk = true;
    let loadingDismissed = false;
    const dismissLoading = () => {
      if (!loadingDismissed) {
        loadingDismissed = true;
        loadingCard.complete();
      }
    };

    getChatResponseStream(
      question,
      selectedModel,
      promptMode,
      useRAG,
      useMCP,
      {
        // onChunk callback - called for each chunk of text
        onChunk: (_chunk, fullResponse) => {
          if (isFirstChunk) {
            isFirstChunk = false;
            dismissLoading();
          }
          if (responseElement.style.display === 'none') {
            responseElement.style.display = 'block';
          }
          renderStreamingPreview(responseElement, fullResponse);
          scrollChatToBottom();
        },
        // onComplete callback - called when streaming is done
        onComplete: (fullResponse, data) => {
          const endTime = performance.now();
          const responseTime = endTime - startTime;
          console.log(`Time taken for streaming response: ${responseTime} ms`);
          console.log('[Debug] onComplete data:', data);
          console.log('[Debug] promptMode:', promptMode);

          const responseText = `FinSearch: ${fullResponse}`;
          if (responseElement.style.display === 'none') {
            responseElement.style.display = 'block';
          }
          // Prefer server-wrapped content (contains data-claim-id spans
          // for in-text Validate markings); fall back to the locally
          // accumulated prose for backwards compatibility with older
          // servers that don't yet emit `wrapped_content`.
          renderMarkdownContent(
            responseElement,
            data?.wrapped_content ?? fullResponse
          );

          // Create action row containing both action buttons and rating
          const actionRow = document.createElement('div');
          actionRow.className = 'response-action-row';

          const actionButtons = createActionButtons(
            responseText,
            question,
            promptMode,
            useRAG,
            useMCP,
            selectedModel
          );
          attachValidateButtonIfClaims(actionButtons, {
            autoFire: looksLikeValidationRequest(question),
          });
          const ratingElement = createRatingElement();

          actionRow.appendChild(actionButtons);
          actionRow.appendChild(ratingElement);
          responseContainer.appendChild(actionRow);

          // If this is research mode streaming and contains used_urls, cache them
          if (promptMode) {
            console.log(
              '[Sources Debug] Research mode streaming - checking for URLs'
            );
            console.log(
              '[Sources Debug] data.used_urls exists?',
              !!data.used_urls
            );
            console.log(
              '[Sources Debug] data.used_urls is array?',
              Array.isArray(data.used_urls)
            );
            console.log(
              '[Sources Debug] data.used_urls length:',
              data.used_urls?.length
            );
            console.log(
              '[Sources Debug] data.used_sources exists?',
              !!data.used_sources
            );

            if (data.used_urls && data.used_urls.length > 0) {
              console.log(
                '[Sources Debug] Research mode streaming response received'
              );
              console.log('[Sources Debug] used_urls:', data.used_urls);
              console.log(
                '[Sources Debug] Number of URLs:',
                data.used_urls.length
              );

              const metadata = Array.isArray(data.used_sources)
                ? data.used_sources
                : [];
              setCachedSources(data.used_urls, question, metadata);
              console.log(
                '[Sources Debug] Called setCachedSources with',
                data.used_urls.length,
                'URLs and',
                metadata.length,
                'metadata entries'
              );
              console.log(
                '[Sources Debug] Sources should now be cached for query:',
                question
              );
            } else {
              console.warn(
                '[Sources Debug] Research mode but NO URLs found in response!'
              );
            }
          } else {
            console.log(
              '[Sources Debug] Not in promptMode, skipping source caching'
            );
          }

          // Clear the user textbox
          document.getElementById('textbox').value = '';
          scrollChatToBottom();
          dismissLoading();
        },
        // onError callback
        onError: (error) => {
          console.error('Streaming error:', error);
          responseElement.style.display = 'block';
          responseElement.innerHTML = `<strong>FinSearch:</strong> Failed to load response (streaming error).`;
          loadingCard.fail('Streaming error');
        },
        onStatus: (status) => {
          pushAgentStatus(status);
        },
      }
    );
  } else {
    // Use regular non-streaming response
    getChatResponse(question, selectedModel, promptMode, useRAG, useMCP)
      .then((data) => {
        const endTime = performance.now();
        const responseTime = endTime - startTime;
        console.log(`Time taken for response: ${responseTime} ms`);

        // Extract the reply: MCP gives `data.reply`, normal gives `data.resp[...]`
        const modelResponse = useMCP ? data.reply : data.resp[selectedModel];

        let responseText = '';
        if (!modelResponse) {
          // Safeguard in case backend does not return something
          responseText = `FinSearch: (No response from server)`;
        } else if (
          modelResponse.startsWith('The following file(s) are missing')
        ) {
          responseText = `FinSearch: Error - ${modelResponse}`;
        } else {
          responseText = `FinSearch: ${modelResponse}`;
        }

        responseElement.style.display = 'block';
        renderMarkdownContent(responseElement, modelResponse);

        // Create action row containing both action buttons and rating
        const actionRow = document.createElement('div');
        actionRow.className = 'response-action-row';

        const actionButtons = createActionButtons(
          responseText,
          question,
          promptMode,
          useRAG,
          useMCP,
          selectedModel
        );
        attachValidateButtonIfClaims(actionButtons, {
          knownFlag: data.has_axiom_claims,
          autoFire: looksLikeValidationRequest(question),
        });
        const ratingElement = createRatingElement();

        actionRow.appendChild(actionButtons);
        actionRow.appendChild(ratingElement);
        responseContainer.appendChild(actionRow);

        // If this is an Advanced Ask response and contains used_urls, cache them
        if (promptMode && data.used_urls && data.used_urls.length > 0) {
          console.log('[Sources Debug] Advanced Ask response received');
          console.log('[Sources Debug] used_urls:', data.used_urls);
          console.log('[Sources Debug] Number of URLs:', data.used_urls.length);

          // Check each URL
          data.used_urls.forEach((url, idx) => {
            console.log(`[Sources Debug] URL ${idx + 1}: ${url}`);
            if (url.includes('duckduckgo')) {
              console.warn(
                `[Sources Debug] WARNING: DuckDuckGo URL found at index ${idx}: ${url}`
              );
            }
          });

          const metadata = Array.isArray(data.used_sources)
            ? data.used_sources
            : [];
          setCachedSources(data.used_urls, question, metadata);
          console.log(
          '[Sources Debug] Cached',
          data.used_urls.length,
          'source URLs from Advanced Ask'
        );
        }

        // Clear the user textbox
        document.getElementById('textbox').value = '';
        scrollChatToBottom();
        loadingCard.complete();
      })
      .catch((error) => {
        console.error('There was a problem with your fetch operation:', error);
        responseElement.style.display = 'block';
        responseElement.innerHTML = `<strong>FinSearch:</strong> Failed to load response.`;
        loadingCard.fail('Network error');
      });
  }
}

// Function to handle image response
function handleImageResponse(question, description) {
  const responseContainer = document.getElementById('respons');
  appendChatElement(responseContainer, 'your_question', question);

  const responseDiv = document.createElement('div');
  responseDiv.className = 'agent_response';
  responseDiv.innerText = description;
  responseContainer.appendChild(responseDiv);

  scrollChatToBottom();
}

export { handleChatResponse, handleImageResponse };
