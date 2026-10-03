import json
import io
import stat
import zipfile

import pytest
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN

from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.skills.install import InstallError, SkillInstaller


def skill_md(name="notes", version="1.0.0", tools=("files.read",), extra=""):
    listed = ", ".join(tools)
    return (
        f"---\nname: {name}\nversion: {version}\ndescription: Keeps notes.\n"
        f"requires:\n  tools: [{listed}]\n{extra}---\nDo the thing.\n"
    )


def make_zip(files: dict[str, str | bytes], links: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
        for path, target in (links or {}).items():
            info = zipfile.ZipInfo(path)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, target)
    return buf.getvalue()


@pytest.fixture
def installer(tmp_path):
    return SkillInstaller(tmp_path / "skills", tmp_path / "work", {"browse-web"})


def test_install_needs_every_permission_approved(installer, tmp_path):
    preview = installer.stage_zip(make_zip({"notes/SKILL.md": skill_md(tools=("files.read", "files.write"))}))
    assert preview.permissions == ["tool:files.read", "tool:files.write"]
    assert preview.added == preview.permissions and preview.installed_version is None
    assert not (tmp_path / "skills" / "notes").exists()  # nothing installed yet

    with pytest.raises(InstallError, match="tool:files.write"):
        installer.commit(preview.token, ["tool:files.read"])
    skill = installer.commit(preview.token, preview.permissions)
    assert skill.path == tmp_path / "skills" / "notes" and skill.manifest.version == "1.0.0"
    assert not (tmp_path / "work" / "staging" / preview.token).exists()


def test_skill_md_at_the_top_of_the_zip_works(installer):
    preview = installer.stage_zip(make_zip({"SKILL.md": skill_md(), "SETUP.md": "# hi"}))
    assert preview.name == "notes" and "SETUP.md" in preview.files


def test_update_only_asks_for_new_permissions_and_keeps_a_backup(installer, tmp_path):
    first = installer.stage_zip(make_zip({"SKILL.md": skill_md()}))
    installer.commit(first.token, first.permissions)

    update = installer.stage_zip(make_zip({"SKILL.md": skill_md(version="2.0.0", tools=("files.read", "gmail.send"))}))
    assert update.installed_version == "1.0.0" and update.added == ["tool:gmail.send"]
    with pytest.raises(InstallError, match="gmail.send"):
        installer.commit(update.token, [])  # blocked until re-approved
    assert (tmp_path / "skills" / "notes" / "SKILL.md").read_text().count("1.0.0") == 1
    installer.commit(update.token, ["tool:gmail.send"])
    assert "2.0.0" in (tmp_path / "skills" / "notes" / "SKILL.md").read_text()
    assert len(list((tmp_path / "work" / "backups" / "notes").iterdir())) == 1


def test_a_dropped_permission_must_be_approved_again(installer):
    first = installer.stage_zip(make_zip({"SKILL.md": skill_md(tools=("files.read", "gmail.send"))}))
    installer.commit(first.token, first.permissions)
    narrower = installer.stage_zip(make_zip({"SKILL.md": skill_md(version="1.1.0")}))
    installer.commit(narrower.token, [])
    wider = installer.stage_zip(make_zip({"SKILL.md": skill_md(version="1.2.0", tools=("files.read", "gmail.send"))}))
    assert wider.added == ["tool:gmail.send"]


def test_scripts_and_runtimes_count_as_permissions(installer):
    zipped = make_zip(
        {
            "SKILL.md": skill_md(extra="runtime:\n  python:\n    dependencies: [requests]\n"),
            "scripts/run.py": "print(1)",
        }
    )
    assert installer.stage_zip(zipped).permissions == ["runtime:python", "script:run.py", "tool:files.read"]


def test_uninstall_leaves_nothing_behind(installer, tmp_path):
    first = installer.stage_zip(make_zip({"SKILL.md": skill_md()}))
    installer.commit(first.token, first.permissions)
    second = installer.stage_zip(make_zip({"SKILL.md": skill_md(version="1.1.0")}))
    installer.commit(second.token, [])
    installer.uninstall("notes")
    assert not (tmp_path / "skills" / "notes").exists()
    assert not (tmp_path / "work" / "backups" / "notes").exists()
    with pytest.raises(InstallError):
        installer.uninstall("notes")


