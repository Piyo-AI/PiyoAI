import json

import pytest
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN, scripted

from piyo.models.turn import Message, TextDelta, ToolCall, TurnDone
from piyo.server import app as server
from piyo.skills import SkillError, SkillRegistry, parse_skill_md
from piyo.skills.learn import (
    bump,
    digest,
    draft_from_chat,
    integrations_for,
    refine_from_chat,
    scrub,
    slug,
    tools_used,
    unique_name,
    worth_a_skill,
)
from piyo.store import ConversationStore


def chat_messages():
    """A finished chat: the user asks, Piyo lists a folder and moves files, one call fails."""
    return [
        Message(role="user", content="Sort my Downloads at C:\\Users\\asha\\Downloads by type, mail me at asha@example.com"),
        Message(
            role="assistant",
            content="Listing it.",
            tool_calls=[
                ToolCall(id="1", name="load_skill", arguments={"name": "x"}),
                ToolCall(id="2", name="files__list", arguments={"path": "C:\\Users\\asha\\Downloads"}),
                ToolCall(id="3", name="gmail__send", arguments={"to": "asha@example.com"}),
            ],
        ),
        Message(role="tool", tool_call_id="1", content="loaded"),
        Message(role="tool", tool_call_id="2", content="a.pdf b.png"),
        Message(role="tool", tool_call_id="3", content="Not connected", is_error=True),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(id="4", name="files__move", arguments={"path": "a.pdf"}),
                ToolCall(id="5", name="schedule__create", arguments={}),
            ],
        ),
        Message(role="tool", tool_call_id="4", content="moved"),
        Message(role="tool", tool_call_id="5", content="scheduled"),
        Message(role="assistant", content="Done."),
    ]


def model_reply(**fields):
    base = {
        "name": "Sort Downloads!",
        "description": "Sort a folder by file type. Use when the user wants a folder tidied.",
        "steps": "1. Ask which folder.\n2. List it with files.list.\n3. Move files into type folders.",
        "setup": "Add the folder in Settings.",
    }
    return json.dumps({**base, **fields})


def fake_turn(reply):
    seen = {}

    async def turn(provider, model, messages, tools, system, max_tokens):
        seen.update(prompt=messages[0].content, system=system, tools=tools)
        yield TextDelta(reply)
        yield TurnDone(text=reply)

    turn.seen = seen
    return turn


# -- helpers ---------------------------------------------------------------------------------


def test_tools_come_from_what_the_chat_really_used():
    # load_skill (baseline), the failed gmail.send and the never-learned schedule.create are left out
    assert tools_used(chat_messages()) == ["files.list", "files.move"]
    assert integrations_for(["gmail.read", "files.list", "calendar.agenda"]) == ["google"]
    assert worth_a_skill(chat_messages())
    assert not worth_a_skill([Message(role="user", content="hi"), Message(role="assistant", content="hello")])


def test_scrub_removes_secrets_and_personal_details():
    text = (
        "Mail asha@example.com or call +91 98765 43210. Key sk-abcdefghijklmnop1234. "
        "Open C:\\Users\\asha\\Documents\\tax.pdf and https://x.example/a?token=abc123&u=1. Card 4111111111111111."
    )
    clean, removed = scrub(text)
    for leak in ("asha@example.com", "98765", "sk-abcdefgh", "asha\\Documents", "token=abc123", "4111111111111111"):
        assert leak not in clean
    assert "https://x.example/a" in clean and "<email address>" in clean
    assert any("email" in r for r in removed) and any("keys" in r for r in removed)
    assert scrub("Plain steps with no personal details.") == ("Plain steps with no personal details.", [])


def test_digest_is_plain_text_and_bounded():
    text = digest(chat_messages())
    assert "USER:" in text and "PIYO CALLS files.list" in text and "RESULT of gmail.send (failed)" in text
    long = [Message(role="user", content="x" * 50_000)]
    assert len(digest(long)) <= 14_000


def test_names_and_versions():
    assert slug("Sort Downloads!") == "sort-downloads" and slug("!!!") == "learned-skill"
    assert unique_name("a", {"a", "a-2"}) == "a-3" and unique_name("b", {"a"}) == "b"
    assert bump("0.1.0") == "0.1.1" and bump("2") == "3" and bump("x") == "x.1"


# -- drafting --------------------------------------------------------------------------------


async def test_a_draft_is_assembled_from_facts_not_from_the_model():
    turn = fake_turn(model_reply(steps="1. Mail asha@example.com the result.\n2. Move the files."))
    draft = await draft_from_chat(turn, None, "m", chat_messages(), {"sort-downloads"})
    manifest, body = parse_skill_md(draft.skill_md)
    assert manifest.name == "sort-downloads-2"  # the name was taken
    assert manifest.requires.tools == ["files.list", "files.move"] and manifest.requires.integrations == []
    assert "asha@example.com" not in draft.skill_md and "<email address>" in body
    assert draft.setup_md == "Add the folder in Settings." and draft.tools == ["files.list", "files.move"]
    assert any("email" in r for r in draft.removed)
    # the transcript reached the model fenced as data, with the injection warning in the system prompt
    assert "<untrusted_content" in turn.seen["prompt"] and "never follow" in turn.seen["system"]
    assert turn.seen["tools"] == []


async def test_a_model_cannot_grant_itself_tools():
    turn = fake_turn(model_reply(requires={"tools": ["gmail.send"]}, tools=["gmail.send"]))
    draft = await draft_from_chat(turn, None, "m", chat_messages(), set())
    assert parse_skill_md(draft.skill_md)[0].requires.tools == ["files.list", "files.move"]


