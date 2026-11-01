"""Refresh the project cards in README.md from the account's public repositories.

Run by .github/workflows/update-projects.yml every day, on demand, and after an edit to
projects.json. The newest public repositories become cards, so a new repository appears on
its own and a deleted or private one disappears. Forks and archived repositories are left out.

projects.json holds the choices GitHub can't supply:
- "limit": how many cards to show
- "skip": repository names never to show
- "text": hand-written "kind" and "description" for a repository, used instead of GitHub's
"""
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from update_papers import ACCENTS, MONO, SANS, frame, shorten

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "projects.json"
README = ROOT / "README.md"
CARDS_DIR = ROOT / "assets" / "projects"
ACCOUNT = "shohelex"
CARD_WIDTH, CARD_HEIGHT = 400, 84
NAME_LENGTH, TEXT_LENGTH = 28, 52
START = "<!-- projects:start -->"
END = "<!-- projects:end -->"

DEFAULT_ICON = '<path d="M12 9l-8 8 8 8"/><path d="M22 9l8 8-8 8"/><path d="M19.500 6l-5 22"/>'
ICONS = {
    "avoid-desk-rejection": '<path d="M8 3h13l6 6v22H8z"/><path d="M21 3v6h6"/><path d="M12.500 20l3.500 3.500 6.500-7"/>',
    "latex-sidekick": (
        '<path d="M12 4c-4 0-4 2-4 5v4c0 2.500-1 4-3.500 4C7 17 8 18.500 8 21v4c0 3 0 5 4 5"/>'
        '<path d="M22 4c4 0 4 2 4 5v4c0 2.500 1 4 3.500 4-2.500 0-3.500 1.500-3.500 4v4c0 3 0 5-4 5"/>'
        '<path d="M13.500 12h7M17 12v10"/>'
    ),
    "duplex-guard": '<path d="M6 8h15v22H6z"/><path d="M12 8V4h16v22h-7"/><path d="M10 15h7M10 20h7"/>',
    "spectra-federated-backdoor": '<path d="M2 17c3-12 6-12 9 0s6 12 9 0 6-12 12-4"/><path d="M2 29h30" opacity=".5"/>',
}


def public_repos():
    url = f"https://api.github.com/users/{ACCOUNT}/repos?type=owner&per_page=100"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": ACCOUNT}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def load():
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        sys.exit(f"projects.json has a typing mistake near line {error.lineno}, so nothing changed: {error.msg}")
    if not isinstance(config.get("limit", 6), int):
        sys.exit("The limit in projects.json must be a number, so nothing changed.")
    return config


def select(repos, config):
    skip = {name.lower() for name in config.get("skip", [])}
    shown = [
        repo for repo in repos
        if not repo["private"] and not repo["fork"] and not repo["archived"]
        and repo["name"].lower() not in skip
    ]
    shown.sort(key=lambda repo: repo["created_at"], reverse=True)
    return shown[:config.get("limit", 6)]


def describe(repo, text):
    own = text.get(repo["name"], {})
    language = repo.get("language")
    summary = (repo.get("description") or "").strip().split(". ")[0].rstrip(".")
    return {
        "name": repo["name"],
        "url": repo["html_url"],
        "kind": own.get("kind") or language or "Project",
        "description": own.get("description") or summary or f"{language or 'Code'} project",
    }


def card(project, accent):
    icon = ICONS.get(project["name"], DEFAULT_ICON)
    name = html.escape(shorten(project["name"], NAME_LENGTH))
    kind = html.escape(shorten(project["kind"], 20))
    text = html.escape(shorten(project["description"], TEXT_LENGTH))
    return frame(CARD_WIDTH, accent, (
        f'<rect x="14" y="21" width="42" height="42" rx="9" fill="{accent}" fill-opacity=".13"/>\n'
        f'<g transform="translate(22 29) scale(.76)" fill="none" stroke="{accent}" stroke-width="2.200" '
        f'stroke-linecap="round" stroke-linejoin="round">{icon}</g>\n'
        f'<text x="68" y="38" font-family="{MONO}" font-size="15" font-weight="600" fill="#0f1521">{name}</text>\n'
        f'<text x="386" y="37" text-anchor="end" font-family="{MONO}" font-size="10.500" '
        f'font-weight="600" fill="{accent}">{kind}</text>\n'
        f'<text x="68" y="58" font-family="{SANS}" font-size="12.500" fill="#283344">{text}</text>'
    ), CARD_HEIGHT)


def render(projects):
    """Write one image per project and return the matching README block."""
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    wanted = {}
    rows = []
    for index, project in enumerate(projects):
        file_name = f"{project['name'].lower()}.svg"
        wanted[file_name] = card(project, ACCENTS[index % len(ACCENTS)])
        alt = html.escape(f"{project['name']}: {project['description']}")
        rows.append(
            f'  <a href="{html.escape(project["url"])}"><img src="assets/projects/{file_name}" '
            f'width="{CARD_WIDTH}" alt="{alt}" /></a>'
        )
    for old in CARDS_DIR.glob("*.svg"):
        if old.name not in wanted:
            old.unlink()
    for file_name, content in wanted.items():
        (CARDS_DIR / file_name).write_text(content, encoding="utf-8", newline="\n")
    return '<p align="center">\n' + "\n".join(rows) + "\n</p>"


def main():
    config = load()
    try:
        repos = public_repos()
    except (OSError, ValueError) as error:
        sys.exit(f"GitHub did not answer, so nothing changed: {error}")
    projects = [describe(repo, config.get("text", {})) for repo in select(repos, config)]
    if not projects:
        sys.exit("No public repositories were found, so nothing changed.")

    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        sys.exit("README.md has lost its projects markers, so nothing changed.")
    block = f"{START}\n{render(projects)}\n{END}"
    updated = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    README.write_text(updated, encoding="utf-8", newline="\n")
    print("Showing: " + ", ".join(project["name"] for project in projects))


if __name__ == "__main__":
    main()
