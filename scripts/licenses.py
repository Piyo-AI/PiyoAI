"""Licence audit for everything Piyo ships, and the notices file that goes with it.

    python scripts/licenses.py --check    # fail if any shipped dependency has a licence we do not allow
    python scripts/licenses.py            # also rewrite THIRD-PARTY-NOTICES.txt (licence texts included)

What is covered: the Python packages the core needs at run time (from `core/uv.lock`), the Rust crates linked into
the desktop shell (`cargo tree`), the JavaScript packages in the UI bundle (`npm ls --omit=dev`), and the programs
shipped beside the core (`uv`, Deno, CPython). Dev and build tools are not shipped and are not checked.

A licence expression passes when at least one of its `OR` alternatives uses only allowed licences. Anything the
script cannot read (no licence, free text) fails: someone has to look at it and add it to `ALLOWED` or
`REVIEWED` with the reason.

Needs `uv`, `cargo` and `npm` on the PATH, `uv sync` done in core/ and `npm ci` in apps/desktop/. The licence texts
of packages that only exist on other platforms (for example `uvloop` on Linux) are fetched with
`uv pip install --python-platform`. The two bundled programs' texts are downloaded from their repositories.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
DESKTOP = ROOT / "apps" / "desktop"
OUTPUT = ROOT / "THIRD-PARTY-NOTICES.txt"

# Permissive licences, plus MPL-2.0 (file-level copyleft: fine to ship unmodified with notice).
ALLOWED = {
    "MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Zlib", "Unlicense", "0BSD",
    "PSF-2.0", "Python-2.0", "MPL-2.0", "Unicode-3.0", "CC0-1.0", "CDLA-Permissive-2.0", "BSL-1.0",
}
# Packages whose metadata is free text or wrong, checked by hand: name -> (licence id, why).
REVIEWED = {
    "jaraco-classes": ("MIT", "no licence field; classifier says MIT License"),
}
CLASSIFIERS = {
    "MIT License": "MIT",
    "Apache Software License": "Apache-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}
NAMES = {"MIT License": "MIT", "BSD License": "BSD-3-Clause", "Apache Software License": "Apache-2.0"}
TEXT_FILE = re.compile(r"(licen[sc]e|copying|notice|unlicense)", re.I)


@dataclass
class Package:
    ecosystem: str  # python | rust | js | program
    name: str
    version: str
    licence: str
    texts: list[tuple[str, str]] = field(default_factory=list)  # (file name, content)
    problem: str = ""


def run(cmd: list[str], cwd: Path) -> str:
    exe = shutil.which(cmd[0]) or cmd[0]
    return subprocess.run([exe, *cmd[1:]], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def read_texts(folder: Path) -> list[tuple[str, str]]:
    out = []
    if not folder.is_dir():
        return out
    for item in sorted(folder.rglob("*")) if folder.name == "licenses" else sorted(folder.iterdir()):
        if item.is_file() and TEXT_FILE.search(item.name) and item.stat().st_size < 200_000:
            out.append((item.name, item.read_text(encoding="utf-8", errors="replace").strip()))
    return out


# --- licence expressions ------------------------------------------------------------------------------------


def acceptable(expression: str) -> str:
    """'' if the expression is allowed, else the reason it is not."""
    text = NAMES.get(expression.strip(), expression).replace("(*)", "").replace("/", " OR ").strip()
    if not text:
        return "no licence declared"
    tokens = re.findall(r"\(|\)|[^\s()]+", text)
    pos = 0

    def parse_or() -> bool:
        nonlocal pos
        value = parse_and()
        while pos < len(tokens) and tokens[pos] == "OR":
            pos += 1
            value = parse_and() or value
        return value

    def parse_and() -> bool:
        nonlocal pos
        value = parse_term()
        while pos < len(tokens) and tokens[pos] == "AND":
            pos += 1
            value = parse_term() and value
        return value

    def parse_term() -> bool:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError
        if tokens[pos] == "(":
            pos += 1
            value = parse_or()
            if pos >= len(tokens) or tokens[pos] != ")":
                raise ValueError
            pos += 1
            return value
        name = tokens[pos]
        pos += 1
        if pos + 1 < len(tokens) and tokens[pos] == "WITH":  # "Apache-2.0 WITH LLVM-exception"
            pos += 2
        return name in ALLOWED

    try:
        ok = parse_or()
        if pos != len(tokens):
            raise ValueError
    except ValueError:
        return f"cannot read {expression!r}: check by hand"
    return "" if ok else f"{expression!r} is not on the allowed list"


# --- collectors ---------------------------------------------------------------------------------------------


def python_packages() -> list[Package]:
    lines = run(["uv", "export", "--no-dev", "--no-emit-project", "--no-hashes"], CORE).splitlines()
    wanted = sorted({m.group(1).lower(): m.group(2) for line in lines if (m := re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line))}.items())
    from importlib import metadata

    packages, missing = [], []
    for name, version in wanted:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append((name, version))
            continue
        packages.append(from_distribution(name, version, dist))
    if missing:  # packages for other platforms: unpack their wheels for the metadata
        with tempfile.TemporaryDirectory() as tmp:
            for platform in ("x86_64-unknown-linux-gnu", "aarch64-apple-darwin"):
                todo = [f"{n}=={v}" for n, v in missing if not any(p.name == n for p in packages)]
                if not todo:
                    break
                subprocess.run(
                    [shutil.which("uv") or "uv", "pip", "install", "--no-deps", "--quiet", "--target", tmp,
                     "--python-platform", platform, *todo],
                    cwd=CORE, capture_output=True,
                )
                found = {d.metadata["Name"].lower().replace("_", "-"): d for d in metadata.distributions(path=[tmp])}
                for n, v in missing:
                    if n in found and not any(p.name == n for p in packages):
                        packages.append(from_distribution(n, v, found[n]))
            for n, v in missing:
                if not any(p.name == n for p in packages):
                    packages.append(Package("python", n, v, "", problem="not installed here and no wheel for another platform"))
    return packages


def from_distribution(name: str, version: str, dist) -> Package:
    meta = dist.metadata
    licence = (meta.get("License-Expression") or "").strip()
    if not licence:
        raw = (meta.get("License") or "").strip()
        licence = raw if raw and "\n" not in raw and len(raw) < 40 else ""
    if not licence:
        for c in meta.get_all("Classifier") or []:
            if c.startswith("License ::") and c.split(" :: ")[-1] in CLASSIFIERS:
                licence = CLASSIFIERS[c.split(" :: ")[-1]]
    if name in REVIEWED:
        licence = REVIEWED[name][0]
    texts = []
    for f in dist.files or []:
        if TEXT_FILE.search(f.name) and ("dist-info" in str(f) or "egg-info" in str(f)) and f.suffix in ("", ".txt", ".md", ".rst"):
            try:
                texts.append((f.name, dist.locate_file(f).read_text(encoding="utf-8", errors="replace").strip()))
            except OSError:
                pass
    return Package("python", name, version, licence, texts)


def rust_packages() -> list[Package]:
    tree = run(["cargo", "tree", "-e", "normal", "--target", "all", "--prefix", "none", "-f", "{p}"], DESKTOP / "src-tauri")
    used = {(m.group(1), m.group(2)) for line in tree.splitlines() if (m := re.match(r"^(\S+) v(\S+)", line))}
    meta = json.loads(run(["cargo", "metadata", "--format-version", "1", "--locked"], DESKTOP / "src-tauri"))
    packages = []
    for p in meta["packages"]:
        if p["source"] is None or (p["name"], p["version"]) not in used:
            continue
        folder = Path(p["manifest_path"]).parent
        packages.append(Package("rust", p["name"], p["version"], p.get("license") or "", read_texts(folder)))
    return packages


def js_packages() -> list[Package]:
    tree = json.loads(run(["npm", "ls", "--omit=dev", "--all", "--json"], DESKTOP))
    seen: dict[str, str] = {}

    def walk(deps: dict) -> None:
        for name, info in (deps or {}).items():
            seen[name] = info.get("version", "?")
            walk(info.get("dependencies"))

    walk(tree.get("dependencies"))
    packages = []
    for name, version in sorted(seen.items()):
        folder = DESKTOP / "node_modules" / name
        try:
            licence = json.loads((folder / "package.json").read_text(encoding="utf-8")).get("license", "")
        except (OSError, ValueError):
            licence = ""
        packages.append(Package("js", name, version, licence if isinstance(licence, str) else "", read_texts(folder)))
    return packages


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as res:
        return res.read().decode("utf-8", "replace").strip()


def program_packages() -> list[Package]:
    pins = json.loads((CORE / "piyo" / "skills" / "helper_tools.json").read_text(encoding="utf-8"))
    uv, deno = pins["uv"]["version"], pins["deno"]["version"]
    out = [
        Package("program", "uv", uv, "MIT OR Apache-2.0", [
            ("LICENSE-MIT", fetch(f"https://raw.githubusercontent.com/astral-sh/uv/{uv}/LICENSE-MIT")),
            ("LICENSE-APACHE", fetch(f"https://raw.githubusercontent.com/astral-sh/uv/{uv}/LICENSE-APACHE")),
        ]),
        Package("program", "deno", deno, "MIT", [
            ("LICENSE.md", fetch(f"https://raw.githubusercontent.com/denoland/deno/v{deno}/LICENSE.md")),
        ]),
    ]
    py = Path(sys.base_prefix)
    texts = read_texts(py) if (py / "LICENSE.txt").exists() else []
    out.append(Package("program", "cpython", f"{sys.version_info.major}.{sys.version_info.minor}", "PSF-2.0",
                       [t for t in texts if t[0].lower().startswith("license")]))
    return out


# --- output -------------------------------------------------------------------------------------------------


def standard_texts(packages: list[Package]) -> dict[str, str]:
    """Canonical Apache-2.0 and MPL-2.0 texts, taken from any package that ships them verbatim."""
    found: dict[str, str] = {}
    for p in packages:
        for name, content in p.texts:
            if "Apache License" in content and "Version 2.0" in content and len(content) > 9000:
                found.setdefault("Apache-2.0", content)
            if "Mozilla Public License Version 2.0" in content and len(content) > 10000:
                found.setdefault("MPL-2.0", content)
    return found


MIT_TEXT = """MIT License