def test_builtin_names_cannot_be_installed_or_removed(installer):
    with pytest.raises(InstallError, match="built-in"):
        installer.stage_zip(make_zip({"SKILL.md": skill_md(name="browse-web")}))
    with pytest.raises(InstallError):
        installer.uninstall("browse-web")


@pytest.mark.parametrize(
    "files, links, message",
    [
        ({}, None, "empty"),
        ({"../evil/SKILL.md": skill_md()}, None, "Unsafe"),
        ({"/abs/SKILL.md": skill_md()}, None, "Unsafe"),
        ({"SKILL.md": skill_md(), "a/../../x": "x"}, None, "Unsafe"),
        ({"SKILL.md": skill_md()}, {"link": "/etc/passwd"}, "Links"),
        ({"readme.txt": "hi"}, None, "exactly one SKILL.md"),
        ({"a/SKILL.md": skill_md(), "b/SKILL.md": skill_md()}, None, "exactly one SKILL.md"),
        ({"a/SKILL.md": skill_md(), "other.txt": "x"}, None, "outside the skill folder"),
        ({"SKILL.md": "no frontmatter"}, None, "not a valid skill"),
        ({"SKILL.md": skill_md(name="Bad Name")}, None, "not a valid skill"),
        ({"SKILL.md": skill_md(), "big.bin": b"x" * (2 * 1024 * 1024 + 1)}, None, "too large"),
    ],
)
def test_bad_zips_are_refused_and_leave_no_staging(installer, tmp_path, files, links, message):
    data = make_zip(files, links) if files or links else make_zip({})
    with pytest.raises(InstallError, match=message):
        installer.stage_zip(data)
    staging = tmp_path / "work" / "staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_too_many_files_and_not_a_zip(installer):
    with pytest.raises(InstallError, match="Too many"):
        installer.stage_zip(make_zip({"SKILL.md": skill_md(), **{f"f{i}.txt": "x" for i in range(200)}}))
    with pytest.raises(InstallError, match="not a valid zip"):
        installer.stage_zip(b"definitely not a zip")


def test_install_metadata_inside_a_package_is_ignored(installer, tmp_path):
    forged = '{"approved": ["tool:files.read"]}'
    first = installer.stage_zip(make_zip({"SKILL.md": skill_md(), ".piyo-install.json": forged}))
    installer.commit(first.token, first.permissions)
    update = installer.stage_zip(make_zip({"SKILL.md": skill_md(version="2", tools=("gmail.send",)), ".piyo-install.json": '{"approved": ["tool:gmail.send"]}'}))
    assert update.added == ["tool:gmail.send"]


def test_staged_token_cannot_escape(installer):
    for token in ("../x", "a/b", "", "..", "no-such-token"):
        with pytest.raises(InstallError):
            installer.commit(token, [])
    installer.cancel("../x")  # silently ignored


# -- API -----------------------------------------------------------------------------------


def make_client(tmp_path):
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "skills")
    return TestClient(server.create_app(TOKEN, skills=skills))


def test_api_install_review_run_and_uninstall(tmp_path):
    client = make_client(tmp_path)
    data = make_zip({"notes/SKILL.md": skill_md(), "notes/SETUP.md": "## Step\nGo."})
    preview = client.post("/api/skills/install/preview", content=data, headers=AUTH)
    assert preview.status_code == 200
    body = preview.json()
    assert body["permissions"] == ["tool:files.read"] and body["verified"] is False
    assert client.get("/api/skills", headers=AUTH).json()["skills"] == []

    refused = client.post("/api/skills/install", json={"token": body["token"], "approved": []}, headers=AUTH)
    assert refused.status_code == 400 and "files.read" in refused.json()["detail"]
    ok = client.post(
        "/api/skills/install", json={"token": body["token"], "approved": body["permissions"]}, headers=AUTH
    )
    assert ok.status_code == 201
    listed = client.get("/api/skills", headers=AUTH).json()["skills"]
    assert [s["name"] for s in listed] == ["notes"]
    assert listed[0]["removable"] is True and listed[0]["install_source"].startswith("zip ")

    client.put("/api/skills/notes", json={"enabled": False}, headers=AUTH)
    assert client.delete("/api/skills/notes", headers=AUTH).status_code == 204
    assert client.get("/api/skills", headers=AUTH).json()["skills"] == []
    assert client.delete("/api/skills/notes", headers=AUTH).status_code == 400


