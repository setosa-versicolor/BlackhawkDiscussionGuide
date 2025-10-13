# scripts/update.py
# Auto-discovers this week's discussion guide, parses HTML first (Reflect + Discuss),
# falls back to PDF, and writes a complete deployable site/ for GitHub Pages.
#
# Discovery flow:
#   /messages -> "Discussion Guide" near today's date
#   message page -> "Discussion Guide"
#   /learn -> current series -> resources list (date-picked)
#
# Outputs:
#   - site/index.html (from templates/page.html if present)
#   - site/data/guide.json (parsed content)
#   - site/static/* (mirrored from ./static if present)

import os
import re
import io
import sys
import shutil
import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import argparse
import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text

# --------- Constants ---------
LEARN_URL     = "https://blackhawk.church/learn/"
MESSAGES_URL  = "https://blackhawk.church/messages/"
TZ            = ZoneInfo("America/Chicago")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) "
        "Gecko/20100101 Firefox/124.0"
    )
}

# Month/Day patterns: "September 28", "Sept 28", "Sep. 28"
MONTH_NAME_PAT = re.compile(
    r"\b(?P<month>"
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+(?P<day>\d{1,2})\b",
    re.IGNORECASE
)
# Numeric patterns: "9/28", "09/28", "9-28"
NUMERIC_DATE_PAT = re.compile(r"\b(?P<m>\d{1,2})[/-](?P<d>\d{1,2})\b")

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# --------- HTTP / Parsing helpers ---------
def get_soup(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")

def _extract_dates_from_text(text: str, year_hint: int) -> list[datetime.date]:
    dates = []
    for m in MONTH_NAME_PAT.finditer(text):
        mon_raw = m.group("month").lower().rstrip(".")
        day = int(m.group("day"))
        mon = MONTHS.get(mon_raw)
        if mon and 1 <= day <= 31:
            try:
                dates.append(datetime.date(year_hint, mon, day))
            except ValueError:
                pass
    for m in NUMERIC_DATE_PAT.finditer(text):
        mon = int(m.group("m")); day = int(m.group("d"))
        if 1 <= mon <= 12 and 1 <= day <= 31:
            try:
                dates.append(datetime.date(year_hint, mon, day))
            except ValueError:
                pass
    return dates

def _collect_nearby_text(a_tag, max_ancestors=3) -> str:
    texts = []
    texts.append(a_tag.get_text(" ", strip=True))
    if a_tag.parent:
        texts.append(a_tag.parent.get_text(" ", strip=True))

    prev = a_tag.previous_sibling
    steps = 0
    while prev and steps < 3:
        if hasattr(prev, "get_text"):
            t = prev.get_text(" ", strip=True)
            if t:
                texts.append(t)
        prev = prev.previous_sibling
        steps += 1

    anc = a_tag.parent
    depth = 0
    while anc and depth < max_ancestors:
        texts.append(anc.get_text(" ", strip=True))
        anc = anc.parent
        depth += 1

    uniq, seen = [], set()
    for t in texts:
        if t and t not in seen:
            uniq.append(t); seen.add(t)
    return "  •  ".join(uniq)

# --------- Discovery flows ---------
def _first_discussion_link_on_messages_page(today: datetime.date) -> str | None:
    soup = get_soup(MESSAGES_URL)
    month_name = today.strftime("%B")
    day = str(today.day)
    year = str(today.year)

    for a in soup.find_all("a"):
        if "discussion guide" in a.get_text(strip=True).lower():
            ctx = _collect_nearby_text(a)
            has_full = (month_name in ctx and day in ctx and year in ctx)
            has_md   = (month_name in ctx and day in ctx)
            if has_full or has_md:
                href = a.get("href")
                if href:
                    return requests.compat.urljoin(MESSAGES_URL, href)
    return None

def _discussion_link_from_message_page(url: str) -> str | None:
    soup = get_soup(url)
    for a in soup.find_all("a"):
        if "discussion guide" in a.get_text(strip=True).lower():
            href = a.get("href")
            if href:
                return requests.compat.urljoin(url, href)
    return None

def _find_message_page_for_today(today: datetime.date) -> str | None:
    soup = get_soup(MESSAGES_URL)
    month_name = today.strftime("%B")
    day = str(today.day)
    mmdd = f"{today.month}/{today.day}"

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/message/" in href:
            ctx = a.parent.get_text(" ", strip=True) if a.parent else a.get_text(" ", strip=True)
            if (month_name in ctx and day in ctx) or (mmdd in ctx):
                return requests.compat.urljoin(MESSAGES_URL, href)
    return None

def find_current_series_resources_url() -> str:
    soup = get_soup(LEARN_URL)
    past_series_header = soup.find(
        lambda tag: tag.name in ("h4","h5","h2","h3") and "Past Series" in tag.get_text()
    )
    candidates = []
    for a in soup.find_all("a"):
        if a.get_text(strip=True) == "Resources":
            if past_series_header and hasattr(a, "sourceline") and hasattr(past_series_header, "sourceline"):
                if (a.sourceline or 0) < (past_series_header.sourceline or 10**9):
                    candidates.append(a)
            else:
                candidates.append(a)
    if not candidates:
        raise RuntimeError("Could not find current series 'Resources' link on Learn page.")
    return requests.compat.urljoin(LEARN_URL, candidates[0].get("href"))

def find_today_discussion_pdf_or_page(series_url: str, today: datetime.date) -> dict:
    """
    Returns: { series_title, date, url, context, all_guides }
    'url' may be a PDF or HTML page. We'll parse accordingly.
    """
    # 1) Messages landing, direct Discussion Guide
    direct = _first_discussion_link_on_messages_page(today)
    if direct:
        return {
            "series_title": "Current Series",
            "date": today,
            "url": direct,
            "context": "Messages landing (direct)",
            "all_guides": []
        }

    # 2) message page -> discussion guide
    msg_page = _find_message_page_for_today(today)
    if msg_page:
        dg = _discussion_link_from_message_page(msg_page)
        if dg:
            return {
                "series_title": "Current Series",
                "date": today,
                "url": dg,
                "context": msg_page,
                "all_guides": []
            }

    # 3) Fallback: series resources list (date-scored)
    soup_r = get_soup(series_url)
    series_title_tag = soup_r.find(lambda t: t.name in ("h1","h2") and t.get_text(strip=True))
    series_title = series_title_tag.get_text(strip=True) if series_title_tag else "Current Series"

    guides = []
    for a in soup_r.find_all("a"):
        if "discussion guide" in a.get_text(strip=True).lower():
            ctx = _collect_nearby_text(a)
            ds = _extract_dates_from_text(ctx, year_hint=today.year)
            if not ds:
                continue
            ds_unique = sorted(set(ds))
            candidates = [d for d in ds_unique if d <= today]
            dt_choice = candidates[-1] if candidates else ds_unique[-1]
            href = a.get("href")
            if href:
                guides.append((dt_choice, requests.compat.urljoin(series_url, href), ctx))

    if guides:
        guides.sort(key=lambda x: x[0])
        exact = [g for g in guides if g[0] == today]
        chosen = exact[0] if exact else max([g for g in guides if g[0] <= today], key=lambda x: x[0], default=guides[-1])
        return {
            "series_title": series_title,
            "date": chosen[0],
            "url": chosen[1],
            "context": chosen[2],
            "all_guides": guides
        }

    raise RuntimeError("Could not locate a Discussion Guide link for today's message.")

# --------- Content parsing (HTML first, then PDF fallback) ---------
_WS = re.compile(r"\s+")
_BULLET = re.compile(r"^\s*[–—-•]\s+")

def _norm(s: str) -> str:
    if not s: return ""
    s = s.replace("\xa0", " ")
    s = _WS.sub(" ", s)
    return s.strip()

def _headings(soup):
    return soup.select("h1,h2,h3,h4,h5,h6,strong,b")

def _is_heading_tag(el) -> bool:
    return bool(getattr(el, "name", "") and re.match(r"^h[1-6]$", el.name, re.I))

def _next_el(el):
    n = el.next_sibling
    while n is not None and getattr(n, "name", None) is None:
        n = n.next_sibling
    return n

def _collect_until_next_heading(start_el):
    parts = []
    n = _next_el(start_el)
    while n is not None and not _is_heading_tag(n):
        parts.append(n.get_text("\n", strip=True))
        n = _next_el(n)
    return "\n".join([_norm(p) for p in parts if _norm(p)]).strip()

def parse_html_guide(html: str):
    """
    Extract questions from 'Reflect + Discuss' section (dash bullets),
    and capture Memorization Challenge, Pray, Next Steps as sections.
    """
    soup = BeautifulSoup(html, "lxml")
    heads = _headings(soup)

    reflect = None
    for h in heads:
        if re.search(r"reflect\s*\+\s*discuss", h.get_text(" ", strip=True), re.I):
            reflect = h; break

    block = _collect_until_next_heading(reflect) if reflect else soup.get_text("\n", strip=True)

    # Split bullets (keep wrapped lines until next bullet)
    items, buf = [], None
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _BULLET.match(line):
            if buf: items.append(buf.strip())
            buf = _BULLET.sub("", line)
        else:
            if buf:
                buf += " " + line
    if buf: items.append(buf.strip())

    qs = [q for q in items if q.endswith("?") or re.search(r"(read\s+|what|how|why|where|when)", q, re.I)]

    def grab(title_re):
        h = next((x for x in heads if re.search(title_re, x.get_text(" ", strip=True), re.I)), None)
        if not h: return None
        title = _norm(h.get_text(" ", strip=True))
        body = _collect_until_next_heading(h)
        body = _norm(body)
        if not body: return None
        return {"title": title, "body": body}

    sections = list(filter(None, [
        grab(r"\bmemorization\s*challenge\b"),
        grab(r"^\s*pray\s*$"),
        grab(r"^\s*next\s*steps\s*$"),
    ]))

    return {"questions": qs, "sections": sections}

def fetch_pdf_text(pdf_url: str) -> str:
    r = requests.get(pdf_url, headers=BROWSER_HEADERS, timeout=30)
    r.raise_for_status()
    return extract_text(io.BytesIO(r.content))

def parse_pdf_guide(pdf_url: str):
    raw = fetch_pdf_text(pdf_url)
    BULLET_RE = re.compile(r'^[\-\u2013\u2014\u2022]\s+')
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    title = lines[0] if lines else "Discussion Guide"

    try:
        start_idx = next(i for i, ln in enumerate(lines) if re.search(r"reflect\s*\+\s*discuss", ln, re.I))
    except StopIteration:
        start_idx = 0

    bullets = []
    buf = None
    for ln in lines[start_idx+1:]:
        if BULLET_RE.match(ln):
            if buf: bullets.append(buf.strip())
            buf = BULLET_RE.sub("", ln)
        else:
            if buf:
                buf += " " + ln
            if re.match(r"^(memorization challenge|pray|next steps)\b", ln, re.I):
                break
    if buf: bullets.append(buf.strip())

    questions = [q for q in bullets if q.endswith("?") or re.search(r"(read\s+|what|how|why|where|when)", q, re.I)]

    def grab_sec(name):
        m = re.search(rf"{name}\s*:?\s*(.+?)(?=\n[A-Z][A-Za-z ]{{2,}}:?\s*|\Z)", raw, re.I | re.S)
        if not m: return None
        return {"title": name.title(), "body": _norm(m.group(1))}
    sections = list(filter(None, [grab_sec("Memorization Challenge"), grab_sec("Pray"), grab_sec("Next Steps")]))

    return {"title": title, "questions": questions, "sections": sections}

# --------- Outputs ---------
def write_json(source_url: str, data: dict, out_path: str = "site/data/guide.json"):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": source_url,
        "questions": data.get("questions", []),
        "sections": data.get("sections", []),
    }
    Path(out_path).write_text(
        __import__("json").dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"Wrote {out_path} ({len(payload['questions'])} questions, {len(payload['sections'])} sections)")