Copyright (c) the authors of {name}

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of
the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
IN THE SOFTWARE."""


BSD3_TEXT = """BSD 3-Clause License

Copyright (c) the authors of {name}

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the
   following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the
   following disclaimer in the documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote
   products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""


def fill_missing_texts(packages: list[Package]) -> list[str]:
    """Crates that publish no licence file get the standard text of their licence, marked as such."""
    standard = standard_texts(packages)
    unresolved = []
    for p in packages:
        if p.texts or p.ecosystem == "program":
            continue
        expr = NAMES.get(p.licence, p.licence).replace("/", " OR ")
        options = [o for o in re.split(r"\s+OR\s+|\s+AND\s+", expr.replace("(", "").replace(")", "")) if o in ALLOWED]
        note = "(this package ships no licence file; the standard text of its licence follows)"
        if "MIT" in options:
            text = MIT_TEXT.format(name=p.name)
        elif "BSD-3-Clause" in options:
            text = BSD3_TEXT.format(name=p.name)
        elif "Apache-2.0" in options and "Apache-2.0" in standard:
            text = standard["Apache-2.0"]
        elif "MPL-2.0" in options and "MPL-2.0" in standard:
            text = standard["MPL-2.0"]
        else:
            unresolved.append(f"{p.ecosystem}: {p.name} {p.version} ({p.licence})")
            continue
        p.texts = [("standard text", note + "\n\n" + text)]
    return unresolved


