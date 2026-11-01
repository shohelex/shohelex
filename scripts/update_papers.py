"""Refresh the Publications list in README.md from papers.json.

Run by .github/workflows/update-papers.yml. It looks for new papers on Google Scholar and
falls back to OpenAlex when Scholar blocks the request, which it often does for cloud servers.
A paper typed into the Run workflow form is added whatever those sources say.

Papers are only ever added. To hide a paper, delete it from "papers" and put its title in "skip".
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
        pdf = (work.get("open_access") or {}).get("oa_url")
        if pdf and pdf != link:
            paper["pdf"] = pdf
        papers.append(paper)
    return papers


def from_form():
    """The optional Run workflow fields, passed in through the environment."""
    title = os.environ.get("PAPER_TITLE", "").strip()
    link = os.environ.get("PAPER_LINK", "").strip()
    if not title and not link:
        return []
    if not title or not link:
        sys.exit("To add a paper by hand, fill in both its title and its link.")
    if not link.startswith(("https://", "http://")):
        sys.exit("The paper link must start with https:// or http://")
    year = os.environ.get("PAPER_YEAR", "").strip()
    return [{
        "title": title,
        "venue": os.environ.get("PAPER_VENUE", "").strip(),
        "year": int(year) if year.isdigit() else 0,
        "link": link,
    }]


def render(papers):
    items = []
    for paper in sorted(papers, key=lambda item: (-item["year"], item["title"])):
        details = [html.escape(part) for part in (paper.get("venue"), str(paper["year"] or "")) if part]
        if paper.get("pdf"):
            details.append(f'<a href="{html.escape(paper["pdf"])}">PDF</a>')
        items.append(
            f'<li><a href="{html.escape(paper["link"])}">{html.escape(paper["title"])}</a>'
            f'<br /><sub>{" · ".join(details)}</sub></li>'
        )
    return "<ol>\n" + "\n".join(items) + "\n</ol>"


def report(lines):
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write("\n\n".join(lines) + "\n")


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    known = {key(paper["title"]) for paper in data["papers"]}
    known |= {key(title) for title in data["skip"]}

    typed = from_form()
    found, source = [], None
    for name, reader in (("Google Scholar", from_scholar), ("OpenAlex", from_openalex)):
        try:
            found, source = reader(), name
            break
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            print(f"{name} was not available: {error}")
    if source is None and not typed:
        sys.exit("Neither Google Scholar nor OpenAlex answered, so nothing changed. Try again later.")

    added = []
    for paper in typed + found:
        if key(paper["title"]) in known:
            continue
        known.add(key(paper["title"]))
        if "scholar.google.com" in paper["link"]:
            scholar_links(paper)
        data["papers"].append(paper)
        added.append(paper["title"])

    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        sys.exit("README.md has lost its papers markers, so nothing changed.")
    block = f"{START}\n{render(data['papers'])}\n{END}"
    updated = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    README.write_text(updated, encoding="utf-8", newline="\n")
    if added:
        DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    lines = [f"Checked {source or 'the form only'}."]
    lines += [f"Added: {title}" for title in added] or ["No new papers found."]
    report(lines)


if __name__ == "__main__":
    main()
