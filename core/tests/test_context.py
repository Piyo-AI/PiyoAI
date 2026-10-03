from piyo.agent import Agent, Finished
from piyo.agent.context import fit_context, message_tokens
from piyo.models import Provider
from piyo.models.providers import ApiStyle
from piyo.models.turn import Message, ToolCall, TurnDone
from piyo.skills import SkillRegistry
from piyo.tools import ToolRegistry, core_tools

PROVIDER = Provider(
    id="t", name="T", api_style=ApiStyle.OPENAI, base_url="http://x", requires_key=False
)
BIG = "x" * 3000


def turn(n: int, result: str = BIG) -> list[Message]:
    return [
        Message(role="user", content=f"question {n}"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id=f"c{n}", name="web__fetch", arguments={"url": "u"})],
        ),
        Message(role="tool", content=result, tool_call_id=f"c{n}"),
        Message(role="assistant", content=f"answer {n}"),
    ]


def tokens(messages: list[Message]) -> int:
    return sum(message_tokens(m) for m in messages)


def paired(messages: list[Message]) -> bool:
    calls = {c.id for m in messages for c in m.tool_calls}
    results = {m.tool_call_id for m in messages if m.role == "tool"}
    return calls == results


def test_fits_untouched():
    history = turn(1) + turn(2)
    assert fit_context(history, tokens(history)) == history


def test_old_tool_output_goes_first():
    history = turn(1) + turn(2) + turn(3)
    out = fit_context(history, tokens(history) - 1500)
    assert len(out) == len(history)  # no turn dropped, only output replaced
    assert "removed" in out[2].content
    assert out[-2].content == BIG  # the current turn is untouched
    assert tokens(out) <= tokens(history) - 1500


def test_old_turns_dropped_whole_and_pairs_stay_intact():
    history = turn(1) + turn(2) + turn(3)
    out = fit_context(history, 1050)
    assert out[0].role == "user" and out[0].content == "question 3"
    assert paired(out)


def test_current_turn_is_never_dropped():
    history = turn(1) + turn(2)
    out = fit_context(history, 10)
    assert [m.content for m in out if m.role == "user"] == ["question 2"]
    assert paired(out)


def test_current_turn_earlier_results_trimmed_last_kept():
    first = turn(1)
    # One turn with two tool rounds: the first result may go, the latest stays.
    extra = [
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c9", name="web__fetch", arguments={})],
        ),
        Message(role="tool", content=BIG, tool_call_id="c9"),
    ]
    history = first[:3] + extra + [Message(role="assistant", content="done")]
    out = fit_context(history, tokens(history) - 500)
    assert "removed" in out[2].content
    assert out[4].content == BIG


def test_stored_history_is_not_modified():
    history = turn(1) + turn(2) + turn(3)
    before = [m.model_copy() for m in history]
    fit_context(history, 10)
    assert history == before


async def test_agent_sends_trimmed_view_but_keeps_full_history():
    sent: list[int] = []

    async def fake(provider, model, messages, tools, system, max_tokens):
        sent.append(len(messages))
        yield TurnDone(text="ok")

    history = turn(1) + turn(2) + [Message(role="user", content="now")]
    agent = Agent(
        PROVIDER, "m", ToolRegistry(core_tools()), SkillRegistry(), turn_fn=fake,
        max_tokens=256, context_tokens=800,
    )
    events = [e async for e in agent.run(history)]
    assert isinstance(events[-1], Finished)
    assert sent[0] == 1  # old turns were cut from what the model saw
    assert len(history) == 10  # 9 stored + the reply
