"""`ui.show`: lets a skill's instructions present an answer as a widget (docs/phase-8-response-views.md).

The model gives a view type and its data; `validate_view` checks it exactly like a view a tool attaches, and a
refused one comes back as an error so the model can fix it. The user sees the widget; the model is told not to
repeat it. It shows what the model passes in and reads nothing, so it is `auto`, and a skill lists it in
`requires.tools` like any other tool.
"""

from __future__ import annotations

from piyo.tools.base import RunContext, Tool
from piyo.views import SHOWABLE, VIEW_DOCS, validate_view


async def _show(args: dict, ctx: RunContext) -> str:
    kind = args.get("type")
    if kind not in SHOWABLE:
        raise ValueError(f"ui.show cannot show {kind!r}; use one of: {', '.join(SHOWABLE)}")
    # A ViewError reaches the model as the error, so it can fix the data.
    view = validate_view({"type": kind, "data": args.get("data")})
    ctx.pending_view = view
    ctx.view_problem = None
    return (
        f"Shown to the user as a {kind}. Do not repeat its contents in your reply; add only what it does not "
        "say, in a sentence or two."
    )


def ui_tools() -> list[Tool]:
    shapes = "\n".join(f"- {kind}: {VIEW_DOCS[kind]}" for kind in SHOWABLE)
    return [
        Tool(
            name="ui.show",
            description=(
                "Show the user a result as a widget (table, key-value card, timeline, list or message list) "
                "instead of describing it in text. The user sees it right away. Shapes of `data`:\n" + shapes
            ),
            parameters={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(SHOWABLE)},
                    "data": {"type": "object", "description": "The widget data, in the shape for its type."},
                },
                "required": ["type", "data"],
            },
            handler=_show,
        )
    ]
