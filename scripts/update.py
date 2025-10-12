# update.py
# Flask server that renders the page and exposes an API to fetch & parse a guide page.
# Requires: pip install flask flask-cors beautifulsoup4 requests lxml (lxml optional but faster)

from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# ---- Helpers ----------------------------------------------------------------

WS_RE = re.compile(r"\s+")
BULLET_START_RE = re.compile(r"^\s*[–—-]\s+")  # en dash, em dash, hyphen

def norm(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\xa0", " ")
    s = WS_RE.sub(" ", s)
    return s.strip()

def node_text(n) -> str:
    return norm(n.get_text("\n", strip=True))

def find_heading_nodes(soup):
    return soup.select("h1, h2, h3, h4, h5, h6, strong, b")

def next_element(el):
    # step to next element sibling (skip over text nodes)
    n = el.next_sibling
    while n is not None and getattr(n, "name", None) is None:
        n = n.next_sibling
    return n

def collect_until_next_heading(start_el):
    parts = []
    n = next_element(start_el)
    while n is not None and not re.match(r"^H[1-6]$", getattr(n, "name", "X"), re.I):
        # capture visible text
        parts.append(node_text(n))
        n = next_element(n)
    return "\n".join(p for p in parts if p).strip()

def split_bullets(block_text: str):
    """
    Split a paragraph block that uses dash bullets into discrete items.
    Keeps multiline bullets together until the next dash that begins a line.
    """
    items = []
    buf = None
    for raw in block_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if BULLET_START_RE.match(line):
            if buf:
                items.append(buf.strip())
            buf = BULLET_START_RE.sub("", line)
        else:
            if buf:
                buf += " " + line
            else:
                # sometimes first line isn't prefixed; ignore
                pass
    if buf:
        items.append(buf.strip())
    return items

def filter_questions(candidates):
    # keep those ending with ? or looking like prompts
    keep = []
    for q in candidates:
        if q.endswith("?"):
            keep.append(q)
            continue
        if re.search(r"(read\s+colossians|based on paul|what.*risk|what.*evidence|how.*change)", q, re.I):
            keep.append(q)
    return keep

def parse_sections(soup):
    def grab(regex):
        for h in find_heading_nodes(soup):
            if re.search(regex, h.get_text(" ", strip=True), re.I):
                body = collect_until_next_heading(h)
                title = norm(h.get_text(" ", strip=True))
                return {"title": title, "body": norm(body)}
        return None

    sections = []
    m = grab(r"\bmemorization\s*challenge\b")
    p = grab(r"^\s*pray\s*$")
    n = grab(r"^\s*next\s*steps\s*$")

    for s in (m, p, n):
        if s and s["body"]:
            sections.append(s)
    return sections

def parse_guide_html(html: str):
    soup = BeautifulSoup(html, "lxml")
    # Locate "Reflect + Discuss"
    reflect_el = None
    for h in find_heading_nodes(soup):
        if re.search(r"reflect\s*\+\s*discuss", h.get_text(" ", strip=True), re.I):
            reflect_el = h
            break

    if reflect_el is None:
        # fallback to body text
        block = soup.get_text("\n", strip=True)
    else:
        block = collect_until_next_heading(reflect_el)

    # Split bullets
    candidates = split_bullets(block)
    questions = filter_questions(candidates)

    sections = parse_sections(soup)

    return {"questions": questions, "sections": sections}

# ---- Routes ------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("page.html")

@app.route("/api/guide")
def api_guide():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url"}), 400
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; DiscussionGuideBot/1.0)"
        }
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        data = parse_guide_html(resp.text)
        return jsonify(data)
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch: {e}"}), 502
    except Exception as e:
        return jsonify({"error": f"Parse error: {e}"}), 500

@app.route("/healthz")
def health():
    return "ok"

# Optional: serve your static JS if needed (e.g., /static/js/viewStore.js)
# Place your viewStore.js at ./static/js/viewStore.js

if __name__ == "__main__":
    # Run: python update.py
    app.run(host="0.0.0.0", port=5000, debug=True)
