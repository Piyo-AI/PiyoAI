import pytest
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN
from test_skill_install import make_zip, skill_md

from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.skills.editor import TEMPLATE, SkillEditor
from piyo.skills.install import InstallError, SkillInstaller


@pytest.fixture
def parts(tmp_path):
    user, work = tmp_path / "skills", tmp_path / "work"
    return SkillEditor(user, work, {"browse-web"}), SkillInstaller(user, work, {"browse-web"}), user, work


def written(editor, **kw):
    text = skill_md(**kw)
    check = editor.check(text)
    return editor.create(text, "# Setup\n", check.added or [])


def test_check_gives_live_feedback(parts):
    editor, *_ = parts
    good = editor.check(skill_md(tools=("files.read", "gmail.send")))
    assert good.ok and good.name == "notes" and good.permissions == ["tool:files.read", "tool:gmail.send"]
    assert good.added == good.permissions
    assert not editor.check("no frontmatter").ok and "frontmatter" in editor.check("no frontmatter").error
    assert "name" in editor.check(skill_md(name="Bad Name")).error
    assert editor.check(skill_md(), name="other").error.startswith("The name must stay 'other'")
    assert not editor.check(123).ok and not editor.check("x" * 200_000).ok


def test_the_template_is_valid_once_given_a_name(parts):
    editor, *_ = parts
    check = editor.check(TEMPLATE.format(name="my-skill", title="My skill"))
    assert check.ok and check.name == "my-skill" and check.permissions == []


def test_create_needs_approval_and_refuses_clashes(parts):
    editor, _, user, _ = parts
    text = skill_md(tools=("files.read",))
    with pytest.raises(InstallError, match="files.read"):
        editor.create(text, "", [])
    skill = editor.create(text, "# Setup", ["tool:files.read"])
    assert skill.path == user / "notes" and (user / "notes" / "SETUP.md").read_text() == "# Setup"
    with pytest.raises(InstallError, match="already exists"):
        editor.create(text, "", ["tool:files.read"])
    with pytest.raises(InstallError, match="already exists"):
        editor.create(skill_md(name="browse-web"), "", [])


def test_save_keeps_the_old_version_and_adding_a_permission_needs_approval(parts):
    editor, _, user, _ = parts
    written(editor, tools=("files.read",))
    edited = skill_md(version="1.1.0", tools=("files.read",))
    editor.save("notes", edited, "", [])
    assert "1.1.0" in (user / "notes" / "SKILL.md").read_text() and not (user / "notes" / "SETUP.md").exists()

    wider = skill_md(version="1.2.0", tools=("files.read", "gmail.send"))
    check = editor.check(wider, "notes")
    assert check.added == ["tool:gmail.send"]
    with pytest.raises(InstallError, match="gmail.send"):
        editor.save("notes", wider, "", [])
    assert "1.2.0" not in (user / "notes" / "SKILL.md").read_text()  # nothing changed
    editor.save("notes", wider, "", ["tool:gmail.send"])
    assert [v.version for v in editor.history("notes")] == ["1.1.0", "1.0.0"]
    assert {v.reason for v in editor.history("notes")} == {"edit"}


def test_save_cannot_rename_or_touch_builtin_or_missing_skills(parts):
    editor, _, user, _ = parts
    written(editor)
    with pytest.raises(InstallError, match="must stay"):
        editor.save("notes", skill_md(name="other"), "", [])
    with pytest.raises(InstallError, match="no skill called"):
        editor.save("ghost", skill_md(name="ghost"), "", [])
    for bad in ("browse-web", "../x", "a/b", ".hidden"):
        with pytest.raises(InstallError):
            editor.read(bad)


