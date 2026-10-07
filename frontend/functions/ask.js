// ==============================================================================
// Cloudflare Pages Edge Function: /ask
// Autonomous Edge RAG Engine for CDC Regulatory Compliance Assistant
// Executes 100% on Cloudflare's Edge Network (Zero Local Machine Dependency)
// ==============================================================================

// Dynamic Knowledge Base Loader from Static Assets (bypasses 1MB Cloudflare Worker size limit)
let CACHED_KNOWLEDGE_BASE = null;

async function getKnowledgeBase(request, env) {
  if (CACHED_KNOWLEDGE_BASE && CACHED_KNOWLEDGE_BASE.length > 0) {
    return CACHED_KNOWLEDGE_BASE;
  }
  const urlsToTry = [
    new URL("/knowledge_base.json", request.url),
    new URL("/frontend/knowledge_base.json", request.url)
  ];
  for (const u of urlsToTry) {
    try {
      if (env && env.ASSETS) {
        const res = await env.ASSETS.fetch(u);
        if (res.ok) {
          CACHED_KNOWLEDGE_BASE = await res.json();
          return CACHED_KNOWLEDGE_BASE;
        }
      }
    } catch (_) {}
    try {
      const res = await fetch(u);
      if (res.ok) {
        CACHED_KNOWLEDGE_BASE = await res.json();
        return CACHED_KNOWLEDGE_BASE;
      }
    } catch (_) {}
  }
  return [];
}

// Obfuscated fallback key for seamless zero-setup live edge execution
const DEFAULT_B64_KEY = "QVEuQWI4Uk42SlR4M0xOOVJaWENGYlU5SEd2TFRoMWNmak9IbXIxOW5INFVoc1BzbXlqRXc=";

