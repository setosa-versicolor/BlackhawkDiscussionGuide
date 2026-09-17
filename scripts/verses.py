"""
Resolve the scripture references in a discussion guide at build time.

The page can look references up in the browser, but that spends one API.Bible
call per reference, per translation, per reader. Doing it here instead means
the weekly guide costs a fixed handful of calls at build time and none at all
when people read it.

Fetching goes through the same Cloudflare Workers the page uses, so no API key
is needed in CI, and every lookup warms the Worker's KV cache as a side effect
- which also covers any reference typed into a custom card later.

The detection logic mirrors detectVerseReferences() in templates/page.html and
must produce identical reference strings, since those strings are the keys the
page looks up. scripts/check_verses_sync.py asserts the two agree.
"""

import json
import re
import urllib.parse
import urllib.request

BIBLE_PROXY_URL = "https://bible-proxy.johnston-thayer.workers.dev"
ESV_WORKER_URL = "https://esv-bible-proxy.johnston-thayer.workers.dev"

# api.esv.org returns no attribution string and the licence requires one.
ESV_COPYRIGHT = (
    "Scripture quotations are from the ESV® Bible (The Holy Bible, English "
    "Standard Version®), copyright © 2001 by Crossway, a publishing "
    "ministry of Good News Publishers. Used by permission. All rights reserved."
)

TRANSLATIONS = ("niv", "esv", "nlt", "kjv")

BOOK_MAPPINGS = {
    'gen': 'Genesis', 'genesis': 'Genesis',
    'ex': 'Exodus', 'exo': 'Exodus', 'exod': 'Exodus', 'exodus': 'Exodus',
    'lev': 'Leviticus', 'leviticus': 'Leviticus',
    'num': 'Numbers', 'numbers': 'Numbers',
    'deut': 'Deuteronomy', 'dt': 'Deuteronomy', 'deuteronomy': 'Deuteronomy',
    'josh': 'Joshua', 'joshua': 'Joshua',
    'judg': 'Judges', 'judges': 'Judges',
    'ruth': 'Ruth',
    '1 sam': '1 Samuel', '1 samuel': '1 Samuel', '1sam': '1 Samuel',
    '2 sam': '2 Samuel', '2 samuel': '2 Samuel', '2sam': '2 Samuel',
    '1 kings': '1 Kings', '1 ki': '1 Kings', '1ki': '1 Kings',
    '2 kings': '2 Kings', '2 ki': '2 Kings', '2ki': '2 Kings',
    '1 chron': '1 Chronicles', '1 chronicles': '1 Chronicles', '1chron': '1 Chronicles',
    '2 chron': '2 Chronicles', '2 chronicles': '2 Chronicles', '2chron': '2 Chronicles',
    'ezra': 'Ezra',
    'neh': 'Nehemiah', 'nehemiah': 'Nehemiah',
    'esth': 'Esther', 'esther': 'Esther',
    'job': 'Job',
    'ps': 'Psalm', 'psa': 'Psalm', 'psalm': 'Psalm', 'psalms': 'Psalm',
    'prov': 'Proverbs', 'proverbs': 'Proverbs',
    'eccl': 'Ecclesiastes', 'ecclesiastes': 'Ecclesiastes',
    'song': 'Song of Solomon', 'song of solomon': 'Song of Solomon',
    'isa': 'Isaiah', 'isaiah': 'Isaiah',
    'jer': 'Jeremiah', 'jeremiah': 'Jeremiah',
    'lam': 'Lamentations', 'lamentations': 'Lamentations',
    'ezek': 'Ezekiel', 'ezekiel': 'Ezekiel',
    'dan': 'Daniel', 'daniel': 'Daniel',
    'hos': 'Hosea', 'hosea': 'Hosea',
    'joel': 'Joel',
    'amos': 'Amos',
    'obad': 'Obadiah', 'obadiah': 'Obadiah',
    'jonah': 'Jonah',
    'mic': 'Micah', 'micah': 'Micah',
    'nah': 'Nahum', 'nahum': 'Nahum',
    'hab': 'Habakkuk', 'habakkuk': 'Habakkuk',
    'zeph': 'Zephaniah', 'zephaniah': 'Zephaniah',
    'hag': 'Haggai', 'haggai': 'Haggai',
    'zech': 'Zechariah', 'zechariah': 'Zechariah',
    'mal': 'Malachi', 'malachi': 'Malachi',
    'matt': 'Matthew', 'mt': 'Matthew', 'matthew': 'Matthew',
    'mark': 'Mark', 'mk': 'Mark',
    'luke': 'Luke', 'lk': 'Luke',
    'john': 'John', 'jn': 'John',
    'acts': 'Acts',
    'rom': 'Romans', 'romans': 'Romans',
    '1 cor': '1 Corinthians', '1 corinthians': '1 Corinthians', '1cor': '1 Corinthians',
    '2 cor': '2 Corinthians', '2 corinthians': '2 Corinthians', '2cor': '2 Corinthians',
    'gal': 'Galatians', 'galatians': 'Galatians',
    'eph': 'Ephesians', 'ephesians': 'Ephesians',
    'phil': 'Philippians', 'php': 'Philippians', 'philippians': 'Philippians',
    'col': 'Colossians', 'colossians': 'Colossians',
    '1 thess': '1 Thessalonians', '1 thessalonians': '1 Thessalonians', '1thess': '1 Thessalonians',
    '2 thess': '2 Thessalonians', '2 thessalonians': '2 Thessalonians', '2thess': '2 Thessalonians',
    '1 tim': '1 Timothy', '1 timothy': '1 Timothy', '1tim': '1 Timothy',
    '2 tim': '2 Timothy', '2 timothy': '2 Timothy', '2tim': '2 Timothy',
    'titus': 'Titus',
    'philem': 'Philemon', 'philemon': 'Philemon',
    'heb': 'Hebrews', 'hebrews': 'Hebrews',
    'james': 'James', 'jas': 'James',
    '1 pet': '1 Peter', '1 peter': '1 Peter', '1pet': '1 Peter',
    '2 pet': '2 Peter', '2 peter': '2 Peter', '2pet': '2 Peter',
    '1 john': '1 John', '1john': '1 John', '1 jn': '1 John',
    '2 john': '2 John', '2john': '2 John', '2 jn': '2 John',
    '3 john': '3 John', '3john': '3 John', '3 jn': '3 John',
    'jude': 'Jude',
    'rev': 'Revelation', 'revelation': 'Revelation',
}