def maybe_write_site(series_title, date_obj, title_line, sections, source_pdf, out_dir="site"):
    # Render index.html via Jinja (if template exists)
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except Exception:
        print("Jinja2 not installed; skipping static site rendering.")
        return

    os.makedirs(out_dir, exist_ok=True)
    env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape())
    try:
        tpl = env.get_template("page.html")
    except Exception:
        print("templates/page.html not found; skipping static site rendering.")
        return

    day_fmt = "%-d" if os.name != "nt" else "%#d"
    date_str = date_obj.strftime(f"%A, %B {day_fmt}, %Y") if hasattr(date_obj, "strftime") else str(date_obj)

    html = tpl.render(
        series_title=series_title or "Current Series",
        title_line=title_line or "Discussion Guide",
        date_str=date_str,
        sections=sections or [],
        pdf_url=source_pdf,
        updated=datetime.datetime.now(tz=TZ).strftime("%Y-%m-%d %I:%M %p %Z"),
    )
    (Path(out_dir) / "index.html").write_text(html, encoding="utf-8")
    print(f"Wrote {out_dir}/index.html")

    # Mirror ./static → site/static (so ./static/js/viewStore.js resolves)
    src_static = Path("static")
    dst_static = Path(out_dir) / "static"
    if src_static.exists():
        # Python 3.8+: dirs_exist_ok available
        shutil.copytree(src_static, dst_static, dirs_exist_ok=True)

    # Ensure site/data/guide.json exists even if user customized --out-json
    site_data_dir = Path(out_dir) / "data"
    site_data_dir.mkdir(parents=True, exist_ok=True)
    # If there's a non-site JSON, copy it in; otherwise leave the one we wrote
    default_src = Path("data/guide.json")
    default_dst = site_data_dir / "guide.json"
    if default_src.exists() and not default_dst.exists():
        shutil.copyfile(default_src, default_dst)

