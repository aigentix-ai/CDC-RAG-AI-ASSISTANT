// ============================================================================
// CDC Regulatory Assistant — Frontend Client (Module 4)
// High-End FinTech & Regulatory Intelligence Experience
// ============================================================================
const API_BASE_URL = "http://localhost:5000/ask";

// Dynamic API resolution for autonomous Cloudflare Pages and local environments
function getResolvedApiUrl() {
  if (typeof window !== "undefined") {
    // Purge any stale localhost or tunnel keys in production/hosted environments
    try {
      if (window.location && window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1") {
        localStorage.removeItem("cdc_api_url");
        localStorage.removeItem("cdc_backend_url");
      }
    } catch (e) {}

    // 1. Explicit window override (e.g. config.js)
    if (window.CDC_API_URL) return window.CDC_API_URL;
    // 2. Same-origin deployment (Cloudflare Pages edge function /ask or Flask /ask)
    if (window.location && window.location.origin && window.location.origin.startsWith("http")) {
      return `${window.location.origin}/ask`;
    }
  }
  return API_BASE_URL;
}

let RESOLVED_API_URL = getResolvedApiUrl();

document.addEventListener("DOMContentLoaded", () => {
  const chatMain = document.getElementById("chatMain");
  const chatMessages = document.getElementById("chatMessages");
  const chatForm = document.getElementById("chatForm");
  const questionInput = document.getElementById("questionInput");
  const sendBtn = document.getElementById("sendBtn");
  const clearSessionBtn = document.getElementById("clearSessionBtn");

  // Maintain initial welcome DOM state for instant session resets
  const initialWelcomeHtml = chatMessages ? chatMessages.innerHTML : "";

  let isSubmitting = false;
  let activeAbortController = null;

  // --------------------------------------------------------------------------
  // Auto-resize textarea to fit text content dynamically
  // --------------------------------------------------------------------------
  function autoResizeTextarea() {
    questionInput.style.height = "auto";
    const newHeight = Math.min(questionInput.scrollHeight, 140);
    questionInput.style.height = `${newHeight}px`;
  }

  questionInput.addEventListener("input", autoResizeTextarea);

  // --------------------------------------------------------------------------
  // In-flight Debouncing & Controls State Manager
  // --------------------------------------------------------------------------
  function setControlsDisabled(disabled) {
    isSubmitting = disabled;
    questionInput.disabled = disabled;
    sendBtn.disabled = disabled;

    if (disabled) {
      sendBtn.setAttribute("aria-busy", "true");
      questionInput.setAttribute("aria-disabled", "true");
    } else {
      sendBtn.removeAttribute("aria-busy");
      questionInput.removeAttribute("aria-disabled");
    }

    // Disable all starter prompt chips during in-flight requests
    document.querySelectorAll(".prompt-chip").forEach((chip) => {
      chip.disabled = disabled;
      if (disabled) {
        chip.setAttribute("aria-disabled", "true");
      } else {
        chip.removeAttribute("aria-disabled");
      }
    });

    if (clearSessionBtn) {
      clearSessionBtn.disabled = disabled;
    }
  }

  // --------------------------------------------------------------------------
  // Session-Only Chat History: Reset Session Handler
  // --------------------------------------------------------------------------
  function resetSession() {
    if (activeAbortController) {
      activeAbortController.abort();
      activeAbortController = null;
    }
    removeTypingIndicator();
    if (chatMessages) {
      chatMessages.innerHTML = initialWelcomeHtml;
    }
    questionInput.value = "";
    autoResizeTextarea();
    setControlsDisabled(false);
    chatMain.scrollTo({ top: 0, behavior: "smooth" });
    questionInput.focus();
  }

  if (clearSessionBtn) {
    clearSessionBtn.addEventListener("click", () => {
      if (!isSubmitting) {
        resetSession();
      }
    });
  }

  // --------------------------------------------------------------------------
  // Handle Keyboard Navigation (Enter sends, Shift+Enter adds newline)
  // --------------------------------------------------------------------------
  questionInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!isSubmitting && questionInput.value.trim()) {
        chatForm.dispatchEvent(new Event("submit", { cancelable: true }));
      }
    }
  });

  // --------------------------------------------------------------------------
  // Starter Prompt Chips (Delegated to support dynamic resets)
  // --------------------------------------------------------------------------
  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".prompt-chip");
    if (chip && !isSubmitting && !chip.disabled) {
      const promptText = chip.getAttribute("data-prompt") || chip.textContent.trim();
      questionInput.value = promptText;
      autoResizeTextarea();
      chatForm.dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });

  // --------------------------------------------------------------------------
  // Scroll chat area to bottom
  // --------------------------------------------------------------------------
  function scrollToBottom() {
    chatMain.scrollTo({
      top: chatMain.scrollHeight,
      behavior: "smooth"
    });
  }

  // --------------------------------------------------------------------------
  // Helper: Format Current Time
  // --------------------------------------------------------------------------
  function getFormattedTimestamp() {
    const now = new Date();
    return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  // --------------------------------------------------------------------------
  // Safe & Robust Markdown Formatter
  // Converts bold, bullets, headers, numbers, and code to clean HTML safely
  // --------------------------------------------------------------------------
  function formatMarkdown(text) {
    if (!text) return "";

    // Escape HTML entities to prevent XSS
    let escaped = text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");

    // Split into lines for structured block parsing
    const lines = escaped.split("\n");
    let inList = false;
    let listType = "ul";
    const result = [];

    lines.forEach((line) => {
      const trimmed = line.trim();

      // Check unordered list item (* or -)
      const ulMatch = line.match(/^(\s*)[*-]\s+(.+)$/);
      // Check ordered list item (1. 2.)
      const olMatch = line.match(/^(\s*)\d+\.\s+(.+)$/);
      // Check header (### or ##)
      const hMatch = line.match(/^###\s+(.+)$/) || line.match(/^##\s+(.+)$/);

      if (ulMatch) {
        if (!inList || listType !== "ul") {
          if (inList) result.push(`</${listType}>`);
          result.push("<ul>");
          inList = true;
          listType = "ul";
        }
        result.push(`<li>${parseInlineMarkdown(ulMatch[2])}</li>`);
      } else if (olMatch) {
        if (!inList || listType !== "ol") {
          if (inList) result.push(`</${listType}>`);
          result.push("<ol>");
          inList = true;
          listType = "ol";
        }
        result.push(`<li>${parseInlineMarkdown(olMatch[2])}</li>`);
      } else {
        if (inList) {
          result.push(`</${listType}>`);
          inList = false;
        }

        if (hMatch) {
          result.push(`<h4>${parseInlineMarkdown(hMatch[1])}</h4>`);
        } else if (trimmed === "") {
          // Empty line
        } else {
          result.push(`<p>${parseInlineMarkdown(line)}</p>`);
        }
      }
    });

    if (inList) {
      result.push(`</${listType}>`);
    }

    return result.join("");
  }

  function parseInlineMarkdown(str) {
    return str
      // Bold: **text**
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      // Inline Code: `code`
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      // Highlight PKR currency figures
      .replace(/(PKR\s?[\d,]+(\.\d+)?)/gi, "<strong style='color:#0369a1;'>$1</strong>");
  }

  // --------------------------------------------------------------------------
  // Message Rendering Functions
  // --------------------------------------------------------------------------

  // Render User Question Message
  function appendUserMessage(text) {
    const messageEl = document.createElement("div");
    messageEl.className = "message user-message";

    const avatarEl = document.createElement("div");
    avatarEl.className = "message-avatar";
    avatarEl.setAttribute("aria-hidden", "true");
    avatarEl.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
        <circle cx="12" cy="7" r="4"/>
      </svg>
    `;

    const contentEl = document.createElement("div");
    contentEl.className = "message-content";

    const bubbleEl = document.createElement("div");
    bubbleEl.className = "message-bubble";
    bubbleEl.textContent = text;

    contentEl.appendChild(bubbleEl);
    messageEl.appendChild(avatarEl);
    messageEl.appendChild(contentEl);
    chatMessages.appendChild(messageEl);

    scrollToBottom();
  }

  // Render Loading / Typing Indicator
  function showTypingIndicator() {
    const indicatorEl = document.createElement("div");
    indicatorEl.id = "activeTypingIndicator";
    indicatorEl.className = "message assistant-message";

    const avatarEl = document.createElement("div");
    avatarEl.className = "message-avatar";
    avatarEl.setAttribute("aria-hidden", "true");
    avatarEl.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
        <path d="m9 12 2 2 4-4"/>
      </svg>
    `;

    const contentEl = document.createElement("div");
    contentEl.className = "message-content";

    const bubbleEl = document.createElement("div");
    bubbleEl.className = "typing-indicator-card";
    bubbleEl.setAttribute("aria-label", "CDC Assistant is retrieving documents and generating response");
    bubbleEl.innerHTML = `
      <div class="typing-pulse-wave" aria-hidden="true">
        <span class="typing-dot"></span>
        <span class="typing-dot"></span>
        <span class="typing-dot"></span>
      </div>
      <span class="typing-text">Cross-referencing SECP circulars & CDC operating regulations...</span>
    `;

    contentEl.appendChild(bubbleEl);
    indicatorEl.appendChild(avatarEl);
    indicatorEl.appendChild(contentEl);
    chatMessages.appendChild(indicatorEl);

    scrollToBottom();
    return indicatorEl;
  }

  function removeTypingIndicator() {
    const indicator = document.getElementById("activeTypingIndicator");
    if (indicator) {
      indicator.remove();
    }
  }

  // Safe URL sanitizer for citation links
  function sanitizeUrl(rawUrl) {
    if (!rawUrl || typeof rawUrl !== "string") return "#";
    const trimmed = rawUrl.trim();
    if (trimmed.startsWith("http://") || trimmed.startsWith("https://") || trimmed.startsWith("/api/docs")) {
      return trimmed;
    }
    return "#";
  }

  // Render Assistant Answer Message (with optional Sources section)
  function appendAssistantMessage(answer, citations) {
    const messageEl = document.createElement("div");
    messageEl.className = "message assistant-message";

    const avatarEl = document.createElement("div");
    avatarEl.className = "message-avatar";
    avatarEl.setAttribute("aria-hidden", "true");
    avatarEl.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
        <path d="m9 12 2 2 4-4"/>
      </svg>
    `;

    const contentEl = document.createElement("div");
    contentEl.className = "message-content";

    // Answer Bubble
    const bubbleEl = document.createElement("div");
    bubbleEl.className = "message-bubble";

    // Message Header Info (Sender title + Copy Button)
    const headerInfoEl = document.createElement("div");
    headerInfoEl.className = "message-header-info";
    
    const senderTitle = document.createElement("span");
    senderTitle.className = "message-sender-name";
    senderTitle.innerHTML = `CDC Compliance Assistant &bull; <span style="font-weight:400; color:var(--color-text-muted);">${getFormattedTimestamp()}</span>`;

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "message-copy-btn";
    copyBtn.title = "Copy answer to clipboard";
    copyBtn.innerHTML = `
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
      </svg>
      <span>Copy</span>
    `;

    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(answer);
        copyBtn.innerHTML = `
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
          <span style="color:#059669; font-weight:600;">Copied!</span>
        `;
        setTimeout(() => {
          copyBtn.innerHTML = `
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            <span>Copy</span>
          `;
        }, 2000);
      } catch (err) {
        console.error("Failed to copy:", err);
      }
    });

    headerInfoEl.appendChild(senderTitle);
    headerInfoEl.appendChild(copyBtn);
    bubbleEl.appendChild(headerInfoEl);

    // Formatted Body
    const bodyEl = document.createElement("div");
    bodyEl.className = "assistant-body";
    bodyEl.innerHTML = formatMarkdown(answer);
    bubbleEl.appendChild(bodyEl);

    contentEl.appendChild(bubbleEl);

    // Citations / Sources Section (render only if citations exist and non-empty)
    if (Array.isArray(citations) && citations.length > 0) {
      const sourcesEl = document.createElement("div");
      sourcesEl.className = "message-sources";

      const headerEl = document.createElement("div");
      headerEl.className = "sources-header";
      headerEl.innerHTML = `
        <svg class="sources-header-shield" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          <path d="m9 12 2 2 4-4"/>
        </svg>
        <span>Verified Regulatory Citations (SECP / CDC)</span>
      `;
      sourcesEl.appendChild(headerEl);

      const listEl = document.createElement("div");
      listEl.className = "sources-list";

      citations.forEach((cit, index) => {
        const itemEl = document.createElement("div");
        itemEl.className = "source-item";

        const localDocUrl = cit.doc_id ? `/api/docs/${cit.doc_id}${cit.source_type === 'pdf' ? '.pdf' : ''}` : "";
        const targetUrl = cit.citation_url || localDocUrl || cit.source_url || "#";
        const isPdf = cit.source_type === "pdf" || targetUrl.toLowerCase().includes(".pdf");
        const titleText = cit.title || "Referenced Regulatory Document";
        const pageNum = cit.page_number;
        const externalUrl = (cit.source_url && cit.source_url.startsWith("http")) ? cit.source_url : "";

        const linkEl = document.createElement("a");
        linkEl.className = "source-link-card";
        linkEl.href = sanitizeUrl(targetUrl);
        linkEl.target = "_blank";
        linkEl.rel = "noopener noreferrer";
        linkEl.title = `Open verified local backup of ${titleText}${pageNum ? ` (Page ${pageNum})` : ''} in new tab`;

        linkEl.innerHTML = `
          <div class="source-meta-group">
            <span class="source-type-pill ${isPdf ? 'source-type-pdf' : 'source-type-web'}">${isPdf ? 'PDF' : 'WEB'}</span>
            <span class="source-title-text" title="${titleText}">${titleText}</span>
          </div>
          <div style="display:flex; align-items:center; gap:6px;">
            <span class="source-backup-badge" style="font-size:0.68rem; font-weight:600; color:#047857; background:#ecfdf5; padding:2px 6px; border-radius:4px; border:1px solid #a7f3d0;" title="Verified local copy saved on this server">Offline Safe</span>
            ${(pageNum && isPdf) ? `<span class="source-page-badge">Page ${pageNum}</span>` : ''}
            <svg class="source-jump-arrow" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
              <polyline points="15 3 21 3 21 9"/>
              <line x1="10" y1="14" x2="21" y2="3"/>
            </svg>
          </div>
        `;

        itemEl.appendChild(linkEl);

        if (externalUrl) {
          const extWrapper = document.createElement("div");
          extWrapper.style.cssText = "padding: 0 12px 6px 12px; font-size: 0.72rem; color: var(--color-text-muted); display:flex; justify-content: flex-end;";
          extWrapper.innerHTML = `<a href="${sanitizeUrl(externalUrl)}" target="_blank" rel="noopener noreferrer" style="color:var(--color-text-muted); text-decoration:underline; display:inline-flex; align-items:center; gap:3px;">Live Web Version ↗</a>`;
          itemEl.appendChild(extWrapper);
        }

        listEl.appendChild(itemEl);
      });

      sourcesEl.appendChild(listEl);
      contentEl.appendChild(sourcesEl);
    }

    messageEl.appendChild(avatarEl);
    messageEl.appendChild(contentEl);
    chatMessages.appendChild(messageEl);

    scrollToBottom();
  }

  // --------------------------------------------------------------------------
  // Non-technical error resolver for corporate & compliance users
  // --------------------------------------------------------------------------
  function resolveErrorMessage(status, backendError, networkError = null) {
    if (networkError) {
      if (networkError.name === "AbortError") {
        return {
          title: "Request Timed Out",
          message: "Searching regulatory records took longer than expected. Please try again shortly."
        };
      }
      return {
        title: "Connection Unavailable",
        message: "Unable to connect to the regulatory assistant. Please check your internet connection or verify the service is running."
      };
    }

    if (status === 429) {
      return {
        title: "Service Busy",
        message: "The assistant is currently handling multiple requests. Please wait a moment and try asking again."
      };
    }

    if (status === 404) {
      return {
        title: "Service Endpoint Not Found",
        message: "The regulatory service could not be located at the configured address. If you are viewing this on Cloudflare Pages, please click 'Server Settings' in the top header and enter your live backend or Cloudflare Tunnel URL."
      };
    }

    if (status === 400) {
      if (backendError && typeof backendError === "string" && !backendError.includes("Traceback") && !backendError.includes("Error:")) {
        return {
          title: "Unable to Process Question",
          message: backendError
        };
      }
      return {
        title: "Unable to Process Question",
        message: "We could not understand or process your question. Please rephrase your query and try again."
      };
    }

    if (status >= 500) {
      return {
        title: "Service Temporarily Unavailable",
        message: "An internal issue occurred while searching the regulatory documents. Please try asking again in a few moments."
      };
    }

    if (backendError && typeof backendError === "string") {
      if (backendError.includes("Traceback") || backendError.includes("Exception") || backendError.includes("Error:")) {
        return {
          title: "Service Issue",
          message: "An unexpected problem occurred while generating the answer. Please try again."
        };
      }
      return {
        title: "Notice",
        message: backendError
      };
    }

    return {
      title: "Response Unavailable",
      message: "The assistant could not retrieve a complete answer. Please rephrase your question or try again."
    };
  }

  // Render System / Error Message
  function appendSystemError(title, message) {
    const errorContainer = document.createElement("div");
    errorContainer.className = "system-error-message";

    const cardEl = document.createElement("div");
    cardEl.className = "system-error-card";
    cardEl.setAttribute("role", "alert");

    const iconEl = document.createElement("div");
    iconEl.className = "system-error-icon";
    iconEl.innerHTML = `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="8" x2="12" y2="12"/>
        <line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
    `;

    const bodyEl = document.createElement("div");
    bodyEl.className = "system-error-body";

    const titleEl = document.createElement("div");
    titleEl.className = "system-error-title";
    titleEl.textContent = title;

    const textEl = document.createElement("div");
    textEl.className = "system-error-text";
    textEl.textContent = message;

    bodyEl.appendChild(titleEl);
    bodyEl.appendChild(textEl);
    cardEl.appendChild(iconEl);
    cardEl.appendChild(bodyEl);
    errorContainer.appendChild(cardEl);

    chatMessages.appendChild(errorContainer);
    scrollToBottom();
  }

  // --------------------------------------------------------------------------
  // Form Submission & API Request
  // --------------------------------------------------------------------------
  chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const questionText = questionInput.value.trim();
    if (!questionText || isSubmitting) return;

    // Set UI state to submitting
    setControlsDisabled(true);
    questionInput.value = "";
    autoResizeTextarea();

    // 1. Render User Question
    appendUserMessage(questionText);

    // 2. Show Typing Indicator
    showTypingIndicator();

    // 3. Prepare request with 45-second timeout
    activeAbortController = new AbortController();
    const timeoutId = setTimeout(() => {
      if (activeAbortController) activeAbortController.abort();
    }, 45000);

    try {
      const response = await fetch(RESOLVED_API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ question: questionText }),
        signal: activeAbortController.signal
      });

      clearTimeout(timeoutId);

      let data = null;
      try {
        data = await response.json();
      } catch (jsonErr) {
        // Non-JSON response
      }

      removeTypingIndicator();

      if (!response.ok || (data && data.error)) {
        const errorInfo = resolveErrorMessage(response.status, data ? data.error : null, null);
        appendSystemError(errorInfo.title, errorInfo.message);
      } else if (data && typeof data.answer === "string") {
        appendAssistantMessage(data.answer, data.citations || []);
      } else {
        const errorInfo = resolveErrorMessage(response.status, null, null);
        appendSystemError(errorInfo.title, errorInfo.message);
      }
    } catch (networkError) {
      clearTimeout(timeoutId);
      removeTypingIndicator();
      const errorInfo = resolveErrorMessage(0, null, networkError);
      appendSystemError(errorInfo.title, errorInfo.message);
    } finally {
      activeAbortController = null;
      setControlsDisabled(false);
      questionInput.focus();
    }
  });

  // Initial focus on input
  questionInput.focus();
});
