import re

import pytest
from test_agent import PROVIDER, Script, call

from piyo.agent import Agent, ToolFinished
from piyo.models.turn import Message, TurnDone
from piyo.skills import SkillRegistry
from piyo.store.runs import RunLog
from piyo.tools import Risk, RunContext, ToolRegistry, core_tools
from piyo.tools.ui import ui_tools
from piyo.views import SHOWABLE, VIEW_DOCS, VIEW_TYPES, ViewError, validate_view
from piyo.views.text import plain_text

LINK = {"text": "Track", "url": "https://carrier.example/t/1"}
GOOD = {
    "table": {
        "title": "Prices",
        "columns": ["Shop", "Price"],
        "rows": [["A", 10.0], ["B", LINK], ["C", None]],
    },
    "key-value": {
        "title": "Parcel",
        "items": [{"label": "Status", "value": "In transit"}, {"label": "Page", "value": LINK}],
    },
    "timeline": {
        "title": "Today",
        "items": [
            {"when": "09:00-10:00", "title": "Standup", "detail": "Room 2", "tone": "warn"},
            {"title": "Lunch", "link": LINK},
        ],
    },
    "list": {
        "title": "Todo",
        "ordered": True,
        "items": [{"text": "Milk", "checked": True}, {"text": "Eggs", "checked": False}],
    },
    "message-list": {
        "title": "Unread",
        "items": [
            {
                "sender": "Ana",
                "subject": "Lunch?",
                "snippet": "Are you free",
                "when": "10:12",
                "action": "reply",
            }
        ],
    },
}  # fmt: skip


def test_every_type_has_good_data_docs_and_a_showable_flag():
    assert set(GOOD) == set(VIEW_TYPES) - {"weather"} == set(SHOWABLE) == set(VIEW_DOCS)


@pytest.mark.parametrize("kind", sorted(GOOD))
def test_good_data_validates_and_gets_a_plain_text_fallback(kind):
    view = validate_view({"type": kind, "data": GOOD[kind]})
    assert view["type"] == kind and view["fallback"]
    assert view["fallback"] == plain_text(kind, view["data"])
    assert len(view["fallback"]) <= 2000


def test_fallback_texts_read_well():
    def text(kind):
        return validate_view({"type": kind, "data": GOOD[kind]})["fallback"]

    assert text("table") == "Prices\nShop | Price\nA | 10\nB | Track (https://carrier.example/t/1)\nC | "
    assert text("key-value").splitlines()[1] == "Status: In transit"
    assert "09:00-10:00  Standup - Room 2" in text("timeline") and "\nLunch" in text("timeline")
    assert text("list") == "Todo\n1. [x] Milk\n2. [ ] Eggs"
    assert text("message-list") == "Unread\nAna: Lunch? (10:12)\n  Are you free"


def test_an_explicit_fallback_wins():
    view = validate_view({"type": "list", "data": GOOD["list"], "fallback": "mine"})
    assert view["fallback"] == "mine"


BAD = [
    ("table", {"columns": ["A", "B"], "rows": [["only one"]]}, "cells"),
    ("table", {"columns": [], "rows": []}, "columns"),
    ("table", {"columns": [str(i) for i in range(9)], "rows": []}, "columns"),
    ("table", {"columns": ["A"], "rows": [["x"]] * 51}, "rows"),
    ("table", {"columns": ["A"], "rows": [["x" * 300]]}, "rows"),
    ("table", {"columns": ["A"], "rows": [[{"text": "x", "url": "javascript:alert(1)"}]]}, "rows"),
    ("table", {"columns": ["A"], "rows": [[{"text": "x", "url": "https://a.test/ b"}]]}, "rows"),
    ("table", {"columns": ["A"], "rows": [[{"text": "x", "url": "data:text/html,<b>"}]]}, "rows"),
    ("table", {"columns": ["A"], "rows": [[True]]}, "rows"),
    ("table", {"columns": ["A"], "rows": [[float("inf")]]}, "rows"),
    ("key-value", {"items": []}, "items"),
    ("key-value", {"items": [{"label": "", "value": "x"}]}, "label"),
    ("timeline", {"items": [{"title": ""}]}, "title"),
    ("timeline", {"items": [{"title": "x", "tone": "angry"}]}, "tone"),
    ("timeline", {"items": [{"title": "x", "icon": "https://evil.test/p.gif"}]}, "icon"),
    ("list", {"items": [{"text": "x", "checked": "yes"}]}, "checked"),
    ("list", {"items": [{"text": "x"}] * 51}, "items"),
    ("message-list", {"items": [{"subject": "no sender"}]}, "sender"),
    ("message-list", {"items": [{"sender": "a", "action": "delete"}]}, "action"),
    ("message-list", {"items": [{"sender": "a", "html": "<b>"}]}, "html"),
]


@pytest.mark.parametrize(("kind", "data", "where"), BAD)
def test_bad_data_is_refused_with_a_reason(kind, data, where):
    with pytest.raises(ViewError, match=where):
        validate_view({"type": kind, "data": data})


