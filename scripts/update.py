# scripts/update.py
# Dual mode: (a) --auto crawl roots & pick the correct weekly guide
#            (b) --fetch URL explicit
# Also supports --serve for local dev with Flask API (unchanged UX).

import argparse, json, re, sys, datetime as dt
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ------------------------- Text helpers -------------------------
_WS = re.compile(r"\s+")
_BULLET = re.compile(r"^\s*[–—-]\s+")
def _norm(s: str) -> str:
    if not s: return ""
    s = s.replace("\xa0", " ")
    s = _WS.sub(" ", s)
    return s.strip()
def _node_text(n) -> str:
    return _norm(n.get_text("\n", strip=True))
def _heading_nodes(soup):
    return soup.select("h1,h2,h3,h4,h5,h6,strong,b")
def _next_el(el):
    n = el.next_sibling
    while n is not None and getattr(n, "name", None) is None:
        n = n.next_sibling
    return n
def _collect_until_heading(start_el):
    parts = []
    n = _next_el(start_el)
    while n is not None and not re.match(r"^H[1-6]$", getattr(n, "name", "X"), re.I):
        parts.append(_node_text(n))
        n = _next_el(n)
    return "\n".join(p for p in parts if p).strip()
def _split_bullets(block_text: str):
    items, buf = [], None
    for raw in block_text.splitlines():
        line = raw.strip()
        if not line: continue
        if _BULLET.match(line):
            if buf: items.append(buf.strip())
            buf = _BULLET.sub("", line)
        else:
            if buf: buf += " " + line
    if buf: items.append(buf.strip())
    return items
def _filter_questions(cands):
    keep = []
    for q in cands:
        if q.endswith("?") or re.search(r"(read\s+colossians|based on paul|what.*risk|what.*evidence|how.*change)", q, re.I):
            keep.append(q)
    return keep

# ------------------------- Date scoring -------------------------
# Parse dates from text/url (MM/DD/YY, Month D, YYYY, /YYYY/MM/DD/).
_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b"),
    re.compile(r"\b([A-Za-z]+)\s+(\d{1,2})(?:,?\s*(\d{4}))?\b"),
    re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})/"),  # in URL
]
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January","February","March","April","May","June","July","August","September","October","November","December"], 1)}

def _try_parse_date(text: str):
    text = text or ""
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if not m: continue
        if pat.pattern.startswith(r"\b(\d{1,2})"):
            mm, dd, yy = map(int, m.groups())
            if yy < 100: yy += 2000
            return dt.date(yy, mm, dd)
        if pat.pattern.startswith(r"\b([A-Za-z]+)"):
            mon, dd, yy = m.groups()
            mm = _MONTHS.get(mon.lower())
            if not mm: continue
            dd = int(dd)
            yy = int(yy) if yy else dt.date.today().year
            return dt.date(yy, mm, dd)
        if pat.pattern.startswith(r"/(\d{4})/"):
            yy, mm, dd = map(int, m.groups())
            return dt.date(yy, mm, dd)
    return None

def _sunday_of_this_week(today=None):
    today = today or dt.date.today()
    # assuming Sunday-start week
    return today - dt.timedelta(days=today.weekday()+1 if today.weekday()!=6 else 0)

def _score_candidate(date_found: dt.date, target: dt.date):
    if not date_found: return 9999
    return abs((date_found - target).days)

# ------------------------- Parsing -------------------------
def parse_sections(soup):
    def grab(regex):
        for h in _heading_nodes(soup):
            if re.search(regex, h.get_text(" ", strip=True), re.I):
                body = _collect_until_heading(h)
                title = _norm(h.get_text(" ", strip=True))
                if body.strip():
                    return {"title": title, "body": _norm(body)}
        return None
    sections = [grab(r"\bmemorization\s*challenge\b"),
                grab(r"^\s*pray\s*$"),
                grab(r"^\s*next\s*steps\s*$")]
    return [s for s in sections if s]

def parse_guide_html(html: str):
    soup = BeautifulSoup(html, "lxml")
    # Reflect + Discuss
    reflect = None
    for h in _heading_nodes(soup):
        if re.search(r"reflect\s*\+\s*discuss", h.get_text(" ", strip=True), re.I):
            reflect = h; break
    block = _collect_until_heading(reflect) if reflect else soup.get_text("\n", strip=True)
    candidates = _split_bullets(block)
    questions = _filter_questions(candidates)
    sections = parse_sections(soup)
    return {"questions": questions, "sections": sections}

# Optional: if page has only a PDF
def parse_guide_pdf(pdf_url: str):
    try:
        from pdfminer.high_level import extract_text
        txt = extract_text(pdf_url)  # pdfminer can read from URL
        # similar split: look for "Reflect + Discuss" line first
        m = re.search(r"Reflect\s*\+\s*Discuss(.+)", txt, re.I | re.S)
        block = m.group(1) if m else txt
        # split on lines starting with dash-like bullets
        lines = [ln.strip() for ln in block.splitlines()]
        buf, items = None, []
        for ln in lines:
            if not ln: continue
            if _BULLET.match(ln):
                if buf: items.append(buf.strip())
                buf = _BULLET.sub("", ln)
            else:
                if buf: buf += " " + ln
        if buf: items.append(buf.strip())
        questions = _filter_questions(items)
        # naive sections
        def grab(sec):
            m = re.search(rf"{sec}\s*:?(.+?)(?=\n[A-Z][A-Za-z ]{2,}:\s*|$)", txt, re.I | re.S)
            if not m: return None
            return {"title": sec, "body": _norm(m.group(1))}
        sections = list(filter(None, [grab("Memorization Challenge"), grab("Pray"), grab("Next Steps")]))
        return {"questions": questions, "sections": sections}
    except Exception:
        return {"questions": [], "sections": []}

