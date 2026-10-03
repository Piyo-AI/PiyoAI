"""Google sign-in for a desktop app: loopback redirect with PKCE, using the user's own OAuth client.

Several Google accounts can be connected. Each account's tokens are one keychain entry (never a file); a
small index entry lists which accounts exist. The refresh token is the long-lived secret; access tokens are
kept in memory. Scopes are requested in groups so a user who only wants reading never grants sending, and
each account has its own grants.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from piyo.config.secrets import delete_secret, get_secret, set_secret

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CONSOLE_URL = "https://console.cloud.google.com/apis/credentials"

CLIENT_ID_SECRET = "google.client_id"
CLIENT_SECRET_SECRET = "google.client_secret"
ACCOUNTS_SECRET = "google.accounts"  # JSON list of account ids (the email addresses)
ACCOUNT_PREFIX = "google.account."  # + id: that account's tokens
LEGACY_TOKENS_SECRET = "google.tokens"  # the single-account layout, migrated on first use
CLIENT_ID_ENV = "GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV = "GOOGLE_CLIENT_SECRET"

LOGIN_TIMEOUT = 300.0
HTTP_TIMEOUT = 15.0
EXPIRY_MARGIN = 60.0

_G = "https://www.googleapis.com/auth/"
IDENTITY_SCOPES = ["openid", "email"]
# Start read-only; each later group is requested only when the user enables the tools that need it.
SCOPE_GROUPS: dict[str, list[str]] = {
    "read": [_G + "gmail.readonly", _G + "calendar.readonly"],
    "gmail_draft": [_G + "gmail.compose"],
    "gmail_send": [_G + "gmail.send"],
    "gmail_modify": [_G + "gmail.modify"],
    "calendar_write": [_G + "calendar.events"],
}

_DONE_PAGE = (
    "<!doctype html><meta charset=utf-8><title>Piyo AI</title>"
    "<body style='font:16px system-ui;margin:3rem auto;max-width:28rem'>"
    "<h2>{title}</h2><p>{text}</p></body>"
)


class GoogleError(Exception):
    """Something the user can act on. `kind` lets the app and the tools react; the message is for people."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class _Flow:
    state: str
    verifier: str
    redirect_uri: str
    groups: list[str]
    hint: str | None = None  # the account this sign-in adds access to; None for a new account
    result: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    server: asyncio.AbstractServer | None = None
    task: asyncio.Task | None = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pkce() -> tuple[str, str]:
    verifier = _b64(secrets.token_bytes(48))
    return verifier, _b64(hashlib.sha256(verifier.encode()).digest())


def scopes_for(groups: list[str]) -> list[str]:
    unknown = [g for g in groups if g not in SCOPE_GROUPS]
    if unknown:
        raise ValueError(f"unknown scope group: {', '.join(unknown)}")
    out = list(IDENTITY_SCOPES)
    for g in groups:
        out += [s for s in SCOPE_GROUPS[g] if s not in out]
    return out


def account_secret(account: str) -> str:
    return ACCOUNT_PREFIX + account


def _id_token_email(id_token: str | None) -> str | None:
    # Read straight from Google's token endpoint over TLS, so the signature isn't needed to show an address.
    try:
        payload = id_token.split(".")[1]  # type: ignore[union-attr]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data.get("email")
    except (AttributeError, IndexError, ValueError):
        return None


