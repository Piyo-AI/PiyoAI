"""A view as plain text: what the user sees when the app cannot draw it.

Written by the core from the checked data, so a tool never has to write its answer twice.
"""

from __future__ import annotations

MAX_CHARS = 2000


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):  # a link
        return f"{value.get('text', '')} ({value.get('url', '')})"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _lines(kind: str, data: dict) -> list[str]:
    items = data.get("items") or []
    if kind == "table":
        out = [" | ".join(data["columns"])]
        out += [" | ".join(_cell(c) for c in row) for row in data["rows"]]
        return out
    if kind == "key-value":
        return [f"{i['label']}: {_cell(i['value'])}" for i in items]
    if kind == "timeline":
        out = []
        for i in items:
            head = f"{i['when']}  {i['title']}" if i.get("when") else i["title"]
            out.append(head + (f" - {i['detail']}" if i.get("detail") else ""))
        return out
    if kind == "list":
        out = []
        for n, i in enumerate(items, 1):
            mark = ("[x] " if i["checked"] else "[ ] ") if i.get("checked") is not None else ""
            out.append(f"{n}. {mark}{i['text']}" if data.get("ordered") else f"- {mark}{i['text']}")
        return out
    if kind == "message-list":
        out = []
        for i in items:
            when = f" ({i['when']})" if i.get("when") else ""
            out.append(f"{i['sender']}: {i.get('subject', '')}{when}")
            if i.get("snippet"):
                out.append(f"  {i['snippet']}")
        return out
    return []  # weather and unknown types: the tool's own text is the fallback


def plain_text(kind: str, data: dict) -> str:
    lines = _lines(kind, data)
    if lines and data.get("title"):
        lines.insert(0, data["title"])
    return "\n".join(lines)[:MAX_CHARS]