BOOK_IDS = {
    'Genesis': 'GEN', 'Exodus': 'EXO', 'Leviticus': 'LEV', 'Numbers': 'NUM',
    'Deuteronomy': 'DEU', 'Joshua': 'JOS', 'Judges': 'JDG', 'Ruth': 'RUT',
    '1 Samuel': '1SA', '2 Samuel': '2SA', '1 Kings': '1KI', '2 Kings': '2KI',
    '1 Chronicles': '1CH', '2 Chronicles': '2CH', 'Ezra': 'EZR',
    'Nehemiah': 'NEH', 'Esther': 'EST', 'Job': 'JOB', 'Psalm': 'PSA',
    'Proverbs': 'PRO', 'Ecclesiastes': 'ECC', 'Song of Solomon': 'SNG',
    'Isaiah': 'ISA', 'Jeremiah': 'JER', 'Lamentations': 'LAM',
    'Ezekiel': 'EZK', 'Daniel': 'DAN', 'Hosea': 'HOS', 'Joel': 'JOL',
    'Amos': 'AMO', 'Obadiah': 'OBA', 'Jonah': 'JON', 'Micah': 'MIC',
    'Nahum': 'NAM', 'Habakkuk': 'HAB', 'Zephaniah': 'ZEP', 'Haggai': 'HAG',
    'Zechariah': 'ZEC', 'Malachi': 'MAL', 'Matthew': 'MAT', 'Mark': 'MRK',
    'Luke': 'LUK', 'John': 'JHN', 'Acts': 'ACT', 'Romans': 'ROM',
    '1 Corinthians': '1CO', '2 Corinthians': '2CO', 'Galatians': 'GAL',
    'Ephesians': 'EPH', 'Philippians': 'PHP', 'Colossians': 'COL',
    '1 Thessalonians': '1TH', '2 Thessalonians': '2TH', '1 Timothy': '1TI',
    '2 Timothy': '2TI', 'Titus': 'TIT', 'Philemon': 'PHM', 'Hebrews': 'HEB',
    'James': 'JAS', '1 Peter': '1PE', '2 Peter': '2PE', '1 John': '1JN',
    '2 John': '2JN', '3 John': '3JN', 'Jude': 'JUD', 'Revelation': 'REV',
}

