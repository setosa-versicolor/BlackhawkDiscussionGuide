import os, re, io, sys, datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text
from dateutil import parser as dateparser
from jinja2 import Environment, FileSystemLoader, select_autoescape

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
    """Find dates like 'September 28', 'Sept. 28', and '9/28' in text."""
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
        mon = int(m.group("m"))
        day = int(m.group("d"))
        if 1 <= mon <= 12 and 1 <= day <= 31:
            try:
                dates.append(datetime.date(year_hint, mon, day))
            except ValueError:
                pass

    return dates

def _collect_nearby_text(a_tag, max_ancestors=3) -> str:
    """Gather text around an <a> to help associate the closest date to that link."""
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
    """
    Preferred path: open /messages/ and find a 'Discussion Guide' link whose
    nearby text mentions today's Month Day, Year (or at least Month & Day).
    """
    soup = get_soup(MESSAGES_URL)
    month_name = today.strftime("%B")
    day = str(today.day)
    year = str(today.year)

    for a in soup.find_all("a"):
        if "discussion guide" in a.get_text(strip=True).lower():
            ctx = _collect_nearby_text(a)
            # Check for "September 28, 2025" OR "September 28"
            has_full = (month_name in ctx and day in ctx and year in ctx)
            has_md   = (month_name in ctx and day in ctx)
            if has_full or has_md:
                href = a.get("href")
                if href:
                    return requests.compat.urljoin(MESSAGES_URL, href)
    return None

def _discussion_link_from_message_page(url: str) -> str | None:
    """Open a specific message page and return its 'Discussion Guide' link if present."""
    soup = get_soup(url)
    for a in soup.find_all("a"):
        if "discussion guide" in a.get_text(strip=True).lower():
            href = a.get("href")
            if href:
                return requests.compat.urljoin(url, href)
    return None

def _find_message_page_for_today(today: datetime.date) -> str | None:
    """
    On /messages/, locate the tile/anchor for today's message and return its page URL.
    """
    soup = get_soup(MESSAGES_URL)
    month_name = today.strftime("%B")
    day = str(today.day)
    # Accept numeric as well
    mmdd = f"{today.month}/{today.day}"

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/message/" in href:
            ctx = a.parent.get_text(" ", strip=True) if a.parent else a.get_text(" ", strip=True)
            if (month_name in ctx and day in ctx) or (mmdd in ctx):
                return requests.compat.urljoin(MESSAGES_URL, href)
    return None

def find_current_series_resources_url() -> str:
    """
    Fallback discovery: on the Learn page, grab the first 'Resources' link above 'Past Series'.
    """
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

def find_today_discussion_pdf(series_url: str, today: datetime.date) -> dict:
    """
    Strategy:
      1) /messages/ → 'Discussion Guide' with today's date nearby
      2) /messages/ → find today's message page → open → 'Discussion Guide'
      3) Fallback: scan series resources page for dates near 'Discussion Guide'
    Returns: { series_title, date, pdf_url, context, all_guides }
    """
    # 1) Messages landing, direct Discussion Guide
    direct = _first_discussion_link_on_messages_page(today)
    if direct:
        return {
            "series_title": "Current Series",
            "date": today,
            "pdf_url": direct,
            "context": "Messages landing (direct)",
            "all_guides": []
        }

    # 2) Find today's message page, then extract Discussion Guide
    msg_page = _find_message_page_for_today(today)
    if msg_page:
        pdf = _discussion_link_from_message_page(msg_page)
        if pdf:
            return {
                "series_title": "Current Series",
                "date": today,
                "pdf_url": pdf,
                "context": msg_page,
                "all_guides": []
            }

    # 3) Fallback: scan resources page (older series often list links here)
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
            # Prefer latest <= today
            candidates = [d for d in ds_unique if d <= today]
            dt = candidates[-1] if candidates else ds_unique[-1]
            href = a.get("href")
            if href:
                guides.append((dt, requests.compat.urljoin(series_url, href), ctx))

    if guides:
        guides.sort(key=lambda x: x[0])
        exact = [g for g in guides if g[0] == today]
        chosen = exact[0] if exact else max([g for g in guides if g[0] <= today], key=lambda x: x[0], default=guides[-1])
        return {
            "series_title": series_title,
            "date": chosen[0],
            "pdf_url": chosen[1],
            "context": chosen[2],
            "all_guides": guides
        }

    raise RuntimeError("Could not locate a Discussion Guide link for today's message.")

# --------- PDF → structured content ---------
def fetch_pdf_text(pdf_url: str) -> str:
    r = requests.get(pdf_url, headers=BROWSER_HEADERS, timeout=30)
    r.raise_for_status()
    return extract_text(io.BytesIO(r.content))

def structure_text(raw: str):
    """
    Convert PDF text into sections with wrapped bullets handled.
    A bullet starts with '- '. Lines that follow (and don't start with '- ')
    are appended to the last bullet until the next bullet/heading.
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    title = lines[0] if lines else "Discussion Guide"
    sections = []
    current = {"heading": None, "bullets": [], "paras": []}
    bullet_active = False

    def commit():
        nonlocal current, bullet_active
        if current["heading"] or current["bullets"] or current["paras"]:
            sections.append(current)
        current = {"heading": None, "bullets": [], "paras": []}
        bullet_active = False

    KNOWN_HEADINGS = {"reflect + discuss", "pray", "next steps"}

    for ln in lines[1:]:
        if ln.startswith("- "):
            bullet_active = True
            current["bullets"].append(ln[2:].strip())
            continue
        if bullet_active and not ln.startswith("- "):
            current["bullets"][-1] += " " + ln.strip()
            continue
        bullet_active = False
        if re.match(r"^[A-Za-z].*$", ln):
            if ln.lower() in KNOWN_HEADINGS or len(ln) <= 60:
                commit()
                current["heading"] = ln
                continue
        current["paras"].append(ln)

    commit()
    return title, sections

# --------- Site writer ---------
def write_site(series_title, date_obj, title_line, sections, source_pdf, out_dir="site"):
    os.makedirs(out_dir, exist_ok=True)
    env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape())
    tpl = env.get_template("page.html")

    # %-d not supported on Windows (local dev). Use %#d on Windows.
    day_fmt = "%-d" if os.name != "nt" else "%#d"
    date_str = date_obj.strftime(f"%A, %B {day_fmt}, %Y") if hasattr(date_obj, "strftime") else str(date_obj)

    html = tpl.render(
        series_title=series_title,
        title_line=title_line,
        date_str=date_str,
        sections=sections,
        pdf_url=source_pdf,
        updated=datetime.datetime.now(tz=TZ).strftime("%Y-%m-%d %I:%M %p %Z")
    )
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

# --------- Main ---------
def main():
    today = datetime.datetime.now(tz=TZ).date()
    series_url = find_current_series_resources_url()
    meta = find_today_discussion_pdf(series_url, today)
    raw = fetch_pdf_text(meta["pdf_url"])
    title_line, sections = structure_text(raw)
    write_site(
        series_title=meta["series_title"],
        date_obj=meta["date"],
        title_line=title_line,
        sections=sections,
        source_pdf=meta["pdf_url"]
    )
    print(f"Built page for {meta['series_title']} — {meta['date']} from {meta['pdf_url']}")

if __name__ == "__main__":
    sys.exit(main())
