"""Checks a skill package before it may enter the public catalog (PLAN.md section 5, "Submissions").

The catalog repo's CI runs this on every submission (`piyo-skills/scripts/build_index.py` calls it), so the
rules live next to the skill format and cannot drift from it. It never runs the skill: everything here is a
static check, and none of it replaces the maintainer's review. A clean report means "nothing obviously wrong",
not "safe". What the app does at install and run time (permission review, the gate, script sandboxes) is
unchanged by anything a package says.

Errors block a submission. Warnings are for the reviewer: they are printed but do not fail the build.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from piyo.skills.install import MAX_FILE_BYTES, MAX_FILES
from piyo.skills.manifest import Skill, SkillError, load_skill_dir

MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MIN_SETUP_CHARS = 80

# Every tool a skill may request. tests/test_skill_validate.py compares this with the tools the app registers,
# so a new tool cannot be forgotten here.
KNOWN_TOOLS = frozenset(
    {
        "browser.click",
        "browser.open",
        "browser.read",
        "browser.screenshot",
        "browser.type",
        "browser.wait",
        "calendar.agenda",
        "calendar.create",
        "calendar.delete",
        "calendar.freebusy",
        "calendar.update",
        "files.create_folder",
        "files.delete",
        "files.folders",
        "files.list",
        "files.move",
        "files.read",
        "files.write",
        "gmail.archive",
        "gmail.draft",
        "gmail.label",
        "gmail.read",
        "gmail.search",
        "gmail.send",
        "google.accounts",
        "memory.forget",
        "memory.recall",
        "memory.remember",
        "schedule.cancel",
        "schedule.create",
        "schedule.list",
        "skill.run_script",
        "weather.forecast",
        "web.fetch",
        "web.search",
    }
)
KNOWN_INTEGRATIONS = frozenset({"google"})
# Tools that change or send something. They stay behind the permission gate, but a reviewer should look twice.
HIGH_IMPACT_TOOLS = frozenset(
    {
        "files.delete",
        "files.move",
        "files.write",
        "gmail.send",
        "gmail.archive",
        "gmail.label",
        "calendar.create",
        "calendar.update",
        "calendar.delete",
        "schedule.create",
        "browser.click",
        "browser.type",
        "memory.forget",
    }
)

# SPDX ids of the common OSI-approved licences (opensource.org/licenses). "-only" and "-or-later" and the
# deprecated bare GPL ids are accepted; anything else (CC0, proprietary, "free to use") is not.
OSI_LICENSES = frozenset(
    {
        "MIT",
        "MIT-0",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "0BSD",
        "ISC",
        "Zlib",
        "Unlicense",
        "MPL-2.0",
        "EPL-2.0",
        "CDDL-1.0",
        "Artistic-2.0",
        "BSL-1.0",
        "PostgreSQL",
        "Python-2.0",
        "NCSA",
        "GPL-2.0",
        "GPL-3.0",
        "LGPL-2.1",
        "LGPL-3.0",
        "AGPL-3.0",
        "EUPL-1.2",
        "OFL-1.1",
    }
)

_TOP_FILES = {"SKILL.md", "SETUP.md", "README.md", "LICENSE", "LICENSE.md", "LICENSE.txt"}
_SCRIPT_SUFFIXES = {".py", ".js", ".ts"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_TEXT_SUFFIXES = {".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".html", ".css", *_SCRIPT_SUFFIXES}
_SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")
_SECRET_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_BINARY_MAGIC = (b"MZ\x90\x00", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"PK\x03\x04")

# Things that look like a credential. Deliberately narrow: a false alarm blocks an honest author.
_SECRET_PATTERNS = {
    "a private key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "an AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "a GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "an API key": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    "a Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}\b"),
    "a Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
}
# Instructions aimed at the permission gate or the model's rules. The gate wins regardless; the catalog
# simply does not list a skill that tries.
_GATE_PHRASES = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|rules)"
    r"|(?:skip|bypass|disable|avoid|without) (?:the )?(?:permission|confirmation|approval|gate)"
    r"|(?:do not|don'?t|never) (?:ask|wait for|request) (?:the user'?s? )?(?:for )?"
    r"(?:confirmation|approval|permission)"
    r"|do not (?:tell|inform|show) the user"
    r"|reveal (?:your |the )?system prompt",
    re.IGNORECASE,
)
_PY_FORBIDDEN_IMPORTS = {
    "subprocess": "starts other programs",
    "ctypes": "calls native code",
    "pty": "starts a shell",
    "pickle": "can run code when loading",
    "marshal": "can run code when loading",
}
_PY_NET_IMPORTS = {
    "socket",
    "ssl",
    "urllib",
    "http",
    "requests",
    "httpx",
    "aiohttp",
    "ftplib",
    "smtplib",
    "websockets",
}
_PY_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
_PY_FORBIDDEN_OS = {
    "system",
    "popen",
    "execv",
    "execve",
    "execl",
    "execlp",
    "execvp",
    "spawnl",
    "spawnv",
    "startfile",
}
_JS_FORBIDDEN = {
    r"\beval\s*\(": "eval",
    r"\bnew\s+Function\b": "new Function",
    r"child_process": "child_process",
    r"\bDeno\.(?:Command|run|dlopen|env)\b": "Deno.Command / run / dlopen / env",
    r"\bprocess\.env\b": "process.env",
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def normalize_license(value: str) -> str:
    return re.sub(r"-(?:only|or-later)$", "", value.strip())


def validate_package(path: Path) -> tuple[Skill | None, Report]:
    """Every problem with one skill folder, so an author fixes them in one go; the skill if it loads."""
    report = Report()
    try:
        skill = load_skill_dir(path, "catalog")
    except (SkillError, OSError, UnicodeDecodeError) as e:
        report.errors.append(f"SKILL.md: {e}")
        return None, report
    _layout(path, report)
    _manifest(skill, report)
    _setup(path, report)
    _runtime(skill, report)
    _scripts(skill, report)
    _text(path, report)
    return skill, report


def _layout(path: Path, report: Report) -> None:
    files = []
    for item in sorted(path.rglob("*")):
        rel = item.relative_to(path)
        if item.is_symlink():
            report.errors.append(f"{rel.as_posix()}: links are not allowed")
        elif item.is_file():
            files.append((item, rel))
        elif item.is_dir() and any(part.startswith(".") for part in rel.parts):
            report.errors.append(f"{rel.as_posix()}: hidden folders are not allowed")
    if len(files) > MAX_FILES:
        report.errors.append(f"{len(files)} files (limit {MAX_FILES})")
    total = 0
    for item, rel in files:
        name, top = rel.as_posix(), rel.parts[0]
        size = item.stat().st_size
        total += size
        if size > MAX_FILE_BYTES:
            report.errors.append(f"{name}: {size} bytes (limit {MAX_FILE_BYTES} per file)")
        if rel.name.startswith("."):
            report.errors.append(f"{name}: hidden files are not allowed")
        suffix = rel.suffix.lower()
        if len(rel.parts) == 1:
            if name not in _TOP_FILES:
                report.errors.append(
                    f"{name}: only SKILL.md, SETUP.md, README.md, LICENSE, scripts/ and assets/ go here"
                )
        elif top == "scripts":
            if len(rel.parts) > 2 or suffix not in _SCRIPT_SUFFIXES:
                report.errors.append(f"{name}: scripts/ holds .py, .js and .ts files directly, nothing else")
        elif top != "assets":
            report.errors.append(f"{name}: files belong in scripts/ or assets/")
        elif suffix not in _TEXT_SUFFIXES | _IMAGE_SUFFIXES:
            report.errors.append(
                f"{name}: {suffix or 'this'} files are not allowed (text and png/jpg/webp/gif only)"
            )
        data = item.read_bytes()
        if suffix in _TEXT_SUFFIXES:
            if b"\x00" in data[:4096] or data.startswith(_BINARY_MAGIC):
                report.errors.append(f"{name}: binary content in a text file")
        elif data.startswith((*_BINARY_MAGIC, b"#!")):
            report.errors.append(f"{name}: executables and archives are not allowed")
    if total > MAX_PACKAGE_BYTES:
        report.errors.append(f"package is {total} bytes (limit {MAX_PACKAGE_BYTES})")


def _manifest(skill: Skill, report: Report) -> None:
    m = skill.manifest
    if not m.license:
        report.errors.append("SKILL.md: declare a license (an OSI-approved SPDX id such as MIT)")
    elif normalize_license(m.license) not in OSI_LICENSES:
        report.errors.append(f"SKILL.md: license {m.license!r} is not a recognised OSI-approved SPDX id")
    if not m.author:
        report.errors.append("SKILL.md: declare an author")
    if not _SEMVER.fullmatch(m.version):
        report.errors.append(f"SKILL.md: version {m.version!r} must look like 1.2.3")
    req = m.requires
    for tool in req.tools:
        if tool not in KNOWN_TOOLS:
            report.errors.append(f"requires.tools: {tool!r} is not a tool Piyo has")
    for integration in req.integrations:
        if integration not in KNOWN_INTEGRATIONS:
            report.errors.append(f"requires.integrations: {integration!r} is not an integration Piyo has")
    for name in req.secrets:
        if not _SECRET_NAME.fullmatch(name):
            report.errors.append(f"requires.secrets: {name!r} must be letters, digits and underscores")
    for label, items in (("tools", req.tools), ("integrations", req.integrations), ("secrets", req.secrets)):
        if len(set(items)) != len(items):
            report.errors.append(f"requires.{label} lists something twice")
    if skill.scripts and "skill.run_script" not in req.tools:
        report.errors.append(
            "scripts/ has files but requires.tools lacks skill.run_script, so nothing can run them"
        )
    if "skill.run_script" in req.tools and not skill.scripts:
        report.errors.append("requires.tools asks for skill.run_script but the skill has no scripts")
    if (
        any(t.startswith(("gmail.", "calendar.", "google.")) for t in req.tools)
        and "google" not in req.integrations
    ):
        report.errors.append("a Google tool is requested without requires.integrations: [google]")
    risky = sorted(set(req.tools) & HIGH_IMPACT_TOOLS)
    if risky:
        report.warnings.append(
            f"reviewer: changes or sends things ({', '.join(risky)}); check the instructions justify each"
        )
    if len(req.tools) > 8:
        report.warnings.append(f"reviewer: asks for {len(req.tools)} tools; is every one needed?")


def _setup(path: Path, report: Report) -> None:
    setup = path / "SETUP.md"
    if not setup.is_file():
        report.errors.append(
            "SETUP.md: add a step-by-step setup guide (say 'nothing to set up' if that is true)"
        )
    elif len(setup.read_text(encoding="utf-8", errors="replace").strip()) < MIN_SETUP_CHARS:
        report.errors.append(f"SETUP.md: too short to be a guide (under {MIN_SETUP_CHARS} characters)")


def _runtime(skill: Skill, report: Report) -> None:
    runtime = skill.manifest.runtime
    if runtime is None:
        return
    if not isinstance(runtime, dict) or not set(runtime) <= {"python", "deno"}:
        report.errors.append("runtime: only python and deno are known")
        return
    py, deno = runtime.get("python") or {}, runtime.get("deno") or {}
    if not isinstance(py, dict) or not isinstance(deno, dict):
        report.errors.append("runtime: python and deno must be mappings")
        return
    for dep in py.get("dependencies") or []:
        if not re.fullmatch(r"[A-Za-z0-9_.\[\],-]+==[A-Za-z0-9_.+!-]+", str(dep)):
            report.errors.append(f"runtime.python.dependencies: {dep!r} must be pinned like name==1.2.3")
    for host in deno.get("allow_net") or []:
        if not re.fullmatch(r"[A-Za-z0-9.-]+(?::\d{1,5})?", str(host)) or "." not in str(host):
            report.errors.append(
                f"runtime.deno.allow_net: {host!r} must be a specific host such as api.example.com"
            )
    if py.get("network") is True:
        report.warnings.append(
            "reviewer: the Python script may use the network; check what it contacts and why"
        )
    if deno.get("allow_net"):
        report.warnings.append(
            f"reviewer: the Deno script may contact {', '.join(map(str, deno['allow_net']))}"
        )
    if deno.get("npm"):
        report.warnings.append("reviewer: the Deno script downloads npm packages; check them")
    for key in ("timeout_s",):
        for kind in (py, deno):
            if key in kind and not isinstance(kind[key], (int, float)):
                report.errors.append(f"runtime.{key} must be a number")


def _scripts(skill: Skill, report: Report) -> None:
    network = ((skill.manifest.runtime or {}).get("python") or {}).get("network") is True
    for name in skill.scripts:
        file = skill.path / "scripts" / name
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            report.errors.append(f"scripts/{name}: not UTF-8 text")
            continue
        if any(len(line) > 1500 for line in text.splitlines()) or re.search(r"[A-Za-z0-9+/=]{400,}", text):
            report.errors.append(
                f"scripts/{name}: looks obfuscated or embeds a large blob; keep scripts readable"
            )
        if file.suffix == ".py":
            _python(name, text, network, report)
        else:
            for pattern, label in _JS_FORBIDDEN.items():
                if re.search(pattern, text):
                    report.errors.append(f"scripts/{name}: uses {label}, which a skill script may not")


def _python(name: str, text: str, network: bool, report: Report) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        report.errors.append(f"scripts/{name}: not valid Python ({e.msg}, line {e.lineno})")
        return
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules = [node.module]
        for module in modules:
            top = module.split(".")[0]
            if top in _PY_FORBIDDEN_IMPORTS:
                report.errors.append(f"scripts/{name}: imports {top}, which {_PY_FORBIDDEN_IMPORTS[top]}")
            elif top in _PY_NET_IMPORTS and not network:
                report.errors.append(
                    f"scripts/{name}: imports {top} (network) but runtime.python.network is not true"
                )
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _PY_FORBIDDEN_CALLS:
                report.errors.append(f"scripts/{name}: calls {fn.id}(), which a skill script may not")
            elif isinstance(fn, ast.Attribute) and fn.attr in _PY_FORBIDDEN_OS:
                report.errors.append(f"scripts/{name}: calls .{fn.attr}(), which starts another program")


def _text(path: Path, report: Report) -> None:
    for file in sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in _TEXT_SUFFIXES):
        rel = PurePosixPath(file.relative_to(path).as_posix())
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            report.errors.append(f"{rel}: not UTF-8 text")
            continue
        for label, pattern in _SECRET_PATTERNS.items():
            if pattern.search(text):
                report.errors.append(f"{rel}: contains what looks like {label}; remove it and rotate the key")
        if file.suffix.lower() in {".md", ".txt"} and (found := _GATE_PHRASES.search(text)):
            report.errors.append(
                f"{rel}: tells the agent to skip approvals or ignore its rules ({found.group(0)!r}); "
                "the permission gate wins regardless, so a skill must not try"
            )
