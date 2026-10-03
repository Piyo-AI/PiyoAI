import pytest

from piyo.agent import Agent, Finished, Text, ToolFinished
from piyo.models import Provider
from piyo.models.providers import ApiStyle
from piyo.models.turn import (
    Message,
    TextDelta,
    ToolCall,
    TurnDone,
    to_anthropic_messages,
    to_openai_messages,
)
from piyo.safety import PermissionGate
from piyo.skills import SkillRegistry
from piyo.tools import Risk, Tool, ToolRegistry, core_tools

PROVIDER = Provider(
    id="t", name="T", api_style=ApiStyle.OPENAI, base_url="http://x", requires_key=False
)

SKILL = """---
name: mailer
description: Handle email.
requires:
  tools: [mail.read, mail.send]
---
Read mail, then send replies.
"""


class Script:
    """Fake model: plays back one list of events per turn and records what it was sent."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen: list[dict] = []

    async def __call__(self, provider, model, messages, tools, system, max_tokens):
        self.seen.append({"tools": [t.name for t in tools], "system": system, "n": len(messages)})
        for event in self.turns.pop(0):
            yield event


def call(tool, _id="c1", **args):
    return ToolCall(id=_id, name=tool, arguments=args)


def mail_tools(sent):
    async def read(args, ctx):
        return "1 unread"

    async def send(args, ctx):
        sent.append(args)
        return "sent"

    return [
        Tool("mail.read", "Read mail", read),
        Tool(
            "mail.send",
            "Send mail",
            send,
            risk=Risk.CONFIRM,
            parameters={"type": "object", "properties": {}, "required": ["to"]},
        ),
    ]


def _always(answer):
    async def approver(req):
        return answer

    return approver


@pytest.fixture
def skills(tmp_path):
    d = tmp_path / "u" / "mailer"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "u")


def make_agent(script, skills, sent, approver=None, **kw):
    tools = ToolRegistry(core_tools() + mail_tools(sent))
    return Agent(PROVIDER, "m", tools, skills, PermissionGate(approver), turn_fn=script, **kw)


async def collect(agent, text="hi"):
    messages = [Message(role="user", content=text)]
    return messages, [e async for e in agent.run(messages)]


async def test_plain_reply(skills):
    script = Script([TextDelta("Hel"), TextDelta("lo"), TurnDone(text="Hello")])
    messages, events = await collect(make_agent(script, skills, []))
    assert [e.text for e in events if isinstance(e, Text)] == ["Hel", "lo"]
    assert events[-1] == Finished("done", [])
    assert messages[-1].role == "assistant" and messages[-1].content == "Hello"
    assert "mailer: Handle email." in script.seen[0]["system"]


async def test_skill_unlocks_tools_progressively(skills):
    script = Script(
        [TurnDone(tool_calls=[call("load_skill", name="mailer")])],
        [TurnDone(tool_calls=[call("mail.read")])],
        [TurnDone(text="done")],
    )
    _, events = await collect(make_agent(script, skills, []))
    assert "mail__read" not in script.seen[0]["tools"]  # hidden until the skill is loaded
    assert "mail__read" in script.seen[1]["tools"]
    results = [e for e in events if isinstance(e, ToolFinished)]
    assert "Read mail, then send replies." in results[0].output
    assert results[1].output == "1 unread"
    assert events[-1].active_skills == ["mailer"]


async def test_ungranted_tool_is_rejected(skills):
    sent = []
    script = Script(
        [TurnDone(tool_calls=[call("mail.send", to="a@b.c")])],  # skill never loaded
        [TurnDone(text="ok")],
    )
    _, events = await collect(make_agent(script, skills, sent, approver=_always(True)))
    result = next(e for e in events if isinstance(e, ToolFinished))
    assert result.is_error and sent == []


@pytest.mark.parametrize("approve, expect_sent", [(True, 1), (False, 0)])
async def test_confirm_tools_need_approval(skills, approve, expect_sent):
    sent = []
    script = Script(
        [TurnDone(tool_calls=[call("load_skill", name="mailer")])],
        [TurnDone(tool_calls=[call("mail.send", to="a@b.c")])],
        [TurnDone(text="ok")],
    )
    _, events = await collect(make_agent(script, skills, sent, approver=_always(approve)))
    assert len(sent) == expect_sent
    assert [e.is_error for e in events if isinstance(e, ToolFinished)][-1] is (not approve)


async def test_confirm_without_approver_is_denied(skills):
    sent = []
    script = Script(
        [TurnDone(tool_calls=[call("load_skill", name="mailer")])],
        [TurnDone(tool_calls=[call("mail.send", to="a@b.c")])],
        [TurnDone(text="ok")],
    )
    await collect(make_agent(script, skills, sent))
    assert sent == []


async def test_bad_arguments_reported_to_model(skills):
    script = Script(
        [TurnDone(tool_calls=[call("load_skill", name="mailer")])],
        [TurnDone(tool_calls=[call("mail.send")])],  # missing "to"
        [TurnDone(tool_calls=[ToolCall(id="c3", name="mail.read", parse_error="bad json")])],
        [TurnDone(text="ok")],
    )
    _, events = await collect(make_agent(script, skills, [], approver=_always(True)))
    errors = [e.output for e in events if isinstance(e, ToolFinished) and e.is_error]
    assert "Missing required" in errors[0] and "bad json" in errors[1]


async def test_tool_exception_becomes_error_result(skills):
    async def boom(args, ctx):
        raise RuntimeError("disk on fire")

    tools = ToolRegistry(core_tools() + [Tool("boom", "x", boom, core=True)])
    script = Script([TurnDone(tool_calls=[call("boom")])], [TurnDone(text="ok")])
    agent = Agent(PROVIDER, "m", tools, skills, turn_fn=script)
    _, events = await collect(agent)
    result = next(e for e in events if isinstance(e, ToolFinished))
    assert result.is_error and "disk on fire" in result.output


async def test_step_limit(skills):
    script = Script(*[[TurnDone(tool_calls=[call("current_time", f"c{i}")])] for i in range(3)])
    _, events = await collect(make_agent(script, skills, [], max_steps=3))
    assert events[-1].reason == "step_limit"


def test_wire_formats_group_tool_results():
    msgs = [
        Message(role="user", content="go"),
        Message(role="assistant", content="", tool_calls=[call("a", "1"), call("b", "2")]),
        Message(role="tool", content="A", tool_call_id="1"),
        Message(role="tool", content="B", tool_call_id="2", is_error=True),
    ]
    anthropic = to_anthropic_messages(msgs)
    assert [m["role"] for m in anthropic] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in anthropic[2]["content"]] == ["1", "2"]
    openai = to_openai_messages(msgs, system="sys")
    assert [m["role"] for m in openai] == ["system", "user", "assistant", "tool", "tool"]
    assert openai[2]["tool_calls"][0]["function"]["arguments"] == "{}"
