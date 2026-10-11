"""Response views: a tool shows its result as a widget instead of plain text (docs/phase-8-response-views.md).

A view is data, never markup: `{"type", "v", "data", "fallback"}`. The app renders it with its own
components from a closed list (`VIEW_TYPES`); nothing a tool or skill sends can run code, load a remote
file or look like an approval card. Everything is checked here before it is shown or saved, and the model
never sees a view (it gets the tool's text as before), so a view changes only what the user sees.
"""

from piyo.views.schema import (
    MAX_FALLBACK_CHARS,
    SHOWABLE,
    VIEW_DOCS,
    VIEW_TYPES,
    ViewError,
    validate_view,
)

__all__ = ["MAX_FALLBACK_CHARS", "SHOWABLE", "VIEW_DOCS", "VIEW_TYPES", "ViewError", "validate_view"]
