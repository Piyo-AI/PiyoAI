"""'Test connection': one small read-only call per Google service and account, reported separately."""

from __future__ import annotations

import httpx

from piyo.integrations.google.client import GoogleClient
from piyo.integrations.google.oauth import GoogleAuth, GoogleError

_G = "https://www.googleapis.com/auth/"
GMAIL_PROFILE = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
CALENDAR_PRIMARY = "https://www.googleapis.com/calendar/v3/calendars/primary"


async def check_connection(auth: GoogleAuth, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """{"ok": bool, "results": [{"service", "ok", "detail"}]}; raises GoogleError if not connected."""
    client = GoogleClient(auth, transport)
    accounts = auth.accounts()
    if not accounts:
        raise GoogleError("not_connected", "Google is not connected yet. Connect it first, then test.")
    results = []

    async def check(label: str, account: str, scopes: tuple[str, ...], url: str, describe) -> None:
        if not client.can(*scopes, account=account):
            results.append({"service": label, "ok": False, "detail": "Access was not granted for this."})
            return
        try:
            data = await client.request("GET", url, account=account)
            results.append({"service": label, "ok": True, "detail": describe(data)})
        except GoogleError as e:
            results.append({"service": label, "ok": False, "detail": str(e)})

    for account in accounts:
        suffix = f" ({account})" if len(accounts) > 1 else ""
        await check(
            f"Gmail{suffix}",
            account,
            (_G + "gmail.readonly", _G + "gmail.modify"),
            GMAIL_PROFILE,
            lambda d: f"Can read the mailbox {d.get('emailAddress', '')}".strip(),
        )
        await check(
            f"Calendar{suffix}",
            account,
            (_G + "calendar.readonly", _G + "calendar.events"),
            CALENDAR_PRIMARY,
            lambda d: f"Can read the calendar (time zone {d.get('timeZone', 'unknown')})",
        )
    return {"ok": all(r["ok"] for r in results), "results": results}
