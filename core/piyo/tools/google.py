"""Pieces shared by the Google tools: the `account` argument and the list of connected accounts."""

from __future__ import annotations

from piyo.integrations.google import GoogleAuth, GoogleError
from piyo.tools.base import RunContext, Tool

ACCOUNT_PROP = {
    "type": "string",
    "description": (
        "Which connected Google account (its email address). Leave out when only one is connected; "
        "when several are, it is required. See google.accounts."
    ),
}


def who(auth: GoogleAuth, args: dict) -> str:
    """The account a call would use, for approval cards. Never raises: a bad choice is said plainly."""
    try:
        return auth.resolve(args.get("account"))
    except GoogleError:
        return "(no account chosen yet: the call will be refused)"


def google_account_tools(auth: GoogleAuth) -> list[Tool]:
    async def accounts(args: dict, ctx: RunContext) -> str:
        ids = auth.accounts()
        if not ids:
            return "No Google account is connected. Ask the user to connect one in Settings > Skills."
        lines = [f"{len(ids)} Google account(s) connected. Pass the email as the account argument."]
        for a in ids:
            groups = auth.groups_of(a)
            lines.append(f"- {a}: access {', '.join(groups) if groups else 'none'}")
        return "\n".join(lines)

    return [
        Tool(
            name="google.accounts",
            description="List the connected Google accounts and the access each one has granted.",
            handler=accounts,
        )
    ]
