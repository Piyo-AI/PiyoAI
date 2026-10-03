"""browser.screenshot and browser.wait."""

import base64

import pytest

from piyo.models.capabilities import ModelCaps
from piyo.skills import SkillRegistry
from piyo.tools import Risk
from piyo.tools.base import RunContext
from piyo.tools.browser import BrowserError, PageView, browser_tools
from tests.test_browser import FakeSession


@pytest.fixture
def ctx(tmp_path):
    return RunContext(skills=SkillRegistry(builtin_dir=tmp_path, user_dir=tmp_path))


class CaptureSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.url = "https://example.com/"

    async def screenshot(self):
        self.calls.append(("screenshot",))
        return b"JPEGBYTES", PageView(url=self.url, title=self.title)

    async def wait(self, seconds, text):
        self.calls.append(("wait", seconds, text))
        return text != "never appears", PageView(url=self.url, title=self.title)


def tools(session):
    return {t.name: t for t in browser_tools(session)}


def caps(vision):
    return ModelCaps(model="m1", context=32000, vision=vision)


async def test_screenshot_attaches_the_picture_and_fences_the_text(ctx):
    out = await tools(CaptureSession())["browser.screenshot"].handler({}, ctx)
    images = ctx.take_images()
    assert len(images) == 1 and base64.b64decode(images[0].data) == b"JPEGBYTES"
    assert images[0].media_type == "image/jpeg"
    assert out.startswith('<untrusted_content source="https://example.com/">')
    assert "not instructions" in out


@pytest.mark.parametrize("vision,allowed", [(None, True), (True, True), (False, False)])
async def test_screenshot_refuses_only_models_known_to_lack_vision(ctx, vision, allowed):
    session = CaptureSession()
    ctx.model_caps = caps(vision)
    handler = tools(session)["browser.screenshot"].handler
    if allowed:
        await handler({}, ctx)
        assert len(ctx.take_images()) == 1
    else:
        with pytest.raises(BrowserError, match="m1 can't read images"):
            await handler({}, ctx)
        assert ctx.take_images() == [] and session.calls == []


async def test_wait_pauses_or_waits_for_text(ctx):
    session = CaptureSession()
    wait = tools(session)["browser.wait"].handler
    assert "Waited 2 seconds" in await wait({}, ctx)
    assert "Waited 0.5 seconds" in await wait({"seconds": 0.5}, ctx)
    assert "The text is on the page" in await wait({"text": "Results", "seconds": 5}, ctx)
    assert "did not appear within 3 seconds" in await wait({"text": "never appears", "seconds": 3}, ctx)
    assert session.calls == [
        ("wait", 2.0, None),
        ("wait", 0.5, None),
        ("wait", 5.0, "Results"),
        ("wait", 3.0, "never appears"),
    ]


@pytest.mark.parametrize(
    "args", [{"seconds": 0}, {"seconds": 11}, {"seconds": "5"}, {"seconds": True}, {"text": ""}, {"text": 5}]
)
async def test_wait_rejects_bad_arguments(ctx, args):
    session = CaptureSession()
    with pytest.raises(BrowserError):
        await tools(session)["browser.wait"].handler(args, ctx)
    assert session.calls == []


def test_screenshot_and_wait_are_auto():
    t = tools(CaptureSession())
    assert t["browser.screenshot"].risk is Risk.AUTO and t["browser.wait"].risk is Risk.AUTO
