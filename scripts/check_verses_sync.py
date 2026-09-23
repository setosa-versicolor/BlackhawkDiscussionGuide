"""
Assert scripts/verses.py agrees with templates/page.html.

The prefetcher keys verses by their reference string and the page looks them up
by the string its own detector produces. If the two ever disagree, prefetching
silently stops working - the page just falls back to live API calls. This
catches that.

Run: python scripts/check_verses_sync.py
"""
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import verses  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "templates" / "page.html"

SAMPLES = [
    "Read Luke 19:10. Read John 20:21.",
    "Compare 1 Cor 13:4-7 and Ps 23:1 and 2 Tim 1:7.",
    "Genesis 1:27, Proverbs 27:5-6, Proverbs 28:23, Colossians 1:15-18",
    "Song of Solomon 2:10 and Song 2:10 should dedupe.",
    "Lowercase john 3:16 and JOHN 3:16 and John 3:16.",
    "Abbreviations: Matt 5:3, Mt 5:3, Rom 8:28-30, Php 4:13, 1 Thess 5:16.",
    "Not references: 3:16 alone, Chapter 5, 2026, see page 12:00.",
    "Ranges: Rev 21:1-4 and Heb 11:1 and Jas 1:2-4.",
    "Edge: 1John 1:9, 2 Jn 1:6, 3John 1:4, Philem 1:6.",
    "Dashes: Galatians 5:22–23, Proverbs 3:5–6, Ps 23:1—4, Rom 12:1-2, Luke 19:10 — 3 things.",
    "No refs here at all, just prose about the sermon.",
]


def js_detect(samples):
    """Run the page's own detector under node."""
    script = PAGE.read_text(encoding="utf-8")
    block = re.search(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", script, re.S).group(1)

    def grab(start, end):
        i = block.index(start)
        return block[i:block.index(end, i)]

    src = "\n".join([
        grab("const bookMappings = {", "'rev': 'Revelation'") + "'rev': 'Revelation'\n};\n",
        grab("function detectVerseReferences(text) {", "// api.esv.org prefixes"),
        "const out = %s.map(t => detectVerseReferences(t).map(r => r.reference));" % json.dumps(samples),
        "console.log(JSON.stringify(out));",
    ])
    tmp = ROOT / ".verses-sync.tmp.js"
    try:
        tmp.write_text(src, encoding="utf-8")
        res = subprocess.run([ "node", str(tmp) ], capture_output=True, text=True, check=True)
        return json.loads(res.stdout)
    finally:
        tmp.unlink(missing_ok=True)


def js_book_ids():
    block = PAGE.read_text(encoding="utf-8")
    i = block.index("const bookIds = {")
    chunk = block[i:block.index("};", i)]
    return dict(re.findall(r"'([^']+)':\s*'([^']+)'", chunk))


def main():
    failures = []

    ids = js_book_ids()
    if ids != verses.BOOK_IDS:
        only_js = set(ids) - set(verses.BOOK_IDS)
        only_py = set(verses.BOOK_IDS) - set(ids)
        mismatched = {k for k in set(ids) & set(verses.BOOK_IDS) if ids[k] != verses.BOOK_IDS[k]}
        failures.append(f"bookIds differ: only-in-js={sorted(only_js)} "
                        f"only-in-py={sorted(only_py)} mismatched={sorted(mismatched)}")
    else:
        print(f"  OK   bookIds identical ({len(ids)} books)")

    js_results = js_detect(SAMPLES)
    for sample, js_refs in zip(SAMPLES, js_results):
        py_refs = [r["reference"] for r in verses.detect_references(sample)]
        if py_refs != js_refs:
            failures.append(f"detection differs for {sample!r}\n"
                            f"        js: {js_refs}\n        py: {py_refs}")
        else:
            print(f"  OK   {len(py_refs)} ref(s): {sample[:52]}")

    if failures:
        print("\nOUT OF SYNC:")
        for f in failures:
            print("  FAIL " + f)
        return 1
    print("\nverses.py and page.html agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
