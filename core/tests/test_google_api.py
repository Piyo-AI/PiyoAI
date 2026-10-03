import httpx
from fastapi.testclient import TestClient
from test_google_oauth import FakeGoogle, sign_in
from test_runs import AUTH, TOKEN

from piyo.config.secrets import get_secret
from piyo.integrations.google import GoogleAuth
from piyo.integrations.google.oauth import CLIENT_ID_SECRET, account_secret
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.tools.base import RunContext
from piyo.tools.builtin import core_tools

MAILER = """---
name: mailer
description: Handle email.
requires:
  integrations: [google]
---
Read mail.
"""


def skills_with_google(tmp_path):
    d = tmp_path / "u" / "mailer"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(MAILER, encoding="utf-8")
    return SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "u")


def make_client(tmp_path):
    auth = GoogleAuth(httpx.MockTransport(FakeGoogle()))
    return auth, TestClient(server.create_app(TOKEN, skills=skills_with_google(tmp_path), google=auth))


def test_requires_the_token(tmp_path):
    _, client = make_client(tmp_path)
    assert client.get("/api/integrations/google").status_code == 401
    assert client.post("/api/integrations/google/connect", json={}).status_code == 401
    assert client.delete("/api/integrations/google").status_code == 401


def test_status_and_client_setup(tmp_path):
    _, client = make_client(tmp_path)
    st = client.get("/api/integrations/google", headers=AUTH).json()
    assert st["state"] == "disconnected" and st["has_client"] is False
    assert "read" in st["available_groups"]
    blank = client.put("/api/integrations/google/client", json={"client_id": " "}, headers=AUTH)
    assert blank.status_code == 400
    body = {"client_id": "cid", "client_secret": "sec"}
    assert client.put("/api/integrations/google/client", json=body, headers=AUTH).status_code == 204
    assert client.get("/api/integrations/google", headers=AUTH).json()["has_client"] is True
    assert "sec" not in client.get("/api/integrations/google", headers=AUTH).text


def test_connect_without_a_client_is_a_readable_400(tmp_path):
    _, client = make_client(tmp_path)
    res = client.post("/api/integrations/google/connect", json={}, headers=AUTH)
    assert res.status_code == 400 and "client ID" in res.json()["detail"]


def test_connect_returns_a_url_and_cancel_resets(tmp_path):
    _, client = make_client(tmp_path)
    with client:  # one event loop for the whole test, like the real server; the flow lives in it
        client.put("/api/integrations/google/client", json={"client_id": "cid"}, headers=AUTH)
        res = client.post("/api/integrations/google/connect", json={"groups": ["gmail_draft"]}, headers=AUTH)
        assert res.status_code == 200 and res.json()["url"].startswith("https://accounts.google.com/")
        assert client.get("/api/integrations/google", headers=AUTH).json()["state"] == "connecting"
        assert client.post("/api/integrations/google/cancel", headers=AUTH).status_code == 204
        assert client.get("/api/integrations/google", headers=AUTH).json()["state"] == "disconnected"
        bad = client.post("/api/integrations/google/connect", json={"groups": ["root"]}, headers=AUTH)
        assert bad.status_code == 400


def test_skill_list_flags_an_unmet_integration(tmp_path):
    auth, client = make_client(tmp_path)
    skill = client.get("/api/skills", headers=AUTH).json()["skills"][0]
    assert "google" in skill["integration_issues"]
    auth._save_tokens({"refresh_token": "r", "scopes": [], "email": None})
    skill = client.get("/api/skills", headers=AUTH).json()["skills"][0]
    assert skill["integration_issues"] == {}


def test_disconnect_removes_tokens_and_optionally_the_client(tmp_path):
    auth, client = make_client(tmp_path)
    auth.set_client("cid", "sec")
    auth._save_tokens({"refresh_token": "r", "scopes": [], "email": None})
    assert client.delete("/api/integrations/google", headers=AUTH).status_code == 204
    assert get_secret(account_secret("account")) is None
    assert get_secret(CLIENT_ID_SECRET) == "cid"
    client.delete("/api/integrations/google", params={"remove_client": "true"}, headers=AUTH)
    assert get_secret(CLIENT_ID_SECRET) is None


async def test_load_skill_refuses_when_the_integration_is_not_ready(tmp_path):
    skills = skills_with_google(tmp_path)
    load = core_tools()[0].handler
    ctx = RunContext(skills=skills, integration_issue=lambda name: "Google is not connected.")
    out = await load({"name": "mailer"}, ctx)
    assert "not loaded" in out and "Settings" in out
    assert "mailer" not in ctx.active_skills
    ctx.integration_issue = lambda name: None
    assert "Read mail." in await load({"name": "mailer"}, ctx)
    assert "mailer" in ctx.active_skills


async def test_the_full_flow_through_the_api_marks_the_skill_ready(tmp_path):
    auth, client = make_client(tmp_path)
    auth.set_client("cid", None)
    await sign_in(auth)
    accounts = client.get("/api/integrations/google", headers=AUTH).json()["accounts"]
    assert [a["email"] for a in accounts] == ["me@example.com"]
    assert client.get("/api/skills", headers=AUTH).json()["skills"][0]["integration_issues"] == {}
