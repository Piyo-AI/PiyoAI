"""What the selected model can do, checked against what a skill declares in `requires.model`.

`None` means unknown (most providers don't report vision). Unknown never blocks: only a known
mismatch does, so a local vision model the provider can't describe still works. Tool calling is not
checked here: a model without it still runs through the text fallback (`models/prompt_tools.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from piyo.skills.manifest import ModelNeeds


@dataclass(frozen=True)
class ModelCaps:
    model: str
    context: int  # the window Piyo fits the conversation into
    vision: bool | None = None


def model_issues(needs: ModelNeeds, caps: ModelCaps) -> list[str]:
    """Why `caps` can't satisfy `needs`, as sentences a user can act on. Empty means fine."""
    issues = []
    if needs.vision and caps.vision is False:
        issues.append(f"it needs a model that can read images, and {caps.model} can't")
    if needs.min_context and caps.context < needs.min_context:
        issues.append(
            f"it needs a context window of at least {needs.min_context} tokens, but "
            f"{caps.model} is set to {caps.context} (raise Context in Model settings, "
            "or pick another model)"
        )
    return issues
