import sys
from pathlib import Path

from piyo import runtime
from piyo.skills.registry import default_builtin_dir
from piyo.skills.runner import ScriptRunner
from piyo.tools.browser.install import installer_command


def freeze(monkeypatch, folder: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(folder), raising=False)
    monkeypatch.setattr(sys, "executable", str(folder / "piyo-core.exe"))


def test_from_source_nothing_is_bundled():
    assert not runtime.frozen() and runtime.bundled_tool("uv") is None
    assert default_builtin_dir().name == "skills" and default_builtin_dir().is_dir()


def test_a_bundle_finds_its_skills_and_tools_beside_itself(tmp_path, monkeypatch):
    freeze(monkeypatch, tmp_path)
    assert default_builtin_dir() == tmp_path / "skills"
    assert runtime.bundled_tool("uv") is None  # not shipped: falls back to the user's PATH
    (tmp_path / "bin").mkdir()
    name = "uv.exe" if sys.platform == "win32" else "uv"
    (tmp_path / "bin" / name).write_text("x")
    assert runtime.bundled_tool("uv") == str(tmp_path / "bin" / name)


def test_the_script_runner_prefers_the_shipped_tool_over_the_path(tmp_path, monkeypatch):
    freeze(monkeypatch, tmp_path)
    (tmp_path / "bin").mkdir()
    name = "deno.exe" if sys.platform == "win32" else "deno"
    (tmp_path / "bin" / name).write_text("x")
    monkeypatch.delenv("PIYO_DENO", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/deno")
    assert ScriptRunner(tmp_path / "work", lambda _n: None)._deno_bin == str(tmp_path / "bin" / name)


def test_the_browser_installer_runs_the_driver_when_frozen(tmp_path, monkeypatch):
    command, env = installer_command()
    assert command[:3] == [sys.executable, "-m", "playwright"] and env is None
    freeze(monkeypatch, tmp_path)
    command, env = installer_command()
    assert command[0] != sys.executable and command[-2:] == ["install", "chromium"]
    assert env and env["PW_LANG_NAME"] == "python"


def test_a_bundle_uses_the_per_user_browser_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    runtime.use_shared_browser_cache()  # from source the setting is the developer's own
    assert sys.modules["os"].environ["PLAYWRIGHT_BROWSERS_PATH"] == "0"
    freeze(monkeypatch, tmp_path)
    runtime.use_shared_browser_cache()
    assert Path(sys.modules["os"].environ["PLAYWRIGHT_BROWSERS_PATH"]).name == "ms-playwright"
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    runtime.use_shared_browser_cache()  # Playwright would otherwise default to the bundle
    assert Path(sys.modules["os"].environ["PLAYWRIGHT_BROWSERS_PATH"]).name == "ms-playwright"
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "D:/my-browsers")
    runtime.use_shared_browser_cache()  # a path the user chose stays
    assert sys.modules["os"].environ["PLAYWRIGHT_BROWSERS_PATH"] == "D:/my-browsers"
