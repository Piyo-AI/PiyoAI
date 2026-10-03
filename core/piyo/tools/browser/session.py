"""The browser session behind the `browser.*` tools.

`BrowserSession` is the seam the tools and tests use; `PlaywrightSession` is the real thing. It starts
Chromium on first use with Piyo's own persistent profile (`browser_profile_dir()`), so logins made in it
stay in it. Every request the page makes is checked against the same public-hosts-only rule as
`web.fetch`, so a page cannot steer the browser at the local machine, the home network or Piyo's API.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from piyo.config import browser_profile_dir
from piyo.config.browser_rules import BrowserRules
from piyo.tools.browser.rules import is_captcha, sensitive_field
from piyo.tools.web import Resolver, WebError, check_url, resolve_host

NAV_TIMEOUT_MS = 20_000
ACTION_TIMEOUT_MS = 5_000
SETTLE_TIMEOUT_MS = 5_000
MAX_SCREENSHOT_BYTES = 2_000_000
SNAPSHOT_TIMEOUT_MS = 10_000
INSTALL_HINT = (
    "Piyo's browser is not installed yet. Tell the user to press Install browser in the app (a one-time "
    "download of about 150 MB), then try again."
)


# Runs in every page of the visible window. The badge sits in a closed shadow root so page styles can't touch
# it; a page could still remove it, which is fine: it only tells the user what Piyo is doing.
BADGE_SCRIPT = """
(() => {
  if (window.top !== window || !window.__piyoWorking) return;
  let badge = null;
  const render = (working) => {
    if (working && !badge) {
      const host = document.createElement('div');
      host.style.cssText = 'all:initial;position:fixed;right:16px;bottom:16px;'
        + 'z-index:2147483647;pointer-events:none';
      const root = host.attachShadow({ mode: 'closed' });
      const box = document.createElement('div');
      box.textContent = '\u25CF Piyo is working';
      box.style.cssText = 'font:600 13px system-ui,sans-serif;color:#1b1d22;background:#f5a524;'
        + 'padding:8px 12px;'
        + 'border-radius:99px;box-shadow:0 2px 10px rgba(0,0,0,.35)';
      root.appendChild(box);
      (document.documentElement || document.body).appendChild(host);
      badge = host;
    } else if (!working && badge) {
      badge.remove();
      badge = null;
    }
  };
  const tick = () => window.__piyoWorking().then(render, () => {});
  tick();
  setInterval(tick, 600);
})();
"""


class BrowserError(Exception):
    """Message goes back to the model, so keep it actionable."""


@dataclass
class PageView:
    url: str
    title: str
    text: str = ""  # accessibility snapshot; empty for `open`
    notes: list[str] = field(default_factory=list)  # our own remarks (login page, CAPTCHA), not page text


@dataclass
class RefInfo:
    """What an element ref from the last `read` points at, as the page's accessibility tree names it."""

    role: str
    name: str
    host: str


class BrowserSession(Protocol):
    async def open(self, url: str) -> PageView: ...
    async def read(self) -> PageView: ...
    def describe(self, ref: str) -> RefInfo | None: ...
    async def screenshot(self) -> tuple[bytes, PageView]: ...
    async def wait(self, seconds: float, text: str | None) -> tuple[bool, PageView]: ...
    async def click(self, ref: str) -> PageView: ...
    async def type(self, ref: str, text: str, submit: bool) -> PageView: ...
    async def close(self) -> None: ...
    @property
    def visible(self) -> bool: ...
    async def set_visible(self, visible: bool) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> BrowserStatus: ...
    def set_working(self, working: bool) -> None: ...
    def begin_run(self) -> None: ...


@dataclass
class BrowserStatus:
    visible: bool  # the window is shown (not headless)
    running: bool  # a browser is started
    url: str  # the current page, empty when none


