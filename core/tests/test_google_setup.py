import re

import httpx
from fastapi.testclient import TestClient
from test_runs import AUTH, TOKEN

from piyo.integrations.google import GoogleAuth
from piyo.integrations.google.oauth import scopes_for
from piyo.server import app as server
from piyo.skills import SkillRegistry

KNOWN_WIDGETS = {"google", "google-test", "open-setup"}
DIRECTIVE = re.compile(r"^:::([a-z-]+)(?:\s+([a-z0-9-]+))?:::$", re.MULTILINE)


class Fake:
    def __init__(self):
        self.profile = (200, {"emailAddress": "me@example.com"})
        self.calendar = (200, {"timeZone": "Europe/Berlin"})
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        self.calls.append((request.method, request.url.path))
        status, body = self.profile if "gmail" in request.url.host else self.calendar
        return httpx.Response(status, json=body)


def make(connected=True):
    fake = Fake()
    auth = GoogleAuth(httpx.MockTransport(fake))
    auth.set_client("cid", None)
    if connected:
        auth._save_tokens({"refresh_token": "r", "scopes": scopes_for(["read"]), "email": None})
    return fake, TestClient(server.create_app(TOKEN, google=auth))


def test_connection_test_reports_each_service():
    fake, client = make()
    res = client.post("/api/integrations/google/test", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert [r["service"] for r in body["results"]] == ["Gmail", "Calendar"]
    assert "me@example.com" in body["results"][0]["detail"]
    assert "Europe/Berlin" in body["results"][1]["detail"]
    assert all(method == "GET" for method, _ in fake.calls)  # read-only


def test_a_service_that_fails_is_named_with_the_reason():
    fake, client = make()
    fake.profile = (403, {"error": {"details": [{"reason": "SERVICE_DISABLED"}]}})
    body = client.post("/api/integrations/google/test", headers=AUTH).json()
    assert body["ok"] is False
    gmail, calendar = body["results"]
    assert not gmail["ok"] and "not turned on" in gmail["detail"]
    assert calendar["ok"]


def test_a_service_without_granted_access_makes_no_call():
    fake = Fake()
    auth = GoogleAuth(httpx.MockTransport(fake))
    auth.set_client("cid", None)
    only_calendar = [s for s in scopes_for(["read"]) if "gmail" not in s]  # an unticked box at Google
    auth._save_tokens({"refresh_token": "r", "scopes": only_calendar, "email": None})
    client = TestClient(server.create_app(TOKEN, google=auth))
    body = client.post("/api/integrations/google/test", headers=AUTH).json()
    assert body["ok"] is False
    assert body["results"][0]["detail"].startswith("Access was not granted")
    assert body["results"][1]["ok"] is True
    assert all("gmail" not in path for _, path in fake.calls)


def test_not_connected_is_a_readable_400():
    _, client = make(connected=False)
    res = client.post("/api/integrations/google/test", headers=AUTH)
    assert res.status_code == 400 and "Connect it first" in res.json()["detail"]
    assert client.post("/api/integrations/google/test").status_code == 401


def test_setup_guide_is_served_for_builtin_skills():
    _, client = make()
    for name in ("gmail-triage", "calendar", "morning-brief"):
        res = client.get(f"/api/skills/{name}/setup", headers=AUTH)
        assert res.status_code == 200, name
        assert res.json()["markdown"].startswith("# ")
    assert client.get("/api/skills/gmail-triage/setup").status_code == 401


def test_a_skill_without_a_guide_or_an_unknown_name_is_404():
    _, client = make()
    assert client.get("/api/skills/web-research/setup", headers=AUTH).status_code == 404
    assert client.get("/api/skills/nope/setup", headers=AUTH).status_code == 404
    assert client.get("/api/skills/..%2F..%2Fetc/setup", headers=AUTH).status_code == 404


def test_the_guide_of_a_disabled_skill_is_still_available():
    _, client = make()
    client.put("/api/skills/calendar", json={"enabled": False}, headers=AUTH)
    assert client.get("/api/skills/calendar/setup", headers=AUTH).status_code == 200


def test_an_oversized_guide_is_refused(tmp_path):
    d = tmp_path / "u" / "big"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: big\ndescription: x\n---\nbody\n", encoding="utf-8")
    (d / "SETUP.md").write_text("x" * 200_000, encoding="utf-8")
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "u")
    client = TestClient(server.create_app(TOKEN, skills=skills))
    assert client.get("/api/skills/big/setup", headers=AUTH).status_code == 413


def test_every_builtin_guide_uses_only_known_widgets_and_real_targets():
    skills = SkillRegistry()
    names = {s.manifest.name for s in skills.list()}
    guides = [s for s in skills.list() if s.has_setup]
    assert {"gmail-triage", "calendar", "morning-brief"} <= {s.manifest.name for s in guides}
    for skill in guides:
        text = (skill.path / "SETUP.md").read_text(encoding="utf-8")
        assert text.startswith("# ")
        for name, arg in DIRECTIVE.findall(text):
            assert name in KNOWN_WIDGETS, f"{skill.manifest.name}: unknown widget {name}"
            if name == "open-setup":
                assert arg in names, f"{skill.manifest.name}: open-setup target {arg!r} is not a skill"
        assert "\r" not in text