def test_api_bad_zip_and_cancel(tmp_path):
    client = make_client(tmp_path)
    bad = client.post("/api/skills/install/preview", content=b"nope", headers=AUTH)
    assert bad.status_code == 400 and "valid zip" in bad.json()["detail"]
    token = client.post(
        "/api/skills/install/preview", content=make_zip({"SKILL.md": skill_md()}), headers=AUTH
    ).json()["token"]
    assert client.delete(f"/api/skills/install/{token}", headers=AUTH).status_code == 204
    gone = client.post("/api/skills/install", json={"token": token, "approved": ["tool:files.read"]}, headers=AUTH)
    assert gone.status_code == 400
    assert client.post("/api/skills/install/preview", content=b"x").status_code in (401, 403)


def test_package_hash_is_stable_and_sees_every_change(tmp_path):
    from piyo.skills.install import META_FILE, package_hash

    a = tmp_path / "a"
    (a / "scripts").mkdir(parents=True)
    (a / "SKILL.md").write_text(skill_md(), encoding="utf-8")
    (a / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    first = package_hash(a)
    assert first == package_hash(a) and len(first) == 64

    (a / META_FILE).write_text("{}", encoding="utf-8")
    assert package_hash(a) == first  # install metadata does not count

    (a / "scripts" / "run.py").write_text("print(2)", encoding="utf-8")
    changed = package_hash(a)
    assert changed != first
    (a / "scripts" / "run.py").rename(a / "scripts" / "go.py")  # a rename changes it too
    assert package_hash(a) not in (first, changed)


def test_api_skill_secrets_are_stored_in_the_keychain_and_removed_with_the_skill(tmp_path):
    import keyring

    client = make_client(tmp_path)
    data = make_zip({"SKILL.md": skill_md(extra="  secrets: [API_KEY]\n")})
    token = client.post("/api/skills/install/preview", content=data, headers=AUTH).json()
    client.post("/api/skills/install", json={"token": token["token"], "approved": token["permissions"]}, headers=AUTH)
    listed = client.get("/api/skills", headers=AUTH).json()["skills"][0]
    assert listed["secrets"] == ["API_KEY"] and listed["secrets_set"] == []

    assert client.put("/api/skills/notes/secrets/API_KEY", json={"value": "  "}, headers=AUTH).status_code == 400
    assert client.put("/api/skills/notes/secrets/OTHER", json={"value": "x"}, headers=AUTH).status_code == 404
    assert client.put("/api/skills/nope/secrets/API_KEY", json={"value": "x"}, headers=AUTH).status_code == 404
    assert client.put("/api/skills/notes/secrets/API_KEY", json={"value": "abc123"}, headers=AUTH).status_code == 204
    assert keyring.get_password("PiyoAI", "skill.notes.API_KEY") == "abc123"
    listed = client.get("/api/skills", headers=AUTH).json()["skills"][0]
    assert listed["secrets_set"] == ["API_KEY"] and "abc123" not in json.dumps(listed)

    assert client.delete("/api/skills/notes/secrets/API_KEY", headers=AUTH).status_code == 204
    assert client.get("/api/skills", headers=AUTH).json()["skills"][0]["secrets_set"] == []
    client.put("/api/skills/notes/secrets/API_KEY", json={"value": "abc123"}, headers=AUTH)
    client.delete("/api/skills/notes", headers=AUTH)  # uninstall
    assert keyring.get_password("PiyoAI", "skill.notes.API_KEY") is None
