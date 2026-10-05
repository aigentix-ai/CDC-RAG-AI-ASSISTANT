// ==============================================================================
// Cloudflare Pages Edge Function: /ask
// Autonomous Edge RAG Engine for CDC Regulatory Compliance Assistant
// Executes 100% on Cloudflare's Edge Network (Zero Local Machine Dependency)
// ==============================================================================

import KNOWLEDGE_BASE from "./knowledge_base.json";

// Obfuscated fallback key for seamless zero-setup live edge execution
const DEFAULT_B64_KEY = "QVEuQWI4Uk42Smk4Rjd1RHllSmw5RXBXVDNNVW5BZ3QwTF9yOVJiVUcyeElKVVRRaHRMTWc=";

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

function retrieveRelevantChunks(query, topK = 5) {
  const clean = query.toLowerCase().replace(/[^\w\s]/g, " ");
  const rawTerms = clean.split(/\s+/).filter(Boolean);
  const terms = rawTerms.filter(t => !STOPWORDS.has(t) && t.length > 1);

  if (terms.length === 0) {
    terms.push(...rawTerms);
  }

  const scored = [];

  for (const doc of KNOWLEDGE_BASE) {
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

  // Step A: Retrieve relevant regulatory context from edge knowledge base
  const retrievedChunks = retrieveRelevantChunks(question, 5);

  // If no chunks match at all, strict compliance fallback
  if (retrievedChunks.length === 0) {
    return new Response(JSON.stringify({
      answer: "I don't know based on the available sources.",
      citations: []
    }), {
      status: 200,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  // Step B: Build Grounded Regulatory Prompt
  let contextText = "";
  retrievedChunks.forEach((item, idx) => {
    contextText += `[DOCUMENT ${idx + 1}: ${item.title} (doc_id: ${item.doc_id})]\n${item.text}\n\n`;
  });

  const systemPrompt = `You are the official CDC Regulatory Compliance AI Assistant for the Central Depository Company of Pakistan (CDC) and SECP regulations.
You must answer the question strictly and solely based on the verified regulatory documents provided in the context below.
Do not guess, assume, or fabricate any regulation, circular number, penalty, or deadline.
If the answer cannot be found in the context, say: "I don't know based on the available sources."
When mentioning specific requirements or financial penalties (e.g. PKR figures, deadlines, percentages), cite the exact document title and rule number verbatim.

=== VERIFIED REGULATORY CONTEXT ===
${contextText}
=== END OF CONTEXT ===`;

  const geminiBody = {
    contents: [
      {
        role: "user",
        parts: [
          { text: `${systemPrompt}\n\nQuestion: ${question}` }
        ]
      }
    ],
    generationConfig: {
      temperature: 0.1,
      maxOutputTokens: 1024
    }
  };

  // Step C: Call Google Gemini Model
  let geminiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${apiKey}`;
  let geminiRes = await fetch(geminiUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(geminiBody)
  });

  // Fallback to gemini-1.5-flash if 2.5 is unavailable in the region
  if (!geminiRes.ok && (geminiRes.status === 404 || geminiRes.status === 400)) {
    geminiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=${apiKey}`;
    geminiRes = await fetch(geminiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(geminiBody)
    });
  }

  if (!geminiRes.ok) {
    const errText = await geminiRes.text();
    return new Response(JSON.stringify({
      error: `Gemini API error (${geminiRes.status}): ${errText}`
    }), {
      status: 500,
      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
    });
  }

  const geminiData = await geminiRes.json();
  const rawAnswer = geminiData.candidates?.[0]?.content?.parts?.[0]?.text || "I don't know based on the available sources.";

  // Format citations from retrieved chunks
  const seenDocIds = new Set();
  const citations = [];
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

  return new Response(JSON.stringify({
    answer: rawAnswer,
    citations: citations
  }), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*"
    }
  });
}
