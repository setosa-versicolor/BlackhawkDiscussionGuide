/**
 * bible-proxy — Cloudflare Worker
 *
 * Fronts API.Bible (https://rest.api.bible) so the API key never ships to the
 * browser. The Discussion Guide page is a static GitHub Pages site in a public
 * repo, so anything it holds is world-readable; the key lives here instead, as
 * a Worker secret.
 *
 * Request:   GET /?translation=niv&passage=JHN.3.16-JHN.3.17
 * Response:  { passages: ["..."], reference, copyright, translation }
 *
 * The `passages` array mirrors the shape of the existing esv-bible-proxy worker
 * so the page can treat every translation the same way.
 *
 * Resolved passages are cached in Workers KV, so a given translation+passage
 * costs one API.Bible call once and is then served from KV for every reader,
 * in every room, indefinitely. Scripture does not change, so entries never
 * expire. This deliberately does NOT use the Cache API: that requires a custom
 * domain and is a no-op on *.workers.dev, where it silently cached nothing.
 */

const API_BASE = "https://rest.api.bible/v1/bibles";

// Allowlisted translations. Keeping this server-side means the worker can only
// ever be used to read these four Bibles, not as a general API.Bible proxy.
const TRANSLATIONS = {
  niv: { id: "78a9f6124f344018-01", label: "NIV" },
  nlt: { id: "d6e14a625393b4da-01", label: "NLT" },
  kjv: { id: "de4e12af7f28f599-01", label: "KJV" },
};

// e.g. JHN.3.16 or JHN.3.16-JHN.3.17 — anchored so a crafted `passage` can't
// escape the path segment and address some other endpoint.
const PASSAGE_RE = /^[1-5A-Z]{3}\.\d{1,3}\.\d{1,3}(-[1-5A-Z]{3}\.\d{1,3}\.\d{1,3})?$/;

// Origins allowed to call this worker. Add your own if you fork the site.
const ALLOWED_ORIGINS = [
  "https://setosa-versicolor.github.io",
  "http://localhost:8080",
  "http://127.0.0.1:8080",
];

function corsHeaders(origin) {
  const allowed = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

function json(body, status, origin, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...corsHeaders(origin),
      ...extra,
    },
  });
}

/**
 * API.Bible returns text with indentation, hard newlines and (in the KJV)
 * pilcrows marking paragraph starts. Flatten it to a single clean run of prose,
 * which is what the verse cards render.
 */
function cleanPassageText(raw) {
  return String(raw || "")
    .replace(/¶/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get("Origin") || "";

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }
    if (request.method !== "GET") {
      return json({ error: "Method not allowed" }, 405, origin);
    }

    const url = new URL(request.url);
    const translation = (url.searchParams.get("translation") || "niv").toLowerCase();
    const passage = (url.searchParams.get("passage") || "").toUpperCase();

    const bible = TRANSLATIONS[translation];
    if (!bible) {
      return json(
        { error: `Unknown translation '${translation}'`, supported: Object.keys(TRANSLATIONS) },
        400,
        origin
      );
    }
    if (!PASSAGE_RE.test(passage)) {
      return json({ error: "Invalid or missing 'passage' parameter" }, 400, origin);
    }

    if (!env.API_BIBLE_KEY) {
      return json({ error: "Worker is missing the API_BIBLE_KEY secret" }, 500, origin);
    }

    // Bump the prefix if the stored shape ever changes, to retire old entries.
    const kvKey = `v1:${translation}:${passage}`;
    if (env.VERSES) {
      try {
        const hit = await env.VERSES.get(kvKey, { type: "json" });
        if (hit) return json(hit, 200, origin, { "X-Cache": "HIT" });
      } catch (err) {
        // A KV read failure should slow us down, never break the page.
        console.error("KV read failed", err);
      }
    }

    const params = new URLSearchParams({
      "content-type": "text",
      "include-notes": "false",
      "include-titles": "false",
      "include-chapter-numbers": "false",
      "include-verse-numbers": "false",
      "include-verse-spans": "false",
    });

    let upstream;
    try {
      upstream = await fetch(`${API_BASE}/${bible.id}/passages/${passage}?${params}`, {
        headers: { "api-key": env.API_BIBLE_KEY, Accept: "application/json" },
      });
    } catch (err) {
      return json({ error: "Upstream request failed" }, 502, origin);
    }

    if (!upstream.ok) {
      // 404 here usually means the reference doesn't exist in that Bible
      // (e.g. an NT-only edition), which the page shows as a per-verse error.
      const status = upstream.status === 404 ? 404 : 502;
      return json(
        { error: `API.Bible returned ${upstream.status}`, translation: bible.label },
        status,
        origin
      );
    }

    const data = await upstream.json();
    const text = cleanPassageText(data?.data?.content);
    if (!text) {
      return json({ error: "Passage not found", translation: bible.label }, 404, origin);
    }

    const body = {
      passages: [text],
      reference: data?.data?.reference || passage,
      copyright: (data?.data?.copyright || "").trim(),
      translation: bible.label,
    };

    // Written without waitUntil: the next reader may arrive within
    // milliseconds, and a fire-and-forget put can lose that race and spend a
    // second API call. Paying ~10ms here makes the cache reliable.
    if (env.VERSES) {
      try {
        await env.VERSES.put(kvKey, JSON.stringify(body));
      } catch (err) {
        console.error("KV write failed", err);
      }
    }

    return json(body, 200, origin, { "X-Cache": "MISS" });
  },
};
