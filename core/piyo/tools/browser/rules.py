"""What Piyo may do on a page, decided by plain code (never by model text).

Hard stops: password, card and ID fields are never filled, and CAPTCHAs are never touched (`PLAN.md` §4.9).
Risk: a click that could send, buy, delete, submit or accept something needs the user's approval.
"""

from __future__ import annotations

import re

from piyo.tools.base import Risk

_SENSITIVE_AUTOCOMPLETE = re.compile(r"\b(cc-[\w-]+|current-password|new-password|one-time-code)\b")
_SENSITIVE_WORDS = re.compile(
    r"passw|passcode|passphrase|\bpin\b|card.?(number|no\b|num)|credit.?card|\bcvv\b|\bcvc\b|\bcsc\b"
    r"|security.?code|expir|\bssn\b|social.?security|\biban\b|routing.?number|account.?number"
    r"|passport|tax.?id|verification.?code|one.?time|\botp\b",
    re.IGNORECASE,
)
_CAPTCHA = re.compile(r"captcha|not a robot|verify (that )?you are (a )?human|turnstile", re.IGNORECASE)

# Anything that sends, buys, deletes, submits, posts or agrees, whatever the element is.
_RISKY = re.compile(
    r"\b(send|submit|buy|pay|purchase|order|checkout|check out|place|book|reserve|confirm|delete|remove|"
    r"cancel|unsubscribe|subscribe|post|publish|reply|share|tweet|donate|transfer|withdraw|deposit|apply|"
    r"sign ?up|sign ?in|log ?in|register|create account|join|agree|accept|consent|terms|allow|authori[sz]e|"
    r"grant|install|download|upload|save|add to cart|buy now|continue to pay|trade|sell)\b",
    re.IGNORECASE,
)
# Buttons that only move around the page. Every other button is treated as possibly submitting.
_BENIGN_BUTTONS = re.compile(
    r"^(next|previous|prev|back|more|show more|load more|see more|read more|view more|close|dismiss|menu|"
    r"open menu|search|go|expand|collapse|show|hide|toggle|filter|sort|reject( all)?|decline|no thanks|"
    r"not now|skip|\d+|>|<|»|«|×|x)$",
    re.IGNORECASE,
)
_PLAIN_ROLES = {
    "link", "tab", "checkbox", "radio", "switch", "menuitem", "menuitemcheckbox", "menuitemradio",
    "option", "treeitem", "combobox", "listbox", "heading", "img", "row", "cell", "gridcell",
}


def is_captcha(name: str) -> bool:
    return bool(_CAPTCHA.search(name))


def sensitive_field(input_type: str, autocomplete: str, hints: str) -> str | None:
    """Why this field must be left to the user, or None when it is fine to fill."""
    if input_type.lower() == "password":
        return "a password field"
    if _SENSITIVE_AUTOCOMPLETE.search(autocomplete.lower()):
        return "a password, payment or one-time-code field"
    if _SENSITIVE_WORDS.search(hints):
        return "a field for a password, payment or ID detail"
    return None


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


_TRACKING_FIELD = re.compile(r"track|parcel|shipment|waybill|consignment", re.IGNORECASE)


def is_tracking_field(name: str) -> bool:
    return bool(_TRACKING_FIELD.search(name))


def looks_like_secret(text: str, tracking_field: bool = False) -> str | None:
    """A card or ID number typed as text is refused whatever the field is called.

    Tracking numbers are often 13-19 digits and some pass the card check by chance, so a field the page
    calls a tracking number may take a number of that length. Everything else stays refused.
    """
    digits = re.sub(r"[ -]", "", text.strip())
    if digits.isdigit() and 13 <= len(digits) <= 19 and _luhn(digits) and not tracking_field:
        return "a card number"
    if re.fullmatch(r"\d{3}-\d{2}-\d{4}", text.strip()):
        return "an ID number"
    return None


def click_risk(role: str, name: str) -> Risk:
    if _RISKY.search(name):
        return Risk.CONFIRM
    role = role.lower()
    if role in _PLAIN_ROLES:
        return Risk.AUTO
    if role == "button" and _BENIGN_BUTTONS.match(name.strip()):
        return Risk.AUTO
    return Risk.CONFIRM
