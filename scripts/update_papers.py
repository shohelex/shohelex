"""Refresh the Publications list in README.md and its cards from papers.json.

Run by .github/workflows/update-papers.yml in one of three ways:
- Run workflow with the form empty: look for new papers on Google Scholar, falling back to
  OpenAlex when Scholar blocks the request, which it does for GitHub's servers.
- Run workflow with the form filled in: add that paper, or change the listed paper with that
  title. Fields left blank stay as they are. Nothing is looked up online.
- A saved edit to papers.json: rebuild the list exactly as the file says.

Online lookups only ever add papers. To hide a paper, delete it from "papers" in papers.json
and put its title in "skip", so a later lookup does not bring it back.
"""
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "papers.json"
README = ROOT / "README.md"
SCHOLAR_USER = "6rIIH8IAAAAJ"
OPENALEX_AUTHOR = "A5019447278"
OPENALEX_TYPES = {"article", "conference-paper", "preprint", "book-chapter"}
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
START = "<!-- papers:start -->"
END = "<!-- papers:end -->"

PREPRINT_VENUES = re.compile(r"arxiv|ssrn|biorxiv|medrxiv|techrxiv|chemrxiv|engrxiv|research square|\bpreprints?\b", re.I)
PAPERS_DIR = ROOT / "assets" / "papers"
CARD_WIDTH, CHIP_WIDTH, ROW_HEIGHT = 712, 88, 72
LINE_LENGTH = 88
VENUE_LENGTH = 104
ACCENTS = ("#a8620f", "#0a7569", "#c2442b", "#5546c0")
CHIP_ACCENT = "#0a7569"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def key(title):
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def from_scholar():
    page = fetch(f"https://scholar.google.com/citations?user={SCHOLAR_USER}&hl=en&pagesize=100")
    rows = re.findall(r'<tr class="gsc_a_tr">(.*?)</tr>', page, re.S)
    if not rows:
        raise RuntimeError("the page has no paper list, so Scholar probably blocked the request")
    papers = []
    for row in rows:
        title = re.search(r'href="([^"]+)" class="gsc_a_at">([^<]*)<', row)
        year = re.search(r'class="gsc_a_h[^>]*>(\d{4})<', row)
        if not title:
            continue
        grays = re.findall(r'<div class="gs_gray">(.*?)</div>', row, re.S)
        venue = strip_tags(grays[1]) if len(grays) > 1 else ""
        papers.append({
            "title": html.unescape(title.group(2)).strip(),
            "venue": re.sub(r",\s*\d{4}$", "", venue),
            "year": int(year.group(1)) if year else 0,
            "link": "https://scholar.google.com" + html.unescape(title.group(1)),
        })
    return papers


def scholar_links(paper):
    """Swap a paper's Scholar page for its publisher page, and find a free PDF if listed."""
    try:
        detail = fetch(paper["link"])
    except OSError as error:
        print(f"Kept the Scholar link for a new paper: {error}")
        return
    source = re.search(r'href="([^"]+)"[^>]*class="gsc_oci_title_link"|class="gsc_oci_title_link"[^>]*href="([^"]+)"', detail)
    if source:
        paper["link"] = html.unescape(source.group(1) or source.group(2))
    pdf = re.search(r'class="gsc_oci_title_ggi".*?href="([^"]+)"', detail, re.S)
    if pdf:
        paper["pdf"] = html.unescape(pdf.group(1))


def from_openalex():
    url = f"https://api.openalex.org/works?per-page=100&filter=author.id:{OPENALEX_AUTHOR}"
    papers = []
    for work in json.loads(fetch(url))["results"]:
        if work.get("type") not in OPENALEX_TYPES or not work.get("title"):
            continue
        location = work.get("primary_location") or {}
        link = work.get("doi") or location.get("landing_page_url") or work["id"]
        paper = {
            "title": strip_tags(work["title"]),
            "venue": (location.get("source") or {}).get("display_name") or "",
            "year": work.get("publication_year") or 0,
            "link": link,
        }
        if work["type"] == "preprint":
            paper["preprint"] = True
        pdf = (work.get("open_access") or {}).get("oa_url")
        if pdf and pdf != link:
            paper["pdf"] = pdf
        papers.append(paper)
    return papers