# ------------------------- Discovery -------------------------
def _same_host(root, href):
    try:
        return urlparse(root).netloc == urlparse(href).netloc
    except Exception:
        return False

def discover_latest(roots, keywords):
    """
    Crawl the given root pages for links likely to be the current week's guide.
    Strategy:
      - collect all <a> links containing any keyword
      - score each by date parsed from link text or URL vs this week's Sunday
      - fetch the best-scoring page; if 0 questions found, follow its first PDF link
    """
    headers = {"User-Agent": "Mozilla/5.0 (DiscussionGuide/1.0)"}
    target = _sunday_of_this_week()
    best = None  # (score, url, html)

    for root in roots:
        try:
            r = requests.get(root, headers=headers, timeout=20)
            r.raise_for_status()
        except Exception:
            continue

        soup = BeautifulSoup(r.text, "lxml")
        anchors = soup.select("a[href]")
        for a in anchors:
            text = _norm(a.get_text(" ", strip=True))
            href = a["href"]
            abs_url = urljoin(root, href)
            if not _same_host(root, abs_url):
                continue
            blob = (text + " " + abs_url).lower()
            if not any(k in blob for k in keywords):
                continue
            date_found = _try_parse_date(text) or _try_parse_date(abs_url)
            score = _score_candidate(date_found, target)
            # keep a few good candidates
            if best is None or score < best[0]:
                best = (score, abs_url, None)

    if not best:
        raise RuntimeError("No candidate guide links found. Adjust ROOTS/KEYWORDS.")

    # Fetch the best candidate page
    score, url, _ = best
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    html = r.text
    data = parse_guide_html(html)

    # If HTML had no questions, try PDF on that page
    if len(data["questions"]) == 0:
        soup = BeautifulSoup(html, "lxml")
        pdf_link = None
        for a in soup.select("a[href]"):
            h = a["href"]
            if h.lower().endswith(".pdf"):
                pdf_link = urljoin(url, h); break
        if pdf_link:
            data = parse_guide_pdf(pdf_link)

    return url, data

# ------------------------- IO -------------------------
def write_json(url: str, data: dict, out_path: str = "data/guide.json"):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({"url": url, **data}, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} ({len(data['questions'])} questions, {len(data['sections'])} sections)")

# ------------------------- Flask (optional local) -------------------------
def serve():
    from flask import Flask, request, jsonify, render_template
    from flask_cors import CORS
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    CORS(app)

    @app.route("/")
    def index():
        return render_template("page.html")

    @app.route("/api/guide")
    def api_guide():
        url = (request.args.get("url") or "").strip()
        if not url:
            return jsonify({"error": "Missing url"}), 400
        headers = {"User-Agent": "Mozilla/5.0 (DiscussionGuide/1.0)"}
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            return jsonify(parse_guide_html(resp.text))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    app.run(host="0.0.0.0", port=5000, debug=True)

# ------------------------- CLI -------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", metavar="URL", help="Fetch a specific guide URL and write data/guide.json")
    ap.add_argument("--auto", action="store_true", help="Auto-discover latest guide from roots")
    ap.add_argument("--roots", help="Comma-separated root pages to crawl")
    ap.add_argument("--keywords", help="Comma-separated keywords used to filter links")
    ap.add_argument("--serve", action="store_true", help="Run local Flask dev server")
    args = ap.parse_args()

    if args.fetch:
        headers = {"User-Agent": "Mozilla/5.0 (DiscussionGuide/1.0)"}
        r = requests.get(args.fetch, headers=headers, timeout=20)
        r.raise_for_status()
        data = parse_guide_html(r.text)
        if len(data["questions"]) == 0:
            # optional: try PDF on that page
            soup = BeautifulSoup(r.text, "lxml")
            for a in soup.select("a[href]"):
                if a["href"].lower().endswith(".pdf"):
                    pdf_url = urljoin(args.fetch, a["href"])
                    pdf_data = parse_guide_pdf(pdf_url)
                    if pdf_data["questions"]:
                        data = pdf_data
                        break
        write_json(args.fetch, data)

    elif args.auto:
        roots = [u.strip() for u in (args.roots or "").split(",") if u.strip()]
        if not roots:
            print("ERROR: --auto requires --roots with one or more start pages.", file=sys.stderr)
            sys.exit(2)
        keywords = [k.strip().lower() for k in (args.keywords or "discussion guide,greater things,reflect + discuss,questions").split(",") if k.strip()]
        url, data = discover_latest(roots, keywords)
        write_json(url, data)

    elif args.serve:
        serve()

    else:
        ap.print_help()
        sys.exit(1)
