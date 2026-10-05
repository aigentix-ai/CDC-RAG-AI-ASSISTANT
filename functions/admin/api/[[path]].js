// ==============================================================================
// Cloudflare Pages Function: /admin/api/*
// Proxies admin requests to BACKEND_URL or returns helpful status
// ==============================================================================

export async function onRequest(context) {
  const { request, env, params } = context;
  const path = Array.isArray(params.path) ? params.path.join("/") : (params.path || "");

  // Handle CORS Preflight
  if (request.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Admin-Password, X-Admin-Key"
      }
    });
  }

  // Allow auth-status check even if backend is offline so dashboard loads gracefully
  if (path === "auth-status") {
    if (env && env.BACKEND_URL) {
      // Forward to backend
    } else {
      return new Response(JSON.stringify({ authenticated: true, cloudflare_edge: true }), {
        status: 200,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }
  }

  // Forward to BACKEND_URL if configured
  if (env && env.BACKEND_URL) {
    const backendBase = env.BACKEND_URL.replace(/\/$/, "");
    const targetUrl = `${backendBase}/admin/api/${path}${new URL(request.url).search}`;
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
      return new Response(JSON.stringify({
        error: `Could not connect to backend at ${backendBase}: ${err.message}`
      }), {
        status: 502,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
    }
  }

  return new Response(JSON.stringify({
    error: "Admin backend is not connected yet. Please configure BACKEND_URL in Cloudflare Pages or connect via Cloudflare Tunnel."
  }), {
    status: 503,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
  });
}
