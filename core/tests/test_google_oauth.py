import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from piyo.config.secrets import get_secret
from piyo.integrations.google.oauth import (
    SCOPE_GROUPS,
    GoogleAuth,
    GoogleError,
    account_secret,
    scopes_for,
)


def id_token(email: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"x.{body}.y"


class FakeGoogle:
    """Stands in for Google's token and revoke endpoints."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_response: dict = {}
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/revoke":
            return httpx.Response(200)
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.last_form = form
        if self.token_response:
            return httpx.Response(self.status, json=self.token_response)
        if form["grant_type"] == "refresh_token":
            return httpx.Response(200, json={"access_token": "access-2", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "access_token": "access-1",
                "expires_in": 3600,
                "refresh_token": "refresh-1",
                "scope": " ".join(scopes_for(["read"])),
                "id_token": id_token("me@example.com"),
            },
        )


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def auth(google):
    a = GoogleAuth(httpx.MockTransport(google))
    a.set_client("client-id", "client-secret")
    return a


async def sign_in(auth: GoogleAuth, groups=(), **callback) -> str:
    """Begin a flow and play the browser: open the redirect with the given query."""
    url = await auth.begin(list(groups))
    query = parse_qs(urlsplit(url).query)
    redirect = query["redirect_uri"][0]
    params = {"state": query["state"][0], "code": "the-code"} | callback
    async with httpx.AsyncClient() as http:
        await http.get(redirect, params={k: v for k, v in params.items() if v is not None})
    await auth._flow.task if auth._flow and auth._flow.task else None
    return url


async def test_begin_needs_a_client():
    with pytest.raises(GoogleError) as e:
        await GoogleAuth().begin([])
    assert e.value.kind == "no_client"


async def test_auth_url_uses_pkce_and_read_only_scopes(auth):
    url = await auth.begin([])
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert q["client_id"] == "client-id"
    assert q["code_challenge_method"] == "S256"
    assert q["access_type"] == "offline"
    assert q["redirect_uri"].startswith("http://127.0.0.1:")
    scopes = q["scope"].split()
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
    assert not any(s.endswith(("gmail.send", "gmail.compose", "calendar.events")) for s in scopes)
    assert auth.status()["state"] == "connecting"
    await auth.cancel()
    assert auth.status()["state"] == "disconnected"


async def test_sign_in_stores_tokens_in_the_keychain(auth, google):
    url = await sign_in(auth)
    q = parse_qs(urlsplit(url).query)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(google.last_form["code_verifier"].encode()).digest())
    assert challenge.rstrip(b"=").decode() == q["code_challenge"][0]
    assert google.last_form["code"] == "the-code"
    st = auth.status()
    assert st["state"] == "connected"
    assert st["accounts"] == [{"email": "me@example.com", "groups": ["read"]}]
    assert "refresh-1" in get_secret(account_secret("me@example.com"))
    assert await auth.access_token() == "access-1"  # cached, no refresh yet
    assert "refresh-1" in auth.secret_values()


async def test_wrong_state_is_ignored_and_flow_keeps_waiting(auth):
    url = await auth.begin([])
    redirect = parse_qs(urlsplit(url).query)["redirect_uri"][0]
    async with httpx.AsyncClient() as http:
        res = await http.get(redirect, params={"state": "forged", "code": "evil"})
    assert res.status_code == 404
    assert auth.status()["state"] == "connecting"
    await auth.cancel()
    assert not auth.connected()


async def test_denied_consent_reports_a_readable_error(auth):
    await sign_in(auth, error="access_denied", code=None)
    st = auth.status()
    assert st["state"] == "disconnected"
    assert "not granted" in st["error"]


async def test_refresh_when_the_access_token_expired(auth, google):
    clock = [1000.0]
    auth._clock = lambda: clock[0]
    await sign_in(auth)
    clock[0] += 4000
    assert await auth.access_token() == "access-2"
    assert google.last_form["refresh_token"] == "refresh-1"


async def test_revoked_grant_clears_tokens_and_asks_to_reconnect(auth, google):
    await sign_in(auth)
    auth.invalidate_access_token()
    google.token_response = {"error": "invalid_grant"}
    google.status = 400
    with pytest.raises(GoogleError) as e:
        await auth.access_token()
    assert e.value.kind == "expired"
    assert not auth.connected()
    assert get_secret(account_secret("me@example.com")) is None
    assert "Reconnect" in auth.status()["error"]


async def test_offline_is_its_own_failure():
    def boom(request):
        raise httpx.ConnectError("down")

    a = GoogleAuth(httpx.MockTransport(boom))
    a.set_client("id", None)
    a._save_tokens({"refresh_token": "r", "scopes": [], "email": None})
    with pytest.raises(GoogleError) as e:
        await a.access_token()
    assert e.value.kind == "offline"
    assert a.connected()  # a network blip must not sign the user out


async def test_not_connected_message(auth):
    with pytest.raises(GoogleError) as e:
        await auth.access_token()
    assert e.value.kind == "not_connected"


async def test_disconnect_revokes_and_removes_every_token(auth, google):
    await sign_in(auth)
    await auth.disconnect()
    assert get_secret(account_secret("me@example.com")) is None
    assert not auth.connected()
    assert auth.secret_values() == ("client-secret",)
    assert any(r.url.path == "/revoke" for r in google.requests)
    assert auth.status()["state"] == "disconnected"


async def test_disconnect_works_even_when_revoke_fails(auth):
    await sign_in(auth)
    auth._transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x")))
    await auth.disconnect()
    assert get_secret(account_secret("me@example.com")) is None


async def test_adding_a_group_keeps_what_was_granted(auth, google):
    google.token_response = {
        "access_token": "a",
        "refresh_token": "r",
        "scope": " ".join(scopes_for(["read", "gmail_draft"])),
    }
    await sign_in(auth, groups=["gmail_draft"])
    assert auth.has_group("gmail_draft")
    url = await auth.begin(["calendar_write"], account="account")
    scopes = parse_qs(urlsplit(url).query)["scope"][0].split()
    assert set(SCOPE_GROUPS["gmail_draft"]) <= set(scopes)
    assert set(SCOPE_GROUPS["calendar_write"]) <= set(scopes)
    await auth.cancel()


async def test_unknown_group_is_rejected(auth):
    with pytest.raises(ValueError):
        await auth.begin(["root"])
    await asyncio.sleep(0)
