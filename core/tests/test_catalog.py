import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN
from test_skill_install import skill_md

from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.skills.catalog import CatalogClient, CatalogError
from piyo.skills.install import SkillInstaller, package_hash

SHA = "a" * 40
FILES = {"SKILL.md": skill_md(name="notes", tools=("files.read", "files.write")), "SETUP.md": "# Setup\n"}


def package_dir(tmp_path, files):
    d = tmp_path / "pkg"
    d.mkdir(exist_ok=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8", newline="\n")
    return d


def repo_zip(files, extra=None, top=f"piyo-skills-{SHA}"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{top}/README.md", "catalog")
        for name, text in files.items():
            zf.writestr(f"{top}/skills/notes/{name}", text)
        for name, text in (extra or {}).items():
            zf.writestr(f"{top}/{name}", text)
    return buf.getvalue()


def entry(tmp_path, **over):
    base = {
        "name": "notes",
        "version": "1.0.0",
        "description": "Keeps notes.",
        "path": "skills/notes",
        "sha256": package_hash(package_dir(tmp_path, FILES)),
        "permissions": ["tool:files.read", "tool:files.write"],
    }
    return {**base, **over}


def fake_github(index, archive, seen=None, sha=SHA, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if status != 200:
            return httpx.Response(status)
        url = str(request.url)
        if url.startswith("https://api.github.com/repos/Piyo-AI/piyo-skills/commits/"):
            return httpx.Response(200, text=sha)
        if url.endswith("/index.json"):
            return httpx.Response(200, content=json.dumps(index))
        if url.startswith("https://codeload.github.com/Piyo-AI/piyo-skills/zip/"):
            return httpx.Response(200, content=archive)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def installer(tmp_path):
    return SkillInstaller(tmp_path / "skills", tmp_path / "work", {"browse-web"})


async def test_fetch_pins_one_commit_and_skips_unreadable_entries(tmp_path):
    seen: list[str] = []
    index = {"schema": 1, "skills": [entry(tmp_path), {"name": "broken"}]}
    client = CatalogClient(transport=fake_github(index, b"", seen))
    catalog = await client.fetch()
    assert catalog.commit == SHA and [e.name for e in catalog.skills] == ["notes"] and catalog.skipped == 1
    assert any(f"/Piyo-AI/piyo-skills/{SHA}/index.json" in u for u in seen)  # read at the commit, not at main


async def test_newer_schema_and_errors_have_readable_messages(tmp_path):
    with pytest.raises(CatalogError, match="newer version"):
        await CatalogClient(transport=fake_github({"schema": 2, "skills": []}, b"")).fetch()
    with pytest.raises(CatalogError, match="public"):
        await CatalogClient(transport=fake_github({}, b"", status=404)).fetch()
    with pytest.raises(CatalogError, match="limiting"):
        await CatalogClient(transport=fake_github({}, b"", status=403)).fetch()
    with pytest.raises(CatalogError, match="not a commit"):
        await CatalogClient(transport=fake_github({}, b"")).fetch("main")


async def test_stage_checks_the_hash_then_the_normal_review_applies(tmp_path, installer):
    index = {"schema": 1, "skills": [entry(tmp_path)]}
    client = CatalogClient(transport=fake_github(index, repo_zip(FILES)))
    preview = await client.stage(installer, "notes", SHA)
    assert preview.source == f"catalog Piyo-AI/piyo-skills @ {SHA[:7]}"
    assert preview.added == ["tool:files.read", "tool:files.write"] and preview.verified is False
    skill = installer.commit(preview.token, preview.added)
    assert skill.manifest.name == "notes"
    assert json.loads((skill.path / ".piyo-install.json").read_text())["source"] == preview.source


async def test_a_tampered_package_is_refused_and_leaves_nothing(tmp_path, installer):
    index = {"schema": 1, "skills": [entry(tmp_path)]}
    tampered = repo_zip({**FILES, "SKILL.md": FILES["SKILL.md"] + "extra instruction\n"})
    client = CatalogClient(transport=fake_github(index, tampered))
    with pytest.raises(Exception, match="fingerprint"):
        await client.stage(installer, "notes", SHA)
    staging = tmp_path / "work" / "staging"
    assert not staging.exists() or not any(staging.iterdir())


async def test_listing_and_package_must_agree_on_name_and_version(tmp_path, installer):
    index = {"schema": 1, "skills": [entry(tmp_path, version="9.9.9")]}
    client = CatalogClient(transport=fake_github(index, repo_zip(FILES)))
    with pytest.raises(Exception, match="does not match what the catalog lists"):
        await client.stage(installer, "notes", SHA)
    staging = tmp_path / "work" / "staging"
    assert not any(staging.iterdir())


async def test_unknown_skill_and_missing_folder(tmp_path, installer):
    index = {"schema": 1, "skills": [entry(tmp_path)]}
    client = CatalogClient(transport=fake_github(index, repo_zip(FILES)))
    with pytest.raises(Exception, match="no skill called"):
        await client.stage(installer, "nope", SHA)
    gone = CatalogClient(transport=fake_github(index, repo_zip({}, extra={"other/x.txt": "x"})))
    with pytest.raises(Exception, match="not found in the download"):
        await gone.stage(installer, "notes", SHA)


async def test_only_github_hosts_over_https(tmp_path):
    client = CatalogClient(transport=fake_github({}, b""))
    for url in ("http://api.github.com/x", "https://evil.example/x", "https://127.0.0.1/x"):
        with pytest.raises(CatalogError, match="unexpected address"):
            await client._get(url, 10)


async def test_oversized_download_is_stopped(tmp_path):
    client = CatalogClient(transport=fake_github({}, b"x" * 5000))
    with pytest.raises(CatalogError, match="larger than expected"):
        await client._get(f"https://codeload.github.com/Piyo-AI/piyo-skills/zip/{SHA}", 1000)


def test_subpath_cannot_escape_the_archive(tmp_path, installer):
    for bad in ("../x", "skills/../../x", "/abs"):
        with pytest.raises(Exception, match="Unsafe skill path"):
            installer.stage_zip(repo_zip(FILES), subpath=bad)


# -- API -----------------------------------------------------------------------------------


def make_client(tmp_path, index, archive):
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "skills")
    catalog = CatalogClient(transport=fake_github(index, archive))
    return TestClient(server.create_app(TOKEN, skills=skills, catalog=catalog))


def test_api_browse_stage_install_and_see_it_installed(tmp_path):
    client = make_client(tmp_path, {"schema": 1, "skills": [entry(tmp_path)]}, repo_zip(FILES))
    listing = client.get("/api/catalog", headers=AUTH).json()
    assert listing["commit"] == SHA and listing["skills"][0]["installed_version"] is None

    staged = client.post("/api/catalog/install", json={"name": "notes", "commit": SHA}, headers=AUTH)
    assert staged.status_code == 200 and staged.json()["verified"] is False
    token, perms = staged.json()["token"], staged.json()["added"]
    refused = client.post("/api/skills/install", json={"token": token, "approved": []}, headers=AUTH)
    assert refused.status_code == 400
    assert client.post("/api/skills/install", json={"token": token, "approved": perms}, headers=AUTH).status_code == 201

    after = client.get("/api/catalog", headers=AUTH).json()["skills"][0]
    assert after["installed_version"] == "1.0.0"


def test_api_catalog_errors_are_502_with_a_message(tmp_path):
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "skills")
    down = CatalogClient(transport=fake_github({}, b"", status=404))
    client = TestClient(server.create_app(TOKEN, skills=skills, catalog=down))
    res = client.get("/api/catalog", headers=AUTH)
    assert res.status_code == 502 and "public" in res.json()["detail"]
    bad = client.post("/api/catalog/install", json={"name": "notes", "commit": "main"}, headers=AUTH)
    assert bad.status_code == 502
