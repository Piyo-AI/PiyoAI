"""File tools, limited to folders the user approved in Settings.

Paths are resolved (symlinks and `..` followed) before the folder check, so a link inside an
approved folder can't lead outside it. Reads are `auto`. Changes are `confirm` unless the folder is
marked `auto_changes`; delete and overwrite always are.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from piyo.config.folders import ApprovedFolders
from piyo.tools.base import Risk, RunContext, Tool

MAX_READ_BYTES = 100_000
MAX_LIST_ENTRIES = 500


class FileAccessError(Exception):
    """The message goes back to the model, so keep it actionable."""


class FileTools:
    def __init__(self, folders: ApprovedFolders) -> None:
        self._folders = folders

    def _resolve(self, raw: object) -> tuple[Path, Path]:
        """Return (resolved path, the approved root containing it)."""
        roots = [g.path.resolve() for g in self._folders.list()]
        if not roots:
            raise FileAccessError(
                "No folders are approved for file access. Ask the user to add one in Settings."
            )
        if not isinstance(raw, str) or not raw.strip():
            raise FileAccessError("A path is required.")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise FileAccessError(f"Use a full path, not {raw!r}.")
        path = path.resolve()
        for root in roots:
            if path == root or path.is_relative_to(root):
                return path, root
        approved = ", ".join(str(r) for r in roots)
        raise FileAccessError(f"{raw} is outside the approved folders ({approved}).")

    def _auto(self, raw: object) -> bool:
        """True if the path is inside a folder where changes need no per-call approval."""
        try:
            _, root = self._resolve(raw)
        except FileAccessError:
            return False
        return any(g.auto_changes and g.path.resolve() == root for g in self._folders.list())

    def risk_create(self, args: dict) -> Risk:
        return Risk.AUTO if self._auto(args.get("path")) else Risk.CONFIRM

    def risk_write(self, args: dict) -> Risk:
        # Overwriting destroys the old content, so that always asks.
        if args.get("overwrite"):
            return Risk.CONFIRM
        return self.risk_create(args)

    def risk_move(self, args: dict) -> Risk:
        both = self._auto(args.get("source")) and self._auto(args.get("destination"))
        return Risk.AUTO if both else Risk.CONFIRM

    async def folders(self, args: dict, ctx: RunContext) -> str:
        grants = self._folders.list()
        if not grants:
            return "No folders are approved for file access. Ask the user to add one in Settings."
        lines = ["Approved folders (reading is always allowed in them):"]
        for g in grants:
            if g.auto_changes:
                mode = "creating folders, moving files and writing new files need no approval"
            else:
                mode = "every change needs the user's approval"
            lines.append(f"- {g.path}: {mode}")
        lines.append("Deleting and overwriting files always need the user's approval.")
        return "\n".join(lines)

    async def list(self, args: dict, ctx: RunContext) -> str:
        path, _ = self._resolve(args.get("path"))
        if not path.is_dir():
            raise FileAccessError(f"Not a folder: {path}")
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = [
            f"{p.name}/" if p.is_dir() else f"{p.name}  ({p.stat().st_size} bytes)"
            for p in entries[:MAX_LIST_ENTRIES]
        ]
        if len(entries) > MAX_LIST_ENTRIES:
            lines.append(f"... {len(entries) - MAX_LIST_ENTRIES} more not shown")
        return "\n".join(lines) or "(empty folder)"

    async def read(self, args: dict, ctx: RunContext) -> str:
        path, _ = self._resolve(args.get("path"))
        if not path.is_file():
            raise FileAccessError(f"Not a file: {path}")
        with path.open("rb") as f:
            data = f.read(MAX_READ_BYTES + 1)
        text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        if len(data) > MAX_READ_BYTES:
            text += f"\n[truncated at {MAX_READ_BYTES} bytes]"
        return text

    async def write(self, args: dict, ctx: RunContext) -> str:
        path, _ = self._resolve(args.get("path"))
        content = args.get("content")
        if not isinstance(content, str):
            raise FileAccessError("content must be text.")
        if path.is_dir():
            raise FileAccessError(f"{path} is a folder.")
        if path.exists() and not args.get("overwrite"):
            raise FileAccessError(f"{path} already exists. Pass overwrite=true to replace it.")
        if not path.parent.is_dir():
            raise FileAccessError(f"Folder does not exist: {path.parent}")
        path.write_text(content, encoding="utf-8", newline="")
        return f"Wrote {len(content)} characters to {path}"

    async def create_folder(self, args: dict, ctx: RunContext) -> str:
        path, _ = self._resolve(args.get("path"))
        if path.exists():
            raise FileAccessError(f"{path} already exists.")
        if not path.parent.is_dir():
            raise FileAccessError(f"Folder does not exist: {path.parent}")
        path.mkdir()
        return f"Created folder {path}"

    async def move(self, args: dict, ctx: RunContext) -> str:
        src, src_root = self._resolve(args.get("source"))
        dest, _ = self._resolve(args.get("destination"))
        if src == src_root:
            raise FileAccessError("An approved folder itself can't be moved.")
        if not src.exists():
            raise FileAccessError(f"Not found: {src}")
        if dest.is_dir():
            dest = dest / src.name
        if dest.exists():
            raise FileAccessError(f"{dest} already exists; nothing was moved.")
        if not dest.parent.is_dir():
            raise FileAccessError(f"Folder does not exist: {dest.parent}")
        shutil.move(str(src), str(dest))
        return f"Moved {src} to {dest}"

    async def delete(self, args: dict, ctx: RunContext) -> str:
        path, root = self._resolve(args.get("path"))
        if path == root:
            raise FileAccessError("An approved folder itself can't be deleted.")
        if not path.exists():
            raise FileAccessError(f"Not found: {path}")
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                raise FileAccessError(f"{path} is not empty; only empty folders go.") from None
        else:
            path.unlink()
        return f"Deleted {path}"


def file_tools(folders: ApprovedFolders) -> list[Tool]:
    impl = FileTools(folders)
    path = {"type": "string", "description": "Full path inside an approved folder"}

    def schema(required: list[str], **props: dict) -> dict:
        return {"type": "object", "properties": props, "required": required}

    return [
        Tool(
            name="files.folders",
            description=(
                "List the folders you may use for files and how changes are handled in each. "
                "Call this when unsure where you can work."
            ),
            handler=impl.folders,
            core=True,
        ),
        Tool(
            name="files.list",
            description="List the files and folders in an approved folder.",
            parameters=schema(["path"], path=path),
            handler=impl.list,
        ),
        Tool(
            name="files.read",
            description=f"Read a text file (first {MAX_READ_BYTES} bytes) from an approved folder.",
            parameters=schema(["path"], path=path),
            handler=impl.read,
        ),
        Tool(
            name="files.write",
            description=(
                "Create a text file in an approved folder. Refuses to replace an existing file "
                "unless overwrite is true."
            ),
            parameters=schema(
                ["path", "content"],
                path=path,
                content={"type": "string"},
                overwrite={"type": "boolean"},
            ),
            handler=impl.write,
            risk=Risk.CONFIRM,
            risk_for=impl.risk_write,
        ),
        Tool(
            name="files.create_folder",
            description="Create one new folder inside an approved folder (its parent must exist).",
            parameters=schema(["path"], path=path),
            handler=impl.create_folder,
            risk=Risk.CONFIRM,
            risk_for=impl.risk_create,
        ),
        Tool(
            name="files.move",
            description=(
                "Move or rename a file or folder within the approved folders. If the destination "
                "is a folder, the item goes into it. Never overwrites."
            ),
            parameters=schema(["source", "destination"], source=path, destination=path),
            handler=impl.move,
            risk=Risk.CONFIRM,
            risk_for=impl.risk_move,
        ),
        Tool(
            name="files.delete",
            description="Permanently delete one file or one empty folder in an approved folder.",
            parameters=schema(["path"], path=path),
            handler=impl.delete,
            risk=Risk.CONFIRM,
        ),
    ]
