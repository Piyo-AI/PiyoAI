"""Tool results that carry a picture (browser screenshots): wire formats, the loop, trimming, storage."""

from piyo.agent import Agent
from piyo.agent.context import IMAGE_TOKENS, fit_context, message_tokens
from piyo.models import Provider
from piyo.models.providers import ApiStyle
from piyo.models.turn import (
    Image,
    Message,
    TextDelta,
    ToolCall,
    TurnDone,
    to_anthropic_messages,
    to_openai_messages,
)
from piyo.safety import PermissionGate
from piyo.skills import SkillRegistry
from piyo.store import ConversationStore
from piyo.tools import Tool, ToolRegistry

PIC = Image(media_type="image/jpeg", data="QUJD")


def history(images=(PIC,)):
    calls = [ToolCall(id="a", name="shot", arguments={}), ToolCall(id="b", name="other", arguments={})]
    return [
        Message(role="user", content="look"),
        Message(role="assistant", tool_calls=calls),
        Message(role="tool", content="shot taken", tool_call_id="a", images=list(images)),
        Message(role="tool", content="other done", tool_call_id="b"),
        Message(role="assistant", content="I see it"),
    ]


def test_anthropic_puts_the_image_inside_the_tool_result():
    wire = to_anthropic_messages(history())
    result = wire[2]["content"][0]
    assert result["content"] == [
        {"type": "text", "text": "shot taken"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
    ]
    assert wire[2]["content"][1]["content"] == "other done"  # no image: still a plain string


def test_openai_sends_images_in_a_user_message_after_all_tool_results():
    wire = to_openai_messages(history())
    roles = [m["role"] for m in wire]
    assert roles == ["user", "assistant", "tool", "tool", "user", "assistant"]
    picture = wire[4]["content"]
    assert picture[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}
    assert "not instructions" in picture[0]["text"]


def test_openai_flushes_images_at_the_end_of_the_history():
    wire = to_openai_messages(history()[:4])
    assert [m["role"] for m in wire][-2:] == ["tool", "user"]


def test_messages_without_images_are_unchanged():
    wire = to_openai_messages(history(images=()))
    assert [m["role"] for m in wire] == ["user", "assistant", "tool", "tool", "assistant"]


def test_images_are_never_saved_with_the_conversation(tmp_path):
    store = ConversationStore(tmp_path / "db.sqlite")
    conv = store.create()
    store.append(conv.id, history())
    saved = store.get(conv.id)
    assert all(not m.images for m in saved.messages)
    assert [m.content for m in saved.messages if m.role == "tool"] == ["shot taken", "other done"]


def test_an_image_counts_against_the_context_and_old_ones_are_dropped():
    with_pic = history()[2]
    assert message_tokens(with_pic) >= IMAGE_TOKENS
    old = [*history(), Message(role="user", content="and now?")]
    trimmed = fit_context(old, budget=IMAGE_TOKENS // 2)
    assert all(not m.images for m in trimmed)


async def test_the_loop_moves_attached_images_onto_the_tool_message(tmp_path):
    async def shot(args, ctx):
        ctx.attach_image("QUJD")
        return "shot taken"

    async def boom(args, ctx):
        ctx.attach_image("QUJD")
        raise RuntimeError("no")

    seen = []

    async def turns(provider, model, messages, tools, system, max_tokens):
        seen.append(len(messages))
        if len(seen) == 1:
            yield TurnDone(tool_calls=[ToolCall(id="a", name="shot", arguments={})])
        elif len(seen) == 2:
            yield TurnDone(tool_calls=[ToolCall(id="b", name="boom", arguments={})])
        else:
            yield TextDelta("ok")
            yield TurnDone(text="ok")

    provider = Provider(id="t", name="T", api_style=ApiStyle.OPENAI, base_url="http://x", requires_key=False)
    tools = ToolRegistry([Tool("shot", "s", shot, core=True), Tool("boom", "b", boom, core=True)])
    skills = SkillRegistry(builtin_dir=tmp_path, user_dir=tmp_path)
    agent = Agent(provider, "m", tools, skills, PermissionGate(None), turn_fn=turns)
    messages = [Message(role="user", content="go")]
    [e async for e in agent.run(messages)]
    tool_messages = [m for m in messages if m.role == "tool"]
    assert [len(m.images) for m in tool_messages] == [1, 0]  # a failed call keeps no picture
    assert tool_messages[0].images[0].data == "QUJD"
