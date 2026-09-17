# bible-proxy

Cloudflare Worker that fronts [API.Bible](https://scripture.api.bible) for the
Discussion Guide page, so the API key never ships to the browser.

The site is a static GitHub Pages build in a **public** repo — anything the page
holds is world-readable. The key lives here as an encrypted Worker secret
instead. This mirrors how `esv-bible-proxy` already handles the ESV key.

## What it serves

| Translation | Source | Proxy |
|---|---|---|
| NIV (default) | API.Bible | this worker |
| NLT | API.Bible | this worker |
| KJV | API.Bible | this worker |
| ESV | api.esv.org | existing `esv-bible-proxy` worker |

## Deploy

```bash
cd workers/bible-proxy
npx wrangler login
npx wrangler secret put API_BIBLE_KEY   # paste the API.Bible key when prompted
npx wrangler deploy
```

Wrangler prints the deployed URL, e.g.
`https://bible-proxy.<your-subdomain>.workers.dev`.

Put that URL into `templates/page.html`:

```js
const BIBLE_PROXY_URL = 'https://bible-proxy.<your-subdomain>.workers.dev';
```

Then rebuild/redeploy the site. Until that constant is filled in, NIV/NLT/KJV
show a "not configured" message and ESV keeps working as before.

## API

```
GET /?translation=niv&passage=JHN.3.16-JHN.3.17
```

- `translation` — one of `niv`, `nlt`, `kjv` (allowlisted in the worker)
- `passage` — API.Bible passage id, `BOK.chapter.verse[-BOK.chapter.verse]`

Response:

```json
{
  "passages": ["For God so loved the world..."],
  "reference": "John 3:16-17",
  "copyright": "The Holy Bible, New International Version®...",
  "translation": "NIV"
}
```

`passages` is an array to match the existing ESV worker's response shape, so the
page handles all four translations through one code path.

## Notes

- **Allowlists.** Only the three Bible ids above are reachable, and `passage` is
  regex-validated, so the worker can't be repurposed as an open API.Bible proxy.
- **KV cache.** Every resolved passage is stored in the `VERSES` KV namespace,
  so a given translation+passage costs one API.Bible call once, ever, and is
  then served from KV for every reader in every room. Scripture does not
  change, so entries never expire.
- **Not the Cache API.** An earlier version used `caches.default`. That needs a
  custom domain and is a no-op on `*.workers.dev`, so it silently cached
  nothing. KV is a binding and works regardless of domain.
- **KV is eventually consistent** (~60s to propagate globally), so a brand-new
  passage may be fetched a couple of extra times in its first minute. The
  build-time prefetch warms the cache days before anyone reads, so in practice
  readers always hit a warm key.
- **CORS.** `ALLOWED_ORIGINS` in `src/index.js` lists who may call it. Add your
  origin there if you fork the site.
- **Attribution.** API.Bible returns a `copyright` string per translation and
  the page displays it. NIV and NLT are licensed texts — keep that visible.
