"""The release body is written from commit subjects (scripts/release_notes.py)."""

import importlib.util
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_notes.py"
spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)


def test_groups_by_prefix_and_lists_body_bullets():
    entries = [
        ("security: ask before remembering web text", ["only after a run read outside text"]),
        ("fix: the updater showed no progress", []),
        ("Rollback remembers bad versions", []),
    ]
    text = notes.render("v0.2.0", "v0.1.5", entries, "Piyo-AI/PiyoAI")
    assert "### Security\n\n- Ask before remembering web text\n  - only after a run read outside text" in text
    assert "### Fixed\n\n- The updater showed no progress" in text
    assert "### Changes\n\n- Rollback remembers bad versions" in text
    assert text.index("### Security") < text.index("### Fixed") < text.index("### Changes")
    assert "compare/v0.1.5...v0.2.0" in text and "Unsigned preview build" in text


def test_a_single_group_has_no_heading_and_an_empty_release_says_so():
    one = notes.render("v0.2.0", None, [("Add a thing", [])])
    assert "###" not in one and "- Add a thing" in one
    assert "no user-facing changes" in notes.render("v0.2.0", "v0.1.5", [])


def test_previous_tag_uses_version_order_not_text_order(monkeypatch):
    monkeypatch.setattr(notes, "git", lambda *a: "v0.10.0\nv0.9.0\nv0.2.0\n")
    assert notes.previous_tag("v0.10.0") == "v0.9.0"
    assert notes.previous_tag("v0.2.0") is None


def test_reads_commits_between_tags(tmp_path, monkeypatch):
    def run(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       cwd=tmp_path, check=True, capture_output=True)

    def commit(message):
        run("commit", "--allow-empty", "-m", message)

    run("init", "-q")
    commit("First")
    run("tag", "v0.1.0")
    commit("Add search\n\n- searches names\n- searches tags")
    commit("Version 0.2.0")
    run("tag", "v0.2.0")
    monkeypatch.chdir(tmp_path)
    assert notes.previous_tag("v0.2.0") == "v0.1.0"
    assert notes.commits("v0.2.0", "v0.1.0") == [("Add search", ["searches names", "searches tags"])]


def test_a_wrapped_bullet_is_one_bullet():
    body = (
        "Some text\n\n- The signed catalog's badge is kept with a catalog install\n  and shown on the card.\n"
        "- A second one\n\nA closing paragraph\n  that is indented but not a bullet\n"
    )
    assert notes.bullets_of(body) == [
        "The signed catalog's badge is kept with a catalog install and shown on the card.",
        "A second one",
    ]


def test_housekeeping_commits_are_left_out():
    housekeeping = ("chore: bump a lock", "ci: fix it", "Docs: reword", "test(core): more", "Version 0.2.0")
    for subject in housekeeping:
        assert notes.SKIP.match(subject), subject
    for subject in ("Fix the updater", "fix: the updater", "feat: badges", "Documentation page for skills"):
        assert not notes.SKIP.match(subject), subject
