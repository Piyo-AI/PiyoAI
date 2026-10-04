"""Folders too broad to approve, and files the file tools never touch (config/protected.py)."""

import json
import sys
from pathlib import Path

import pytest

from piyo.config import data_dir
from piyo.config.folders import ApprovedFolders, FolderGrant
from piyo.config.protected import broad_reason, protected_reason
from piyo.skills import SkillRegistry
from piyo.tools.base import RunContext
from piyo.tools.files import FileAccessError, file_tools

HOME = Path.home().resolve()
SYSTEM = Path("C:/Windows") if sys.platform == "win32" else Path("/etc")


def test_a_whole_drive_the_home_folder_and_system_folders_are_too_broad():
    assert "whole drive" in broad_reason(Path(HOME.anchor))
    assert "home folder" in broad_reason(HOME)
    assert "home folder" in broad_reason(HOME.parent)  # C:\Users or /home
    assert "system folder" in broad_reason(SYSTEM)
    assert "system folder" in broad_reason(SYSTEM / ("System32" if sys.platform == "win32" else "ssl"))


def test_ordinary_folders_are_fine_including_under_the_home_folder():
    assert broad_reason(HOME / "Documents") is None
    assert broad_reason(HOME / "projects" / "piyo") is None


@pytest.mark.parametrize(
    "parts",
    [
        (".ssh", "id_ed25519"), ("proj", ".env"), ("proj", "prod.env"), ("certs", "site.pem"),
        (".aws", "config"),
        ("a", "id_rsa.pub"), ("Google", "Chrome", "User Data", "Default", "Login Data"),
        ("Mozilla", "Firefox", "Profiles", "x.default", "logins.json"), ("keys", "vault.kdbx"),
    ],
)
def test_keys_credentials_and_browser_data_are_protected(tmp_path, parts):
    assert protected_reason(tmp_path.joinpath(*parts))


@pytest.mark.parametrize("parts", [("notes.txt",), ("proj", "environment.md"), ("pemberton", "report.docx")])
def test_ordinary_files_are_not(tmp_path, parts):
    assert protected_reason(tmp_path.joinpath(*parts)) is None


def test_pieces_own_data_is_protected(tmp_path):
    assert protected_reason(data_dir() / "piyo.db")
    assert protected_reason(tmp_path / "docs" / "piyo.db") is None


def test_approving_a_broad_folder_is_refused_with_a_reason(tmp_path):
    folders = ApprovedFolders(tmp_path / "folders.json")
    with pytest.raises(ValueError, match="home folder"):
        folders.set([FolderGrant(HOME)])
    ok = tmp_path / "docs"
    ok.mkdir()
    assert folders.set([FolderGrant(ok)])[0].path == ok.resolve()


@pytest.fixture
def tools(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    folders = ApprovedFolders(tmp_path / "folders.json")
    folders.set([FolderGrant(root, auto_changes=True)])
    ctx = RunContext(skills=SkillRegistry(builtin_dir=tmp_path / "a", user_dir=tmp_path / "b"))
    return root, {t.name: t for t in file_tools(folders)}, ctx, folders


async def test_protected_files_in_an_approved_folder_cannot_be_read_written_or_moved(tools):
    root, t, ctx, _ = tools
    (root / ".env").write_text("TOKEN=abc")
    (root / "notes.txt").write_text("hello")
    for name, args in [
        ("files.read", {"path": str(root / ".env")}),
        ("files.write", {"path": str(root / "new.pem"), "content": "x"}),
        ("files.move", {"source": str(root / ".env"), "destination": str(root / "copy.txt")}),
        ("files.move", {"source": str(root / "notes.txt"), "destination": str(root / "id_rsa")}),
        ("files.delete", {"path": str(root / ".env")}),
    ]:
        with pytest.raises(FileAccessError, match="does not touch"):
            await t[name].handler(args, ctx)
    assert (root / ".env").read_text() == "TOKEN=abc" and not (root / "new.pem").exists()


async def test_listing_hides_protected_items_and_says_so(tools):
    root, t, ctx, _ = tools
    (root / ".env").write_text("x")
    (root / ".ssh").mkdir()
    (root / "notes.txt").write_text("hello")
    out = await t["files.list"].handler({"path": str(root)}, ctx)
    assert "notes.txt" in out and ".env" not in out and ".ssh" not in out
    assert "2 protected item(s) not shown" in out


async def test_a_folder_approved_before_the_check_existed_is_ignored(tools):
    root, t, ctx, folders = tools
    folders.path.write_text(json.dumps([{"path": str(HOME), "auto_changes": True}]), encoding="utf-8")
    with pytest.raises(FileAccessError, match="No folders are approved"):
        await t["files.list"].handler({"path": str(HOME)}, ctx)