def from_form():
    """Return the Run workflow fields that were filled in, checked and ready to use."""
    fields = {"title": "PAPER_TITLE", "link": "PAPER_LINK", "pdf": "PAPER_PDF", "venue": "PAPER_VENUE", "year": "PAPER_YEAR"}
    form = {field: os.environ.get(name, "").strip() for field, name in fields.items()}
    form = {field: value for field, value in form.items() if value}
    preprint = os.environ.get("PAPER_PREPRINT", "").strip()
    if preprint in ("yes", "no"):
        form["preprint"] = preprint == "yes"
    for field in ("link", "pdf"):
        if field in form and not form[field].startswith(("https://", "http://")):
            sys.exit(f"The {field} must start with https:// or http://")
    if "year" in form:
        if not form["year"].isdigit():
            sys.exit("The year must be a number, such as 2026.")
        form["year"] = int(form["year"])
    if form and "title" not in form:
        sys.exit("Fill in the paper's title too, so the right paper can be added or changed.")
    return form


def apply_form(data, form):
    """Change the listed paper with this title, or add it as a new paper."""
    for paper in data["papers"]:
        if key(paper["title"]) == key(form["title"]):
            changes = {field: value for field, value in form.items() if field != "title"}
            if not changes:
                sys.exit("That paper is already listed. Fill in the details you want to change.")
            paper.update(changes)
            return f"Updated: {paper['title']}"
    if "link" not in form:
        sys.exit("That title is not in the list yet. A new paper needs its link too.")
    form.setdefault("year", 0)
    data["papers"].append(form)
    data["skip"] = [title for title in data["skip"] if key(title) != key(form["title"])]
    return f"Added: {form['title']}"


def load():
    """Read papers.json, catching the mistakes a hand edit can introduce."""
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        sys.exit(f"papers.json has a typing mistake near line {error.lineno}, so nothing changed: {error.msg}")
    data.setdefault("skip", [])
    for paper in data["papers"]:
        name = paper.get("title") or paper
        if not paper.get("title") or not paper.get("link"):
            sys.exit(f"Every paper needs a title and a link, so nothing changed. Check: {name}")
        try:
            paper["year"] = int(paper.get("year") or 0)
        except (TypeError, ValueError):
            sys.exit(f"The year must be a number, so nothing changed. Check: {name}")
    return data


def find_new(data):
    """Add papers found online. Returns what was checked and what was added."""
    known = {key(paper["title"]) for paper in data["papers"]}
    known |= {key(title) for title in data["skip"]}
    for name, reader in (("Google Scholar", from_scholar), ("OpenAlex", from_openalex)):
        try:
            found = reader()
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            print(f"{name} was not available: {error}")
            continue
        lines = [f"Checked {name}."]
        for paper in found:
            if key(paper["title"]) in known:
                continue
            known.add(key(paper["title"]))
            if "scholar.google.com" in paper["link"]:
                scholar_links(paper)
            data["papers"].append(paper)
            lines.append(f"Added: {paper['title']}")
        return lines if len(lines) > 1 else lines + ["No new papers found."]
    sys.exit("Neither Google Scholar nor OpenAlex answered, so nothing changed. Try again later.")


def is_preprint(paper):
    if "preprint" in paper:
        return bool(paper["preprint"])
    return bool(PREPRINT_VENUES.search(paper.get("venue") or ""))


def slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].rstrip("-")


def shorten(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,:;-") + "…"


def wrap(title):
    """Split a title over at most two lines of roughly equal length."""
    if len(title) <= LINE_LENGTH:
        return [title]
    words = title.split()
    splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
    fitting = [pair for pair in splits if len(pair[0]) <= LINE_LENGTH]
    if not fitting:
        return [shorten(title, LINE_LENGTH)]
    first, rest = min(fitting, key=lambda pair: abs(len(pair[0]) - len(pair[1])))
    return [first, shorten(rest, LINE_LENGTH)]


def frame(width, accent, body):
    gradient = (
        f'<radialGradient id="tint" cx="1" cy=".5" r=".75">'
        f'<stop offset="0" stop-color="{accent}" stop-opacity=".12"/>'
        f'<stop offset="1" stop-color="{accent}" stop-opacity="0"/></radialGradient>'
    )
    box = f'x=".5" y=".5" width="{width - 1}" height="{ROW_HEIGHT - 1}" rx="14"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{ROW_HEIGHT}" '
        f'viewBox="0 0 {width} {ROW_HEIGHT}">\n<defs>{gradient}</defs>\n'
        f'<rect {box} fill="#fff"/><rect {box} fill="url(#tint)" stroke="#e3e8f0"/>\n{body}\n</svg>\n'
    )