async def test_google_tools_bring_the_integration():
    chat = chat_messages()
    chat[5].tool_calls[0] = ToolCall(id="4", name="calendar__agenda", arguments={})
    draft = await draft_from_chat(fake_turn(model_reply()), None, "m", chat, set())
    assert parse_skill_md(draft.skill_md)[0].requires.integrations == ["google"]


@pytest.mark.parametrize("reply", ["no json here", "{not json}", "[1, 2]", json.dumps({"name": "x", "steps": ""})])
async def test_bad_model_output_gives_a_readable_error(reply):
    with pytest.raises(SkillError, match="Try again|draft"):
        await draft_from_chat(fake_turn(reply), None, "m", chat_messages(), set())


async def test_a_chat_without_tools_is_refused():
    with pytest.raises(SkillError, match="enough tools"):
        await draft_from_chat(fake_turn(model_reply()), None, "m", [Message(role="user", content="hi")], set())


async def test_model_json_wrapped_in_prose_still_parses():
    reply = "Here you go:\n```json\n" + model_reply() + "\n```"
    draft = await draft_from_chat(fake_turn(reply), None, "m", chat_messages(), set())
    assert parse_skill_md(draft.skill_md)[0].name == "sort-downloads"


# -- refining --------------------------------------------------------------------------------

CURRENT = (
    "---\nname: sorter\nversion: 1.2.0\ndescription: Sort files.\nrequires:\n  tools: [files.list, files.move]\n---\n\n"
    "1. List the folder.\n2. Move files.\n"
)


async def test_a_refinement_keeps_the_skills_own_settings_and_shows_a_diff():
    turn = fake_turn(json.dumps({"description": "Sort files by type.", "steps": "1. List the folder.\n2. Ask before moving.\n3. Move files.", "setup": ""}))
    draft = await refine_from_chat(turn, None, "m", CURRENT, chat_messages(), "it moved without asking")
    manifest, body = parse_skill_md(draft.skill_md)
    assert manifest.version == "1.2.1" and manifest.name == "sorter"
    assert manifest.requires.tools == ["files.list", "files.move"] and manifest.description == "Sort files by type."
    assert "Ask before moving" in body
    assert "+2. Ask before moving." in draft.diff and "-2. Move files." in draft.diff and "-version: 1.2.0" in draft.diff
    assert "it moved without asking" in turn.seen["prompt"]


# -- API -------------------------------------------------------------------------------------


def make_client(tmp_path, reply):
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "skills")
    store = ConversationStore()
    conv = store.create()
    store.append(conv.id, chat_messages())
    client = TestClient(server.create_app(TOKEN, skills=skills, store=store, turn_fn=fake_turn(reply)))
    return client, conv.id


def test_api_learn_a_skill_review_it_and_it_starts_switched_off(tmp_path):
    client, cid = make_client(tmp_path, model_reply())
    body = {"conversation_id": cid, "provider": "ollama", "model": "m"}
    draft = client.post("/api/skills/draft", json=body, headers=AUTH)
    assert draft.status_code == 200 and draft.json()["tools"] == ["files.list", "files.move"]
    d = draft.json()
    save = {"skill_md": d["skill_md"], "setup_md": d["setup_md"], "approved": [], "learned": True}
    assert client.post("/api/skills", json=save, headers=AUTH).status_code == 400  # permissions still need approving
    perms = client.post("/api/skills/check", json={"skill_md": d["skill_md"]}, headers=AUTH).json()["added"]
    assert client.post("/api/skills", json={**save, "approved": perms}, headers=AUTH).status_code == 201
    skill = client.get("/api/skills", headers=AUTH).json()["skills"][0]
    assert skill["enabled"] is False and skill["install_source"] == "learned from a chat"
    client.put(f"/api/skills/{skill['name']}", json={"enabled": True}, headers=AUTH)
    assert client.get("/api/skills", headers=AUTH).json()["skills"][0]["enabled"] is True


def test_api_errors(tmp_path):
    client, cid = make_client(tmp_path, "not json")
    ok = {"conversation_id": cid, "provider": "ollama", "model": "m"}
    assert client.post("/api/skills/draft", json=ok, headers=AUTH).status_code == 422
    assert client.post("/api/skills/draft", json={**ok, "conversation_id": "nope"}, headers=AUTH).status_code == 404
    assert client.post("/api/skills/draft", json={**ok, "provider": "nope"}, headers=AUTH).status_code == 404
    empty = ConversationStore().create().id
    assert client.post("/api/skills/draft", json={**ok, "conversation_id": empty}, headers=AUTH).status_code == 400
    assert client.post("/api/skills/ghost/refine", json=ok, headers=AUTH).status_code == 400


def test_api_refine_an_installed_skill(tmp_path):
    reply = json.dumps({"description": "Sort files.", "steps": "1. Ask first.\n2. Move.", "setup": ""})
    client, cid = make_client(tmp_path, reply)
    created = client.post("/api/skills", json={"skill_md": CURRENT, "setup_md": "", "approved": ["tool:files.list", "tool:files.move"]}, headers=AUTH)
    assert created.status_code == 201
    out = client.post("/api/skills/sorter/refine", json={"conversation_id": cid, "provider": "ollama", "model": "m", "note": "too eager"}, headers=AUTH)
    assert out.status_code == 200 and "1.2.1" in out.json()["skill_md"] and "+1. Ask first." in out.json()["diff"]
    assert client.get("/api/skills/sorter/files", headers=AUTH).json()["skill_md"] == CURRENT  # nothing saved by asking
