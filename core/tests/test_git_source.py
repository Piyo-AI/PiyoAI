import io
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN
from test_skill_install import skill_md

from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.skills.catalog import CatalogClient
from piyo.skills.git_source import GitChoice, parse_address, skill_folders, stage_git
from piyo.skills.install import InstallError, SkillInstaller

SHA = "b" * 40


def archive(layout: dict[str, str], top=f"repo-{SHA}") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, text in layout.items():
            zf.writestr(f"{top}/{path}", text)
    return buf.getvalue()


def github(data: bytes, seen=None, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if seen is not None:
            seen.append(url)
        if status != 200:
            return httpx.Response(status)
        if url.startswith("https://api.github.com/repos/owner/repo/commits/"):
            return httpx.Response(200, text=SHA)
        if url == f"https://codeload.github.com/owner/repo/zip/{SHA}":
            return httpx.Response(200, content=data)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def client_for(transport):
    return lambda repo: CatalogClient(repo, transport=transport)


@pytest.fixture
def installer(tmp_path):
    return SkillInstaller(tmp_path / "skills", tmp_path / "work", {"browse-web"})


# -- addresses -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/owner/repo", ("owner/repo", None, "")),
        ("https://github.com/owner/repo/", ("owner/repo", None, "")),
        ("https://github.com/owner/repo.git", ("owner/repo", None, "")),
        ("https://github.com/owner/repo/tree/v1.2", ("owner/repo", "v1.2", "")),
        ("https://github.com/owner/repo/tree/main/skills/notes", ("owner/repo", "main", "skills/notes")),
    ],
)
def test_addresses_are_understood(url, expected):
    a = parse_address(url)
    assert (a.repo, a.ref, a.path) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/repo",
        "https://gitlab.com/owner/repo",
        "https://evil.example/github.com/owner/repo",
        "https://github.com.evil.example/owner/repo",
        "https://user:pw@github.com/owner/repo",
        "https://github.com/owner",
        "https://github.com/owner/repo?token=1",
        "https://github.com/owner/repo/blob/main/x",
        "https://github.com/owner/repo/tree/main/../x",
        "https://github.com:444/owner/repo",
        "git@github.com:owner/repo.git",
        "file:///etc/passwd",
    ],
)
def test_other_addresses_are_refused(url):
    with pytest.raises(InstallError):
        parse_address(url)


def test_skill_folders_are_found_shallowest_first():
    data = archive({"SKILL.md": "x", "skills/a/SKILL.md": "x", "skills/b/SKILL.md": "x", "README.md": "x"})
    assert skill_folders(data) == ["", "skills/a", "skills/b"]
    with pytest.raises(InstallError, match="not a zip"):
        skill_folders(b"nope")


# -- staging ---------------------------------------------------------------------------------


async def test_a_repo_with_one_skill_at_the_root(installer):
    data = archive({"SKILL.md": skill_md(), "SETUP.md": "# s", "README.md": "readme", "docs/guide.md": "g"})
    seen: list[str] = []
    preview = await stage_git(client_for(github(data, seen)), installer, "https://github.com/owner/repo")
    assert preview.name == "notes" and preview.source == f"git github.com/owner/repo @ {SHA[:7]}"
    assert preview.verified is False and "docs/guide.md" in preview.files
    assert any(u.endswith(f"/zip/{SHA}") for u in seen)  # the exact commit was downloaded
    assert installer.commit(preview.token, preview.permissions).manifest.name == "notes"


async def test_a_repo_with_one_skill_in_a_folder_needs_no_choice(installer):
    data = archive({"README.md": "r", "tools/notes/SKILL.md": skill_md()})
    preview = await stage_git(client_for(github(data)), installer, "https://github.com/owner/repo")
    assert preview.source.endswith("(tools/notes)")


async def test_a_repo_with_several_skills_asks_which_then_stays_pinned(installer):
    data = archive(
        {
            "skills/notes/SKILL.md": skill_md(),
            "skills/other/SKILL.md": skill_md(name="other"),
            "README.md": "r",
        }
    )
    transport = github(data)
    choice = await stage_git(client_for(transport), installer, "https://github.com/owner/repo")
    assert (
        isinstance(choice, GitChoice)
        and choice.folders == ["skills/notes", "skills/other"]
        and choice.commit == SHA
    )

    seen: list[str] = []
    picked = await stage_git(
        client_for(github(data, seen)),
        installer,
        "https://github.com/owner/repo",
        "skills/other",
        choice.commit,
    )
    assert picked.name == "other" and not any(
        "api.github.com" in u for u in seen
    )  # no second lookup of the branch


