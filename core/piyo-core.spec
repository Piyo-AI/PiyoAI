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
    # uv and Deno are not bundled; this says which release to download on first use, and the hash it must have
    (str(CORE / "piyo" / "skills" / "helper_tools.json"), "piyo/skills"),
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
    excludes=["tkinter", "pytest", "ruff", "setuptools", "pkg_resources", "pip", "lib2to3"],
)
# Parts of the Playwright driver only its trace viewer, recorder, HTML report and TypeScript users read. Piyo runs
# the server side (pages, clicks, the Chromium installer), so they are dead weight (about 6 MB). Filtered here,
# after the analysis, because PyInstaller's own Playwright hook adds the driver files too.
UNUSED_DRIVER = ("/driver/package/lib/vite/", "/driver/package/types/")
a.datas = [d for d in a.datas if not any(part in "/" + Path(d[0]).as_posix() for part in UNUSED_DRIVER)]
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
