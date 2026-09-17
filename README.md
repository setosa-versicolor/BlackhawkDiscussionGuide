# Blackhawk Discussion Guide Agent

This repo scrapes the current message series at Blackhawk Church → finds **today's** discussion guide PDF → extracts text → publishes a mobile-friendly page on GitHub Pages.

## Quick start

1. **Create a new GitHub repo** (public or private).
2. Download this starter, unzip it, and copy contents into the repo.
3. Commit & push.
4. In **Settings → Pages**, set **Build and deployment** to **GitHub Actions**.
5. In **Actions**, run the **Build & Deploy Discussion Guide** workflow once.
6. Your site will be live at `https://<your-username>.github.io/<repo>/`.

### Local test (optional)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/update.py
python -m http.server -d site 8080
# open http://localhost:8080
```

## Bible translations

Verse references detected in the questions are looked up live. Four
translations are available from the ⋮ menu; the choice is remembered per
device and defaults to **NIV**.

| Translation | Source | Proxy |
|---|---|---|
| NIV (default) | API.Bible | `workers/bible-proxy` |
| ESV | api.esv.org | existing `esv-bible-proxy` |
| NLT | API.Bible | `workers/bible-proxy` |
| KJV | API.Bible | `workers/bible-proxy` |

Both proxies exist so the API keys stay out of this public repo. NIV, NLT and
KJV need `workers/bible-proxy` deployed and its URL set as `BIBLE_PROXY_URL` in
`templates/page.html` — see
[workers/bible-proxy/README.md](workers/bible-proxy/README.md). Until that is
set the page falls back to ESV, which is unaffected.

### Keeping API usage down

API.Bible allows 5,000 calls a month. Two things keep usage far below that:

1. **Build-time prefetch.** `scripts/verses.py` resolves every reference in the
   week's guide, in all four translations, and bakes the text into
   `data/guide.json`. Readers then cost **zero** API calls — the verses ship
   with the page. A typical guide has ~4 references, so this is ~16 calls a
   week no matter how many people read it.
2. **Worker KV cache.** Anything not prefetched — a reference typed into a
   custom card — goes through `workers/bible-proxy`, which stores every
   resolved passage in Workers KV. A given translation+passage costs one call
   once, ever, then serves from KV for every reader and every room.

Prefetching also warms the KV cache as a side effect, since it fetches through
the same worker.

`scripts/check_verses_sync.py` asserts the Python detector and the page's
JavaScript detector produce identical reference strings. If they drift, the
prefetch keys stop matching and the page silently falls back to live calls —
so run it after touching either.

## Notes
- Timezone is America/Chicago.
- If the series markup changes, adjust `find_current_series_resources_url()` or date parsing in `find_today_discussion_pdf()`.