# --------- Main ---------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--override", help="Optional: force a specific URL (HTML or PDF)")
    # Default to writing inside site/, so Pages artifact is self-contained
    ap.add_argument("--out-json", default="site/data/guide.json", help="Where to write the JSON")
    args = ap.parse_args()

    today = datetime.datetime.now(tz=TZ).date()
    series_url = find_current_series_resources_url()
    meta = {"series_title": "Current Series", "date": today, "url": None, "context": ""}

    if args.override:
        source_url = args.override
    else:
        meta = find_today_discussion_pdf_or_page(series_url, today)
        source_url = meta["url"]

    # Fetch & parse (HTML first; if no questions found, fall back to PDF on that page)
    r = requests.get(source_url, headers=BROWSER_HEADERS, timeout=30)
    r.raise_for_status()
    content_type = r.headers.get("content-type", "").lower()

    if source_url.lower().endswith(".pdf") or "application/pdf" in content_type:
        data = parse_pdf_guide(source_url)
    else:
        data = parse_html_guide(r.text)
        if not data["questions"]:
            soup = BeautifulSoup(r.text, "lxml")
            pdf = None
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if href.lower().endswith(".pdf"):
                    pdf = urljoin(source_url, href)
                    break
            if pdf:
                data = parse_pdf_guide(pdf)

    # Write JSON for the interactive front-end (into site/)
    write_json(source_url, data, out_path=args.out_json)

    # Build the Jinja static page and stage assets
    title_line = "Reflect + Discuss" if data.get("questions") else "Discussion Guide"
    sections = [
        {"heading": "Reflect + Discuss", "bullets": data.get("questions", []), "paras": []},
        *[
            {"heading": s["title"], "bullets": [], "paras": [s["body"]]}
            for s in data.get("sections", [])
        ],
    ]
    maybe_write_site(
        series_title=meta.get("series_title"),
        date_obj=meta.get("date"),
        title_line=title_line,
        sections=sections,
        source_pdf=source_url,
    )

    print(f"Built site for {meta.get('series_title','Current Series')} — {meta.get('date')} from {source_url}")

if __name__ == "__main__":
    sys.exit(main())
