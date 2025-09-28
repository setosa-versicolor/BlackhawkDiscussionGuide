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

## Notes
- Timezone is America/Chicago.
- If the series markup changes, adjust `find_current_series_resources_url()` or date parsing in `find_today_discussion_pdf()`.