class GoogleAuth:
    def __init__(
        self, transport: httpx.AsyncBaseTransport | None = None, clock=time.time
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._access: dict[str, tuple[str, float]] = {}  # account -> token, expiry
        self._flow: _Flow | None = None
        self._last_error: str | None = None

    @property
    def transport(self) -> httpx.AsyncBaseTransport | None:
        return self._transport

    # --- the user's OAuth client -------------------------------------------------------------

    def client(self) -> tuple[str, str | None] | None:
        client_id = get_secret(CLIENT_ID_SECRET, CLIENT_ID_ENV)
        if not client_id:
            return None
        return client_id, get_secret(CLIENT_SECRET_SECRET, CLIENT_SECRET_ENV)

    def set_client(self, client_id: str, client_secret: str | None) -> None:
        set_secret(CLIENT_ID_SECRET, client_id.strip())
        if client_secret and client_secret.strip():
            set_secret(CLIENT_SECRET_SECRET, client_secret.strip())
        else:
            delete_secret(CLIENT_SECRET_SECRET)

    # --- stored accounts ---------------------------------------------------------------------

    def _index(self) -> list[str]:
        raw = get_secret(ACCOUNTS_SECRET)
        try:
            ids = json.loads(raw) if raw else []
        except ValueError:
            ids = []
        return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []

    def _read(self, account: str) -> dict | None:
        raw = get_secret(account_secret(account))
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            return None
        return data if isinstance(data, dict) and data.get("refresh_token") else None

    def _migrate_legacy(self) -> None:
        raw = get_secret(LEGACY_TOKENS_SECRET)
        if not raw:
            return
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("refresh_token"):
            self._store(self._account_id(data.get("email")), data)
        delete_secret(LEGACY_TOKENS_SECRET)

    @staticmethod
    def _account_id(email: str | None) -> str:
        return (email or "account").strip().lower()

    def _store(self, account: str, data: dict) -> None:
        set_secret(account_secret(account), json.dumps({**data, "email": data.get("email") or account}))
        ids = self._index()
        if account not in ids:
            set_secret(ACCOUNTS_SECRET, json.dumps([*ids, account]))

    def _save_tokens(self, data: dict) -> str:
        """Store (or replace) the account the tokens belong to; returns its id."""
        account = self._account_id(data.get("email"))
        self._store(account, data)
        return account

    def accounts(self) -> list[str]:
        """Connected account ids (their email addresses), in the order they were added."""
        self._migrate_legacy()
        return [a for a in self._index() if self._read(a) is not None]

    def connected(self) -> bool:
        return bool(self.accounts())

    def resolve(self, account: object = None) -> str:
        """The account a call is for. Without a name this only works while exactly one is connected."""
        ids = self.accounts()
        if not ids:
            raise GoogleError(
                "not_connected", "Google is not connected. Ask the user to connect it in Settings."
            )
        if isinstance(account, str) and account.strip():
            wanted = account.strip().lower()
            if wanted in ids:
                return wanted
            raise GoogleError(
                "bad_account",
                f"No connected Google account {account.strip()[:80]!r}. Connected: {', '.join(ids)}.",
            )
        if len(ids) == 1:
            return ids[0]
        raise GoogleError(
            "account_needed",
            f"Several Google accounts are connected ({', '.join(ids)}). "
            "Ask the user which one, or use each in turn, and pass it as the account argument.",
        )

    def granted_scopes(self, account: str | None = None) -> set[str]:
        if not self.accounts():
            return set()
        tokens = self._read(self.resolve(account))
        return set(tokens.get("scopes", [])) if tokens else set()

    def has_group(self, group: str, account: str | None = None) -> bool:
        return set(SCOPE_GROUPS[group]) <= self.granted_scopes(account)

    def groups_of(self, account: str) -> list[str]:
        have = self.granted_scopes(account)
        return [g for g, scopes in SCOPE_GROUPS.items() if set(scopes) <= have]

    def secret_values(self) -> tuple[str, ...]:
        """Values that must never appear in logs or the task log."""
        values = [t for t, _ in self._access.values()]
        values += [(self._read(a) or {}).get("refresh_token") for a in self.accounts()]
        client = self.client()
        if client and client[1]:
            values.append(client[1])
        return tuple(v for v in values if v)

    def status(self) -> dict:
        ids = self.accounts()
        if self._flow is not None:
            state = "connecting"
        elif ids:
            state = "connected"
        else:
            state = "disconnected"
        return {
            "state": state,
            "has_client": self.client() is not None,
            "accounts": [{"email": a, "groups": self.groups_of(a)} for a in ids],
            "error": self._last_error,
            "console_url": CONSOLE_URL,
        }

    # --- sign-in -----------------------------------------------------------------------------

    async def begin(self, groups: list[str], account: str | None = None) -> str:
        """Start a sign-in and return the address the user opens in their browser.

        With `account`, it adds access to that account (Google is told which one to use). Without it,
        Google shows its account chooser, so the user can pick a different account to add.
        """
        client = self.client()
        if client is None:
            raise GoogleError(
                "no_client", "Add your Google OAuth client ID in Settings first (see the setup guide)."
            )
        hint = self.resolve(account) if account else None
        # Ask for everything the account already has too, so adding a group never drops another.
        already = self.groups_of(hint) if hint else []
        wanted = list(dict.fromkeys(["read", *groups, *already]))
        scopes = scopes_for(wanted)
        await self.cancel()
        verifier, challenge = _pkce()
        flow = _Flow(
            state=_b64(secrets.token_bytes(24)), verifier=verifier, redirect_uri="", groups=wanted, hint=hint
        )
        flow.server = await asyncio.start_server(lambda r, w: self._on_request(flow, r, w), "127.0.0.1", 0)
        port = flow.server.sockets[0].getsockname()[1]
        flow.redirect_uri = f"http://127.0.0.1:{port}/"
        self._flow = flow
        self._last_error = None
        flow.task = asyncio.create_task(self._finish(flow, client))
        query = {
            "client_id": client[0],
            "redirect_uri": flow.redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": flow.state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            # consent: Google returns a refresh token every time. select_account: the chooser is shown,
            # so a second account can be picked even while the first is signed in in the browser.
            "prompt": "consent" if hint else "consent select_account",
        }
        if hint:
            query["login_hint"] = hint
        return f"{AUTH_URL}?{urlencode(query)}"

    async def cancel(self) -> None:
        flow, self._flow = self._flow, None
        if flow is None:
            return
        if flow.task and not flow.task.done():
            flow.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flow.task
        await self._close(flow)

    @staticmethod
    async def _close(flow: _Flow) -> None:
        if flow.server is not None:
            flow.server.close()
            with contextlib.suppress(Exception):
                await flow.server.wait_closed()

    async def _on_request(
        self, flow: _Flow, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            parts = line.decode("latin-1").split()
            query = parse_qs(urlsplit(parts[1]).query) if len(parts) >= 2 else {}
            got_state = (query.get("state") or [""])[0]
            if not secrets.compare_digest(got_state, flow.state):
                # Not our redirect (a favicon request, another page poking the port): keep waiting.
                await self._reply(writer, "404 Not Found", "Not found", "This address is only used by Piyo.")
                return
            if not flow.result.done():
                flow.result.set_result({k: v[0] for k, v in query.items()})
            ok = "code" in query
            await self._reply(
                writer,
                "200 OK",
                "You're connected" if ok else "Not connected",
                "You can close this tab and go back to Piyo." if ok else "Google did not grant access.",
            )
        except (TimeoutError, ConnectionError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: str, title: str, text: str) -> None:
        body = _DONE_PAGE.format(title=title, text=text).encode()
        head = (
            f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        )
        writer.write(head.encode() + body)
        await writer.drain()

    async def _finish(self, flow: _Flow, client: tuple[str, str | None]) -> None:
        try:
            try:
                params = await asyncio.wait_for(flow.result, LOGIN_TIMEOUT)
            except TimeoutError:
                raise GoogleError("timeout", "Sign-in was not finished in time. Try Connect again.") from None
            if "error" in params:
                if params["error"] == "access_denied":
                    raise GoogleError(
                        "denied", "Access was not granted. Connect again and allow the requested access."
                    )
                raise GoogleError("denied", f"Google refused the sign-in ({params['error']}).")
            data = await self._token_request(
                client,
                {
                    "grant_type": "authorization_code",
                    "code": params["code"],
                    "code_verifier": flow.verifier,
                    "redirect_uri": flow.redirect_uri,
                },
            )
            refresh = data.get("refresh_token")
            if not refresh:
                raise GoogleError("denied", "Google did not return long-lived access. Connect again.")
            email = _id_token_email(data.get("id_token")) or flow.hint
            account = self._save_tokens(
                {
                    "refresh_token": refresh,
                    "scopes": str(data.get("scope", "")).split(),
                    "email": email,
                }
            )
            lifetime = float(data.get("expires_in", 3600))
            self._access[account] = (data["access_token"], self._clock() + lifetime)
        except GoogleError as e:
            self._last_error = str(e)
        except Exception as e:  # never leave the flow dangling
            self._last_error = f"Sign-in failed: {type(e).__name__}."
        finally:
            await self._close(flow)
            if self._flow is flow:
                self._flow = None

    # --- tokens ------------------------------------------------------------------------------

    async def _token_request(self, client: tuple[str, str | None], form: dict) -> dict:
        form = {**form, "client_id": client[0]}
        if client[1]:
            form["client_secret"] = client[1]
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=HTTP_TIMEOUT) as http:
                res = await http.post(TOKEN_URL, data=form)
        except httpx.TimeoutException:
            raise GoogleError(
                "offline", "Google did not answer in time. Check the connection and retry."
            ) from None
        except httpx.HTTPError:
            raise GoogleError("offline", "Could not reach Google. Check the internet connection.") from None
        try:
            body = res.json()
        except ValueError:
            body = {}
        if res.status_code == 200 and "access_token" in body:
            return body
        err = body.get("error") if isinstance(body, dict) else None
        if err == "invalid_grant":
            raise GoogleError(
                "expired", "Google access expired or was revoked. Reconnect Google in Settings."
            )
        if err in ("invalid_client", "unauthorized_client"):
            raise GoogleError(
                "bad_client", "Google rejected the OAuth client ID or secret. Check them in Settings."
            )
        if res.status_code == 429:
            raise GoogleError("quota", "Google asked us to slow down. Try again in a few minutes.")
        raise GoogleError("other", f"Google answered with HTTP {res.status_code} during sign-in.")

    async def access_token(self, account: str | None = None) -> str:
        account = self.resolve(account)
        cached = self._access.get(account)
        if cached and cached[1] - EXPIRY_MARGIN > self._clock():
            return cached[0]
        tokens = self._read(account)
        client = self.client()
        if tokens is None:
            raise GoogleError(
                "not_connected", "Google is not connected. Ask the user to connect it in Settings."
            )
        if client is None:
            raise GoogleError("no_client", "The Google OAuth client is missing. Add it in Settings.")
        try:
            data = await self._token_request(
                client, {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
            )
        except GoogleError as e:
            if e.kind == "expired":
                self._forget(account)
                self._last_error = (
                    f"Google access for {account} expired or was revoked. Reconnect it in Settings."
                )
                raise GoogleError("expired", self._last_error) from None
            raise
        self._access[account] = (data["access_token"], self._clock() + float(data.get("expires_in", 3600)))
        return self._access[account][0]

    def invalidate_access_token(self, account: str | None = None) -> None:
        """Drop the cached access token (one account, or all when none is named)."""
        if account is None:
            self._access.clear()
        else:
            self._access.pop(account, None)

    def _forget(self, account: str) -> None:
        self._access.pop(account, None)
        delete_secret(account_secret(account))
        remaining = [a for a in self._index() if a != account]
        if remaining:
            set_secret(ACCOUNTS_SECRET, json.dumps(remaining))
        else:
            delete_secret(ACCOUNTS_SECRET)

    async def disconnect(self, account: str | None = None) -> None:
        """Revoke at Google (best effort) and remove tokens from the keychain: one account, or all."""
        if account is None:
            await self.cancel()
            targets = self.accounts()
        else:
            targets = [self.resolve(account)]
        self._last_error = None
        revoke = [(a, (self._read(a) or {}).get("refresh_token")) for a in targets]
        for a in targets:
            self._forget(a)
        if account is None:
            delete_secret(LEGACY_TOKENS_SECRET)
        for _, token in revoke:
            if not token:
                continue
            with contextlib.suppress(httpx.HTTPError):
                async with httpx.AsyncClient(transport=self._transport, timeout=HTTP_TIMEOUT) as http:
                    await http.post(REVOKE_URL, data={"token": token})

    def clear_client(self) -> None:
        delete_secret(CLIENT_ID_SECRET)
        delete_secret(CLIENT_SECRET_SECRET)