_PATTERN = re.compile(
    r"\b((?:1|2|3)\s+)?([A-Za-z][A-Za-z]*(?:\s+[A-Za-z][A-Za-z]*)?\.?)\s+(\d+):(\d+)(?:-(\d+))?\b"
)


def detect_references(text):
    """Mirror of detectVerseReferences() in templates/page.html."""
    refs, seen = [], set()
    for m in _PATTERN.finditer(text or ""):
        prefix = (m.group(1).strip() + " ") if m.group(1) else ""
        book_raw = m.group(2).rstrip(".")
        chapter, v_start = m.group(3), m.group(4)
        v_end = m.group(5) or v_start

        key = (prefix + book_raw).lower()
        book = BOOK_MAPPINGS.get(key)
        if not book and " " in book_raw:
            key = (prefix + book_raw.split()[-1]).lower()
            book = BOOK_MAPPINGS.get(key)
        if not book:
            continue

        reference = f"{book} {chapter}:{v_start}" + (f"-{v_end}" if v_end != v_start else "")
        if reference.lower() in seen:
            continue
        seen.add(reference.lower())
        refs.append({
            "reference": reference, "book": book, "chapter": chapter,
            "verseStart": v_start, "verseEnd": v_end,
            "query": f"{book} {chapter}:{v_start}-{v_end}",
        })
    return refs


def build_passage_id(ref):
    book_id = BOOK_IDS.get(ref["book"])
    if not book_id:
        return None
    start = f"{book_id}.{ref['chapter']}.{ref['verseStart']}"
    if ref["verseEnd"] != ref["verseStart"]:
        return f"{start}-{book_id}.{ref['chapter']}.{ref['verseEnd']}"
    return start


_HEADING = re.compile(r"^\s*(?:[1-3]\s+)?[A-Za-z][A-Za-z\s]*\s+\d+:\d+(?:\s*[-–]\s*\d+)?\s*$")


def clean_verse_text(raw):
    """Mirror of cleanVerseText() in templates/page.html."""
    text = str(raw or "")
    parts = re.split(r"\n\s*\n", text)
    if len(parts) > 1 and _HEADING.match(parts[0]):
        text = " ".join(parts[1:])
    return re.sub(r"\s+", " ", text).strip()


# Cloudflare rejects the default urllib User-Agent with a 403, so identify the
# build properly. Without this every prefetch silently fails and the page
# quietly falls back to live lookups.
_HEADERS = {
    "User-Agent": (
        "BlackhawkDiscussionGuide-build/1.0 "
        "(+https://github.com/setosa-versicolor/BlackhawkDiscussionGuide)"
    ),
    "Accept": "application/json",
}


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_one(ref, translation):
    """Return {text, copyright} for one reference, or None if it can't be had."""
    try:
        if translation == "esv":
            url = f"{ESV_WORKER_URL}?q={urllib.parse.quote(ref['query'])}"
            data = _get_json(url)
            text = clean_verse_text((data.get("passages") or [""])[0])
            return {"text": text, "copyright": ESV_COPYRIGHT} if text else None

        passage = build_passage_id(ref)
        if not passage:
            return None
        url = (f"{BIBLE_PROXY_URL}?translation={translation}"
               f"&passage={urllib.parse.quote(passage)}")
        data = _get_json(url)
        text = clean_verse_text((data.get("passages") or [""])[0])
        return {"text": text, "copyright": (data.get("copyright") or "").strip()} if text else None
    except Exception as exc:
        print(f"    ! {translation.upper()} {ref['reference']}: {exc}")
        return None


def prefetch(text, translations=TRANSLATIONS):
    """Resolve every reference found in `text` across `translations`."""
    refs = detect_references(text)
    if not refs:
        print("No scripture references found; nothing to prefetch.")
        return {}

    print(f"Prefetching {len(refs)} reference(s) x {len(translations)} translations:")
    out, ok, failed = {}, 0, 0
    for ref in refs:
        entry = {}
        for t in translations:
            got = fetch_one(ref, t)
            if got:
                entry[t] = got
                ok += 1
            else:
                failed += 1
        if entry:
            out[ref["reference"]] = entry
        print(f"  {ref['reference']}: {', '.join(sorted(entry)) or 'none'}")

    print(f"Prefetched {ok} passage(s){f', {failed} failed' if failed else ''}.")
    return out
