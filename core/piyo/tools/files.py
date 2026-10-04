"""File tools, limited to folders the user approved in Settings and Piyo's workspace (`~/Piyo`).

A relative path means "in the workspace", so a task that names no folder puts its files there.

Paths are resolved (symlinks and `..` followed) before the folder check, so a link inside an
approved folder can't lead outside it. A link swapped in after that check is caught by resolving again
right before the file is touched (`_recheck`) and, where the OS has it, by opening without following a link
at the last step. The window that remains is the few instructions between the second check and the call.

Reads are `auto`. Changes are `confirm` unless the folder is
marked `auto_changes`; delete and overwrite always are. Delete goes to the OS trash.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from send2trash import send2trash

from piyo.config.folders import ApprovedFolders
from piyo.config.protected import broad_reason, protected_reason
from piyo.safety.untrusted import wrap_untrusted
from piyo.tools.base import Risk, RunContext, Tool

MAX_READ_BYTES = 100_000
MAX_LIST_ENTRIES = 500
# Open the last path part without following a link where the OS can (not Windows); O_BINARY is Windows-only.
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)


class FileAccessError(Exception):
    """The message goes back to the model, so keep it actionable."""


class FileTools:
    def __init__(self, folders: ApprovedFolders) -> None:
        self._folders = folders

    def _resolve(self, raw: object) -> tuple[Path, Path]:
        """Return (resolved path, the approved root containing it)."""
        # A folder approved before the breadth check existed (a drive, the home folder) is ignored.
        roots = [r for r in (g.path.resolve() for g in self._folders.list()) if not broad_reason(r)]
        if not roots:
            raise FileAccessError(
                "No folders are approved for file access. Ask the user to add one in Settings."
            )
        if not isinstance(raw, str) or not raw.strip():
            raise FileAccessError("A path is required.")
        path = Path(raw).expanduser()
        workspace = self._folders.workspace
        if not path.is_absolute():
            if workspace is None:
                raise FileAccessError(f"Use a full path, not {raw!r}.")
            path = workspace / path
        path = path.resolve()
        for root in roots:
            if path == root or path.is_relative_to(root):
                if reason := protected_reason(path):
                    raise FileAccessError(f"Piyo does not touch {raw}: {reason}.")
                if root == workspace:
                    workspace.mkdir(parents=True, exist_ok=True)  # created on first use
                return path, root
        approved = ", ".join(str(r) for r in roots)
        raise FileAccessError(f"{raw} is outside the approved folders ({approved}).")

    def _recheck(self, path: Path, root: Path) -> None:
        """Resolve again just before use: a link swapped in since `_resolve` would lead somewhere else."""
        again = path.resolve()
        if again != path or not (again == root or again.is_relative_to(root)) or protected_reason(again):
            raise FileAccessError(f"{path} changed while it was being checked; nothing was done.")

    def _auto(self, raw: object) -> bool:
        """True if the path is inside a folder where changes need no per-call approval."""
        try:
            _, root = self._resolve(raw)
        except FileAccessError:
            return False
        return any(g.auto_changes and g.path.resolve() == root for g in self._folders.list())

    # One-line descriptions for the approval card, from the real arguments (see `Tool.summarize`).
    def summary_write(self, args: dict) -> str:
        n = len(args.get("content") or "")
        verb = "Replace the contents of" if args.get("overwrite") else "Create the file"
        return f"{verb} {args.get('path')} ({n} characters)"

    def summary_create(self, args: dict) -> str:
        return f"Create the folder {args.get('path')}"

    def summary_move(self, args: dict) -> str:
        return f"Move {args.get('source')} to {args.get('destination')}"

    def summary_delete(self, args: dict) -> str:
        return f"Move {args.get('path')} to the trash"

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
        workspace = self._folders.workspace
        lines = []
        if workspace is not None:
            lines.append(
                f"Your workspace is {workspace}. Save new files and folders there unless the user "
                "names another approved folder; a relative path such as notes/todo.txt means the workspace."
            )
        lines.append("Approved folders (reading is always allowed in them):")
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
        everything = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        entries = [p for p in everything if not protected_reason(p)]
        hidden = len(everything) - len(entries)
        lines = [
            f"{p.name}/" if p.is_dir() else f"{p.name}  ({p.stat().st_size} bytes)"
            for p in entries[:MAX_LIST_ENTRIES]
        ]
        if len(entries) > MAX_LIST_ENTRIES:
            lines.append(f"... {len(entries) - MAX_LIST_ENTRIES} more not shown")
        if hidden:
            lines.append(f"({hidden} protected item(s) not shown: keys, credentials, browser data)")
        # File names are chosen by whoever made the file, so they are outside text too.
        return wrap_untrusted("\n".join(lines) or "(empty folder)", f"folder: {path}")

    async def read(self, args: dict, ctx: RunContext) -> str:
        path, root = self._resolve(args.get("path"))
        if not path.is_file():
            raise FileAccessError(f"Not a file: {path}")
        self._recheck(path, root)
        with os.fdopen(os.open(path, os.O_RDONLY | _NO_FOLLOW), "rb") as f:
            data = f.read(MAX_READ_BYTES + 1)
        text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        if len(data) > MAX_READ_BYTES:
            text += f"\n[truncated at {MAX_READ_BYTES} bytes]"
        return wrap_untrusted(text, f"file: {path}")

    async def write(self, args: dict, ctx: RunContext) -> str:
        path, root = self._resolve(args.get("path"))
        content = args.get("content")
        if not isinstance(content, str):
            raise FileAccessError("content must be text.")
        if path.is_dir():
            raise FileAccessError(f"{path} is a folder.")
        if path.exists() and not args.get("overwrite"):
            raise FileAccessError(f"{path} already exists. Pass overwrite=true to replace it.")
        if not path.parent.is_dir():
            raise FileAccessError(f"Folder does not exist: {path.parent}")
        self._recheck(path, root)
        # Without overwrite the file is created exclusively: one that appeared since the check stays.
        mode = os.O_TRUNC if args.get("overwrite") else os.O_EXCL
        flags = os.O_WRONLY | os.O_CREAT | _NO_FOLLOW | mode
        try:
            fd = os.open(path, flags, 0o666)
        except FileExistsError:
            raise FileAccessError(f"{path} already exists. Pass overwrite=true to replace it.") from None
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return f"Wrote {len(content)} characters to {path}"

    async def create_folder(self, args: dict, ctx: RunContext) -> str:
        path, root = self._resolve(args.get("path"))
        if path.exists():
            raise FileAccessError(f"{path} already exists.")
        if not path.parent.is_dir():
            raise FileAccessError(f"Folder does not exist: {path.parent}")
        self._recheck(path, root)
        path.mkdir()
        return f"Created folder {path}"

    async def move(self, args: dict, ctx: RunContext) -> str:
        src, src_root = self._resolve(args.get("source"))
        dest, dest_root = self._resolve(args.get("destination"))
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
        self._recheck(src, src_root)
        self._recheck(dest, dest_root)
        shutil.move(str(src), str(dest))
        return f"Moved {src} to {dest}"

    async def delete(self, args: dict, ctx: RunContext) -> str:
        path, root = self._resolve(args.get("path"))
        if path == root:
            raise FileAccessError("An approved folder itself can't be deleted.")
        if not path.exists():
            raise FileAccessError(f"Not found: {path}")
        if path.is_dir() and any(path.iterdir()):
            raise FileAccessError(f"{path} is not empty; only empty folders go.")
        self._recheck(path, root)
        try:
            send2trash(str(path))  # recoverable: the OS trash, never a permanent delete
        except Exception as e:
            raise FileAccessError(
                f"Could not move {path} to the trash ({type(e).__name__}), so nothing was deleted."
            ) from None
        return f"Moved {path} to the trash (the user can restore it from there)."


def file_tools(folders: ApprovedFolders) -> list[Tool]:
    impl = FileTools(folders)
    path = {
        "type": "string",
        "description": "Full path inside an approved folder, or a relative path (inside the workspace)",
    }

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
            summarize=impl.summary_write,
        ),
        Tool(
            name="files.create_folder",
            description="Create one new folder inside an approved folder (its parent must exist).",
            parameters=schema(["path"], path=path),
            handler=impl.create_folder,
            risk=Risk.CONFIRM,
            risk_for=impl.risk_create,
            summarize=impl.summary_create,
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
            summarize=impl.summary_move,
        ),
        Tool(
            name="files.delete",
            description="Move one file or one empty folder in an approved folder to the trash.",
            parameters=schema(["path"], path=path),
            handler=impl.delete,
            risk=Risk.CONFIRM,
            summarize=impl.summary_delete,
        ),
    ]