async def test_a_folder_address_installs_that_folder(installer):
    data = archive({"skills/notes/SKILL.md": skill_md(), "skills/other/SKILL.md": skill_md(name="other")})
    preview = await stage_git(
        client_for(github(data)), installer, "https://github.com/owner/repo/tree/main/skills/other"
    )
    assert preview.name == "other"


async def test_a_branch_with_a_slash_is_found_by_trying_longer_refs(installer):
    data = archive({"skills/notes/SKILL.md": skill_md(), "skills/other/SKILL.md": skill_md(name="other")})
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        prefix = "https://api.github.com/repos/owner/repo/commits/"
        if url.startswith(prefix):
            asked.append(url.removeprefix(prefix))
            return httpx.Response(200, text=SHA) if url.endswith("/feature/x") else httpx.Response(422)
        if url == f"https://codeload.github.com/owner/repo/zip/{SHA}":
            return httpx.Response(200, content=data)
        return httpx.Response(404)

    preview = await stage_git(
        client_for(httpx.MockTransport(handler)),
        installer,
        "https://github.com/owner/repo/tree/feature/x/skills/other",
    )
    assert preview.name == "other" and asked == ["feature", "feature/x"]


async def test_an_unknown_ref_says_so(installer):
    from piyo.skills.catalog import CatalogNotFound

    transport = httpx.MockTransport(lambda request: httpx.Response(422))
    with pytest.raises(CatalogNotFound, match="branch, tag or commit"):
        await stage_git(client_for(transport), installer, "https://github.com/owner/repo/tree/nope/a/b")


async def test_failures_are_readable(installer, tmp_path):
    with pytest.raises(InstallError, match="no SKILL.md"):
        await stage_git(
            client_for(github(archive({"README.md": "r"}))), installer, "https://github.com/owner/repo"
        )
    with pytest.raises(InstallError, match="not a commit"):
        await stage_git(client_for(github(b"")), installer, "https://github.com/owner/repo", "x", "main")
    from piyo.skills.catalog import CatalogError

    with pytest.raises(CatalogError, match="public"):
        await stage_git(client_for(github(b"", status=404)), installer, "https://github.com/owner/repo")
    staging = tmp_path / "work" / "staging"
    assert not staging.exists() or not any(staging.iterdir())


async def test_a_repo_with_hostile_paths_is_refused(installer):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"repo-{SHA}/SKILL.md", skill_md())
        zf.writestr(f"repo-{SHA}/../../evil.txt", "x")
    with pytest.raises(InstallError, match="Unsafe"):
        await stage_git(client_for(github(buf.getvalue())), installer, "https://github.com/owner/repo")


# -- API -------------------------------------------------------------------------------------


def make_client(tmp_path, data):
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "skills")
    catalog = CatalogClient(transport=github(data))
    return TestClient(server.create_app(TOKEN, skills=skills, catalog=catalog))


def test_api_stage_a_git_skill_and_install_it(tmp_path):
    client = make_client(tmp_path, archive({"SKILL.md": skill_md(), "SETUP.md": "# s"}))
    res = client.post("/api/skills/install/git", json={"url": "https://github.com/owner/repo"}, headers=AUTH)
    assert res.status_code == 200 and res.json()["choose"] == []
    preview = res.json()["preview"]
    assert preview["source"].startswith("git github.com/owner/repo @ ") and preview["verified"] is False
    ok = client.post(
        "/api/skills/install",
        json={"token": preview["token"], "approved": preview["permissions"]},
        headers=AUTH,
    )
    assert ok.status_code == 201
    listed = client.get("/api/skills", headers=AUTH).json()["skills"][0]
    assert listed["install_source"].startswith("git github.com")


def test_api_choice_and_errors(tmp_path):
    client = make_client(
        tmp_path, archive({"skills/a/SKILL.md": skill_md(), "skills/b/SKILL.md": skill_md(name="b")})
    )
    res = client.post(
        "/api/skills/install/git", json={"url": "https://github.com/owner/repo"}, headers=AUTH
    ).json()
    assert res["preview"] is None and res["choose"] == ["skills/a", "skills/b"] and res["commit"] == SHA
    again = client.post(
        "/api/skills/install/git",
        json={"url": "https://github.com/owner/repo", "folder": "skills/b", "commit": res["commit"]},
        headers=AUTH,
    )
    assert again.json()["preview"]["name"] == "b"
    bad = client.post("/api/skills/install/git", json={"url": "https://gitlab.com/o/r"}, headers=AUTH)
    assert bad.status_code == 400 and "github.com" in bad.json()["detail"]


