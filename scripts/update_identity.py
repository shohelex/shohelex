"""Build the banner, research interests and link images and their README blocks from profile.json.

This needs Edge or Chrome, so it runs on your own computer through Profile Studio, not on GitHub.
Each image name carries a fingerprint of its content, so GitHub's image cache can't show an old one.
"""
import hashlib
import html
import json
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "profile.json"
README = ROOT / "README.md"
TEMPLATE = ROOT / "assets" / "source" / "design.html"
IMAGES = ROOT / "assets" / "identity"
HERO_SIZE = (880, 176)
LINK_SIZE = (420, 46)
INTERESTS_WIDTH = 880
BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RenderError(Exception):
    """A problem worth showing to the person using the tool."""


def load():
    return json.loads(PROFILE.read_text(encoding="utf-8"))


def validate(profile):
    """Return a clean copy of profile data, or raise ValueError with a readable message."""
    def text(value):
        return str(value or "").strip()

    name = text(profile.get("name"))
    if not name:
        raise ValueError("Your name can't be empty.")
    roles = [text(role) for role in profile.get("roles", []) if text(role)]
    interests = [text(item) for item in profile.get("interests", []) if text(item)]
    if not interests:
        raise ValueError("Add at least one research interest.")
    links = []
    for link in profile.get("links", []):
        label, url = text(link.get("label")), text(link.get("url"))
        if not label and not url:
            continue
        if not label or not url:
            raise ValueError("Every link needs both a name and an address.")
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"The address for {label} must start with https:// or http://")
        links.append({
            "label": label,
            "note": text(link.get("note")),
            "url": url,
            "mark": text(link.get("mark")) or label[:2].upper(),
        })
    contact = profile.get("contact") or {}
    emails = [text(email) for email in contact.get("emails", []) if text(email)]
    for email in emails:
        if not EMAIL.match(email):
            raise ValueError(f"{email} doesn't look like an email address.")
    return {
        "greeting": text(profile.get("greeting")),
        "name": name,
        "roles": roles,
        "interests": interests,
        "links": links,
        "contact": {"line": text(contact.get("line")), "emails": emails},
    }


def find_browser():
    for path in BROWSERS:
        if Path(path).exists():
            return path
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise RenderError("Edge or Chrome is needed to draw the images, and neither was found.")


def plan(profile, template):
    """Map each image file name to the section it shows."""
    parts = {
        "hero": {key: profile[key] for key in ("greeting", "name", "roles")},
        "interests": profile["interests"],
    }
    for index, link in enumerate(profile["links"], start=1):
        parts[f"link-{index}"] = {key: link[key] for key in ("label", "note", "mark")}
    files = {}
    for section, data in parts.items():
        digest = hashlib.sha256((template + json.dumps(data, sort_keys=True)).encode("utf-8")).hexdigest()[:10]
        files[section] = f"{section}-{digest}.png"
    return files


def fill(template, profile, folder):
    data = json.dumps(profile, ensure_ascii=False).replace("</", "<\\/")
    page = folder / "design.html"
    page.write_text(template.replace("/*PROFILE*/", data), encoding="utf-8")
    return page.as_uri()


def browser_run(browser, folder, tag, extra):
    command = [
        browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
        f"--user-data-dir={folder / ('edge-' + tag)}", "--virtual-time-budget=7000", *extra,
    ]
    try:
        return subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        raise RenderError("Drawing the images took too long. Try again.") from error


def measure(browser, folder, url):
    result = browser_run(browser, folder, "measure", ["--dump-dom", url])
    title = re.search(r"<title>(.*?)</title>", result.stdout, re.S)
    try:
        report = json.loads(html.unescape(title.group(1)))
    except (AttributeError, ValueError) as error:
        raise RenderError("The image page didn't finish loading. Try again.") from error
    if not report["fonts"]:
        raise RenderError("The fonts didn't load, so nothing was changed. Check your internet connection and try again.")
    if report["overflow"]:
        raise RenderError("One research interest is too long to fit. Shorten it and try again.")
    return report


def shoot(browser, folder, url, section, size, target):
    extra = [
        "--force-device-scale-factor=2", "--default-background-color=00000000",
        f"--window-size={size[0]},{size[1]}", f"--screenshot={target}", f"{url}#{section}",
    ]
    browser_run(browser, folder, section, extra)
    if not target.exists() or target.stat().st_size == 0:
        raise RenderError(f"The {section} image could not be drawn. Try again.")


def draw(profile, files, template):
    missing = {section: name for section, name in files.items() if not (IMAGES / name).exists()}
    if not missing:
        return
    IMAGES.mkdir(parents=True, exist_ok=True)
    browser = find_browser()
    with tempfile.TemporaryDirectory(prefix="profile-images-", ignore_cleanup_errors=True) as temp:
        folder = Path(temp)
        url = fill(template, profile, folder)
        height = measure(browser, folder, url)["height"]

        def size(section):
            if section == "hero":
                return HERO_SIZE
            return (INTERESTS_WIDTH, height) if section == "interests" else LINK_SIZE

        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [
                pool.submit(shoot, browser, folder, url, section, size(section), IMAGES / name)
                for section, name in missing.items()
            ]
            for job in jobs:
                job.result()


def identity_block(profile, files):
    roles = " and ".join(profile["roles"])
    hero_alt = html.escape(f"{profile['name']}. {roles}".strip(), quote=True)
    interests_alt = html.escape("Research interests: " + ", ".join(profile["interests"]) + ".", quote=True)
    rows = [
        f'  <img src="assets/identity/{files["hero"]}" width="804" alt="{hero_alt}" />',
        f'  <img src="assets/identity/{files["interests"]}" width="804" alt="{interests_alt}" />',
    ]
    for index, link in enumerate(profile["links"], start=1):
        alt = html.escape(": ".join(part for part in (link["label"], link["note"]) if part), quote=True)
        rows.append(
            f'  <a href="{html.escape(link["url"], quote=True)}">'
            f'<img src="assets/identity/{files[f"link-{index}"]}" width="400" alt="{alt}" /></a>'
        )
    return '<p align="center">\n' + "\n".join(rows) + "\n</p>"


def contact_block(profile):
    emails = [
        f'<a href="mailto:{html.escape(email, quote=True)}">{html.escape(email)}</a>'
        for email in profile["contact"]["emails"]
    ]
    if not emails:
        return ""
    line = html.escape(profile["contact"]["line"])
    return f'<p align="center">\n  {line} ' + " &nbsp;·&nbsp; ".join(emails) + "\n</p>"


def replace_block(text, name, block):
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    if start not in text or end not in text:
        raise RenderError(f"README.md has lost its {name} markers, so nothing changed.")
    return re.sub(re.escape(start) + r".*?" + re.escape(end), lambda _: f"{start}\n{block}\n{end}", text, flags=re.S)


def build(profile):
    """Draw whatever changed, then update the README and remove images no longer used."""
    template = TEMPLATE.read_text(encoding="utf-8")
    files = plan(profile, template)
    draw(profile, files, template)
    text = README.read_text(encoding="utf-8")
    text = replace_block(text, "identity", identity_block(profile, files))
    text = replace_block(text, "contact", contact_block(profile))
    README.write_text(text, encoding="utf-8", newline="\n")
    for old in IMAGES.glob("*.png"):
        if old.name not in files.values():
            old.unlink()


if __name__ == "__main__":
    build(validate(load()))
    print("Images and README are up to date.")
