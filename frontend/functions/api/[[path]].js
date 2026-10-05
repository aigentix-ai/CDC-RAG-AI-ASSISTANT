// ==============================================================================
// Cloudflare Pages Function: /api/*
// Edge Document Archive Viewer & API Gateway
// ==============================================================================

import KNOWLEDGE_BASE from "../knowledge_base.json";

function renderDocViewerHtml(doc) {
  const title = doc.title || "Regulatory Document";
  const dateStr = doc.date_scraped || "Archived via CDC RAG";
  const category = doc.category || "General Regulatory";
  const fullText = (doc.chunks || []).join("\n\n") || doc.text || "No text content recorded.";

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${title} — CDC Pakistan Regulatory Archive</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Plus+Jakarta+Sans:wght@600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --cdc-green: #047857;
      --color-bg: #f8fafc;
      --color-surface: #ffffff;
      --color-border: #e2e8f0;
      --color-text-main: #0f172a;
      --color-text-muted: #64748b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', system-ui, sans-serif;
      background: var(--color-bg);
      color: var(--color-text-main);
      line-height: 1.65;
      padding: 0 0 60px 0;
    }
    .top-navbar {
      background: var(--color-surface);
      border-bottom: 1px solid var(--color-border);
      padding: 14px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .brand { font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 700; color: var(--cdc-green); font-size: 1.1rem; }
    .badge-verified { background: #ecfdf5; color: #047857; padding: 4px 10px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
    .main-wrapper { max-width: 860px; margin: 32px auto; padding: 0 20px; }
    .doc-card { background: #fff; border-radius: 12px; border: 1px solid var(--color-border); padding: 32px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
    .doc-badge { display: inline-block; background: #e0f2fe; color: #0369a1; padding: 3px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; margin-bottom: 12px; }
    h1 { font-family: 'Plus Jakarta Sans', sans-serif; font-size: 1.5rem; margin-bottom: 14px; color: #0f172a; }
    .meta-row { display: flex; gap: 16px; font-size: 0.8rem; color: var(--color-text-muted); margin-bottom: 24px; border-bottom: 1px solid var(--color-border); padding-bottom: 16px; }
    .content-body { font-size: 0.95rem; line-height: 1.8; color: #334155; white-space: pre-wrap; font-family: 'Inter', sans-serif; }
  </style>
</head>
<body>
  <div class="top-navbar">
    <div class="brand">🏛️ CDC Pakistan Regulatory Archive</div>
    <span class="badge-verified">✓ Verified Cloudflare Edge Backup</span>
  </div>
  <div class="main-wrapper">
    <div class="doc-card">
      <div class="doc-badge">${category}</div>
      <h1>${title}</h1>
      <div class="meta-row">
        <span><strong>Document ID:</strong> <code>${doc.doc_id}</code></span>
        <span><strong>Date Archived:</strong> ${dateStr}</span>
        <span><strong>Source:</strong> ${doc.source_url ? `<a href="${doc.source_url}" target="_blank" rel="noopener">Official Link ↗</a>` : 'Central Depository'}</span>
      </div>
      <div class="content-body">${fullText}</div>
    </div>
  </div>
</body>
</html>`;
}

export async function onRequest(context) {
  const { request, env, params } = context;
  const rawPath = Array.isArray(params.path) ? params.path.join("/") : (params.path || "");

  // CORS Headers
  const corsHeaders = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization"
  };

  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  // Handle document lookup: /api/docs/<doc_id> or /api/docs/<doc_id>.pdf
  if (rawPath.startsWith("docs/")) {
    const rawId = rawPath.replace("docs/", "").replace(/\.pdf$/i, "").trim();
    const doc = KNOWLEDGE_BASE.find(d => d.doc_id === rawId || (d.doc_id && rawId.includes(d.doc_id)));

    if (doc) {
      return new Response(renderDocViewerHtml(doc), {
        status: 200,
        headers: { "Content-Type": "text/html; charset=utf-8", ...corsHeaders }
      });
    }

    // Fallback: if any document matches substring or title
    const cand = KNOWLEDGE_BASE.find(d => (d.title || "").toLowerCase().includes(rawId.toLowerCase()));
    if (cand) {
      return new Response(renderDocViewerHtml(cand), {
        status: 200,
        headers: { "Content-Type": "text/html; charset=utf-8", ...corsHeaders }
      });
    }

    return new Response(JSON.stringify({ error: `Document '${rawId}' not found in edge archive.` }), {
      status: 404,
      headers: { "Content-Type": "application/json", ...corsHeaders }
    });
  }

  // Forward to BACKEND_URL if configured
  if (env && env.BACKEND_URL) {
    const backendBase = env.BACKEND_URL.replace(/\/$/, "");
    const targetUrl = `${backendBase}/api/${rawPath}${new URL(request.url).search}`;
    try {
      const backendRes = await fetch(targetUrl, {
        method: request.method,
        headers: request.headers
      });
      const data = await backendRes.arrayBuffer();
      const headers = new Headers(backendRes.headers);
      headers.set("Access-Control-Allow-Origin", "*");
      return new Response(data, { status: backendRes.status, headers: headers });
    } catch (err) {
      console.warn("Backend proxy error:", err.message);
    }
  }

  return new Response(JSON.stringify({ error: `Endpoint '/api/${rawPath}' not found.` }), {
    status: 404,
    headers: { "Content-Type": "application/json", ...corsHeaders }
  });
}
