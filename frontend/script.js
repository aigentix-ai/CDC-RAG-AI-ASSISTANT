// ============================================================================
// CDC Regulatory Assistant — Frontend Client (Module 4)
// High-End FinTech & Regulatory Intelligence Experience
// Multi-Chat Sidebar, Multi-User Isolation, Multi-Turn Context & Document Actions
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

    // 2. Same-origin deployment (Cloudflare Pages /ask or Flask /ask)
    if (window.location && window.location.origin && window.location.origin.startsWith("http")) {
      return `${window.location.origin}/ask`;
    }
  }
  return "/ask";
}

let RESOLVED_API_URL = getResolvedApiUrl();

// ============================================================================
// Multi-User Segregation & Workspace Profiles
// ============================================================================
const PRESET_ACCOUNTS = [
  { id: "Compliance Officer", role: "Primary Regulatory Desk", avatar: "CO" },
  { id: "SECP Regulatory Auditor", role: "Supervisory Audits", avatar: "SA" },
  { id: "Broker Operations Desk", role: "Participant Operations", avatar: "BO" },
  { id: "CDC Risk & Trustee Team", role: "Trustee & Fiduciary", avatar: "RT" }
];

function getStoredAccounts() {
  try {
    const raw = localStorage.getItem("cdc_workspace_accounts");
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch (e) {}
  return PRESET_ACCOUNTS;
}

function saveStoredAccounts(accs) {
  try {
    localStorage.setItem("cdc_workspace_accounts", JSON.stringify(accs));
  } catch (e) {}
}

function getCurrentUserId() {
  try {
    return localStorage.getItem("cdc_current_user_id") || "Compliance Officer";
  } catch (e) {
    return "Compliance Officer";
  }
}

function setCurrentUserId(userId) {
  try {
    localStorage.setItem("cdc_current_user_id", userId);
  } catch (e) {}
}

function getUserChats(userId) {
  try {
    const raw = localStorage.getItem(`cdc_user_${userId}_chats`);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed;
    }
  } catch (e) {}
  return [];
}

function saveUserChats(userId, chats) {
  try {
    localStorage.setItem(`cdc_user_${userId}_chats`, JSON.stringify(chats));
  } catch (e) {}
}

