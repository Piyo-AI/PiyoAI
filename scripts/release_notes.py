"""Write the body of a release from the commits since the previous release.

    python scripts/release_notes.py v0.2.0                 # since the tag before it
    python scripts/release_notes.py v0.2.0 --previous v0.1.5

Needs the tags in the clone (the release workflow fetches the whole history). Commit subjects are the
changelog, so write them for a reader: "Ask before memory.remember once a run has read outside text", not
"fix stuff". A subject may start with `feat:`, `fix:` or `security:` to be listed under that heading; lines
beginning with `- ` in the body are listed under their commit. The notes the release workflow adds on top
(unsigned build, browser download) come from `FOOTER` below.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict

FOOTER = """\
Unsigned preview build. Windows and macOS will warn that the app is from an unknown publisher.
Piyo downloads its browser (Chromium) the first time you use it.
"""

HEADINGS = {"security": "Security", "feat": "New", "fix": "Fixed"}
ORDER = ["Security", "New", "Fixed", "Changes"]
PREFIX = re.compile(r"^(\w+)(?:\([^)]*\))?!?:\s+(.*)$")
SKIP = re.compile(r"^(Merge |Revert \"Merge |Version \d)")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def previous_tag(tag: str) -> str | None:
    """The release tag just below `tag` in version order, or None for the first release."""
    tags = git("tag", "--list", "v*", "--sort=-v:refname").split()
    return next((t for t in tags if _key(t) < _key(tag)), None)


def _key(tag: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", tag))


def commits(tag: str, previous: str | None) -> list[tuple[str, list[str]]]:
    """(subject, bullet lines of the body) for each commit, newest first."""
    span = f"{previous}..{tag}" if previous else tag
    raw = git("log", span, "--no-merges", "--format=%s%x00%b%x1e")
    found = []
    for entry in raw.split("\x1e"):
        subject, _, body = entry.strip().partition("\x00")
        if not subject or SKIP.match(subject):
            continue
        bullets = [line.strip()[2:].strip() for line in body.splitlines() if line.strip().startswith("- ")]
        found.append((subject.strip(), bullets))
    return found


def render(tag: str, previous: str | None, entries: list[tuple[str, list[str]]], repo: str = "") -> str:
    groups: dict[str, list[str]] = defaultdict(list)
    for subject, bullets in entries:
        heading, text = "Changes", subject
        if (m := PREFIX.match(subject)) and m.group(1).lower() in HEADINGS:
            heading, text = HEADINGS[m.group(1).lower()], m.group(2)
        item = f"- {text[:1].upper()}{text[1:]}"
        groups[heading].append("\n".join([item, *(f"  - {b}" for b in bullets)]))
    lines = [f"## What's changed in {tag}", ""]
    if not groups:
        lines += ["Maintenance release; no user-facing changes were recorded.", ""]
    multiple = len(groups) > 1
    for heading in ORDER:
        if heading in groups:
            if multiple:
                lines += [f"### {heading}", ""]
            lines += [*groups[heading], ""]
    if previous and repo:
        lines += [f"Full changelog: https://github.com/{repo}/compare/{previous}...{tag}", ""]
    return "\n".join([*lines, FOOTER])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--previous", help="compare with this tag instead of the one before TAG")
    args = parser.parse_args()
    previous = args.previous or previous_tag(args.tag)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sys.stdout.write(render(args.tag, previous, commits(args.tag, previous), repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