def render(packages: list[Package]) -> str:
    lines = [
        "THIRD-PARTY NOTICES",
        "",
        "Piyo AI itself is MIT licensed (see LICENSE). It includes or is linked with the software below, which has",
        "its own licence. Generated by scripts/licenses.py; do not edit by hand.",
        "",
        "Playwright's driver (Apache-2.0, with its own ThirdPartyNotices.txt) and Node.js ship inside the packaged",
        "core under _internal/playwright/driver, and carry their notices there. Chromium is not shipped: it is",
        "downloaded on request by the user from Playwright's servers.",
        "",
        "=" * 78,
        "PACKAGES",
        "=" * 78,
    ]
    for eco in ("program", "python", "rust", "js"):
        group = sorted((p for p in packages if p.ecosystem == eco), key=lambda p: (p.name.lower(), p.version))
        if group:
            lines += ["", f"[{eco}]"] + [f"  {p.name} {p.version}: {p.licence}" for p in group]
    # Identical texts are printed once, followed by everything they apply to.
    by_text: dict[str, list[str]] = {}
    for p in packages:
        for _, content in p.texts:
            by_text.setdefault(content, []).append(f"{p.name} {p.version}")
    lines += ["", "=" * 78, "LICENCE TEXTS", "=" * 78]
    for content, owners in sorted(by_text.items(), key=lambda kv: (sorted(kv[1])[0].lower(), hashlib.sha1(kv[0].encode()).hexdigest())):
        lines += ["", "-" * 78, "Applies to: " + ", ".join(sorted(set(owners))), "-" * 78, "", content]
    return "\n".join(lines).replace("\r\n", "\n") + "\n"


def main() -> int:
    check_only = "--check" in sys.argv
    packages = python_packages() + rust_packages() + js_packages()
    if not check_only:
        packages += program_packages()
    else:
        packages += [Package("program", "uv", "", "MIT OR Apache-2.0"), Package("program", "deno", "", "MIT")]
    bad = []
    for p in packages:
        reason = p.problem or acceptable(p.licence)
        if reason:
            bad.append(f"{p.ecosystem}: {p.name} {p.version}: {reason}")
    no_text = fill_missing_texts(packages) if not check_only else []
    print(f"{len(packages)} packages checked: {len(bad)} with a problem")
    for line in bad:
        print("  PROBLEM", line)
    if not check_only:
        for line in no_text:
            print("  no licence text could be attached to", line)
        OUTPUT.write_text(render(packages), encoding="utf-8", newline="\n")
        print(f"wrote {OUTPUT.name} ({OUTPUT.stat().st_size // 1024} KiB)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