def card(paper, accent):
    year = html.escape(str(paper["year"] or "—"))
    lines = wrap(paper["title"])
    top = 26 if len(lines) == 2 else 32
    title = "".join(
        f'<text x="76" y="{top + index * 18}" font-family="{SANS}" font-size="14" '
        f'font-weight="600" fill="#0f1521">{html.escape(line)}</text>\n'
        for index, line in enumerate(lines)
    )
    baseline = top + len(lines) * 18
    tag, venue_x, venue_limit = "", 76, VENUE_LENGTH
    if is_preprint(paper):
        venue_x, venue_limit = 142, VENUE_LENGTH - 12
        tag = (
            f'<rect x="76" y="{baseline - 12.5}" width="58" height="17" rx="8.500" fill="{accent}" fill-opacity=".14"/>\n'
            f'<text x="105" y="{baseline}" text-anchor="middle" font-family="{SANS}" font-size="10.500" '
            f'font-weight="600" fill="{accent}">Preprint</text>\n'
        )
    venue = html.escape(shorten(paper.get("venue") or "", venue_limit))
    return frame(CARD_WIDTH, accent, (
        f'<rect x="14" y="15" width="48" height="42" rx="9" fill="{accent}" fill-opacity=".13"/>\n'
        f'<text x="38" y="41" text-anchor="middle" font-family="{MONO}" font-size="13" '
        f'font-weight="600" fill="{accent}">{year}</text>\n'
        f'{title}{tag}<text x="{venue_x}" y="{baseline}" font-family="{SANS}" font-size="12" '
        f'fill="#4f5b70">{venue}</text>'
    ))


def chip(label):
    return frame(CHIP_WIDTH, CHIP_ACCENT, (
        f'<text x="{CHIP_WIDTH / 2}" y="41" text-anchor="middle" font-family="{SANS}" font-size="14" '
        f'font-weight="600" fill="{CHIP_ACCENT}">{label} ↗</text>'
    ))


def render(papers):
    """Write one image per paper and return the matching README block."""
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    wanted = {"pdf.svg": chip("PDF"), "read.svg": chip("Read")}
    rows = []
    ordered = sorted(papers, key=lambda item: (-item["year"], item["title"]))
    for index, paper in enumerate(ordered):
        name = f"{slug(paper['title'])}.svg"
        wanted[name] = card(paper, ACCENTS[index % len(ACCENTS)])
        link = html.escape(paper["link"])
        words = [paper["title"], "Preprint" if is_preprint(paper) else "", paper.get("venue"), str(paper["year"] or "")]
        alt = html.escape(". ".join(part for part in words if part))
        second = html.escape(paper["pdf"]) if paper.get("pdf") else link
        label, image = ("PDF", "pdf.svg") if paper.get("pdf") else ("Read", "read.svg")
        rows.append(f'  <a href="{link}"><img src="assets/papers/{name}" width="{CARD_WIDTH}" alt="{alt}" /></a>')
        rows.append(f'  <a href="{second}"><img src="assets/papers/{image}" width="{CHIP_WIDTH}" alt="{label}" /></a>')
    for old in PAPERS_DIR.glob("*.svg"):
        if old.name not in wanted:
            old.unlink()
    for name, content in wanted.items():
        (PAPERS_DIR / name).write_text(content, encoding="utf-8", newline="\n")
    return '<p align="center">\n' + "\n".join(rows) + "\n</p>"


def report(lines):
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write("\n\n".join(lines) + "\n")


def main():
    data = load()
    form = from_form()
    if os.environ.get("RENDER_ONLY") == "true":
        lines = ["Rebuilt the list from papers.json."]
    elif form:
        lines = [apply_form(data, form)]
    else:
        lines = find_new(data)

    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        sys.exit("README.md has lost its papers markers, so nothing changed.")
    block = f"{START}\n{render(data['papers'])}\n{END}"
    updated = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    README.write_text(updated, encoding="utf-8", newline="\n")
    if any(line.startswith(("Added", "Updated")) for line in lines):
        DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    report(lines)


if __name__ == "__main__":
    main()
