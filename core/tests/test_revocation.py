import httpx
from fastapi.testclient import TestClient
from test_catalog import FILES, SHA, entry, fake_github, package_dir, repo_zip
from test_runs import AUTH, TOKEN
from test_skill_install import make_zip, skill_md

from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.skills.catalog import CatalogClient

GONE = [{"version": "1.0.0", "reason": "It overwrote files it should not have."}]
V2 = {
    "SKILL.md": skill_md(name="notes", version="1.1.0", tools=("files.read", "files.write")),
    "SETUP.md": "# Setup\n",
}


class Env:
    """An app whose catalog index can be changed between calls."""

    def __init__(self, tmp_path, index, archive):
        self.index = index
        skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "skills")
        self.transport = fake_github(index, archive)
        self.catalog = CatalogClient(transport=self.transport)
        self.client = TestClient(server.create_app(TOKEN, skills=skills, catalog=self.catalog))

    def install_from_catalog(self):
        staged = self.client.post("/api/catalog/install", json={"name": "notes", "commit": SHA}, headers=AUTH)
        body = {"token": staged.json()["token"], "approved": staged.json()["added"]}
        assert self.client.post("/api/skills/install", json=body, headers=AUTH).status_code == 201

    def check(self):
        return self.client.post("/api/catalog/check", headers=AUTH).json()

    def skill(self, name="notes"):
        return next(
            s for s in self.client.get("/api/skills", headers=AUTH).json()["skills"] if s["name"] == name
        )


def env(tmp_path, **over):
    return Env(tmp_path, {"schema": 2, "skills": [entry(tmp_path, **over)]}, repo_zip(FILES))


def test_a_withdrawn_version_is_switched_off_and_the_user_is_told(tmp_path):
    e = env(tmp_path)
    e.install_from_catalog()
    assert e.skill()["enabled"] is True
    assert e.check() == {"checked": True, "withdrawn": [], "updates": []}

    e.index["skills"][0]["revoked"] = GONE
    result = e.check()
    assert result["withdrawn"] == [
        {"name": "notes", "version": "1.0.0", "reason": GONE[0]["reason"], "acknowledged": False}
    ]
    assert e.skill()["enabled"] is False and e.skill()["withdrawn_reason"] == GONE[0]["reason"]

    e.client.post("/api/catalog/withdrawn/notes/acknowledge", headers=AUTH)
    assert e.check()["withdrawn"][0]["acknowledged"] is True


def test_the_user_can_switch_it_back_on_and_it_is_not_switched_off_again(tmp_path):
    e = env(tmp_path)
    e.install_from_catalog()
    e.index["skills"][0]["revoked"] = GONE
    e.check()
    assert e.client.put("/api/skills/notes", json={"enabled": True}, headers=AUTH).status_code in (200, 204)
    e.check()
    assert e.skill()["enabled"] is True  # the same withdrawn version is not acted on twice


def test_every_version_can_be_withdrawn_at_once(tmp_path):
    e = env(tmp_path)
    e.install_from_catalog()
    e.index["skills"][0]["revoked"] = [{"version": "*", "reason": "Gone for good."}]
    assert e.check()["withdrawn"][0]["reason"] == "Gone for good."


def test_a_skill_not_from_the_catalog_is_left_alone(tmp_path):
    e = env(tmp_path, revoked=GONE)
    zipped = make_zip({"notes/SKILL.md": FILES["SKILL.md"], "notes/SETUP.md": FILES["SETUP.md"]})
    staged = e.client.post(
        "/api/skills/install/preview", content=zipped, headers={**AUTH, "Content-Type": "application/zip"}
    ).json()
    body = {"token": staged["token"], "approved": staged["added"]}
    assert e.client.post("/api/skills/install", json=body, headers=AUTH).status_code == 201
    assert e.check()["withdrawn"] == [] and e.skill()["enabled"] is True


def test_an_update_is_reported_with_the_permissions_it_adds(tmp_path):
    e = env(tmp_path)
    e.install_from_catalog()
    e.index["skills"][0] = entry(
        tmp_path, version="1.1.0", permissions=["tool:files.read", "tool:files.write", "integration:google"]
    )
    update = e.check()["updates"][0]
    assert (update["name"], update["version"], update["installed_version"]) == ("notes", "1.1.0", "1.0.0")
    assert update["adds_permissions"] == ["integration:google"]  # files.write was approved at install


def test_a_withdrawn_newer_version_is_not_offered_as_an_update(tmp_path):
    e = env(tmp_path)
    e.install_from_catalog()
    e.index["skills"][0] = entry(tmp_path, version="1.1.0", revoked=[{"version": "1.1.0", "reason": "bad"}])
    assert e.check()["updates"] == []


def test_replacing_the_withdrawn_version_switches_the_skill_back_on(tmp_path):
    e = env(tmp_path)
    e.install_from_catalog()
    e.index["skills"][0]["revoked"] = GONE
    e.check()
    assert e.skill()["enabled"] is False

    fixed = entry(tmp_path, version="1.1.0", sha256=_hash_of(tmp_path, V2), revoked=GONE)
    e.index["skills"][0] = fixed
    e.transport.handler = fake_github(e.index, repo_zip(V2)).handler
    e.install_from_catalog()
    result = e.check()
    assert result["withdrawn"] == [] and e.skill()["version"] == "1.1.0" and e.skill()["enabled"] is True


def test_offline_keeps_what_we_knew_and_never_errors(tmp_path):
    e = env(tmp_path)
    e.install_from_catalog()
    e.index["skills"][0]["revoked"] = GONE
    e.check()
    e.catalog._transport = httpx.MockTransport(lambda request: httpx.Response(500))
    result = e.check()
    assert result["checked"] is False and result["updates"] == []
    assert [w["name"] for w in result["withdrawn"]] == ["notes"]


def _hash_of(tmp_path, files):
    from piyo.skills.install import package_hash

    return package_hash(package_dir(tmp_path, files))
