import os, re, io, sys, datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text
from dateutil import parser as dateparser
from jinja2 import Environment, FileSystemLoader, select_autoescape

LEARN_URL = "https://blackhawk.church/learn/"
TZ = ZoneInfo("America/Chicago")

def get_soup(url):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")

def find_current_series_resources_url():
    """
    Heuristic: on the Learn page, the current series block includes a lone link
    titled exactly 'Resources'. We take the first such link above 'Past Series'.
    """
    soup = get_soup(LEARN_URL)

    # stop before 'Past Series' section if present
    past_series_header = soup.find(lambda tag: tag.name in ("h4","h5","h2","h3") and "Past Series" in tag.get_text())
    candidates = []
    for a in soup.find_all("a"):
        if a.get_text(strip=True) == "Resources":
            # prefer it if it is before 'Past Series' in DOM order
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
    On the series page, lines often look like:
    'September 28 // Title | Speaker â Discussion Guide'
    We parse the month/day near each 'Discussion Guide' link.
    """
    soup = get_soup(series_url)
    series_title_tag = soup.find(lambda t: t.name in ("h1","h2") and t.get_text(strip=True))
    series_title = series_title_tag.get_text(strip=True) if series_title_tag else "Current Series"

    guides = []
    for a in soup.find_all("a"):
        if "discussion guide" in a.get_text(strip=True).lower():
            context_text = a.parent.get_text(" ", strip=True) if a.parent else a.get_text(" ", strip=True)
            m = re.search(r'([A-Za-z]+)\s+(\d{1,2})', context_text)
            if not m:
                continue
            month_name, day_str = m.group(1), m.group(2)
            try:
                dt = dateparser.parse(f"{month_name} {day_str} {today.year}", fuzzy=True).date()
            except Exception:
                continue
            href = a.get("href")
            if href:
                guides.append((dt, requests.compat.urljoin(series_url, href), context_text))

    if not guides:
        raise RuntimeError("No 'Discussion Guide' links found on the series page.")

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
    r = requests.get(pdf_url, timeout=30)
    r.raise_for_status()
    text = extract_text(io.BytesIO(r.content))
    return text

def structure_text(raw):
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    title = lines[0] if lines else "Discussion Guide"
    sections = []
    current = {"heading": None, "bullets": [], "paras": []}

    def commit():
        nonlocal current
        if current["heading"] or current["bullets"] or current["paras"]:
            sections.append(current)
        current = {"heading": None, "bullets": [], "paras": []}

    for ln in lines[1:]:
        if re.match(r'^[A-Za-z].*$', ln) and not ln.startswith("- "):
            if ln.lower() in {"reflect + discuss", "pray", "next steps"} or len(ln) <= 60:
                commit()
                current["heading"] = ln
            else:
                current["paras"].append(ln)
        elif ln.startswith("- "):
            current["bullets"].append(ln[2:].strip())
        else:
            current["paras"].append(ln)

    commit()
    return title, sections

def write_site(series_title, date_obj, title_line, sections, source_pdf, out_dir="site"):
    os.makedirs(out_dir, exist_ok=True)
    env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape())
    tpl = env.get_template("page.html")
    date_str = date_obj.strftime("%A, %B %-d, %Y") if hasattr(date_obj, "strftime") else str(date_obj)
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
    print(f"Built page for {meta['series_title']} â {meta['date']} from {meta['pdf_url']}")

if __name__ == "__main__":
    sys.exit(main())
