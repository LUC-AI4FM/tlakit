/**
 * tla-runner — the public edge for tlakit's spec-checking service.
 *
 * The origin can already only do one thing (model check a spec, with no I/O
 * primitives available to that spec). This Worker exists for the problems the
 * origin cannot solve on its own: it is a single 8 GB Mac Mini that also hosts
 * other services, so unmetered public traffic is the real risk, not escape.
 *
 * Responsibilities, in order of importance:
 *   1. rate limit per client address
 *   2. refuse oversized bodies before they reach a JVM
 *   3. authenticate to the origin with a shared key, so the tunnel hostname
 *      alone is not enough to reach it
 *   4. CORS, so a browser page can call it
 */

const MAX_BODY_BYTES = 64 * 1024; // must not exceed the origin's own cap
const ALLOWED_PATHS = new Set(["/check", "/parse", "/health"]);
const POST_PATHS = new Set(["/check", "/parse"]);
const UPSTREAM_TIMEOUT_MS = 40_000; // origin caps a check at 30s

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "POST, GET, OPTIONS",
  "access-control-allow-headers": "content-type",
  "access-control-max-age": "86400",
};

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...CORS },
  });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    if (!ALLOWED_PATHS.has(url.pathname)) {
      return json({ error: "not found" }, 404);
    }
    if (POST_PATHS.has(url.pathname) && request.method !== "POST") {
      return json({ error: "use POST" }, 405);
    }
    if (url.pathname === "/health" && request.method !== "GET") {
      return json({ error: "use GET" }, 405);
    }

    // Rate limit by client address. Keyed per path so /health cannot exhaust
    // the budget that /check needs.
    //
    // /parse draws on a separate, much larger binding rather than sharing
    // /check's. Keying by path already gives each its own counter, but not its
    // own *limit* -- and a parse costs a SANY invocation with no state space,
    // so pricing it like a check would throttle the fast feedback it exists to
    // provide. A module cell parses on every edit.
    const who = request.headers.get("cf-connecting-ip") ?? "unknown";
    const limiter =
      url.pathname === "/parse" ? env.PARSE_LIMITER : env.CHECK_LIMITER;
    const { success } = await limiter.limit({
      key: `${url.pathname}:${who}`,
    });
    if (!success) {
      return json(
        { error: "rate limit exceeded", retry_after_seconds: 60 },
        429,
      );
    }

    let body = null;
    if (request.method === "POST") {
      // Content-Length can lie or be absent; measure what actually arrived.
      const raw = await request.text();
      if (raw.length > MAX_BODY_BYTES) {
        return json(
          { error: `body exceeds ${MAX_BODY_BYTES} bytes` },
          413,
        );
      }
      try {
        JSON.parse(raw); // fail here rather than at the origin
      } catch {
        return json({ error: "body must be JSON" }, 400);
      }
      body = raw;
    }

    const upstream = new URL(url.pathname, env.ORIGIN);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
    try {
      const response = await fetch(upstream, {
        method: request.method,
        headers: {
          "content-type": "application/json",
          // Proves the request came through this Worker. Without it the origin
          // refuses, so discovering the tunnel hostname is not enough.
          "x-tlakit-key": env.ORIGIN_KEY,
        },
        body,
        signal: controller.signal,
      });
      // Pass the origin's JSON through, but never its headers -- they can carry
      // server versions and other detail the public does not need.
      const text = await response.text();
      return new Response(text, {
        status: response.status,
        headers: { "content-type": "application/json", ...CORS },
      });
    } catch (error) {
      const timedOut = error.name === "AbortError";
      return json(
        { error: timedOut ? "origin timed out" : "origin unavailable" },
        timedOut ? 504 : 502,
      );
    } finally {
      clearTimeout(timer);
    }
  },
};
