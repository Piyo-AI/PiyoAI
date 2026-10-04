# PyInstaller spec for the shipped core (the desktop app's sidecar). Build with `python scripts/build_core.py`.
# One folder (not one file): a one-file build unpacks itself into a temp folder on every launch, which is slow
# and what antivirus products dislike most.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

CORE = Path(SPECPATH)
REPO = CORE.parent

datas = [
    (str(REPO / "skills"), "skills"),  # built-in skills (piyo.skills.registry.default_builtin_dir)
    (str(CORE / "piyo" / "skills" / "_launch.py"), "piyo/skills"),  # run by a skill's own Python, not by us
]
binaries = []
hiddenimports = []

datas += collect_data_files("tzdata")  # Windows has no system time zone database
# Imported by name at run time, so the import scan cannot see them.
for package in ("uvicorn", "keyring.backends", "anyio", "send2trash"):
    hiddenimports += collect_submodules(package)
# Playwright ships its Node driver as package data; the browser tools and the Chromium installer run it.
pw_datas, pw_binaries, pw_hidden = collect_all("playwright")
datas += pw_datas
binaries += pw_binaries
hiddenimports += pw_hidden

a = Analysis(
    [str(CORE / "piyo" / "server" / "__main__.py")],
    pathex=[str(CORE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "ruff"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="piyo-core",
    console=True,  # the app reads the {"port","token"} line from stdout
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="piyo-core", upx=False)