def test_markup_in_text_is_kept_as_text_not_removed_or_run():
    evil = "<img src=x onerror=alert(1)>"
    view = validate_view({"type": "list", "data": {"items": [{"text": evil}]}})
    assert view["data"]["items"][0]["text"] == evil  # the app draws it as text; the core does not guess
    assert evil in view["fallback"]


# --- the tool ------------------------------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path):
    return RunContext(skills=SkillRegistry(builtin_dir=tmp_path, user_dir=tmp_path))


def test_ui_show_is_a_plain_auto_tool_that_a_skill_must_grant():
    tool = ui_tools()[0]
    assert tool.name == "ui.show" and tool.risk is Risk.AUTO and not tool.core
    assert tool.risk_for is None and tool.guard is None
    assert tool.parameters["properties"]["type"]["enum"] == list(SHOWABLE)
    assert "weather" not in tool.parameters["properties"]["type"]["enum"]
    for kind in SHOWABLE:  # the model is told every shape
        assert f"- {kind}:" in tool.description


async def test_ui_show_attaches_a_view_and_tells_the_model_not_to_repeat_it(ctx):
    out = await ui_tools()[0].handler({"type": "table", "data": GOOD["table"]}, ctx)
    assert "Do not repeat" in out and "table" in out
    shown, problem = ctx.take_view()
    assert shown["type"] == "table" and problem is None


@pytest.mark.parametrize(
    "args",
    [
        {"type": "weather", "data": {}},
        {"type": "carousel", "data": {}},
        {"type": "table", "data": {"columns": ["A"], "rows": [["x", "y"]]}},
        {"type": "table"},
        {"data": {}},
    ],
)
async def test_ui_show_refuses_bad_calls_so_the_model_can_fix_them(ctx, args):
    with pytest.raises((ViewError, ValueError)):
        await ui_tools()[0].handler(args, ctx)
    assert ctx.take_view() == (None, None)


async def run_loop(tmp_path, arguments, granted=True):
    skill_dir = tmp_path / "skills" / "shower"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: shower\ndescription: Shows things.\nrequires:\n  tools: [ui.show]\n---\nUse ui.show.\n",
        encoding="utf-8",
    )
    skills = SkillRegistry(builtin_dir=tmp_path / "skills", user_dir=tmp_path / "none")
    skills.reload()
    turns = [[TurnDone(tool_calls=[call("ui.show", "c1", **arguments)])], [TurnDone(text="ok")]]
    if granted:
        turns.insert(0, [TurnDone(tool_calls=[call("load_skill", "c0", name="shower")])])
    script = Script(*turns)
    log = RunLog("c", "t", "m", "go")
    tools = ToolRegistry(core_tools() + ui_tools())
    agent = Agent(PROVIDER, "m", tools, skills, turn_fn=script, log=log)
    messages = [Message(role="user", content="go")]
    events = [e async for e in agent.run(messages)]
    return messages, [e for e in events if isinstance(e, ToolFinished)], log, script


async def test_through_the_loop_a_loaded_skill_can_show_a_view(tmp_path):
    messages, finished, log, _ = await run_loop(tmp_path, {"type": "list", "data": GOOD["list"]})
    shown = next(f for f in finished if f.name == "ui.show")
    assert shown.view["type"] == "list" and not shown.is_error
    saved = [m for m in messages if m.role == "tool"][-1]
    assert saved.view == shown.view and "Shown to the user" in saved.content
    assert [s["data"] for s in log.steps if s["kind"] == "view"] == [{"name": "ui.show", "type": "list"}]


async def test_without_the_skill_the_tool_does_not_exist_for_the_model(tmp_path):
    # the skill is never loaded, so ui.show is not granted
    _, finished, _, script = await run_loop(tmp_path, {"type": "list", "data": GOOD["list"]}, granted=False)
    assert "ui__show" not in script.seen[0]["tools"]
    refused = next(f for f in finished if f.name == "ui.show")
    assert refused.is_error and refused.view is None and "Unknown tool" in refused.output


async def test_a_bad_call_is_an_error_result_the_model_sees(tmp_path):
    wide_row = {"columns": ["A"], "rows": [["x", "y"]]}
    _, finished, _, _ = await run_loop(tmp_path, {"type": "table", "data": wide_row})
    bad = next(f for f in finished if f.name == "ui.show")
    assert bad.is_error and bad.view is None and "cells" in bad.output


# --- the built-in skills that show widgets -------------------------------------------------------

SHOWING_SKILLS = {"morning-brief", "calendar", "gmail-triage", "parcel-tracking", "price-compare"}


def test_built_in_skills_that_show_widgets_ask_for_the_tool_and_say_how():
    skills = SkillRegistry()
    for name in SHOWING_SKILLS:
        skill = skills.get(name)
        assert skill is not None and skill.source == "builtin", name
        assert "ui.show" in skill.manifest.requires.tools, name
        assert "ui.show" in skill.body, f"{name} grants ui.show but never says when to use it"
        assert any(f'"{kind}"' in skill.body for kind in SHOWABLE), f"{name} does not name a view type"
        assert re.search(r"plain[- ]text", skill.body), f"{name} has no fallback for a failed ui.show"
    # no other built-in skill asks for it by accident
    others = {s.manifest.name for s in skills.enabled() if "ui.show" in s.manifest.requires.tools}
    assert others == SHOWING_SKILLS