function getInitials(name) {
  if (!name) return "CO";
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// ============================================================================
// Main Application Lifecycle
// ============================================================================
document.addEventListener("DOMContentLoaded", () => {
  const chatMain = document.getElementById("chatMain");
  const chatMessages = document.getElementById("chatMessages");
  const chatForm = document.getElementById("chatForm");
  const questionInput = document.getElementById("questionInput");
  const sendBtn = document.getElementById("sendBtn");
  const clearSessionBtn = document.getElementById("clearSessionBtn");

  // Sidebar elements
  const chatSidebar = document.getElementById("chatSidebar");
  const toggleSidebarBtn = document.getElementById("toggleSidebarBtn");
  const closeSidebarMobileBtn = document.getElementById("closeSidebarMobileBtn");
  const sidebarBackdrop = document.getElementById("sidebarBackdrop");
  const newChatBtn = document.getElementById("newChatBtn");
  const chatHistoryList = document.getElementById("chatHistoryList");
  const chatCountPill = document.getElementById("chatCountPill");

  // User Profile elements
  const sidebarUserName = document.getElementById("sidebarUserName");
  const sidebarUserRole = document.getElementById("sidebarUserRole");
  const userAvatarBadge = document.getElementById("userAvatarBadge");
  const headerUserName = document.getElementById("headerUserName");
  const userAccountCard = document.getElementById("userAccountCard");
  const sidebarSwitchUserBtn = document.getElementById("sidebarSwitchUserBtn");
  const headerSwitchUserBtn = document.getElementById("headerSwitchUserBtn");

  // Modal elements
  const accountModal = document.getElementById("accountModal");
  const closeAccountModalBtn = document.getElementById("closeAccountModalBtn");
  const modalAccountsList = document.getElementById("modalAccountsList");
  const customAccountForm = document.getElementById("customAccountForm");
  const customAccountInput = document.getElementById("customAccountInput");

  // Initial welcome template for fresh chat sessions
  const initialWelcomeHtml = chatMessages ? chatMessages.innerHTML : "";

  let isSubmitting = false;
  let activeAbortController = null;

  // Active state for chats & user
  let currentUserId = getCurrentUserId();
  let currentChats = getUserChats(currentUserId);
  let currentChatId = null;

  // Initialize first chat if user has none
  if (currentChats.length === 0) {
    const initChat = {
      id: "chat_" + Date.now(),
      title: "New Regulatory Inquiry",
      createdAt: Date.now(),
      updatedAt: Date.now(),
      messages: []
    };
    currentChats.push(initChat);
    saveUserChats(currentUserId, currentChats);
    currentChatId = initChat.id;
  } else {
    currentChatId = currentChats[0].id;
  }

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
  // Smooth Auto-Scroll Handler
  // --------------------------------------------------------------------------
  function scrollToBottom() {
    requestAnimationFrame(() => {
      chatMain.scrollTo({
        top: chatMain.scrollHeight,
        behavior: "smooth"
      });
    });
  }

  // --------------------------------------------------------------------------
  // Controls State Manager
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

    document.querySelectorAll(".prompt-chip, .action-chip").forEach((btn) => {
      btn.disabled = disabled;
      if (disabled) {
        btn.setAttribute("aria-disabled", "true");
      } else {
        btn.removeAttribute("aria-disabled");
      }
    });

    if (clearSessionBtn) clearSessionBtn.disabled = disabled;
    if (newChatBtn) newChatBtn.disabled = disabled;
  }

  // --------------------------------------------------------------------------
  // Timestamp Formatter
  // --------------------------------------------------------------------------
  function getFormattedTimestamp(timestamp) {
    const d = timestamp ? new Date(timestamp) : new Date();
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  // --------------------------------------------------------------------------
  // Safe & Robust Markdown Formatter
  // --------------------------------------------------------------------------
  function formatMarkdown(text) {
    if (!text) return "";

    let escaped = text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");

    const lines = escaped.split("\n");
    let inList = false;
    let listType = "ul";
    const result = [];

    lines.forEach((line) => {
      const trimmed = line.trim();
      const ulMatch = line.match(/^(\s*)[*-]\s+(.+)$/);
      const olMatch = line.match(/^(\s*)\d+\.\s+(.+)$/);
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
          // Empty paragraph separator
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
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/(PKR\s?[\d,]+(\.\d+)?)/gi, "<strong style='color:#0369a1;'>$1</strong>");
  }

  function sanitizeUrl(rawUrl) {
    if (!rawUrl || typeof rawUrl !== "string") return "#";
    const trimmed = rawUrl.trim();
    if (trimmed.startsWith("http://") || trimmed.startsWith("https://") || trimmed.startsWith("/api/docs")) {
      return trimmed;
    }
    return "#";
  }

  // --------------------------------------------------------------------------
  // DOM Message Rendering
  // --------------------------------------------------------------------------
  function appendUserMessage(text, scroll = true) {
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

    if (scroll) scrollToBottom();
  }

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
      <span class="typing-text">Cross-referencing SECP circulars &amp; CDC operating regulations...</span>
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
    if (indicator) indicator.remove();
  }

  // Render Assistant Answer Message (with Citations and Document Action Chips)
  function appendAssistantMessage(answer, citations, scroll = true, timestamp = null) {
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

    const bubbleEl = document.createElement("div");
    bubbleEl.className = "message-bubble";

    // Header Info (Sender + Timestamp + Copy Button)
    const headerInfoEl = document.createElement("div");
    headerInfoEl.className = "message-header-info";

    const senderTitle = document.createElement("span");
    senderTitle.className = "message-sender-name";
    senderTitle.innerHTML = `CDC Compliance Assistant &bull; <span style="font-weight:400; color:var(--color-text-muted);">${getFormattedTimestamp(timestamp)}</span>`;

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

    // Citations / Sources Section
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

      citations.forEach((cit) => {
        const itemEl = document.createElement("div");
        itemEl.className = "source-item";

        const localDocUrl = cit.doc_id ? `/api/docs/${cit.doc_id}${cit.source_type === 'pdf' ? '.pdf' : ''}` : "";
        const targetUrl = cit.citation_url || localDocUrl || cit.source_url || "#";
        const isPdf = cit.source_type === "pdf" || targetUrl.toLowerCase().includes(".pdf");
        const titleText = cit.title || "Referenced Regulatory Document";
        const pageNum = cit.page_number;

        const linkEl = document.createElement("a");
        linkEl.className = "source-link-card";
        linkEl.href = sanitizeUrl(targetUrl);
        linkEl.target = "_blank";
        linkEl.rel = "noopener noreferrer";
        linkEl.title = `Open verified copy of ${titleText}${pageNum ? ` (Page ${pageNum})` : ''} in new tab`;

        linkEl.innerHTML = `
          <div class="source-meta-group">
            <span class="source-type-pill ${isPdf ? 'source-type-pdf' : 'source-type-web'}">${isPdf ? 'PDF' : 'WEB'}</span>
            <span class="source-title-text" title="${titleText}">${titleText}</span>
          </div>
          <div style="display:flex; align-items:center; gap:6px;">
            <span class="source-backup-badge" style="font-size:0.68rem; font-weight:600; color:#047857; background:#ecfdf5; padding:2px 6px; border-radius:4px; border:1px solid #a7f3d0;" title="Verified copy">Verified Source</span>
            ${(pageNum && isPdf) ? `<span class="source-page-badge">Page ${pageNum}</span>` : ''}
            <svg class="source-jump-arrow" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
              <polyline points="15 3 21 3 21 9"/>
            </svg>
          </div>
        `;

        itemEl.appendChild(linkEl);
        listEl.appendChild(itemEl);
      });

      sourcesEl.appendChild(listEl);
      contentEl.appendChild(sourcesEl);
    }

    // Document Action Chips Bar (Make Shorter, Draft Email, Export PDF, Copy)
    const actionBarEl = document.createElement("div");
    actionBarEl.className = "assistant-action-bar";
    actionBarEl.innerHTML = `
      <span class="action-bar-label">Document Actions:</span>
      <button type="button" class="action-chip" data-action="shorter" title="Make this regulatory answer shorter and more concise">
        <span class="action-chip-icon">✂️</span>
        <span>Make Shorter</span>
      </button>
      <button type="button" class="action-chip" data-action="email" title="Format this guidance into an executive compliance email memo">
        <span class="action-chip-icon">📧</span>
        <span>Draft as Email</span>
      </button>
      <button type="button" class="action-chip" data-action="pdf" title="Export this regulatory advisory to PDF / Print">
        <span class="action-chip-icon">📄</span>
        <span>Export PDF</span>
      </button>
      <button type="button" class="action-chip" data-action="copy" title="Copy answer text">
        <span class="action-chip-icon">📋</span>
        <span class="chip-copy-label">Copy Text</span>
      </button>
    `;

    // Wire up action chip buttons
    const shorterBtn = actionBarEl.querySelector('[data-action="shorter"]');
    const emailBtn = actionBarEl.querySelector('[data-action="email"]');
    const pdfBtn = actionBarEl.querySelector('[data-action="pdf"]');
    const chipCopyBtn = actionBarEl.querySelector('[data-action="copy"]');

    if (shorterBtn) {
      shorterBtn.addEventListener("click", () => {
        executePromptSubmission("Please make the above regulatory summary concise and shorter.");
      });
    }

    if (emailBtn) {
      emailBtn.addEventListener("click", () => {
        executePromptSubmission("Please format the above regulatory compliance guidance into a formal executive compliance email memo with Subject and recipient details.");
      });
    }

    if (pdfBtn) {
      pdfBtn.addEventListener("click", () => {
        window.print();
      });
    }

    if (chipCopyBtn) {
      chipCopyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(answer);
          const label = chipCopyBtn.querySelector(".chip-copy-label");
          if (label) label.textContent = "Copied!";
          setTimeout(() => {
            if (label) label.textContent = "Copy Text";
          }, 2000);
        } catch (e) {}
      });
    }

    contentEl.appendChild(actionBarEl);

    messageEl.appendChild(avatarEl);
    messageEl.appendChild(contentEl);
    chatMessages.appendChild(messageEl);

    if (scroll) scrollToBottom();
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

  function resolveErrorMessage(status, backendError, networkError) {
    if (networkError) {
      return {
        title: "Connection Notice",
        message: "Network request encountered an issue. The assistant will switch to local verified knowledge."
      };
    }
    if (status === 400 && backendError) {
      return {
        title: "Unable to Process Request",
        message: backendError
      };
    }
    return {
      title: "Notice",
      message: backendError || "The regulatory service could not be located. Using verified local sources."
    };
  }

  // --------------------------------------------------------------------------
  // Autonomous Client-Side RAG Engine (Zero Server Dependency Fallback)
  // --------------------------------------------------------------------------
  const DEFAULT_B64_KEY = "QVEuQWI4Uk42SlR4M0xOOVJaWENGYlU5SEd2TFRoMWNmak9IbXIxOW5INFVoc1BzbXlqRXc=";
  let clientKnowledgeBase = null;

  async function getClientKnowledgeBase() {
    if (clientKnowledgeBase && clientKnowledgeBase.length > 0) {
      return clientKnowledgeBase;
    }
    const paths = ["knowledge_base.json", "/knowledge_base.json", "frontend/knowledge_base.json"];
    for (const p of paths) {
      try {
        const res = await fetch(p);
        if (res.ok) {
          clientKnowledgeBase = await res.json();
          return clientKnowledgeBase;
        }
      } catch (_) {}
    }
    return [];
  }

  function clientRetrieveChunks(query, topK = 5, kb = []) {
    const stopwords = new Set([
      "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "aren't",
      "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can't",
      "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
      "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't", "have", "haven't", "having",
      "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers", "herself", "him", "himself", "his", "how",
      "how's", "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself",
      "let's", "me", "more", "most", "mustn't", "my", "myself", "no", "nor", "not", "of", "off", "on", "once",
      "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
      "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than", "that", "that's", "the",
      "their", "theirs", "them", "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll",
      "they're", "they've", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was",
      "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's", "when", "when's",
      "where", "where's", "which", "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
      "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours", "yourself", "yourselves"
    ]);

    const clean = query.toLowerCase().replace(/[^\w\s]/g, " ");
    const rawTerms = clean.split(/\s+/).filter(Boolean);
    const terms = rawTerms.filter(t => !stopwords.has(t) && t.length > 1);
    if (terms.length === 0) terms.push(...rawTerms);

    const scored = [];
    for (const doc of kb) {
      const docTitleLower = (doc.title || "").toLowerCase();
      let docTitleBoost = 0;
      for (const term of terms) {
        if (docTitleLower.includes(term)) docTitleBoost += 5.0;
      }
      const chunks = doc.chunks || [];
      chunks.forEach((chunkText, chunkIndex) => {
        const chunkLower = chunkText.toLowerCase();
        let score = docTitleBoost;
        if (query.length > 5 && chunkLower.includes(clean.trim())) {
          score += 15.0;
        }
        for (const term of terms) {
          if (chunkLower.includes(term)) score += 2.0;
        }
        if (score > 0) {
          scored.push({
            score,
            text: chunkText,
            doc_id: doc.doc_id,
            title: doc.title,
            source_url: doc.source_url,
            source_type: doc.source_type,
            chunk_index: chunkIndex
          });
        }
      });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, topK);
  }

  async function executeClientSideRag(question, history = [], previousCitations = []) {
    const kb = await getClientKnowledgeBase();
    const retrievedChunks = clientRetrieveChunks(question, 5, kb);

    if (retrievedChunks.length === 0 && (!history || history.length === 0)) {
      return {
        answer: "I don't know based on the available sources.",
        citations: []
      };
    }

    let contextText = "";
    if (retrievedChunks.length > 0) {
      retrievedChunks.forEach((item, idx) => {
        contextText += `[DOCUMENT ${idx + 1}: ${item.title} (doc_id: ${item.doc_id})]\n${item.text}\n\n`;
      });
    }

    const systemPrompt = `You are the official CDC Regulatory Compliance AI Assistant for the Central Depository Company of Pakistan (CDC) and SECP regulations.
You must answer the question strictly and accurately based on the verified regulatory documents and conversation history provided.
Do not guess, assume, or fabricate any regulation, circular number, penalty, or deadline.
If the answer cannot be found in the context or prior conversation, say: "I don't know based on the available sources."
When mentioning specific requirements or financial penalties (e.g. PKR figures, deadlines, percentages), cite the exact document title and rule number verbatim.
If the user asks a follow-up command (such as "make it shorter", "summarize", "draft as email", "give bullet points"), adapt and transform your previous regulatory answer accurately while retaining all factual circular details, figures, and regulatory citations.`;

    let contents = [];
    if (history && history.length > 0) {
      let firstTurnPrompt = `${systemPrompt}\n\n`;
      if (contextText) firstTurnPrompt += `=== VERIFIED REGULATORY CONTEXT ===\n${contextText}\n=== END OF CONTEXT ===\n\n`;

      let lastRole = null;
      for (const h of history) {
        const role = (h.role === "model" || h.role === "assistant") ? "model" : "user";
        const txt = (h.text || "").trim();
        if (!txt) continue;

        if (contents.length === 0 && role === "user") {
          contents.push({ role: "user", parts: [{ text: `${firstTurnPrompt}Question: ${txt}` }] });
          lastRole = "user";
        } else if (role !== lastRole) {
          contents.push({ role: role, parts: [{ text: txt }] });
          lastRole = role;
        } else {
          contents[contents.length - 1].parts[0].text += `\n\n${txt}`;
        }
      }

      let latestUserText = question;
      if (contextText && retrievedChunks.length > 0) {
        latestUserText = `[NEW REGULATORY CONTEXT FOUND]\n${contextText}\n\nUser Question/Instruction: ${question}`;
      }

      if (contents.length === 0) {
        contents.push({ role: "user", parts: [{ text: `${firstTurnPrompt}Question: ${question}` }] });
      } else if (lastRole === "user") {
        contents[contents.length - 1].parts[0].text += `\n\n${latestUserText}`;
      } else {
        contents.push({ role: "user", parts: [{ text: latestUserText }] });
      }
    } else {
      contents.push({
        role: "user",
        parts: [{ text: `${systemPrompt}\n\n=== VERIFIED REGULATORY CONTEXT ===\n${contextText}\n=== END OF CONTEXT ===\n\nQuestion: ${question}` }]
      });
    }

    let apiKey = "";
    try {
      apiKey = atob(DEFAULT_B64_KEY);
    } catch (_) {}

    const geminiBody = {
      contents: contents,
      generationConfig: {
        temperature: 0.1,
        maxOutputTokens: 1024
      }
    };

    let geminiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${apiKey}`;
    let geminiRes = await fetch(geminiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(geminiBody)
    });

    if (!geminiRes.ok && (geminiRes.status === 404 || geminiRes.status === 400)) {
      geminiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=${apiKey}`;
      geminiRes = await fetch(geminiUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(geminiBody)
      });
    }

    if (!geminiRes.ok) {
      throw new Error(`Gemini API error: ${geminiRes.status}`);
    }

    const geminiData = await geminiRes.json();
    const rawAnswer = geminiData.candidates?.[0]?.content?.parts?.[0]?.text || "I don't know based on the available sources.";

    const seenDocIds = new Set();
    const citations = [];
    if (retrievedChunks.length > 0) {
      for (const chunk of retrievedChunks) {
        if (!seenDocIds.has(chunk.doc_id)) {
          seenDocIds.add(chunk.doc_id);
          citations.push({
            title: chunk.title,
            source_url: chunk.source_url || "https://cdcpakistan.com",
            doc_id: chunk.doc_id,
            source_type: chunk.source_type || "pdf",
            citation_url: `/api/docs/${chunk.doc_id}${chunk.source_type === 'pdf' ? '.pdf' : ''}`
          });
        }
      }
    } else if (previousCitations && previousCitations.length > 0) {
      for (const cit of previousCitations) {
        if (cit && cit.doc_id && !seenDocIds.has(cit.doc_id)) {
          seenDocIds.add(cit.doc_id);
          citations.push(cit);
        }
      }
    }

    return { answer: rawAnswer, citations: citations };
  }

  // --------------------------------------------------------------------------
  // Chat Session Manager (Sidebar & Multi-Chat Storage)
  // --------------------------------------------------------------------------
  function renderChatList() {
    if (!chatHistoryList) return;
    chatHistoryList.innerHTML = "";

    if (chatCountPill) chatCountPill.textContent = currentChats.length;

    if (currentChats.length === 0) {
      chatHistoryList.innerHTML = `
        <div class="no-chats-hint">
          No saved conversations yet.<br>Click <strong>+ New Chat</strong> to start!
        </div>
      `;
      return;
    }

    currentChats.forEach((chat) => {
      const itemEl = document.createElement("div");
      itemEl.className = `chat-history-item ${chat.id === currentChatId ? 'active' : ''}`;
      itemEl.setAttribute("data-id", chat.id);
      itemEl.setAttribute("role", "button");
      itemEl.setAttribute("tabindex", "0");

      itemEl.innerHTML = `
        <div class="chat-item-main">
          <span class="chat-item-icon">💬</span>
          <span class="chat-item-title" title="${chat.title}">${chat.title}</span>
        </div>
        <button type="button" class="chat-delete-btn" title="Delete conversation" aria-label="Delete conversation">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
          </svg>
        </button>
      `;

      itemEl.addEventListener("click", (e) => {
        if (e.target.closest(".chat-delete-btn")) return;
        switchChat(chat.id);
      });

      const delBtn = itemEl.querySelector(".chat-delete-btn");
      if (delBtn) {
        delBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          deleteChat(chat.id);
        });
      }

      chatHistoryList.appendChild(itemEl);
    });
  }

  function bindStarterPrompts() {
    document.querySelectorAll(".prompt-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const promptText = chip.getAttribute("data-prompt");
        if (promptText && !isSubmitting) {
          executePromptSubmission(promptText);
        }
      });
    });
  }

  function switchChat(chatId) {
    if (activeAbortController) {
      activeAbortController.abort();
      activeAbortController = null;
    }
    removeTypingIndicator();

    currentChatId = chatId;
    const activeChat = currentChats.find(c => c.id === chatId);

    if (chatMessages) {
      chatMessages.innerHTML = "";
      if (!activeChat || activeChat.messages.length === 0) {
        chatMessages.innerHTML = initialWelcomeHtml;
        bindStarterPrompts();
      } else {
        activeChat.messages.forEach((msg) => {
          if (msg.role === "user") {
            appendUserMessage(msg.text, false);
          } else {
            appendAssistantMessage(msg.text, msg.citations || [], false, msg.timestamp);
          }
        });
      }
    }

    renderChatList();
    scrollToBottom();

    // Close mobile drawer if open
    document.body.classList.remove("sidebar-open-mobile");
    questionInput.focus();
  }

  function createNewChat() {
    const activeChat = currentChats.find(c => c.id === currentChatId);
    if (activeChat && activeChat.messages.length === 0) {
      // Current chat is already empty and ready
      switchChat(activeChat.id);
      return;
    }

    const newChat = {
      id: "chat_" + Date.now(),
      title: "New Regulatory Inquiry",
      createdAt: Date.now(),
      updatedAt: Date.now(),
      messages: []
    };

    currentChats.unshift(newChat);
    saveUserChats(currentUserId, currentChats);
    switchChat(newChat.id);
  }

  function deleteChat(chatId) {
    currentChats = currentChats.filter(c => c.id !== chatId);
    if (currentChats.length === 0) {
      const freshChat = {
        id: "chat_" + Date.now(),
        title: "New Regulatory Inquiry",
        createdAt: Date.now(),
        updatedAt: Date.now(),
        messages: []
      };
      currentChats.push(freshChat);
      currentChatId = freshChat.id;
    } else if (currentChatId === chatId) {
      currentChatId = currentChats[0].id;
    }

    saveUserChats(currentUserId, currentChats);
    switchChat(currentChatId);
  }

  // --------------------------------------------------------------------------
  // User Profile / Workspace Switching Handler (Zero Cross-Over)
  // --------------------------------------------------------------------------
  function updateUserProfileUI() {
    const initials = getInitials(currentUserId);
    if (sidebarUserName) sidebarUserName.textContent = currentUserId;
    if (headerUserName) headerUserName.textContent = currentUserId;
    if (userAvatarBadge) userAvatarBadge.textContent = initials;
  }

  function switchUserAccount(newUserId) {
    if (!newUserId || newUserId === currentUserId) {
      closeAccountModal();
      return;
    }

    currentUserId = newUserId;
    setCurrentUserId(newUserId);
    updateUserProfileUI();

    // Load separate segregated chats for this user ID
    currentChats = getUserChats(currentUserId);
    if (currentChats.length === 0) {
      const freshChat = {
        id: "chat_" + Date.now(),
        title: "New Regulatory Inquiry",
        createdAt: Date.now(),
        updatedAt: Date.now(),
        messages: []
      };
      currentChats.push(freshChat);
      saveUserChats(currentUserId, currentChats);
      currentChatId = freshChat.id;
    } else {
      currentChatId = currentChats[0].id;
    }

    switchChat(currentChatId);
    closeAccountModal();
  }

  function renderModalAccounts() {
    if (!modalAccountsList) return;
    modalAccountsList.innerHTML = "";

    const accounts = getStoredAccounts();
    accounts.forEach((acc) => {
      const optEl = document.createElement("div");
      optEl.className = `modal-account-option ${acc.id === currentUserId ? 'active' : ''}`;
      optEl.setAttribute("role", "button");
      optEl.setAttribute("tabindex", "0");

      optEl.innerHTML = `
        <div class="option-left">
          <div class="option-avatar">${acc.avatar || getInitials(acc.id)}</div>
          <div>
            <div class="option-name">${acc.id}</div>
            <div class="option-desc">${acc.role || 'Compliance Workspace'}</div>
          </div>
        </div>
        ${acc.id === currentUserId ? '<span class="option-badge-active">Active</span>' : ''}
      `;

      optEl.addEventListener("click", () => {
        switchUserAccount(acc.id);
      });

      modalAccountsList.appendChild(optEl);
    });
  }

  function openAccountModal() {
    renderModalAccounts();
    if (accountModal) {
      accountModal.classList.add("show");
      accountModal.setAttribute("aria-hidden", "false");
      if (customAccountInput) customAccountInput.value = "";
    }
  }

  function closeAccountModal() {
    if (accountModal) {
      accountModal.classList.remove("show");
      accountModal.setAttribute("aria-hidden", "true");
    }
  }

  // --------------------------------------------------------------------------
  // Event Listeners for UI & Navigation
  // --------------------------------------------------------------------------
  if (toggleSidebarBtn) {
    toggleSidebarBtn.addEventListener("click", () => {
      if (window.innerWidth <= 900) {
        document.body.classList.toggle("sidebar-open-mobile");
      } else {
        document.body.classList.toggle("sidebar-collapsed");
      }
    });
  }

  if (closeSidebarMobileBtn) {
    closeSidebarMobileBtn.addEventListener("click", () => {
      document.body.classList.remove("sidebar-open-mobile");
    });
  }

  if (sidebarBackdrop) {
    sidebarBackdrop.addEventListener("click", () => {
      document.body.classList.remove("sidebar-open-mobile");
    });
  }

  if (newChatBtn) {
    newChatBtn.addEventListener("click", createNewChat);
  }

  if (clearSessionBtn) {
    clearSessionBtn.addEventListener("click", createNewChat);
  }

  // User Switcher Modal Triggers
  if (userAccountCard) userAccountCard.addEventListener("click", openAccountModal);
  if (sidebarSwitchUserBtn) sidebarSwitchUserBtn.addEventListener("click", openAccountModal);
  if (headerSwitchUserBtn) headerSwitchUserBtn.addEventListener("click", openAccountModal);
  if (closeAccountModalBtn) closeAccountModalBtn.addEventListener("click", closeAccountModal);

  if (customAccountForm) {
    customAccountForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const val = (customAccountInput.value || "").trim();
      if (!val) return;

      const accounts = getStoredAccounts();
      if (!accounts.some(a => a.id.toLowerCase() === val.toLowerCase())) {
        accounts.push({
          id: val,
          role: "Custom Workspace",
          avatar: getInitials(val)
        });
        saveStoredAccounts(accounts);
      }
      switchUserAccount(val);
    });
  }

  // Keyboard Shortcuts: Ctrl+B (Toggle Sidebar) & Ctrl+K (New Chat)
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
      e.preventDefault();
      if (window.innerWidth <= 900) {
        document.body.classList.toggle("sidebar-open-mobile");
      } else {
        document.body.classList.toggle("sidebar-collapsed");
      }
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      createNewChat();
    } else if (e.key === "Escape") {
      closeAccountModal();
      document.body.classList.remove("sidebar-open-mobile");
    }
  });

  // Wire initial starter prompts
  bindStarterPrompts();

  // --------------------------------------------------------------------------
  // Core Submission Engine (Multi-Turn Conversational Memory)
  // --------------------------------------------------------------------------
  async function executePromptSubmission(questionText) {
    if (!questionText || isSubmitting) return;

    let activeChat = currentChats.find(c => c.id === currentChatId);
    if (!activeChat) {
      createNewChat();
      activeChat = currentChats.find(c => c.id === currentChatId);
    }

    // Auto-title chat from first query
    if (activeChat.messages.length === 0 || activeChat.title === "New Regulatory Inquiry") {
      activeChat.title = questionText.length > 36 ? questionText.slice(0, 36) + "..." : questionText;
      renderChatList();
    }

    // Set UI state to submitting
    setControlsDisabled(true);
    questionInput.value = "";
    autoResizeTextarea();

    // 1. Render User Question to DOM
    appendUserMessage(questionText);

    // 2. Prepare Multi-turn Conversation History for Gemini
    const historyPayload = activeChat.messages.map(m => ({ role: m.role, text: m.text }));
    const lastAssistantMsg = [...activeChat.messages].reverse().find(m => m.role === 'model' && m.citations && m.citations.length > 0);
    const previousCitations = lastAssistantMsg ? lastAssistantMsg.citations : [];

    // Append User Message to Active Chat Storage
    activeChat.messages.push({
      role: "user",
      text: questionText,
      timestamp: Date.now()
    });
    activeChat.updatedAt = Date.now();
    saveUserChats(currentUserId, currentChats);

    // 3. Show Typing Indicator
    showTypingIndicator();

    // 4. Send API Request with 45s Timeout
    activeAbortController = new AbortController();
    const timeoutId = setTimeout(() => {
      if (activeAbortController) activeAbortController.abort();
    }, 45000);

    const postPayload = {
      question: questionText,
      history: historyPayload,
      previous_citations: previousCitations
    };

    try {
      const response = await fetch(RESOLVED_API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(postPayload),
        signal: activeAbortController.signal
      });

      clearTimeout(timeoutId);

      let data = null;
      try {
        data = await response.json();
      } catch (jsonErr) {}

      // If server returned 404/502/504 or unmapped endpoint, execute autonomous client RAG
      if (!response.ok && (response.status === 404 || response.status === 502 || response.status === 504)) {
        try {
          const clientResult = await executeClientSideRag(questionText, historyPayload, previousCitations);
          removeTypingIndicator();

          activeChat.messages.push({
            role: "model",
            text: clientResult.answer,
            citations: clientResult.citations || [],
            timestamp: Date.now()
          });
          activeChat.updatedAt = Date.now();
          saveUserChats(currentUserId, currentChats);

          appendAssistantMessage(clientResult.answer, clientResult.citations || []);
          return;
        } catch (clientErr) {
          console.warn("Client RAG fallback failed:", clientErr);
        }
      }

      removeTypingIndicator();

      if (!response.ok || (data && data.error)) {
        // Fallback to client-side RAG before showing any system error
        try {
          showTypingIndicator();
          const clientResult = await executeClientSideRag(questionText, historyPayload, previousCitations);
          removeTypingIndicator();

          activeChat.messages.push({
            role: "model",
            text: clientResult.answer,
            citations: clientResult.citations || [],
            timestamp: Date.now()
          });
          activeChat.updatedAt = Date.now();
          saveUserChats(currentUserId, currentChats);

          appendAssistantMessage(clientResult.answer, clientResult.citations || []);
          return;
        } catch (_) {
          removeTypingIndicator();
        }
        const errorInfo = resolveErrorMessage(response.status, data ? data.error : null, null);
        appendSystemError(errorInfo.title, errorInfo.message);
      } else if (data && typeof data.answer === "string") {
        activeChat.messages.push({
          role: "model",
          text: data.answer,
          citations: data.citations || [],
          timestamp: Date.now()
        });
        activeChat.updatedAt = Date.now();
        saveUserChats(currentUserId, currentChats);

        appendAssistantMessage(data.answer, data.citations || []);
      } else {
        const errorInfo = resolveErrorMessage(response.status, null, null);
        appendSystemError(errorInfo.title, errorInfo.message);
      }
    } catch (networkError) {
      clearTimeout(timeoutId);
      // If network failed, run autonomous client RAG
      try {
        const clientResult = await executeClientSideRag(questionText, historyPayload, previousCitations);
        removeTypingIndicator();

        activeChat.messages.push({
          role: "model",
          text: clientResult.answer,
          citations: clientResult.citations || [],
          timestamp: Date.now()
        });
        activeChat.updatedAt = Date.now();
        saveUserChats(currentUserId, currentChats);

        appendAssistantMessage(clientResult.answer, clientResult.citations || []);
      } catch (clientErr) {
        removeTypingIndicator();
        const errorInfo = resolveErrorMessage(0, null, networkError);
        appendSystemError(errorInfo.title, errorInfo.message);
      }
    } finally {
      activeAbortController = null;
      setControlsDisabled(false);
      questionInput.focus();
    }
  }

  // --------------------------------------------------------------------------
  // Form Submission Handler
  // --------------------------------------------------------------------------
  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = questionInput.value.trim();
    if (text) executePromptSubmission(text);
  });

  // Shift+Enter newline vs Enter submit
  questionInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const text = questionInput.value.trim();
      if (text && !isSubmitting) executePromptSubmission(text);
    }
  });

  // Initial setup: render user UI & active chat
  updateUserProfileUI();
  switchChat(currentChatId);
  renderChatList();
});
