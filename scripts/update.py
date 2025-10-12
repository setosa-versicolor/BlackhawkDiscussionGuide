# scripts/update.py
# Dual-mode script:
#  - --fetch <URL> : fetch, parse, and write data/guide.json (no Flask import)
#  - --serve       : run local Flask app for dev (imports Flask lazily)

import argparse, json, os, re, sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------- Parsing (no Flask dependencies) ----------
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

    # Sections
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
    sections = [s for s in sections if s]
    return {"questions": questions, "sections": sections}

def fetch_and_write(url: str, out_path: str = "data/guide.json"):
    headers = {"User-Agent": "Mozilla/5.0 (DiscussionGuide/1.0)"}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    data = parse_guide_html(r.text)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({"url": url, **data}, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} ({len(data['questions'])} questions, {len(data['sections'])} sections)")

# ---------- Optional local server (Flask import happens only here) ----------
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
        try:
            headers = {"User-Agent": "Mozilla/5.0 (DiscussionGuide/1.0)"}
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            data = parse_guide_html(resp.text)
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    app.run(host="0.0.0.0", port=5000, debug=True)

# ---------- CLI ----------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", metavar="URL", help="Fetch guide and write data/guide.json")
    ap.add_argument("--serve", action="store_true", help="Run local dev server with API")
    args = ap.parse_args()

    if args.fetch:
        fetch_and_write(args.fetch)
    elif args.serve:
        serve()
    else:
        ap.print_help()
        sys.exit(1)
