// ==============================================================================
// Cloudflare Pages Function: /ask
// Handles user regulatory questions at the edge or proxies to a live backend
// ==============================================================================

export async function onRequest(context) {
  const { request, env } = context;

  // Handle CORS Preflight
  if (request.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Admin-Password, X-Admin-Key"
      }
    });
  }

  if (request.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed. Please submit a POST request." }), {
      status: 405,
      headers: {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
      }
    });
  }

  // 1. If BACKEND_URL is configured in Cloudflare Pages (Environment Variables):
  // Seamlessly proxy query to your live Python Flask + ChromaDB backend
  if (env && env.BACKEND_URL) {
    const backendBase = env.BACKEND_URL.replace(/\/$/, "");
    try {
      const body = await request.text();
      const backendRes = await fetch(`${backendBase}/ask`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "User-Agent": "Cloudflare-Pages-Edge-Proxy"
        },
        body: body
      });
      const data = await backendRes.text();
      return new Response(data, {
        status: backendRes.status,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*"
        }
      });
    } catch (proxyErr) {
      return new Response(JSON.stringify({
        error: `Could not reach configured backend at ${backendBase}: ${proxyErr.message}`
      }), {
        status: 502,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*"
        }
      });
    }
  }

  // 2. If GEMINI_API_KEY is configured directly in Cloudflare Pages:
  const geminiKey = env && (env.GEMINI_API_KEY || env.GOOGLE_API_KEY);
  if (geminiKey) {
    try {
      const payload = await request.json();
      const question = (payload.question || "").trim();
      if (!question) {
        return new Response(JSON.stringify({ error: "Field 'question' is required." }), {
          status: 400,
          headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
        });
      }

      const geminiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${geminiKey}`;
      const systemPrompt = `You are the official CDC Regulatory Compliance AI Assistant for the Central Depository Company of Pakistan and SECP regulations.
Answer the user's regulatory query accurately and concisely. Cite relevant SECP circulars (e.g. SECP Circular No. 12 of 2025 regarding broker capital reserves and reconciliation rules), CDC CDS regulations, or AML/CFT guidelines whenever applicable.`;

      const geminiReqBody = {
        contents: [
          {
            role: "user",
            parts: [{ text: `${systemPrompt}\n\nUser Question: ${question}` }]
          }
        ],
        generationConfig: {
          temperature: 0.1,
          maxOutputTokens: 1024
        }
      };

      const geminiRes = await fetch(geminiUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(geminiReqBody)
      });

      if (!geminiRes.ok) {
        const errDetails = await geminiRes.text();
        return new Response(JSON.stringify({ error: `Gemini API response error: ${errDetails}` }), {
          status: 500,
          headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
        });
      }

      const geminiData = await geminiRes.json();
      const answer = geminiData.candidates?.[0]?.content?.parts?.[0]?.text || "No response generated.";

      return new Response(JSON.stringify({
        answer: answer,
        citations: [
          {
            title: "CDC & SECP Regulatory Framework",
            source_url: "https://cdcpakistan.com",
            doc_id: "cloudflare-edge"
          }
        ]
      }), {
        status: 200,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: `Edge processing error: ${err.message}` }), {
        status: 500,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }
  }

  // 3. Fallback when neither BACKEND_URL nor GEMINI_API_KEY is configured in Cloudflare Pages
  return new Response(JSON.stringify({
    answer: "Welcome to the CDC Regulatory Assistant on Cloudflare Pages!\n\nTo connect this online site to your live RAG backend or Gemini engine:\n\n1. **Connect Local Backend via Cloudflare Tunnel (Instant & Free)**: Run `cloudflared tunnel --url http://localhost:5000` in your terminal, then click **'Server Settings'** in the top-right header and paste your tunnel URL.\n2. **Cloudflare Pages Environment Variable**: In your Cloudflare Dashboard under **Pages > Settings > Environment Variables**, add `BACKEND_URL` (pointing to your server) or `GEMINI_API_KEY`.\n3. **Deploy Backend to Render / Cloud Run**: Deploy the included Dockerfile to Render and link it using `BACKEND_URL`.",
    citations: []
  }), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*"
    }
  });
}