# -- private repositories ----------------------------------------------------------------------

TOKEN_VALUE = "ghp_" + "a" * 36


def private_github(data: bytes, seen: list):
    """Answers only a request that carries the token, like a private repository."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("authorization")))
        if request.headers.get("authorization") == "Bearer wrong":
            return httpx.Response(401)
        if request.headers.get("authorization") != f"Bearer {TOKEN_VALUE}":
            return httpx.Response(404)
        if request.url.host == "api.github.com":
            return httpx.Response(200, text=SHA)
        return httpx.Response(200, content=data)

    return httpx.MockTransport(handler)


async def test_a_private_repo_needs_the_token_and_it_goes_only_to_github_hosts(installer):
    from piyo.skills.catalog import CatalogError

    seen: list = []
    transport = private_github(archive({"SKILL.md": skill_md()}), seen)
    with pytest.raises(CatalogError, match="private one, add a GitHub access token"):
        await stage_git(client_for(transport), installer, "https://github.com/owner/repo")

    seen.clear()
    def with_token(repo):
        return CatalogClient(repo, transport=transport, verify_signature=False, token=TOKEN_VALUE)

    preview = await stage_git(with_token, installer, "https://github.com/owner/repo")
    assert preview.verified is False and preview.source.startswith("git github.com/owner/repo @ ")
    assert {host for host, _ in seen} == {"api.github.com", "codeload.github.com"}
    assert all(auth == f"Bearer {TOKEN_VALUE}" for _, auth in seen)
    assert TOKEN_VALUE not in preview.source


async def test_a_wrong_token_and_a_token_that_cannot_read_it_are_told_apart(installer):
    from piyo.skills.catalog import CatalogError

    transport = private_github(b"", [])
    def wrong(repo):
        return CatalogClient(repo, transport=transport, verify_signature=False, token="wrong")

    with pytest.raises(CatalogError, match="did not accept the access token"):
        await stage_git(wrong, installer, "https://github.com/owner/repo")
    def other(repo):
        return CatalogClient(repo, transport=transport, verify_signature=False, token="x" * 30)

    with pytest.raises(CatalogError, match="token cannot read it"):
        await stage_git(other, installer, "https://github.com/owner/repo")


async def test_the_token_is_never_sent_to_another_host():
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, content=b"x")

    from piyo.skills.catalog import CatalogError

    client = CatalogClient("owner/repo", transport=httpx.MockTransport(handler), token=TOKEN_VALUE)
    with pytest.raises(CatalogError, match="unexpected address"):
        await client._get("https://evil.example.com/x", 100)
    assert seen == []
    # the public catalog's own file host is allowed but gets no token
    headers: list = []

    def record(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("authorization"))
        return httpx.Response(200, content=b"x")

    client = CatalogClient("owner/repo", transport=httpx.MockTransport(record), token=TOKEN_VALUE)
    await client._get("https://raw.githubusercontent.com/owner/repo/main/index.json", 100)
    assert headers == [None]


def test_api_git_token_lifecycle_and_use(tmp_path):
    seen: list = []
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "skills")
    catalog = CatalogClient(transport=private_github(archive({"SKILL.md": skill_md()}), seen))
    client = TestClient(server.create_app(TOKEN, skills=skills, catalog=catalog))
    body = {"url": "https://github.com/owner/repo"}

    assert client.get("/api/git-token", headers=AUTH).json() == {"has_token": False}
    assert client.post("/api/skills/install/git", json=body, headers=AUTH).status_code == 502

    bad = client.put("/api/git-token", json={"key": "has a space in it, not a token"}, headers=AUTH)
    assert bad.status_code == 400 and TOKEN_VALUE not in bad.text
    assert client.put("/api/git-token", json={"key": f"  {TOKEN_VALUE}\n"}, headers=AUTH).status_code == 204
    status = client.get("/api/git-token", headers=AUTH)
    assert status.json() == {"has_token": True} and TOKEN_VALUE not in status.text

    res = client.post("/api/skills/install/git", json=body, headers=AUTH)
    assert res.status_code == 200 and TOKEN_VALUE not in res.text
    # the public catalog's own requests never carry it
    assert client.delete("/api/git-token", headers=AUTH).status_code == 204
    assert client.get("/api/git-token", headers=AUTH).json() == {"has_token": False}
