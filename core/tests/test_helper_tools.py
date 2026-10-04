"""uv and Deno are downloaded on first use, checked against a pinned hash, and never run unchecked."""

import asyncio
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from piyo.server import app as server
from piyo.skills import helper_tools
from piyo.skills.helper_tools import HelperError, HelperTools, exe_name, http_download
from piyo.skills.manifest import load_skill_dir
from piyo.skills.runner import ScriptError, ScriptRunner
from piyo.tools.scripts import script_tools

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def zip_with(tool: str, body: bytes = b"#!fake program") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(f"{tool}-release/{exe_name(tool)}", body)
        zf.writestr(f"{tool}-release/README.md", "not this one")
    return buffer.getvalue()


def tar_with(tool: str, body: bytes = b"#!fake program") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        info = tarfile.TarInfo(f"{tool}-release/{exe_name(tool)}")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


@pytest.fixture
def pinned(monkeypatch):
    """Pins for a fake release of both programs on every platform; none on the PATH or in the environment."""
    archives = {"uv": zip_with("uv"), "deno": zip_with("deno")}
    files = {
        key: {"file": "release.zip", "sha256": ""}
        for key in ("windows-x86_64", "linux-x86_64", "linux-aarch64", "macos-x86_64", "macos-aarch64")
    }
    pins = {
        tool: {
            "version": "1.0.0",
            "approx_mb": 3,
            "url": f"https://example.test/{tool}/{{version}}/{{file}}",
            "files": {k: {**v, "sha256": digest(archives[tool])} for k, v in files.items()},
        }
        for tool in archives
    }
    monkeypatch.setattr(helper_tools, "load_pins", lambda: pins)
    monkeypatch.setattr(helper_tools, "platform_key", lambda: "linux-x86_64")
    monkeypatch.setattr(helper_tools.shutil, "which", lambda _name: None)
    for name in ("PIYO_UV", "PIYO_DENO"):
        monkeypatch.delenv(name, raising=False)
    return archives, pins


def fetcher(archives, calls, data=None):
    async def fetch(url, target: Path, progress):
        calls.append(url)
        tool = "uv" if "/uv/" in url else "deno"
        body = data if data is not None else archives[tool]
        for i in range(0, len(body), 64):
            target.write_bytes(body[: i + 64])
            progress(min(i + 64, len(body)), len(body))
            await asyncio.sleep(0)

    return fetch


async def test_the_first_use_downloads_checks_and_unpacks(tmp_path, pinned):
    archives, _ = pinned
    calls = []
    helpers = HelperTools(tmp_path / "tools", fetcher(archives, calls))
    assert helpers.status("deno").state == "missing" and helpers.download_note("deno")
    path = await helpers.ensure("deno")
    assert Path(path) == tmp_path / "tools" / "deno-1.0.0" / exe_name("deno")
    assert Path(path).read_bytes() == b"#!fake program"  # the program, not the README beside it
    assert calls == ["https://example.test/deno/1.0.0/release.zip"]
    assert helpers.status("deno").state == "installed" and helpers.download_note("deno") == ""
    assert await helpers.ensure("deno") == path and len(calls) == 1  # not downloaded twice
    assert [p.name for p in (tmp_path / "tools").iterdir()] == ["deno-1.0.0"]  # no temporary files left


async def test_a_download_that_does_not_match_its_hash_is_deleted_and_never_used(tmp_path, pinned):
    archives, _ = pinned
    helpers = HelperTools(tmp_path / "tools", fetcher(archives, [], data=zip_with("deno", b"swapped")))
    with pytest.raises(HelperError, match="does not match"):
        await helpers.ensure("deno")
    assert list((tmp_path / "tools").iterdir()) == []
    status = helpers.status("deno")
    assert status.state == "failed" and "fingerprint" in status.message
    assert helpers.path("deno") is None


async def test_a_network_failure_has_a_readable_message(tmp_path, pinned):
    async def offline(url, target, progress):
        raise httpx.ConnectError("no route")

    helpers = HelperTools(tmp_path / "tools", offline)
    with pytest.raises(HelperError, match="internet connection"):
        await helpers.ensure("uv")
    assert list((tmp_path / "tools").iterdir()) == []


async def test_callers_that_arrive_together_share_one_download(tmp_path, pinned):
    archives, _ = pinned
    calls = []
    helpers = HelperTools(tmp_path / "tools", fetcher(archives, calls))
    paths = await asyncio.gather(*(helpers.ensure("uv") for _ in range(4)))
    assert len(set(paths)) == 1 and len(calls) == 1


async def test_progress_is_reported_while_it_downloads(tmp_path, pinned):
    archives, _ = pinned
    helpers = HelperTools(tmp_path / "tools", fetcher(archives, []))
    seen = set()
    task = asyncio.ensure_future(helpers.ensure("uv"))
    while not task.done():
        status = helpers.status("uv")
        if status.state == "installing":
            seen.add(status.percent)
        await asyncio.sleep(0)
    await task
    assert max(seen) >= 50 and helpers.status("uv").state == "installed"


