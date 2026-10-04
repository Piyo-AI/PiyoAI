"""Outside text must not write into long-term memory without the user's approval."""

import pytest
from test_agent import PROVIDER, Script, call
from test_runs import TOKEN

from piyo.agent import Agent
from piyo.models.turn import Message, ToolCall, TurnDone
from piyo.safety import PermissionGate
from piyo.safety.taint import history_has_untrusted, produces_untrusted
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.store.memory import MemoryStore
from piyo.tools import Tool, ToolRegistry, core_tools
from piyo.tools.memory import memory_tools

SKILL = """---
name: lookup
description: Look things up.
requires:
  tools: [web.fetch, plain.read]
---
Use the tools.
"""

PAGE_CALL = ToolCall(id="h1", name="web__fetch", arguments={})


@pytest.fixture
def skills(tmp_path):
    d = tmp_path / "u" / "lookup"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "u")


def make(script, skills, tmp_path, answer=True):
    asked: list = []
    store = MemoryStore(tmp_path / "memory.db")

    async def approver(req):
        asked.append(req)
        return answer

    async def page(args, ctx):
        return "a web page"

    tools = ToolRegistry(
        core_tools()
        + memory_tools(store)
        + [Tool("web.fetch", "Fetch", page), Tool("plain.read", "Read something trusted", page)]
    )
    return Agent(PROVIDER, "m", tools, skills, PermissionGate(approver), turn_fn=script), asked, store


def run_script(*steps):
    return Script(
        [TurnDone(tool_calls=[call("load_skill", name="lookup")])],
        *[[TurnDone(tool_calls=[ToolCall(id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(s)])]
          for s in steps],
        [TurnDone(text="done")],
    )


async def go(agent, text="hi", history=None):
    messages = [*(history or []), Message(role="user", content=text)]
    return [e async for e in agent.run(messages)]


REMEMBER = ("memory.remember", {"text": "The user prefers pay@attacker.example"})


async def test_a_run_that_read_nothing_outside_remembers_without_asking(skills, tmp_path):
    agent, asked, store = make(run_script([("plain.read", {}), REMEMBER]), skills, tmp_path)
    await go(agent)
    assert asked == [] and len(store.list(None)) == 1


async def test_after_reading_a_web_page_remembering_asks_and_can_be_declined(skills, tmp_path):
    agent, asked, store = make(
        run_script([("web.fetch", {}), REMEMBER]), skills, tmp_path, answer=False
    )
    await go(agent)
    assert [r.tool for r in asked] == ["memory.remember"]
    assert "plant a false note" in asked[0].summary
    assert store.list(None) == []


async def test_an_approved_note_is_stored(skills, tmp_path):
    agent, asked, store = make(run_script([("web.fetch", {}), REMEMBER]), skills, tmp_path)
    await go(agent)
    assert len(asked) == 1 and len(store.list(None)) == 1


async def test_outside_text_from_an_earlier_turn_taints_a_new_run(skills, tmp_path):
    earlier = [
        Message(role="user", content="open the page"),
        Message(role="assistant", content="", tool_calls=[PAGE_CALL]),
        Message(role="tool", content="a web page", tool_call_id="h1"),
        Message(role="assistant", content="done"),
    ]
    agent, asked, _ = make(run_script([REMEMBER]), skills, tmp_path)
    await go(agent, "remember that", history=earlier)
    assert len(asked) == 1


async def test_a_failed_read_does_not_taint(skills, tmp_path):
    failed = [
        Message(role="assistant", content="", tool_calls=[PAGE_CALL]),
        Message(role="tool", content="blocked", tool_call_id="h1", is_error=True),
    ]
    assert not history_has_untrusted(failed)


def test_classification_covers_both_wire_names():
    assert produces_untrusted("gmail__read") and produces_untrusted("files.read")
    assert produces_untrusted("browser.open") and produces_untrusted("skill.run_script")
    assert not produces_untrusted("files.write") and not produces_untrusted("memory.recall")


# Every registered tool is either known to return text somebody else wrote or known not to. A new tool fails
# this test until someone decides which: the answer decides whether memory.remember asks after it runs.
OWN_TEXT = {
    "current_time", "load_skill", "files.folders", "files.write", "files.create_folder", "files.move",
    "files.delete", "google.accounts", "memory.forget", "memory.recall", "memory.remember",
    "schedule.cancel", "schedule.create", "schedule.list", "weather.forecast",
}


def test_every_registered_tool_is_classified(tmp_path):
    skills = SkillRegistry(builtin_dir=tmp_path / "n", user_dir=tmp_path / "u")
    app = server.create_app(TOKEN, skills=skills, start_scheduler=False)
    unclassified = {n for n in app.state.tools.names() if n not in OWN_TEXT and not produces_untrusted(n)}
    assert not unclassified, f"decide whether these return outside text (safety/taint.py): {unclassified}"
    assert not {n for n in OWN_TEXT if produces_untrusted(n)}