class PlaywrightSession:
    def __init__(
        self, resolver: Resolver = resolve_host, headless: bool = True, rules: BrowserRules | None = None
    ) -> None:
        self._resolver = resolver
        self._rules = rules or BrowserRules()
        self._pages_this_run = 0
        self._visible = not headless
        self._working = False  # a run is using the browser: the visible window shows a badge
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._allowed_hosts: set[str] = set()
        self._blocked: str | None = None  # why the last navigation was refused
        self._cleanup: Any = None  # keeps the task that stops Playwright after the user closes the window
        self._refs: dict[str, RefInfo] = {}  # from the last read; cleared when the page may have changed

    async def _ensure_page(self) -> Any:
        if self._page is not None and not self._page.is_closed():
            return self._page
        if self._context is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    str(browser_profile_dir()), headless=not self._visible
                )
            except Exception as e:
                await self._stop_playwright()
                if "Executable doesn't exist" in str(e):
                    raise BrowserError(INSTALL_HINT) from None
                raise BrowserError(f"Piyo's browser could not start: {type(e).__name__}.") from None
            await self._context.route("**/*", self._guard)
            if self._visible:
                await self._context.expose_function("__piyoWorking", lambda: self._working)
                await self._context.add_init_script(BADGE_SCRIPT)
            self._context.on("page", self._on_new_page)  # a click that opens a tab: follow it
            self._context.on("close", self._on_closed)  # the user closed the window
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        return self._page

    def _on_closed(self, _context: Any = None) -> None:
        if _context is not self._context:
            return  # an old context closing after a restart
        self._context = self._page = None
        self._refs.clear()
        self._allowed_hosts.clear()
        playwright, self._playwright = self._playwright, None
        if playwright is not None:
            self._cleanup = asyncio.ensure_future(playwright.stop())

    def _on_new_page(self, page: Any) -> None:
        self._page = page
        self._refs.clear()

    async def _guard(self, route: Any) -> None:
        request = route.request
        url = request.url
        parts = urlsplit(url)
        if parts.scheme in ("about", "data", "blob"):
            if parts.scheme != "about" and request.is_navigation_request():
                self._blocked = "Only http and https pages can be opened."
                await route.abort("blockedbyclient")
            else:
                await route.continue_()
            return
        navigation = request.is_navigation_request()
        why = self._rules.check(parts.hostname or "", navigation)
        if why is None and navigation and request.frame.parent_frame is None and not request.redirected_from:
            why = self._page_limit_reached()
            if why is None:
                self._pages_this_run += 1
        if why:
            if navigation:
                self._blocked = why
            await route.abort("blockedbyclient")
            return
        host = parts.netloc
        if host not in self._allowed_hosts:
            try:
                await check_url(url, self._resolver)
            except WebError as e:
                if request.is_navigation_request():
                    self._blocked = str(e)
                await route.abort("blockedbyclient")
                return
            self._allowed_hosts.add(host)
        await route.continue_()

    async def open(self, url: str) -> PageView:
        async with self._lock:
            await check_url(url, self._resolver)  # clear message before any browser work
            if why := self._rules.check(urlsplit(url).hostname or "") or self._page_limit_reached():
                raise BrowserError(why)
            page = await self._ensure_page()
            self._blocked = None
            self._refs.clear()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception as e:
                if self._blocked:
                    raise BrowserError(self._blocked) from None
                if type(e).__name__ == "TimeoutError":
                    raise BrowserError(
                        f"{url} did not load within {NAV_TIMEOUT_MS // 1000} seconds."
                    ) from None
                raise BrowserError(f"Could not open {url}: {_short(e)}") from None
            return PageView(url=page.url, title=await page.title())

    async def read(self) -> PageView:
        async with self._lock:
            page = self._page
            if page is None or page.is_closed() or page.url in ("", "about:blank"):
                raise BrowserError("No page is open. Call browser.open with an address first.")
            if page.url.startswith("chrome-error:"):
                raise BrowserError(self._blocked or "The page could not be loaded.")
            try:
                text = await page.aria_snapshot(mode="ai", timeout=SNAPSHOT_TIMEOUT_MS)
                has_password = await page.locator("input[type=password]").count() > 0
            except Exception as e:
                raise BrowserError(f"Could not read the page: {_short(e)}") from None
            host = urlsplit(page.url).netloc
            self._refs = {
                m["ref"]: RefInfo(m["role"], m["name"] or "", host) for m in _REF_LINE.finditer(text)
            }
            notes = []
            if has_password:
                notes.append(
                    "This page has a password field. Piyo never types passwords: ask the user to sign in "
                    "themselves in the browser, then carry on."
                )
            if is_captcha(text):
                notes.append(
                    "This page has a CAPTCHA or human check. Piyo does not solve those: ask the user to."
                )
            return PageView(url=page.url, title=await page.title(), text=text, notes=notes)

    def describe(self, ref: str) -> RefInfo | None:
        return self._refs.get(ref)

    async def _locate(self, ref: str) -> Any:
        if ref not in self._refs or self._page is None or self._page.is_closed():
            raise BrowserError(
                f"{ref!r} is not an element of the page as last read. "
                "Call browser.read and use a ref from it."
            )
        return self._page.locator(f"aria-ref={ref}")

    async def click(self, ref: str) -> PageView:
        async with self._lock:
            loc = await self._locate(ref)
            info = self._refs[ref]
            if is_captcha(info.name):
                raise BrowserError(
                    "That is a CAPTCHA or human check. Piyo does not solve those: ask the user to."
                )
            self._blocked = None
            try:
                await loc.click(timeout=ACTION_TIMEOUT_MS)
                await self._settle()
            except Exception as e:
                if self._blocked:
                    raise BrowserError(self._blocked) from None
                raise BrowserError(_stale_or(e, "click")) from None
            finally:
                self._refs.clear()  # the page may have changed: read again before the next action
            return await self._view()

    async def type(self, ref: str, text: str, submit: bool) -> PageView:
        async with self._lock:
            loc = await self._locate(ref)
            try:
                facts = await loc.evaluate(_FIELD_FACTS, timeout=ACTION_TIMEOUT_MS)
            except Exception as e:
                raise BrowserError(_stale_or(e, "type into")) from None
            why = sensitive_field(facts["type"], facts["autocomplete"], facts["hints"])
            if why:
                raise BrowserError(
                    f"That is {why}. Piyo never fills those in: "
                    "ask the user to type it themselves in the browser."
                )
            self._blocked = None
            try:
                await loc.fill(text, timeout=ACTION_TIMEOUT_MS)
                if submit:
                    await loc.press("Enter", timeout=ACTION_TIMEOUT_MS)
                    await self._settle()
            except Exception as e:
                if self._blocked:
                    raise BrowserError(self._blocked) from None
                raise BrowserError(_stale_or(e, "type into")) from None
            finally:
                if submit:
                    self._refs.clear()
            return await self._view()

    def _open_page(self) -> Any:
        page = self._page
        if page is None or page.is_closed() or page.url in ("", "about:blank"):
            raise BrowserError("No page is open. Call browser.open with an address first.")
        if page.url.startswith("chrome-error:"):
            raise BrowserError(self._blocked or "The page could not be loaded.")
        return page

    async def screenshot(self) -> tuple[bytes, PageView]:
        """The visible part of the page as a JPEG (a full-page capture can be enormous)."""
        async with self._lock:
            page = self._open_page()
            try:
                data = await page.screenshot(type="jpeg", quality=60, timeout=SNAPSHOT_TIMEOUT_MS)
            except Exception as e:
                raise BrowserError(f"Could not take a screenshot: {_short(e)}") from None
            if len(data) > MAX_SCREENSHOT_BYTES:
                raise BrowserError("The screenshot is too large to send. Use browser.read instead.")
            return data, await self._view()

    async def wait(self, seconds: float, text: str | None) -> tuple[bool, PageView]:
        """Pause `seconds`, or until `text` is visible on the page (True) or the time runs out (False)."""
        async with self._lock:
            page = self._open_page()
            self._refs.clear()  # the page is expected to change: read again afterwards
            found = True
            if text:
                try:
                    await page.get_by_text(text).first.wait_for(state="visible", timeout=seconds * 1000)
                except Exception:
                    found = False
            else:
                await asyncio.sleep(seconds)
            return found, await self._view()

    async def _settle(self) -> None:
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=SETTLE_TIMEOUT_MS)
        except Exception:
            pass

    async def _view(self) -> PageView:
        return PageView(url=self._page.url, title=await self._page.title())

    @property
    def visible(self) -> bool:
        return self._visible

    def set_working(self, working: bool) -> None:
        self._working = working

    def begin_run(self) -> None:
        self._pages_this_run = 0

    def _page_limit_reached(self) -> str | None:
        limit = self._rules.get().max_pages
        if self._pages_this_run >= limit:
            return (
                f"Piyo has already opened {limit} pages for this request, which is the limit. "
                "Tell the user and ask them to send another message if you should carry on."
            )
        return None

    def status(self) -> BrowserStatus:
        page = self._page
        url = page.url if page is not None and not page.is_closed() else ""
        return BrowserStatus(visible=self._visible, running=self._context is not None, url=url)

    async def set_visible(self, visible: bool) -> None:
        """Show or hide the window. Chromium can't switch while running, so a running browser restarts on the
        same profile (logins stay) and reopens the page it was on."""
        async with self._lock:
            if visible == self._visible:
                return
            self._visible = visible
            if self._context is None:
                return
            url = self._page.url if self._page is not None and not self._page.is_closed() else ""
            await self._shutdown()
            if url.startswith(("http://", "https://")):
                try:
                    page = await self._ensure_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                except Exception:
                    pass  # the browser is back; the page just didn't reload

    async def stop(self) -> None:
        """Close every page (Stop button). The browser itself stays up so a visible window isn't torn down."""
        async with self._lock:
            self._refs.clear()
            self._page = None
            if self._context is not None:
                for page in list(self._context.pages):
                    try:
                        await page.close()
                    except Exception:
                        pass

    async def close(self) -> None:
        async with self._lock:
            await self._shutdown()

    async def _shutdown(self) -> None:
        context, self._context, self._page = self._context, None, None  # _on_closed then ignores it
        self._refs.clear()
        self._allowed_hosts.clear()
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        await self._stop_playwright()

    async def _stop_playwright(self) -> None:
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


def _short(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:200]


# One line of the AI snapshot: `- button "Go" [ref=e6] ...`
_REF_LINE = re.compile(
    r'^\s*- (?P<role>[\w-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?[^\n]*?\[ref=(?P<ref>e\d+)\]', re.M
)

_FIELD_FACTS = """el => ({
  type: el.getAttribute('type') || '',
  autocomplete: el.getAttribute('autocomplete') || '',
  hints: [el.getAttribute('name'), el.id, el.getAttribute('aria-label'), el.getAttribute('placeholder'),
          ...(el.labels ? [...el.labels].map(l => l.innerText) : [])].filter(Boolean).join(' ')
})"""


def _stale_or(e: Exception, verb: str) -> str:
    if type(e).__name__ == "TimeoutError":
        return f"Could not {verb} that element in time. Call browser.read to see the page as it is now."
    return f"Could not {verb} that element: {_short(e)}"
