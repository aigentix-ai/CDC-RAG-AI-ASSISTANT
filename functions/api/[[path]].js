// ==============================================================================
// Cloudflare Pages Function: /api/*
// Proxies document and general API requests to BACKEND_URL
// ==============================================================================

export async function onRequest(context) {
  const { request, env, params } = context;
  const path = Array.isArray(params.path) ? params.path.join("/") : (params.path || "");

  if (request.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization"
      }
    });
  }

  if (env && env.BACKEND_URL) {
    const backendBase = env.BACKEND_URL.replace(/\/$/, "");
    const targetUrl = `${backendBase}/api/${path}${new URL(request.url).search}`;
    try {
      const init = {
        method: request.method,
        headers: request.headers
      };
      if (request.method !== "GET" && request.method !== "HEAD") {
        init.body = await request.arrayBuffer();
      }
      const backendRes = await fetch(targetUrl, init);
      const resBody = await backendRes.arrayBuffer();
      const headers = new Headers(backendRes.headers);
      headers.set("Access-Control-Allow-Origin", "*");
      return new Response(resBody, {
        status: backendRes.status,
        headers: headers
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: `Backend proxy error: ${err.message}` }), {
        status: 502,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }
  }

  return new Response(JSON.stringify({
    error: "API endpoint requires a running backend. Configure BACKEND_URL in Cloudflare Pages."
  }), {
    status: 503,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
  });
}
