"""Authorised calls to Google's REST APIs, with failures turned into messages the user can act on."""

from __future__ import annotations

import httpx

from piyo.integrations.google.oauth import GoogleAuth, GoogleError

TIMEOUT = 20.0
_QUOTA_REASONS = {"ratelimitexceeded", "userratelimitexceeded", "quotaexceeded", "dailylimitexceeded"}


def _reasons(body: object) -> set[str]:
    """Every machine-readable reason in a Google error body, lower-cased."""
    found: set[str] = set()
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return found
    for item in error.get("errors") or []:
        if isinstance(item, dict) and item.get("reason"):
            found.add(str(item["reason"]).lower())
    for item in error.get("details") or []:
        if isinstance(item, dict) and item.get("reason"):
            found.add(str(item["reason"]).lower())
    if error.get("status"):
        found.add(str(error["status"]).lower())
    return found


def error_for(status: int, body: object) -> GoogleError:
    reasons = _reasons(body)
    if status == 429 or reasons & _QUOTA_REASONS:
        return GoogleError("quota", "Google's usage limit was reached. Wait a few minutes and try again.")
    if reasons & {"service_disabled", "accessnotconfigured"}:
        return GoogleError(
            "api_disabled",
            "The Gmail or Calendar API is not turned on in the user's Google Cloud project. "
            "Ask them to enable it there (see the setup guide).",
        )
    if status in (401, 403) and (
        reasons & {"insufficientpermissions", "access_token_scope_insufficient", "permission_denied"}
        or status == 401
    ):
        return GoogleError(
            "scope",
            "Google did not allow this. The needed access may not have been granted: ask the user to "
            "reconnect Google in Settings and allow it.",
        )
    if status in (404, 410):
        return GoogleError("not_found", "Google could not find that item.")
    if status == 400:
        return GoogleError("bad_request", "Google rejected the request as invalid.")
    if status >= 500:
        return GoogleError("other", "Google is having trouble right now. Try again shortly.")
    return GoogleError("other", f"Google answered with HTTP {status}.")


class GoogleClient:
    def __init__(self, auth: GoogleAuth, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.auth = auth
        self._transport = transport if transport is not None else auth.transport  # one for both

    def can(self, *scopes: str, account: str | None = None) -> bool:
        """True when the account was granted any one of the scopes."""
        return bool(self.auth.granted_scopes(account) & set(scopes))

    def require(self, what: str, *scopes: str, account: object = None) -> str:
        """Checks the account exists (and is the only choice or named) and was granted the access.

        Returns the account id the call must use.
        """
        acct = self.auth.resolve(account)
        if not self.can(*scopes, account=acct):
            many = len(self.auth.accounts()) > 1
            raise GoogleError(
                "scope",
                f"Google access for {what} was not granted{f' for {acct}' if many else ''}. Ask the user to "
                "turn it on in Settings > Skills (Connect Google) and allow it.",
            )
        return acct

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        account: str | None = None,
    ) -> dict:
        for attempt in (0, 1):
            token = await self.auth.access_token(account)
            try:
                async with httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT) as http:
                    res = await http.request(
                        method, url, params=params, json=json, headers={"Authorization": f"Bearer {token}"}
                    )
            except httpx.TimeoutException:
                raise GoogleError("offline", "Google did not answer in time. Try again.") from None
            except httpx.HTTPError:
                raise GoogleError(
                    "offline", "Could not reach Google. Check the internet connection."
                ) from None
            if res.status_code == 401 and attempt == 0:
                self.auth.invalidate_access_token(account)  # a stale token: refresh once and retry
                continue
            try:
                body = res.json() if res.content else {}
            except ValueError:
                body = {}
            if res.status_code >= 400:
                raise error_for(res.status_code, body)
            return body if isinstance(body, dict) else {}
        raise error_for(401, {})  # unreachable, keeps the type checker honest