async def test_a_program_the_user_has_is_used_and_nothing_is_downloaded(tmp_path, pinned, monkeypatch):
    calls = []
    helpers = HelperTools(tmp_path / "tools", fetcher(pinned[0], calls))
    monkeypatch.setattr(helper_tools.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert await helpers.ensure("deno") == "/usr/bin/deno" and calls == []
    mine = tmp_path / "my-deno"
    mine.write_text("x")
    monkeypatch.setenv("PIYO_DENO", str(mine))
    assert await helpers.ensure("deno") == str(mine)  # a path the user set beats the PATH


async def test_a_downloaded_copy_beats_the_path(tmp_path, pinned, monkeypatch):
    helpers = HelperTools(tmp_path / "tools", fetcher(pinned[0], []))
    await helpers.ensure("uv")
    monkeypatch.setattr(helper_tools.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert helpers.path("uv") == str(tmp_path / "tools" / "uv-1.0.0" / exe_name("uv"))


async def test_tar_archives_and_older_versions(tmp_path, pinned, monkeypatch):
    archives, pins = pinned
    old = tmp_path / "tools" / "uv-0.9.0"
    old.mkdir(parents=True)
    (old / exe_name("uv")).write_text("old")
    tar = tar_with("uv")
    for entry in pins["uv"]["files"].values():
        entry.update(file="release.tar.gz", sha256=hashlib.sha256(tar).hexdigest())
    helpers = HelperTools(tmp_path / "tools", fetcher(archives, [], data=tar))
    path = await helpers.ensure("uv")
    assert Path(path).read_bytes() == b"#!fake program"
    assert not old.exists()  # the version this one replaces is removed


async def test_an_archive_without_the_program_is_refused(tmp_path, pinned):
    archives, pins = pinned
    empty = zip_with("other")
    for entry in pins["deno"]["files"].values():
        entry["sha256"] = hashlib.sha256(empty).hexdigest()
    helpers = HelperTools(tmp_path / "tools", fetcher(archives, [], data=empty))
    with pytest.raises(HelperError, match="no .*deno"):
        await helpers.ensure("deno")


async def test_only_https_is_downloaded(tmp_path):
    with pytest.raises(HelperError, match="https"):
        await http_download("http://example.test/uv.zip", tmp_path / "x", lambda a, b: None)


def test_the_shipped_pins_cover_every_platform_with_a_hash_and_a_size():
    pins = helper_tools.load_pins()
    for tool in ("uv", "deno"):
        assert pins[tool]["url"].startswith("https://github.com/") and pins[tool]["approx_mb"] > 0
        assert set(pins[tool]["files"]) == {
            "windows-x86_64", "linux-x86_64", "linux-aarch64", "macos-x86_64", "macos-aarch64"
        }
        for entry in pins[tool]["files"].values():
            assert len(entry["sha256"]) == 64 and int(entry["sha256"], 16) >= 0


# -- through the script runner, the approval card and the API ---------------------------------------


def skill_with_script(tmp_path, name, script, code):
    folder = tmp_path / name
    (folder / "scripts").mkdir(parents=True)
    manifest = f"---\nname: {name}\ndescription: Demo.\n---\nUse it.\n"
    (folder / "SKILL.md").write_text(manifest, encoding="utf-8")
    (folder / "scripts" / script).write_text(code, encoding="utf-8")
    return load_skill_dir(folder, "user")


async def test_a_script_run_explains_a_failed_download(tmp_path, pinned):
    async def offline(url, target, progress):
        raise httpx.ConnectError("no route")

    skill = skill_with_script(tmp_path, "demo", "go.ts", "console.log('1')")
    helpers = HelperTools(tmp_path / "tools", offline)
    runner = ScriptRunner(tmp_path / "work", lambda _n: None, helpers=helpers)
    with pytest.raises(ScriptError, match="Could not download Deno"):
        await runner.run(skill, "go.ts", {})


def test_the_approval_card_says_when_the_run_will_download(tmp_path, pinned):
    skill = skill_with_script(tmp_path, "demo", "go.ts", "console.log('1')")
    runner = ScriptRunner(tmp_path / "work", lambda _n: None, helpers=HelperTools(tmp_path / "tools"))
    registry = type("R", (), {"get": lambda self, name: skill})()
    tool = script_tools(runner, registry)[0]
    text = tool.summary_of({"skill": "demo", "script": "go.ts", "args": {}})
    assert "downloads Deno 1.0.0 (about 3 MB) from github.com" in text
    py = tool.summary_of({"skill": "demo", "script": "go.py", "args": {}})
    assert "downloads uv 1.0.0" in py
    explicit = ScriptRunner(tmp_path / "work", lambda _n: None, deno="/bin/deno", helpers=runner.helpers)
    assert explicit.download_note(skill, "go.ts") == ""


def test_the_api_lists_and_starts_the_downloads(tmp_path, pinned):
    archives, _ = pinned
    helpers = HelperTools(tmp_path / "tools", fetcher(archives, []))
    with TestClient(server.create_app(TOKEN, helper_tools=helpers, start_scheduler=False)) as client:
        listed = {t["tool"]: t for t in client.get("/api/helper-tools", headers=AUTH).json()}
        assert set(listed) == {"uv", "deno"} and listed["uv"]["state"] == "missing"
        assert listed["uv"]["version"] == "1.0.0" and listed["uv"]["approx_mb"] == 3
        assert client.post("/api/helper-tools/rm/install", headers=AUTH).status_code == 404
        started = client.post("/api/helper-tools/uv/install", headers=AUTH).json()
        assert started["state"] in ("installing", "installed")
        for _ in range(100):
            if client.get("/api/helper-tools", headers=AUTH).json()[0]["state"] == "installed":
                break
            import time

            time.sleep(0.05)
        assert next(t for t in client.get("/api/helper-tools", headers=AUTH).json() if t["tool"] == "uv")[
            "state"
        ] == "installed"
        assert client.get("/api/helper-tools").status_code == 401

