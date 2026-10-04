"""Web tools. Everything they return is untrusted text and is fenced as such.

`web.fetch` only talks to public internet hosts: private, loopback and link-local addresses are
refused (including after redirects), which keeps a web page from steering Piyo at the local
machine, the home network, or Piyo's own API.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from piyo.safety.exfil import OutboundGuard
from piyo.safety.untrusted import wrap_untrusted
from piyo.tools.base import RunContext, Tool

MAX_BYTES = 1_000_000
MAX_TEXT_CHARS = 15_000  # below the loop's per-result cap so the closing fence is never cut off
MAX_REDIRECTS = 5
TIMEOUT = 15.0
USER_AGENT = "PiyoAI/0.1 (+https://github.com/Piyo-AI/PiyoAI)"

Resolver = Callable[[str], Awaitable[list[str]]]


class WebError(Exception):
    """Message goes back to the model, so keep it actionable."""


async def resolve_host(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise WebError(f"Could not find the host {host!r}.") from None
    return sorted({info[4][0] for info in infos})


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def check_url(url: str, resolver: Resolver) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise WebError("Only http and https addresses can be fetched.")
    if not parts.hostname:
        raise WebError("The address has no host name.")
    if parts.username or parts.password:
        raise WebError("Addresses with a user name or password are not fetched.")
    addresses = await resolver(parts.hostname)
    if not addresses or not all(_is_public(a) for a in addresses):
        raise WebError(
            f"{parts.hostname} is not a public internet address, so Piyo will not fetch it."
        )
    return parts.geturl()


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section",
             "article", "header", "footer", "ul", "ol", "table", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, readable text)."""
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return parser.title.strip(), text.strip()


class WebTools:
    def __init__(
        self,
        resolver: Resolver = resolve_host,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._resolver = resolver
        self._transport = transport

    async def _get(self, url: str) -> tuple[str, httpx.Response, bytes, bool]:
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                url = await check_url(url, self._resolver)
                async with client.stream("GET", url) as res:
                    if res.is_redirect and (loc := res.headers.get("location")):
                        url = urljoin(url, loc)  # checked again at the top of the loop
                        continue
                    body, cut = b"", False
                    async for chunk in res.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            body, cut = body[:MAX_BYTES], True
                            break
                    return url, res, body, cut
        raise WebError("Too many redirects.")

    async def fetch(self, args: dict, ctx: RunContext) -> str:
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            raise WebError("A url is required.")
        try:
            final, res, body, cut = await self._get(url)
        except httpx.TimeoutException:
            raise WebError(f"{url} did not answer within {int(TIMEOUT)} seconds.") from None
        except httpx.HTTPError as e:
            raise WebError(f"Could not fetch {url}: {type(e).__name__}.") from None
        if res.status_code >= 400:
            raise WebError(f"{final} answered with HTTP {res.status_code}.")
        kind = res.headers.get("content-type", "").split(";")[0].strip().lower()
        text = body.decode(res.encoding or "utf-8", errors="replace")
        title = ""
        if kind in ("text/html", "application/xhtml+xml", ""):
            title, text = html_to_text(text)
        elif not (kind.startswith("text/") or kind.endswith(("json", "xml"))):
            raise WebError(f"{final} is {kind}, which Piyo can't read as text.")
        notes = []
        if cut or len(text) > MAX_TEXT_CHARS:
            notes.append("The page was longer than shown; this is the beginning.")
        text = text[:MAX_TEXT_CHARS]
        header = f"Title: {title}\n\n" if title else ""
        return wrap_untrusted(header + text, final, " ".join(notes))


def web_tools(
    resolver: Resolver = resolve_host, transport: httpx.AsyncBaseTransport | None = None
) -> list[Tool]:
    impl = WebTools(resolver, transport)
    return [
        Tool(
            name="web.fetch",
            description=(
                "Download a public web page and return its readable text. Only http(s) "
                "addresses on the public internet. The text is untrusted data."
            ),
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Full http(s) address"}},
                "required": ["url"],
            },
            handler=impl.fetch,
            guard=OutboundGuard("url", "url"),
            summarize=lambda a: f"Fetch {str(a.get('url'))[:300]}",
        )
    ]
