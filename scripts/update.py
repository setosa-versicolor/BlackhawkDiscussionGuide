import os, re, io, sys, datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text
from dateutil import parser as dateparser
from jinja2 import Environment, FileSystemLoader, select_autoescape

LEARN_URL = "https://blackhawk.church/learn/"
TZ = ZoneInfo("America/Chicago")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:99.0) "
        "Gecko/20100101 Firefox/99.0"
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

def get_soup(url):
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")

def _collect_nearby_text(a_tag, max_ancestors=3):
    """
    Collect text near the anchor: its parent, previous siblings, and up to N ancestors,
    since the date is often printed in the same card/block just above the link.
    """
    texts = []

    # Anchor text and parent block
    texts.append(a_tag.get_text(" ", strip=True))
    if a_tag.parent:
        texts.append(a_tag.parent.get_text(" ", strip=True))

    # Include a few previous siblings' text (commonly the date line sits just above the link)
    prev = a_tag.previous_sibling
    steps = 0
    while prev and steps < 3:
        if hasattr(prev, "get_text"):
            txt = prev.get_text(" ", strip=True)
            if txt:
                texts.append(txt)
        prev = prev.previous_sibling
        steps += 1

    # Walk up ancestors
    anc = a_tag.parent
    depth = 0
    while anc and depth < max_ancestors:
        texts.append(anc.get_text(" ", strip=True))
        anc = anc.parent
        depth += 1

    # Deduplicate and return a single blob
    uniq = []
    seen = set()
    for t in texts:
        if t and t not in seen:
            uniq.append(t); seen.add(t)
    return "  •  ".join(uniq)

def _extract_dates_from_text(text, year_hint):
    """
    Return a list of date objects found in the text using both month-name and numeric formats.
    We use the current year as a hint since the series page typically omits the year.
    """
    dates = []

    for m in MONTH_NAME_PAT.finditer(text):
        mon_raw = m.group("month").lower().rstrip(".")
        day = int(m.group("day"))
        mon = MONTHS.get(mon_raw, None)
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

def find_current_series_resources_url():
    """
    On the Learn page, the current series block includes a link titled 'Resources'.
    We take the first such link that appears before the 'Past Series' header (if present).
    """
    soup = get_soup(LEARN_URL)

    past_series_header = soup.find(
        lambda tag: tag.name in ("h4", "h5", "h2", "h3")
        and "Past Series" in tag.get_text()
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

def find_today_discussion_pdf(series_url, today):
    """
    On the series page, entries look like:
      'September 28 // Title | Speaker – Discussion Guide' or '9/28 – Discussion Guide'
    We parse the date nearest to each 'Discussion Guide' link and choose:
      - exact match for `today` (America/Chicago), or
      - the most recent past date if today's isn't present yet.
    """
    soup = get_soup(series_url)
    series_title_tag = soup.find(lambda t: t.name in ("h1","h2") and t.get_text(strip=True))
    series_title = series_title_tag.get_text(strip=True) if series_title_tag else "Current Series"

    guides = []
    for a in soup.find_all("a"):
        if "discussion guide" in a.get_text(strip=True).lower():
            ctx = _collect_nearby_text(a)
            # Find all plausible dates near this link
            ds = _extract_dates_from_text(ctx, year_hint=today.year)
            # Prefer the *closest* date to "today" (max date <= today, else just max)
            chosen_date = None
            if ds:
                # Normalize duplicates and pick the latest <= today if possible
                ds_unique = sorted(set(ds))
                candidates = [d for d in ds_unique if d <= today]
                chosen_date = (candidates[-1] if candidates else ds_unique[-1])

            href = a.get("href")
            if href and chosen_date:
                guides.append((chosen_date, requests.compat.urljoin(series_url, href), ctx))

    if not guides:
        raise RuntimeError("No 'Discussion Guide' dates/links found on the series page.")

    # Choose exact match if present, else most recent past
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

def fetch_pdf_text(pdf_url):
    r = requests.get(pdf_url, headers=BROWSER_HEADERS, timeout=30)
    r.raise_for_status()
    text = extract_text(io.BytesIO(r.content))
    return text

def structure_text(raw):
    """
    Parse the PDF text into sections and bullets, merging wrapped bullet lines.
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

def write_site(series_title, date_obj, title_line, sections, source_pdf, out_dir="site"):
    os.makedirs(out_dir, exist_ok=True)
    env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape())
    tpl = env.get_template("page.html")
    # NOTE: %-d is not supported on Windows; if you ever run locally on Windows, use %#d
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