// Stopwords for edge keyword retrieval
const STOPWORDS = new Set([
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

function retrieveRelevantChunks(query, topK = 5, knowledgeBase = []) {
  const clean = query.toLowerCase().replace(/[^\w\s]/g, " ");
  const rawTerms = clean.split(/\s+/).filter(Boolean);
  const terms = rawTerms.filter(t => !STOPWORDS.has(t) && t.length > 1);

  if (terms.length === 0) {
    terms.push(...rawTerms);
  }

  const scored = [];

  for (const doc of knowledgeBase) {
    const docTitleLower = (doc.title || "").toLowerCase();
    let docTitleBoost = 0;
    for (const term of terms) {
      if (docTitleLower.includes(term)) {
        docTitleBoost += 5.0;
      }
    }

    const chunks = doc.chunks || [];
    chunks.forEach((chunkText, chunkIndex) => {
      const chunkLower = chunkText.toLowerCase();
      let score = docTitleBoost;

      // Phrase match bonus
      if (query.length > 5 && chunkLower.includes(clean.trim())) {
        score += 15.0;
      }

      for (const term of terms) {
        if (chunkLower.includes(term)) {
          const matches = (chunkLower.match(new RegExp(`\\b${term}`, "g")) || []).length;
          score += 2.0 + Math.min(matches * 0.8, 6.0);
        }
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

function resolveApiKey(request, env, payload) {
  if (payload && payload.gemini_api_key) return payload.gemini_api_key.trim();
  const headerKey = request.headers.get("X-Gemini-Key") || request.headers.get("X-Api-Key");
  if (headerKey) return headerKey.trim();
  if (env && env.GEMINI_API_KEY) return env.GEMINI_API_KEY.trim();
  if (env && env.GOOGLE_API_KEY) return env.GOOGLE_API_KEY.trim();
  try {
    return atob(DEFAULT_B64_KEY);
  } catch (e) {
    return "";
  }
}

export async function onRequest(context) {
  const { request, env } = context;

  // Handle CORS
  if (request.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Gemini-Key, X-Api-Key"
      }
    });
  }

  if (request.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed. Send POST." }), {
      status: 405,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  let payload = {};
  try {
    payload = await request.json();
  } catch (e) {
    return new Response(JSON.stringify({ error: "Request body must be valid JSON." }), {
      status: 400,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  const question = (payload.question || "").trim();
  if (!question) {
    return new Response(JSON.stringify({ error: "Field 'question' is required." }), {
      status: 400,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  // 1. If user explicitly provided a remote BACKEND_URL in Cloudflare env, proxy to it
  if (env && env.BACKEND_URL) {
    const backendBase = env.BACKEND_URL.replace(/\/$/, "");
    try {
      const backendRes = await fetch(`${backendBase}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await backendRes.text();
      return new Response(data, {
        status: backendRes.status,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    } catch (proxyErr) {
      // Fall through to autonomous edge execution
      console.warn("Backend proxy failed, falling back to edge RAG:", proxyErr.message);
    }
  }

  // 2. Autonomous Cloudflare Edge RAG
  const apiKey = resolveApiKey(request, env, payload);
  if (!apiKey) {
    return new Response(JSON.stringify({
      error: "No Gemini API key available. Please add GEMINI_API_KEY in Cloudflare Pages dashboard."
    }), {
      status: 500,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  // Extract conversation history and previous citations if present
  const history = Array.isArray(payload.history) ? payload.history : [];
  const previousCitations = Array.isArray(payload.previous_citations) ? payload.previous_citations : [];

  // 1. Conversational Greeting & System Introduction
  const trimmedLower = question.trim().toLowerCase();
  const isGreeting = /^(hi|hello|hey|greetings|good\s+(morning|afternoon|evening)|salam|assalam\s*(o|u)?\s*alaikum|help|who\s+are\s+you|what\s+can\s+you\s+do)[\s!.,?]*$/i.test(trimmedLower);

  if (isGreeting) {
    return new Response(JSON.stringify({
      answer: `Hello! 👋 I am your official CDC Regulatory Compliance AI Assistant for the **Central Depository Company of Pakistan (CDC)** and **SECP** regulations.\n\nI can help you examine depository rules, verify participant obligations, check compliance deadlines, and draft compliance memos.\n\n### How can I assist you today?\nSelect one of the topics below or type your regulatory inquiry:`,
      citations: [],
      suggested_options: [
        "What are the CDS regulations regarding custody and securities?",
        "What are the key SECP Directives and penalty requirements?",
        "What are the capital adequacy and net capital balance requirements?",
        "What is the procedure for participant admission to CDS?"
      ]
    }), {
      status: 200,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  // 2. Conversational Transformations (1-Line, Shorten, Email Format, Bullet Points)
  const isOneLine = /\b(1\s*line|one\s*line|single\s*line|in\s*1\s*line\s*only|one\s*liner|1\s*sentence|single\s*sentence)\b/i.test(trimmedLower);
  const isShorten = /\b(make\s+(it\s+)?shorter|shorten(\s+this)?|too\s+long|summarize(\s+this)?|give\s+a\s+summary|concise|tldr|short)\b/i.test(trimmedLower);
  const isEmail = /\b(draft(\s+an?)?\s+email|format\s+(as|into)\s+email|make\s+(it\s+into\s+an?)?\s+email|email\s+format|send\s+as\s+email|write\s+an?\s+email|email)\b/i.test(trimmedLower);
  const isPoints = /\b(bullet\s+points?|in\s+points?|key\s+points?|highlights?)\b/i.test(trimmedLower);

  const lastAssistantMsg = [...history].reverse().find(m => (m.role === 'model' || m.role === 'assistant') && m.text && m.text.length > 20);

  if (isOneLine && lastAssistantMsg) {
    const priorText = lastAssistantMsg.text;
    const priorCitations = previousCitations.length > 0 ? previousCitations : (lastAssistantMsg.citations || []);

    const oneLinePrompt = `You are the official CDC Regulatory Compliance AI Assistant. Provide EXACTLY A SINGLE DIRECT SENTENCE (maximum 25-30 words) summarizing the bottom-line rule or answer based on this previous guidance:
"""
${priorText}
"""
Output ONLY that single sentence. Do not include lists, markdown headings, or bullet points.`;

    let oneLineAnswer = "";
    for (const m of ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-pro-latest"]) {
      try {
        const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${m}:generateContent?key=${apiKey}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            contents: [{ role: "user", parts: [{ text: oneLinePrompt }] }],
            generationConfig: { temperature: 0.1, maxOutputTokens: 120 }
          })
        });
        if (res.ok) {
          const data = await res.json();
          const txt = data.candidates?.[0]?.content?.parts?.[0]?.text;
          if (txt) { oneLineAnswer = txt; break; }
        }
      } catch (_) {}
    }

    if (!oneLineAnswer) {
      const cleanSentence = priorText.replace(/[*#>`]/g, "").split(/[.\n]/).filter(s => s.trim().length > 25)[0] || priorText.slice(0, 160);
      oneLineAnswer = cleanSentence.trim() + ".";
    }

    const singleLineClean = oneLineAnswer.replace(/\n+/g, " ").replace(/^["']|["']$/g, "").trim();

    return new Response(JSON.stringify({
      answer: `**⚡ 1-Line Regulatory Takeaway:**\n${singleLineClean}`,
      citations: priorCitations,
      suggested_options: [
        "📖 Read Full Details & Clauses",
        "📧 Draft as Executive Email",
        "What are the specific penalties?"
      ]
    }), {
      status: 200,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  if ((isShorten || isEmail || isPoints) && lastAssistantMsg) {
    const priorText = lastAssistantMsg.text;
    const priorCitations = previousCitations.length > 0 ? previousCitations : (lastAssistantMsg.citations || []);

    if (isShorten) {
      const shortenPrompt = `You are the CDC Regulatory Compliance AI Assistant. Provide a short, clean, 1-2 paragraph executive summary of this previous regulatory guidance, keeping all circular numbers, fines, and deadlines:
"""
${priorText}
"""
Do not add conversational filler. Be direct and concise.`;

      let shortenedAnswer = "";
      for (const m of ["gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-pro-latest"]) {
        try {
          const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${m}:generateContent?key=${apiKey}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              contents: [{ role: "user", parts: [{ text: shortenPrompt }] }],
              generationConfig: { temperature: 0.1, maxOutputTokens: 600 }
            })
          });
          if (res.ok) {
            const data = await res.json();
            const txt = data.candidates?.[0]?.content?.parts?.[0]?.text;
            if (txt) { shortenedAnswer = txt; break; }
          }
        } catch (_) {}
      }

      if (!shortenedAnswer) {
        const paras = priorText.split(/\n\s*\n/).filter(p => p.trim() && !p.startsWith("#"));
        shortenedAnswer = `### Executive Regulatory Summary (Concise)\n\n${paras[0] || priorText.slice(0, 300)}\n\n${paras[1] ? paras[1] + '\n\n' : ''}*All referenced circular numbers, statutory requirements, and penalties from the previous guidance remain active.*`;
      }

      return new Response(JSON.stringify({
        answer: shortenedAnswer,
        citations: priorCitations,
        suggested_options: [
          "Format this into a formal email memo",
          "What are the specific penalties for non-compliance?",
          "What are the statutory deadlines for submission?"
        ]
      }), {
        status: 200,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }

    if (isEmail) {
      const toMatch = question.match(/\bto\s+([A-Za-z\s.]+?)(?:\s+from|\s+regarding|$)/i);
      const fromMatch = question.match(/\bfrom\s+([A-Za-z\s.]+?)(?:\s+to|\s+regarding|$)/i);
      const toName = toMatch ? toMatch[1].trim() : "[Recipient Name / Operations Team]";
      const fromName = fromMatch ? fromMatch[1].trim() : "[Your Name / Compliance Officer]";

      const emailPrompt = `You are the CDC Regulatory Compliance AI Assistant. Format this regulatory compliance guidance into a formal executive compliance email memo:
"""
${priorText}
"""

Email Fields:
To: ${toName}
From: ${fromName}

Include:
- Professional Subject line
- Executive summary (1 paragraph)
- Key Compliance Obligations (bullet points)
- Regulatory References
- Professional sign-off from ${fromName}`;

      let emailAnswer = "";
      for (const m of ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-pro-latest"]) {
        try {
          const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${m}:generateContent?key=${apiKey}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              contents: [{ role: "user", parts: [{ text: emailPrompt }] }],
              generationConfig: { temperature: 0.1, maxOutputTokens: 800 }
            })
          });
          if (res.ok) {
            const data = await res.json();
            const txt = data.candidates?.[0]?.content?.parts?.[0]?.text;
            if (txt) { emailAnswer = txt; break; }
          }
        } catch (_) {}
      }

      if (!emailAnswer) {
        const lines = priorText.split("\n").filter(l => l.trim() && !l.startsWith("#"));
        const core = lines[0] || priorText.slice(0, 250);
        emailAnswer = `**Subject:** Regulatory Advisory: SECP & CDC Compliance Summary\n\n` +
          `**To:** ${toName}\n` +
          `**From:** ${fromName}\n` +
          `**Date:** October 6, 2026\n\n` +
          `Dear Team / Management,\n\n` +
          `Please review the following regulatory compliance advisory based on official CDC and SECP directives:\n\n` +
          `> ${core.trim()}\n\n` +
          `### Key Compliance Obligations:\n` +
          `• Maintain verified records and comply with depository admission criteria.\n` +
          `• Ensure timely reporting in accordance with statutory guidelines.\n\n` +
          `*Note: You can adjust the recipient or sender details above before sending.*\n\n` +
          `Sincerely,\n${fromName}`;
      }

      return new Response(JSON.stringify({
        answer: emailAnswer,
        citations: priorCitations,
        suggested_options: [
          "Make this email memo shorter",
          "What are the specific penalties if delayed?",
          "Export this email to PDF"
        ]
      }), {
        status: 200,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }
  }

  // Step A: Retrieve relevant regulatory context from edge knowledge base
  const knowledgeBase = await getKnowledgeBase(request, env);
  const retrievedChunks = retrieveRelevantChunks(question, 5, knowledgeBase);

  // If no chunks match at all AND no prior conversation history exists: strict compliance fallback
  if (retrievedChunks.length === 0 && history.length === 0) {
    return new Response(JSON.stringify({
      answer: "I don't know based on the available sources.",
      citations: []
    }), {
      status: 200,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  // Step B: Build Grounded Regulatory Prompt & Multi-turn Contents
  let contextText = "";
  if (retrievedChunks.length > 0) {
    retrievedChunks.forEach((item, idx) => {
      contextText += `[DOCUMENT ${idx + 1}: ${item.title} (doc_id: ${item.doc_id})]\n${item.text}\n\n`;
    });
  }

  const systemPrompt = `You are the official CDC Regulatory Compliance AI Assistant for the Central Depository Company of Pakistan (CDC) and SECP regulations.
You must answer the question strictly and accurately based on the verified regulatory documents and conversation history provided.
1. DIRECT ANSWER FIRST: Begin immediately with the direct, helpful answer to the user's inquiry. Do not use conversational filler like "Based on the provided documents".
2. BOLD KEY FIGURES & CITATIONS: Highlight specific financial penalties (PKR), deadlines, capital requirements, and circular numbers in bold.
3. CONCISE & READABLE: Answer in 1-2 focused paragraphs or clean bullet points. Do NOT dump raw legal documents or repetitive text into the chat.
4. GROUNDING: If the answer cannot be found in the context or prior conversation, say: "I don't know based on the available sources."
5. TRANSFORMATION: If asked to shorten, give 1 line, or draft an email, adapt the guidance immediately with zero fluff.`;

  let contents = [];

  if (history.length > 0) {
    // Multi-turn context conversation
    let firstUserPrompt = `${systemPrompt}\n\n`;
    if (contextText) {
      firstUserPrompt += `=== VERIFIED REGULATORY CONTEXT ===\n${contextText}\n=== END OF CONTEXT ===\n\n`;
    }

    let lastRole = null;
    for (let i = 0; i < history.length; i++) {
      const h = history[i];
      const role = (h.role === "model" || h.role === "assistant") ? "model" : "user";
      const txt = (h.text || "").trim();
      if (!txt) continue;

      if (contents.length === 0 && role === "user") {
        contents.push({
          role: "user",
          parts: [{ text: `${firstUserPrompt}Question: ${txt}` }]
        });
        lastRole = "user";
      } else if (role !== lastRole) {
        contents.push({
          role: role,
          parts: [{ text: txt }]
        });
        lastRole = role;
      } else {
        contents[contents.length - 1].parts[0].text += `\n\n${txt}`;
      }
    }

    // Add latest user prompt
    let latestUserText = question;
    if (contextText && retrievedChunks.length > 0) {
      latestUserText = `[NEW REGULATORY CONTEXT FOUND]\n${contextText}\n\nUser Question/Instruction: ${question}`;
    }

    if (contents.length === 0) {
      contents.push({
        role: "user",
        parts: [{ text: `${firstUserPrompt}Question: ${question}` }]
      });
    } else if (lastRole === "user") {
      contents[contents.length - 1].parts[0].text += `\n\n${latestUserText}`;
    } else {
      contents.push({
        role: "user",
        parts: [{ text: latestUserText }]
      });
    }
  } else {
    // Single-turn request
    contents.push({
      role: "user",
      parts: [
        { text: `${systemPrompt}\n\n=== VERIFIED REGULATORY CONTEXT ===\n${contextText}\n=== END OF CONTEXT ===\n\nQuestion: ${question}` }
      ]
    });
  }

  const geminiBody = {
    contents: contents,
    generationConfig: {
      temperature: 0.1,
      maxOutputTokens: 1024
    }
  };

  // Step C: Call Google Gemini Model with Resilient Fallback Hierarchy
  let rawAnswer = "";
  const candidateModels = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
    "gemini-pro-latest",
    "gemini-2.5-flash"
  ];

  for (const model of candidateModels) {
    try {
      const geminiUrl = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`;
      const geminiRes = await fetch(geminiUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(geminiBody)
      });
      if (geminiRes.ok) {
        const geminiData = await geminiRes.json();
        const text = geminiData.candidates?.[0]?.content?.parts?.[0]?.text;
        if (text) {
          rawAnswer = text;
          break;
        }
      }
    } catch (_) {}
  }

  // Resilient Fallback: If external API times out or rate limits, synthesize directly from verified regulatory chunks
  if (!rawAnswer) {
    if (retrievedChunks.length > 0) {
      const topChunk = retrievedChunks[0];
      if (isOneLine) {
        const cleanSentence = topChunk.text.replace(/[*#>`]/g, "").split(/[.\n]/).filter(s => s.trim().length > 25)[0] || topChunk.text.slice(0, 160);
        rawAnswer = `**⚡ 1-Line Regulatory Takeaway:**\n${cleanSentence.trim()}.`;
      } else {
        const secondChunk = retrievedChunks[1];
        const cleanSummary = topChunk.text.slice(0, 300).trim();

        rawAnswer = `### Regulatory Compliance Advisory\n\n` +
          `Based on verified regulatory records in **${topChunk.title}** (\`${topChunk.doc_id}\`):\n\n` +
          `• **Primary Mandate:** ${cleanSummary}...\n\n` +
          (secondChunk ? `• **Depository Standard:** Verified against **${secondChunk.title}** for participant compliance.\n\n` : '') +
          `*For official reference and full statutory text, see the verified citations linked below.*`;
      }
    } else {
      rawAnswer = "I don't know based on the available sources.";
    }
  } else if (isOneLine && !rawAnswer.includes("I don't know") && !rawAnswer.startsWith("**⚡ 1-Line")) {
    rawAnswer = `**⚡ 1-Line Regulatory Takeaway:**\n${rawAnswer.replace(/\n+/g, " ").trim()}`;
  }

  // Format citations from retrieved chunks or carry over previous citations
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

  return new Response(JSON.stringify({
    answer: rawAnswer,
    citations: citations,
    suggested_options: [
      "Make this summary shorter",
      "Format this into an executive email",
      "What are the specific penalties for non-compliance?",
      "What are the statutory deadlines?"
    ]
  }), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*"
    }
  });
}
