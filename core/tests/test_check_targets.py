"""scripts/check_targets.py reads glibc and macOS requirements out of built files."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_targets.py"
spec = importlib.util.spec_from_file_location("check_targets", SCRIPT)
targets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(targets)

OBJDUMP = """\
0000000000000000      DF *UND*  0000000000000000 (GLIBC_2.2.5) memcpy
0000000000000000      DF *UND*  0000000000000000 (GLIBC_2.34)  pthread_create
0000000000000000      DF *UND*  0000000000000000 (GLIBC_2.9)   pipe2
"""

OTOOL = """\
Load command 8
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 1
    minos 11.0
      sdk 14.2
Load command 9
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.13
      sdk 10.13
"""


def test_the_newest_glibc_is_compared_by_number_not_text():
    assert targets.newest_glibc(OBJDUMP) == (2, 34)
    assert targets.newest_glibc("GLIBC_2.9 GLIBC_2.10") == (2, 10)
    assert targets.newest_glibc("no versioned symbols") is None


def test_macos_targets_read_both_load_command_styles():
    assert targets.macos_targets(OTOOL) == [(11, 0), (10, 13)]
    assert max(targets.macos_targets(OTOOL.replace("minos 11.0", "minos 13.3"))) == (13, 3)


def test_native_files_are_told_apart_from_other_files(tmp_path):
    (tmp_path / "a").write_bytes(b"\x7fELF" + bytes(8))
    (tmp_path / "b").write_bytes(bytes.fromhex("cffaedfe") + bytes(8))
    (tmp_path / "fat").write_bytes(bytes.fromhex("cafebabe") + (2).to_bytes(4, "big"))
    (tmp_path / "class").write_bytes(bytes.fromhex("cafebabe") + (65).to_bytes(4, "big"))
    (tmp_path / "text").write_text("hello", encoding="utf-8")
    kinds = {p.name: targets.native_kind(p) for p in tmp_path.iterdir() if p.is_file()}
    assert kinds == {"a": "elf", "b": "macho", "fat": "macho", "class": None, "text": None}


def test_a_file_that_needs_too_new_an_os_is_reported(tmp_path, monkeypatch):
    elf = tmp_path / "prog"
    elf.write_bytes(b"\x7fELF" + bytes(8))
    monkeypatch.setattr(targets, "_tool", lambda *a: "GLIBC_2.38")
    assert "needs glibc 2.38" in targets.problems(elf)[0]
    monkeypatch.setattr(targets, "_tool", lambda *a: "GLIBC_2.35")
    assert targets.problems(elf) == []
    macho = tmp_path / "lib"
    macho.write_bytes(bytes.fromhex("cffaedfe") + bytes(8))
    monkeypatch.setattr(targets, "_tool", lambda *a: "cmd LC_BUILD_VERSION\n minos 14.0")
    assert "needs macOS 14.0" in targets.problems(macho)[0]
