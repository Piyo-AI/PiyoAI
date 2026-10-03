import pytest

from piyo.skills import SkillError, SkillRegistry, parse_skill_md

GOOD = """---
name: gmail-triage
version: 1.0
description: Summarize unread Gmail. Use when the user asks about email.
requires:
  tools: [gmail.read, gmail.draft]
unknown_key: ignored
---
# Gmail triage
Do the thing.
"""


def make_skill(root, name, text):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


def test_parse_valid():
    manifest, body = parse_skill_md(GOOD)
    assert manifest.name == "gmail-triage"
    assert manifest.version == "1.0"
    assert manifest.requires.tools == ["gmail.read", "gmail.draft"]
    assert body.startswith("# Gmail triage")


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter",
        "---\nname: Bad_Name\ndescription: x\n---\n",
        "---\nname: ok\n---\n",  # description required
        "---\n- a\n- list\n---\n",
        "---\nname: [unclosed\n---\n",
    ],
)
def test_parse_rejects(text):
    with pytest.raises(SkillError):
        parse_skill_md(text)


def test_registry_loads_and_reports_errors(tmp_path):
    user = tmp_path / "user"
    make_skill(user, "gmail-triage", GOOD)
    make_skill(user, "broken", "---\nname: broken\n---\n")
    make_skill(user, "wrong-folder", GOOD)  # name doesn't match folder
    reg = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=user)
    assert [s.manifest.name for s in reg.list()] == ["gmail-triage"]
    assert set(reg.errors) == {"user/broken", "user/wrong-folder"}


def test_builtin_wins_name_clash(tmp_path):
    make_skill(tmp_path / "b", "gmail-triage", GOOD)
    make_skill(tmp_path / "u", "gmail-triage", GOOD.replace("Do the thing", "Evil"))
    reg = SkillRegistry(builtin_dir=tmp_path / "b", user_dir=tmp_path / "u")
    assert reg.get("gmail-triage").source == "builtin"
    assert "user/gmail-triage" in reg.errors


def test_disabled_skills_hidden_from_catalog(tmp_path):
    make_skill(tmp_path / "u", "gmail-triage", GOOD)
    reg = SkillRegistry(
        builtin_dir=tmp_path / "x", user_dir=tmp_path / "u", disabled={"gmail-triage"}
    )
    assert reg.get("gmail-triage") is None
    assert reg.catalog_prompt() == ""


def test_catalog_has_only_name_and_description(tmp_path):
    make_skill(tmp_path / "u", "gmail-triage", GOOD)
    prompt = SkillRegistry(builtin_dir=tmp_path / "x", user_dir=tmp_path / "u").catalog_prompt()
    assert "gmail-triage: Summarize unread Gmail" in prompt
    assert "Do the thing" not in prompt
