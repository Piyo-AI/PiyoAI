"""Check that built programs do not need a newer OS than the one we promise to support.

    python scripts/check_targets.py core/dist/piyo-core apps/desktop/src-tauri/target/release/piyo-desktop

Every native file under the given paths is read (not run):

- Linux (ELF): the newest glibc symbol version it needs must be at most MAX_GLIBC. Ubuntu 22.04 ships glibc 2.35,
  Debian 12 2.36 and Fedora 39 2.38, so a file built on a 22.04 runner works on all three; a newer build machine
  would quietly raise this and the app would not start on 22.04.
- macOS (Mach-O): the deployment target must be at most MAX_MACOS.
- Windows: nothing to read here. The supported floor (Windows 10 22H2) is the Python 3.12 / WebView2 floor.

CI runs it after the core is packaged (ci.yml `package`) and after the installer is built (release.yml).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MAX_GLIBC = (2, 35)  # Ubuntu 22.04
MAX_MACOS = (12, 0)  # macOS 12 Monterey

ELF = b"\x7fELF"
MACHO = {bytes.fromhex(h) for h in ("feedface", "feedfacf", "cefaedfe", "cffaedfe")}
FAT = bytes.fromhex("cafebabe")  # also the magic of a Java class, told apart by the architecture count


def native_kind(path: Path) -> str | None:
    """'elf', 'macho' or None, from the first bytes of the file."""
    try:
        with path.open("rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head[:4] == ELF:
        return "elf"
    if head[:4] in MACHO or (head[:4] == FAT and 0 < int.from_bytes(head[4:8], "big") < 20):
        return "macho"
    return None


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in text.split("."))


def newest_glibc(objdump_output: str) -> tuple[int, ...] | None:
    """The highest GLIBC_x.y in the output of `objdump -T`, or None when it needs none."""
    found = [_version(v) for v in re.findall(r"GLIBC_(\d+(?:\.\d+)+)", objdump_output)]
    return max(found, default=None)


def macos_targets(otool_output: str) -> list[tuple[int, ...]]:
    """Every macOS deployment target in the output of `otool -l` (one per architecture)."""
    targets, command = [], ""
    for line in otool_output.splitlines():
        words = line.split()
        if len(words) == 2 and words[0] == "cmd":
            command = words[1]
        elif command == "LC_BUILD_VERSION" and len(words) == 2 and words[0] == "minos":
            targets.append(_version(words[1]))
        elif command == "LC_VERSION_MIN_MACOSX" and len(words) == 2 and words[0] == "version":
            targets.append(_version(words[1]))
    return targets


def _tool(*args: str) -> str:
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.stdout if done.returncode == 0 else ""


def problems(path: Path) -> list[str]:
    kind = native_kind(path)
    if kind == "elf":
        needed = newest_glibc(_tool("objdump", "-T", str(path)))
        if needed and needed > MAX_GLIBC:
            return [f"{path}: needs glibc {'.'.join(map(str, needed))} (limit {'.'.join(map(str, MAX_GLIBC))})"]
    elif kind == "macho":
        newest = max(macos_targets(_tool("otool", "-l", str(path))), default=None)
        if newest and newest > MAX_MACOS:
            return [f"{path}: needs macOS {'.'.join(map(str, newest))} (limit {'.'.join(map(str, MAX_MACOS))})"]
    return []


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    files = [p for arg in argv for p in ([Path(arg)] if Path(arg).is_file() else Path(arg).rglob("*")) if p.is_file()]
    checked = [p for p in files if native_kind(p)]
    bad = [line for p in checked for line in problems(p)]
    for line in bad:
        print(line)
    print(f"checked {len(checked)} native files; {len(bad)} too new")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