def test_rollback_restores_files_keeps_the_current_version_and_checks_permissions(parts):
    editor, _, user, _ = parts
    written(editor, tools=("files.read",))
    editor.save("notes", skill_md(version="2.0.0", tools=("files.read", "gmail.send")), "", ["tool:gmail.send"])
    editor.save("notes", skill_md(version="3.0.0", tools=("files.read",)), "", [])
    oldest = editor.history("notes")[-1]
    assert oldest.version == "1.0.0"

    skill = editor.rollback("notes", oldest.id, [])
    assert skill.manifest.version == "1.0.0" and (user / "notes" / "SETUP.md").read_text() == "# Setup\n"
    assert not (user / "notes" / ".piyo-backup.json").exists()
    assert editor.history("notes")[0].reason == "rollback"  # the version we left is kept: rollback can be undone
    assert editor.history("notes")[0].version == "3.0.0"

    # 2.0.0 had gmail.send, which the current approved list no longer has: it needs approving again
    wider = next(v for v in editor.history("notes") if v.version == "2.0.0")
    with pytest.raises(InstallError, match="gmail.send"):
        editor.rollback("notes", wider.id, [])
    assert editor.rollback("notes", wider.id, ["tool:gmail.send"]).manifest.version == "2.0.0"
    for bad in ("../x", "nope", ""):
        with pytest.raises(InstallError):
            editor.rollback("notes", bad, [])


def test_installed_skills_can_be_edited_and_rolled_back_to_the_installed_version(parts):
    editor, installer, user, _ = parts
    preview = installer.stage_zip(make_zip({"SKILL.md": skill_md(), "scripts/run.py": "print(1)"}))
    installer.commit(preview.token, preview.permissions)
    editor.save("notes", skill_md(version="1.0.1"), "", [])
    assert (user / "notes" / "scripts" / "run.py").exists()  # scripts are left alone
    restored = editor.rollback("notes", editor.history("notes")[0].id, [])
    assert restored.manifest.version == "1.0.0" and (user / "notes" / "scripts" / "run.py").exists()


def test_history_is_trimmed(parts, monkeypatch):
    from piyo.skills import editor as module

    monkeypatch.setattr(module, "KEEP_VERSIONS", 3)
    editor, *_ = parts
    written(editor)
    for i in range(6):
        editor.save("notes", skill_md(version=f"1.0.{i + 1}"), "", [])
    assert len(editor.history("notes")) == 3


# -- API -------------------------------------------------------------------------------------


def make_client(tmp_path):
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "skills")
    return TestClient(server.create_app(TOKEN, skills=skills))


def test_api_write_edit_history_rollback(tmp_path):
    client = make_client(tmp_path)
    template = client.get("/api/skills-template", headers=AUTH).json()["skill_md"]
    assert client.post("/api/skills/check", json={"skill_md": template}, headers=AUTH).json()["ok"] is True
    bad = client.post("/api/skills/check", json={"skill_md": "nope"}, headers=AUTH).json()
    assert bad["ok"] is False and "frontmatter" in bad["error"]

    body = {"skill_md": skill_md(tools=("files.read",)), "setup_md": "# S", "approved": []}
    assert client.post("/api/skills", json=body, headers=AUTH).status_code == 400
    assert client.post("/api/skills", json={**body, "approved": ["tool:files.read"]}, headers=AUTH).status_code == 201
    listed = client.get("/api/skills", headers=AUTH).json()["skills"][0]
    assert listed["name"] == "notes" and listed["removable"] is True

    files = client.get("/api/skills/notes/files", headers=AUTH).json()
    assert files["setup_md"] == "# S"
    edit = {"skill_md": skill_md(version="2.0.0", tools=("files.read",)), "setup_md": "# S2"}
    assert client.put("/api/skills/notes/files", json=edit, headers=AUTH).json()["version"] == "2.0.0"
    history = client.get("/api/skills/notes/history", headers=AUTH).json()
    assert [h["version"] for h in history] == ["1.0.0"]
    back = client.post("/api/skills/notes/rollback", json={"id": history[0]["id"]}, headers=AUTH)
    assert back.status_code == 200 and back.json()["version"] == "1.0.0"
    assert client.get("/api/skills", headers=AUTH).json()["skills"][0]["version"] == "1.0.0"
    assert client.get("/api/skills/ghost/files", headers=AUTH).status_code == 400
    assert client.post("/api/skills/notes/rollback", json={"id": "zzz"}, headers=AUTH).status_code == 400
