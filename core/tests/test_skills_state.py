from fastapi.testclient import TestClient
from test_agent import SKILL
from test_runs import AUTH, TOKEN, chat, runs, scripted

from piyo.config.skill_state import SkillState
from piyo.models.turn import TurnDone
from piyo.server import app as server
from piyo.skills import SkillRegistry


def make_skills(tmp_path):
    d = tmp_path / "u" / "mailer"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "u")


def test_state_round_trip_and_junk_is_ignored(tmp_path):
    state = SkillState(tmp_path / "s.json")
    assert state.disabled() == set()
    assert state.set_enabled("a", False) == {"a"}
    assert state.set_enabled("b", False) == {"a", "b"}
    assert state.set_enabled("a", True) == {"b"}
    state.path.write_text('{"not": "a list"}', encoding="utf-8")
    assert state.disabled() == set()


def test_toggle_hides_the_skill_and_survives_a_restart(tmp_path):
    client = TestClient(server.create_app(TOKEN, skills=make_skills(tmp_path)))
    listed = client.get("/api/skills", headers=AUTH).json()["skills"][0]
    assert listed["enabled"] is True and listed["tools"] == ["mail.read", "mail.send"]
    assert client.put("/api/skills/mailer", json={"enabled": False}, headers=AUTH).status_code == 204
    assert client.get("/api/skills", headers=AUTH).json()["skills"][0]["enabled"] is False
    assert client.put("/api/skills/nope", json={"enabled": False}, headers=AUTH).status_code == 404

    restarted = TestClient(server.create_app(TOKEN, skills=make_skills(tmp_path)))  # same data dir
    assert restarted.get("/api/skills", headers=AUTH).json()["skills"][0]["enabled"] is False
    restarted.put("/api/skills/mailer", json={"enabled": True}, headers=AUTH)
    assert restarted.get("/api/skills", headers=AUTH).json()["skills"][0]["enabled"] is True


def test_a_disabled_skill_is_not_in_the_prompt(tmp_path):
    seen = []

    async def turn_fn(provider, model, messages, tools, system, max_tokens):
        seen.append(system)
        yield TurnDone(text="ok")

    client = TestClient(server.create_app(TOKEN, skills=make_skills(tmp_path), turn_fn=turn_fn))
    chat(client)
    client.put("/api/skills/mailer", json={"enabled": False}, headers=AUTH)
    chat(client)
    assert "mailer" in seen[0] and "mailer" not in seen[1]


def test_run_log_records_which_skills_were_loaded(tmp_path):
    from piyo.models.turn import ToolCall

    turn_fn = scripted(
        [TurnDone(tool_calls=[ToolCall(id="c1", name="load_skill", arguments={"name": "mailer"})])],
        [TurnDone(text="ok")],
    )
    client = TestClient(server.create_app(TOKEN, skills=make_skills(tmp_path), turn_fn=turn_fn))
    chat(client)
    detail = client.get(f"/api/runs/{runs(client)[0]['id']}", headers=AUTH).json()
    skill_steps = [s for s in detail["steps"] if s["kind"] == "skill"]
    assert [s["data"]["name"] for s in skill_steps] == ["mailer"]
