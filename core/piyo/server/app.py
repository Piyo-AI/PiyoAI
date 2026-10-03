"""Local API for the desktop app. Bound to 127.0.0.1 and protected by a per-launch token."""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

from piyo import __version__
from piyo.agent import Agent, Finished, Text, ToolFinished, ToolStarted
from piyo.config import data_dir
from piyo.config.browser_rules import BrowserRules
from piyo.config.folders import ApprovedFolders, FolderGrant
from piyo.config.model_limits import ContextLimits, ModelLimits
from piyo.config.model_prices import ModelPrices, Price
from piyo.config.model_vision import ModelVision
from piyo.config.run_settings import RunSettings
from piyo.config.skill_state import SkillState
from piyo.config.tool_modes import ToolModes
from piyo.integrations.google import SCOPE_GROUPS, GoogleAuth, GoogleError
from piyo.integrations.google.check import check_connection
from piyo.models import (
    MissingApiKey,
    ModelInfo,
    Provider,
    ProviderRegistry,
    ProviderUpdate,
    describe_error,
    list_models,
)
from piyo.models.capabilities import ModelCaps, model_issues
from piyo.models.prompt_tools import native_with_fallback, prompt_turn
from piyo.models.turn import Message, stream_turn
from piyo.safety import ApprovalRequest, PermissionGate
from piyo.skills import SkillRegistry
from piyo.skills.catalog import CatalogClient, CatalogEntry, CatalogError
from piyo.skills.install import InstallError, SkillInstaller, read_meta
from piyo.store import (
    AuditStore,
    ConversationStore,
    RunLog,
    RunStore,
    UnknownConversation,
    UnknownRun,
    complete_tool_calls,
)
from piyo.tools import ToolRegistry, core_tools
from piyo.tools import search as search_mod
from piyo.tools.browser import BrowserInstaller, BrowserSession, PlaywrightSession, browser_tools
from piyo.tools.calendar import calendar_tools
from piyo.tools.files import file_tools
from piyo.tools.gmail import gmail_tools
from piyo.tools.google import google_account_tools
from piyo.tools.search import search_tools
from piyo.tools.weather import weather_tools
from piyo.tools.web import web_tools

# Tauri webview origins (Windows/Linux, macOS) and the Vite dev server.
ALLOWED_ORIGINS = [
    "http://tauri.localhost",
    "tauri://localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]

# Tool output shown in the app is a preview; the model still gets the full result.
UI_OUTPUT_CHARS = 2000
MAX_SETUP_BYTES = 100_000
WHY_CHARS = 400  # the model's reason shown on an approval card


class SkillOut(BaseModel):
    name: str
    description: str
    version: str
    source: str
    tools: list[str]
    has_setup: bool
    enabled: bool
    author: str | None = None
    risk: str | None = None
    integrations: list[str] = []
    integration_issues: dict[str, str] = {}  # integration -> why it isn't ready; empty when all are
    secrets: list[str] = []
    model_needs: dict = {}  # what the skill needs from the model: vision, min_context
    model_issues: list[str] = []  # why the model named in the request can't run it; empty when fine
    removable: bool = False  # installed by the user (not built in), so it can be uninstalled
    install_source: str | None = None  # where an installed skill came from
    verified: bool = False  # from the signed catalog; installs from a file never are


class InstallPreviewOut(BaseModel):
    token: str
    name: str
    version: str
    description: str
    author: str | None
    source: str
    permissions: list[str]
    files: list[str]
    installed_version: str | None
    added: list[str]  # permissions the user has to approve now
    verified: bool


class CatalogEntryOut(CatalogEntry):
    installed_version: str | None = None  # the version you have, if any
    builtin: bool = False  # a built-in skill has this name, so it can't be installed


class CatalogOut(BaseModel):
    commit: str  # pass back to /api/catalog/install so the listing and the download agree
    skills: list[CatalogEntryOut]
    skipped: int


class CatalogInstallIn(BaseModel):
    name: str
    commit: str


class InstallCommitIn(BaseModel):
    token: str
    approved: list[str]


class SkillEnabledIn(BaseModel):
    enabled: bool


class SkillsOut(BaseModel):
    skills: list[SkillOut]
    errors: dict[str, str]


class FolderEntry(BaseModel):
    path: str
    auto_changes: bool = False


class SearchOut(BaseModel):
    provider: str = "brave"
    has_key: bool
    docs_url: str = search_mod.DOCS_URL


class OutputLimitIn(BaseModel):
    provider: str
    model: str
    tokens: int | None = None  # None removes the custom setting


class OutputLimitOut(BaseModel):
    output_limit: int  # what will be sent as max_tokens
    custom: bool  # set by the user
    reported_max: int | None  # the model's own maximum, when the provider tells us


class ContextLimitIn(BaseModel):
    provider: str
    model: str
    tokens: int | None = None  # None removes the custom setting


class ContextLimitOut(BaseModel):
    context_limit: int  # the window Piyo fits the conversation into
    custom: bool  # set by the user
    reported: int | None  # the model's own window, when the provider tells us


class ToolModeIn(BaseModel):
    provider: str
    model: str
    mode: str  # auto | native | prompt


class ToolModeOut(BaseModel):
    mode: str  # the user's choice
    effective: str  # what a run will use: native | prompt
    reason: str


class ModelCapabilitiesOut(BaseModel):
    """Everything Piyo knows about one model: what the provider reported, what you set, what a run uses."""

    provider: str
    model: str
    tools: ToolModeOut
    tools_reported: bool | None  # what the provider says about tool calling, when it says
    vision: VisionOut
    context: ContextLimitOut
    output: OutputLimitOut


class BrowserOut(BaseModel):
    visible: bool
    running: bool
    url: str


class BrowserInstallOut(BaseModel):
    state: str  # unknown | missing | installed | installing | failed
    percent: int
    message: str


class BrowserRulesIn(BaseModel):
    allow: list[str] | None = None
    deny: list[str] | None = None
    max_pages: int | None = None


class BrowserRulesOut(BaseModel):
    allow: list[str]
    deny: list[str]
    max_pages: int


class BrowserIn(BaseModel):
    visible: bool


class RunSettingsIn(BaseModel):
    max_steps: int | None = None
    max_tokens: int | None = None
    timeout_s: int | None = None
    local_only: bool | None = None


class RunSettingsOut(BaseModel):
    max_steps: int
    max_tokens: int
    timeout_s: int
    local_only: bool


class PriceIn(BaseModel):
    provider: str
    model: str
    input: float | None = None  # USD per million input tokens; both None removes the setting
    output: float | None = None


class PriceOut(BaseModel):
    input: float | None  # what a run is costed with; None when unknown
    output: float | None
    source: str  # custom | reported | local | unknown


class VisionIn(BaseModel):
    provider: str
    model: str
    mode: str  # auto | yes | no


class VisionOut(BaseModel):
    mode: str  # the user's choice
    effective: bool | None  # what skill checks use; None when unknown
    reported: bool | None  # what the provider says, when it says


class FoldersIn(BaseModel):
    folders: list[FolderEntry]


class ProviderOut(Provider):
    has_key: bool


class KeyIn(BaseModel):
    key: str


class GoogleClientIn(BaseModel):
    client_id: str
    client_secret: str | None = None


class GoogleConnectIn(BaseModel):
    groups: list[str] = []  # scope groups beyond the read-only default
    account: str | None = None  # add access to this account; omit to add a new one


class GoogleAccountOut(BaseModel):
    email: str
    groups: list[str] = []  # the access this account has granted


class GoogleStatusOut(BaseModel):
    state: str  # disconnected | connecting | connected (connecting can happen while others are connected)
    has_client: bool
    accounts: list[GoogleAccountOut] = []
    error: str | None = None
    console_url: str
    available_groups: list[str] = list(SCOPE_GROUPS)


class GoogleCheckItem(BaseModel):
    service: str
    ok: bool
    detail: str


class GoogleCheckOut(BaseModel):
    ok: bool
    results: list[GoogleCheckItem]


class SetupOut(BaseModel):
    name: str
    markdown: str


class GoogleConnectOut(BaseModel):
    url: str  # the app opens this in the user's browser


class ChatRequest(BaseModel):
    type: str = "chat"
    provider: str
    model: str
    message: str
    conversation_id: str | None = None


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationDetail(ConversationOut):
    messages: list[Message]
    active_skills: list[str]


class RunOut(BaseModel):
    id: str
    conversation_id: str
    started_at: str
    provider: str
    model: str
    request: str
    outcome: str
    error: str | None
    duration_ms: int
    input_tokens: int
    output_tokens: int
    tokens_estimated: bool
    cost_usd: float | None  # None when the model's price is unknown


class RunDetail(RunOut):
    steps: list[dict]


class AuditEntryOut(BaseModel):
    seq: int
    at: str
    run_id: str
    conversation_id: str
    tool: str
    summary: str
    arguments: dict
    args_digest: str
    why: str
    decision: str  # allowed | declined
    hash: str


class AuditOut(BaseModel):
    entries: list[AuditEntryOut]  # newest first
    verified: bool  # the whole chain checks out
    problem: str | None


class TitleIn(BaseModel):
    title: str


def grants_out(grants: list[FolderGrant]) -> FoldersIn:
    return FoldersIn(
        folders=[FolderEntry(path=str(g.path), auto_changes=g.auto_changes) for g in grants]
    )


def create_app(
    token: str,
    registry: ProviderRegistry | None = None,
    skills: SkillRegistry | None = None,
    turn_fn=stream_turn,
    store: ConversationStore | None = None,
    google: GoogleAuth | None = None,
    browser: BrowserSession | None = None,
    browser_installer: BrowserInstaller | None = None,
    catalog: CatalogClient | None = None,
) -> FastAPI:
    google = google or GoogleAuth()
    browser_rules = BrowserRules()
    browser = browser or PlaywrightSession(rules=browser_rules)
    installer = browser_installer or BrowserInstaller()
    registry = registry or ProviderRegistry()
    skills = skills or SkillRegistry()
    skill_state = SkillState()
    skills.disabled |= skill_state.disabled()  # the user's switches survive restarts
    store = store or ConversationStore()
    run_store = RunStore(store)
    audit = AuditStore(store)
    limits = ModelLimits()
    context_limits = ContextLimits()
    tool_modes = ToolModes()
    run_settings = RunSettings()
    prices = ModelPrices()
    vision = ModelVision()
    reported_prices: dict[tuple[str, str], Price] = {}
    # What we learned about models' tool support: reported by the provider, or from a failed request.
    reported_tools: dict[tuple[str, str], bool] = {}
    learned_no_tools: set[tuple[str, str]] = set()
    # Output maximums providers reported in model lists the app has loaded.
    reported: dict[tuple[str, str], int] = {}
    reported_context: dict[tuple[str, str], int] = {}  # context windows, same source
    reported_vision: dict[tuple[str, str], bool] = {}
    folders = ApprovedFolders()
    tools = ToolRegistry(
        core_tools()
        + file_tools(folders)
        + web_tools()
        + browser_tools(browser)
        + search_tools()
        + gmail_tools(google)
        + google_account_tools(google)
        + calendar_tools(google)
        + weather_tools()
    )
    app = FastAPI(title="Piyo Core", version=__version__)
    app.router.add_event_handler("shutdown", browser.close)
    app.router.add_event_handler("shutdown", installer.close)
    app.state.tools = tools
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_token(authorization: str = Header(default="")) -> None:
        if not secrets.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="invalid token")

    def get_provider(provider_id: str) -> Provider:
        try:
            return registry.get(provider_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown provider") from None

    def out(p: Provider) -> ProviderOut:
        return ProviderOut(**p.model_dump(), has_key=p.key_configured())

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    auth = [Depends(require_token)]

    @app.get("/api/search", dependencies=auth)
    def search_status() -> SearchOut:
        return SearchOut(has_key=bool(search_mod.get_key()))

    @app.put("/api/search/key", dependencies=auth, status_code=204)
    def set_search_key(body: KeyIn) -> None:
        if not body.key.strip():
            raise HTTPException(status_code=400, detail="The key is empty.")
        search_mod.set_key(body.key.strip())

    @app.delete("/api/search/key", dependencies=auth, status_code=204)
    def delete_search_key() -> None:
        search_mod.delete_key()

    def integration_issue(name: str) -> str | None:
        if name == "google":
            if google.connected():
                return None
            return google.status()["error"] or "Google is not connected."
        return f"Unknown integration {name!r}."

    def google_status() -> GoogleStatusOut:
        return GoogleStatusOut(**google.status())

    @app.get("/api/integrations/google", dependencies=auth)
    def get_google() -> GoogleStatusOut:
        return google_status()

    @app.put("/api/integrations/google/client", dependencies=auth, status_code=204)
    def set_google_client(body: GoogleClientIn) -> None:
        if not body.client_id.strip():
            raise HTTPException(status_code=400, detail="The client ID is empty.")
        google.set_client(body.client_id, body.client_secret)

    @app.post("/api/integrations/google/connect", dependencies=auth)
    async def connect_google(body: GoogleConnectIn) -> GoogleConnectOut:
        try:
            return GoogleConnectOut(url=await google.begin(body.groups, body.account))
        except GoogleError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.post("/api/integrations/google/test", dependencies=auth)
    async def test_google() -> GoogleCheckOut:
        try:
            return GoogleCheckOut(**await check_connection(google))
        except GoogleError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.post("/api/integrations/google/cancel", dependencies=auth, status_code=204)
    async def cancel_google() -> None:
        await google.cancel()

    @app.delete("/api/integrations/google", dependencies=auth, status_code=204)
    async def disconnect_google(account: str | None = None, remove_client: bool = False) -> None:
        """Disconnect one account, or all of them when none is named."""
        try:
            await google.disconnect(account)
        except GoogleError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        if remove_client:
            google.clear_client()

    @app.get("/api/folders", dependencies=auth)
    def get_folders() -> FoldersIn:
        return grants_out(folders.list())

    @app.put("/api/folders", dependencies=auth)
    def set_folders(body: FoldersIn) -> FoldersIn:
        try:
            grants = [FolderGrant(Path(f.path), f.auto_changes) for f in body.folders]
            return grants_out(folders.set(grants))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.get("/api/providers", dependencies=auth)
    def providers() -> list[ProviderOut]:
        return [out(p) for p in registry.list()]

    @app.post("/api/providers", dependencies=auth, status_code=201)
    def add_provider(provider: Provider) -> ProviderOut:
        try:
            return out(registry.add(provider))
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None

    @app.patch("/api/providers/{provider_id}", dependencies=auth)
    def update_provider(provider_id: str, changes: ProviderUpdate) -> ProviderOut:
        get_provider(provider_id)
        return out(registry.update(provider_id, changes))

    @app.delete("/api/providers/{provider_id}", dependencies=auth, status_code=204)
    def remove_provider(provider_id: str) -> None:
        get_provider(provider_id)
        try:
            registry.remove(provider_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.put("/api/providers/{provider_id}/key", dependencies=auth, status_code=204)
    def set_key(provider_id: str, body: KeyIn) -> None:
        get_provider(provider_id)
        registry.set_key(provider_id, body.key.strip())

    @app.delete("/api/providers/{provider_id}/key", dependencies=auth, status_code=204)
    def delete_key(provider_id: str) -> None:
        get_provider(provider_id)
        registry.delete_key(provider_id)

    @app.get("/api/providers/{provider_id}/models", dependencies=auth)
    async def models(provider_id: str) -> list[ModelInfo]:
        provider = get_provider(provider_id)
        try:
            found = await list_models(provider)
            for m in found:
                if m.max_output:
                    reported[(provider.id, m.id)] = m.max_output
                if m.tools is not None:
                    reported_tools[(provider.id, m.id)] = m.tools
                if m.context_length:
                    reported_context[(provider.id, m.id)] = m.context_length
                if m.price_input is not None and m.price_output is not None:
                    reported_prices[(provider.id, m.id)] = Price(m.price_input, m.price_output)
                if m.vision is not None:
                    reported_vision[(provider.id, m.id)] = m.vision
            return found
        except MissingApiKey as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except Exception as e:  # network / auth errors from the provider
            raise HTTPException(status_code=502, detail=describe_error(provider, e)) from None

    def output_limit_info(provider_id: str, model: str) -> OutputLimitOut:
        cap = reported.get((provider_id, model))
        return OutputLimitOut(
            output_limit=limits.resolve(provider_id, model, cap),
            custom=limits.get(provider_id, model) is not None,
            reported_max=cap,
        )

    @app.get("/api/output-limit", dependencies=auth)
    def get_output_limit(provider: str, model: str) -> OutputLimitOut:
        get_provider(provider)
        return output_limit_info(provider, model)

    @app.put("/api/output-limit", dependencies=auth)
    def set_output_limit(body: OutputLimitIn) -> OutputLimitOut:
        get_provider(body.provider)
        model = body.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="Pick a model first.")
        cap = reported.get((body.provider, model))
        if body.tokens is not None and cap and body.tokens > cap:
            raise HTTPException(
                status_code=400, detail=f"{model} can produce at most {cap} tokens per reply."
            )
        try:
            limits.set(body.provider, model, body.tokens)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        return output_limit_info(body.provider, model)

    def context_limit_info(provider_id: str, model: str) -> ContextLimitOut:
        cap = reported_context.get((provider_id, model))
        return ContextLimitOut(
            context_limit=context_limits.resolve(provider_id, model, cap),
            custom=context_limits.get(provider_id, model) is not None,
            reported=cap,
        )

    @app.get("/api/context-limit", dependencies=auth)
    def get_context_limit(provider: str, model: str) -> ContextLimitOut:
        get_provider(provider)
        return context_limit_info(provider, model)

    @app.put("/api/context-limit", dependencies=auth)
    def set_context_limit(body: ContextLimitIn) -> ContextLimitOut:
        get_provider(body.provider)
        model = body.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="Pick a model first.")
        try:
            context_limits.set(body.provider, model, body.tokens)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        return context_limit_info(body.provider, model)

    def tool_mode_info(provider_id: str, model: str) -> ToolModeOut:
        mode = tool_modes.get(provider_id, model)
        key = (provider_id, model)
        if mode != "auto":
            return ToolModeOut(mode=mode, effective=mode, reason="Set by you.")
        if reported_tools.get(key) is False:
            return ToolModeOut(
                mode=mode, effective="prompt", reason="The provider says this model has no tool calling."
            )
        if key in learned_no_tools:
            return ToolModeOut(
                mode=mode, effective="prompt", reason="The provider rejected tool calling for this model."
            )
        return ToolModeOut(mode=mode, effective="native", reason="Assumed; switches itself if rejected.")

    def turn_for(provider_id: str, model: str):
        """The turn function for a run: native calling, or tools as text, per the model's mode."""
        key = (provider_id, model)
        if tool_mode_info(provider_id, model).effective == "prompt":

            def as_text(provider, name, messages, specs, system, max_tokens):
                return prompt_turn(turn_fn, provider, name, messages, specs, system, max_tokens)

            return as_text

        def native(provider, name, messages, specs, system, max_tokens):
            if tool_modes.get(provider_id, model) == "native":  # forced: no fallback
                return turn_fn(provider, name, messages, specs, system, max_tokens)
            if key in learned_no_tools:  # learned earlier in this run
                return prompt_turn(turn_fn, provider, name, messages, specs, system, max_tokens)
            return native_with_fallback(
                turn_fn, lambda: learned_no_tools.add(key), provider, name, messages, specs,
                system, max_tokens,
            )

        return native

    @app.get("/api/tool-mode", dependencies=auth)
    def get_tool_mode(provider: str, model: str) -> ToolModeOut:
        get_provider(provider)
        return tool_mode_info(provider, model)

    @app.put("/api/tool-mode", dependencies=auth)
    def set_tool_mode(body: ToolModeIn) -> ToolModeOut:
        get_provider(body.provider)
        model = body.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="Pick a model first.")
        try:
            tool_modes.set(body.provider, model, body.mode)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        learned_no_tools.discard((body.provider, model))  # the user's word replaces a guess
        return tool_mode_info(body.provider, model)

    def price_for(provider: Provider, model: str) -> Price | None:
        return prices.resolve(
            provider.id, model, reported_prices.get((provider.id, model)), provider.local
        )

    def price_info(provider: Provider, model: str) -> PriceOut:
        price = price_for(provider, model)
        if provider.local:
            source = "local"
        elif prices.get(provider.id, model):
            source = "custom"
        else:
            source = "reported" if price else "unknown"
        return PriceOut(
            input=price.input if price else None, output=price.output if price else None, source=source
        )

    @app.get("/api/price", dependencies=auth)
    def get_price(provider: str, model: str) -> PriceOut:
        return price_info(get_provider(provider), model)

    @app.put("/api/price", dependencies=auth)
    def set_price(body: PriceIn) -> PriceOut:
        provider = get_provider(body.provider)
        model = body.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="Pick a model first.")
        if (body.input is None) != (body.output is None):
            raise HTTPException(status_code=400, detail="Give both the input and the output price.")
        try:
            price = None if body.input is None else Price(body.input, body.output)
            prices.set(provider.id, model, price)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        return price_info(provider, model)

    def caps_for(provider_id: str, model: str) -> ModelCaps:
        key = (provider_id, model)
        return ModelCaps(
            model=model,
            context=context_limits.resolve(provider_id, model, reported_context.get(key)),
            vision=vision.resolve(provider_id, model, reported_vision.get(key)),
        )

    def vision_info(provider_id: str, model: str) -> VisionOut:
        reported = reported_vision.get((provider_id, model))
        return VisionOut(
            mode=vision.get(provider_id, model),
            effective=vision.resolve(provider_id, model, reported),
            reported=reported,
        )

    @app.get("/api/model-capabilities", dependencies=auth)
    def get_model_capabilities(provider: str, model: str) -> ModelCapabilitiesOut:
        get_provider(provider)
        return ModelCapabilitiesOut(
            provider=provider,
            model=model,
            tools=tool_mode_info(provider, model),
            tools_reported=reported_tools.get((provider, model)),
            vision=vision_info(provider, model),
            context=context_limit_info(provider, model),
            output=output_limit_info(provider, model),
        )

    @app.get("/api/vision", dependencies=auth)
    def get_vision(provider: str, model: str) -> VisionOut:
        get_provider(provider)
        return vision_info(provider, model)

    @app.put("/api/vision", dependencies=auth)
    def set_vision(body: VisionIn) -> VisionOut:
        get_provider(body.provider)
        model = body.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="Pick a model first.")
        try:
            vision.set(body.provider, model, body.mode)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        return vision_info(body.provider, model)

    @app.get("/api/browser", dependencies=auth)
    def get_browser() -> BrowserOut:
        return BrowserOut(**vars(browser.status()))

    @app.put("/api/browser", dependencies=auth)
    async def set_browser(body: BrowserIn) -> BrowserOut:
        try:
            await browser.set_visible(body.visible)
        except Exception as e:
            detail = f"Could not change the browser: {type(e).__name__}"
            raise HTTPException(status_code=500, detail=detail) from None
        return BrowserOut(**vars(browser.status()))

    @app.get("/api/browser/install", dependencies=auth)
    async def get_browser_install() -> BrowserInstallOut:
        return BrowserInstallOut(**vars(await installer.refresh()))

    @app.post("/api/browser/install", dependencies=auth)
    async def start_browser_install() -> BrowserInstallOut:
        """The user pressed Install browser: this is the only thing that ever starts the download."""
        status = await installer.refresh()
        if status.state in ("installed", "installing"):
            return BrowserInstallOut(**vars(status))
        return BrowserInstallOut(**vars(installer.start()))

    @app.get("/api/browser/rules", dependencies=auth)
    def get_browser_rules() -> BrowserRulesOut:
        return BrowserRulesOut(**vars(browser_rules.get()))

    @app.put("/api/browser/rules", dependencies=auth)
    def set_browser_rules(body: BrowserRulesIn) -> BrowserRulesOut:
        try:
            return BrowserRulesOut(**vars(browser_rules.update(**body.model_dump())))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.post("/api/browser/stop", dependencies=auth)
    async def stop_browser() -> BrowserOut:
        await browser.stop()
        return BrowserOut(**vars(browser.status()))

    @app.get("/api/run-settings", dependencies=auth)
    def get_run_settings() -> RunSettingsOut:
        return RunSettingsOut(**vars(run_settings.get()))

    @app.put("/api/run-settings", dependencies=auth)
    def set_run_settings(body: RunSettingsIn) -> RunSettingsOut:
        try:
            return RunSettingsOut(**vars(run_settings.update(**body.model_dump())))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.get("/api/skills", dependencies=auth)
    def list_skills(provider: str | None = None, model: str | None = None) -> SkillsOut:
        caps = caps_for(provider, model) if provider and model else None
        return SkillsOut(
            skills=[
                SkillOut(
                    name=sk.manifest.name,
                    description=sk.manifest.description,
                    version=sk.manifest.version,
                    source=sk.source,
                    tools=sk.manifest.requires.tools,
                    has_setup=sk.has_setup,
                    enabled=sk.manifest.name not in skills.disabled,
                    author=sk.manifest.author,
                    risk=sk.manifest.risk,
                    integrations=sk.manifest.requires.integrations,
                    integration_issues={
                        i: issue
                        for i in sk.manifest.requires.integrations
                        if (issue := integration_issue(i))
                    },
                    secrets=sk.manifest.requires.secrets,
                    model_needs=sk.manifest.requires.model.model_dump(exclude_defaults=True),
                    model_issues=model_issues(sk.manifest.requires.model, caps) if caps else [],
                    removable=sk.source == "user",
                    install_source=read_meta(sk.path).get("source") if sk.source == "user" else None,
                )
                for sk in skills.list()
            ],
            errors=skills.errors,
        )

    @app.get("/api/skills/{name}/setup", dependencies=auth)
    def skill_setup(name: str) -> SetupOut:
        skill = next((sk for sk in skills.list() if sk.manifest.name == name), None)
        if skill is None or not skill.has_setup:
            raise HTTPException(status_code=404, detail="This skill has no setup guide.")
        path = skill.path / "SETUP.md"
        if path.stat().st_size > MAX_SETUP_BYTES:
            raise HTTPException(status_code=413, detail="The setup guide is too large to show.")
        return SetupOut(name=name, markdown=path.read_text(encoding="utf-8", errors="replace"))

    def skill_installer() -> SkillInstaller:
        return SkillInstaller(
            skills.user_dir, data_dir() / "install", {s.manifest.name for s in skills.list() if s.source == "builtin"}
        )

    @app.post("/api/skills/install/preview", dependencies=auth)
    async def preview_skill_install(request: Request) -> InstallPreviewOut:
        data = await request.body()  # the raw .piyoskill zip
        try:
            return InstallPreviewOut(**vars(skill_installer().stage_zip(data)))
        except InstallError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    catalog_client = catalog or CatalogClient()

    @app.get("/api/catalog", dependencies=auth)
    async def get_catalog() -> CatalogOut:
        try:
            found = await catalog_client.fetch()
        except CatalogError as e:
            raise HTTPException(status_code=502, detail=str(e)) from None
        have = {sk.manifest.name: sk for sk in skills.list()}
        return CatalogOut(
            commit=found.commit,
            skipped=found.skipped,
            skills=[
                CatalogEntryOut(
                    **e.model_dump(),
                    installed_version=have[e.name].manifest.version if e.name in have else None,
                    builtin=e.name in have and have[e.name].source == "builtin",
                )
                for e in found.skills
            ],
        )

    @app.post("/api/catalog/install", dependencies=auth)
    async def stage_catalog_skill(body: CatalogInstallIn) -> InstallPreviewOut:
        """Downloads one catalog skill for review; finish with POST /api/skills/install like any other."""
        try:
            return InstallPreviewOut(**vars(await catalog_client.stage(skill_installer(), body.name, body.commit)))
        except (CatalogError, InstallError) as e:
            raise HTTPException(status_code=502 if isinstance(e, CatalogError) else 400, detail=str(e)) from None

    @app.post("/api/skills/install", dependencies=auth, status_code=201)
    def install_skill(body: InstallCommitIn) -> dict:
        try:
            skill = skill_installer().commit(body.token, body.approved)
        except InstallError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        skills.reload()
        return {"name": skill.manifest.name, "version": skill.manifest.version}

    @app.delete("/api/skills/install/{token}", dependencies=auth, status_code=204)
    def cancel_skill_install(token: str) -> None:
        skill_installer().cancel(token)

    @app.delete("/api/skills/{name}", dependencies=auth, status_code=204)
    def uninstall_skill(name: str) -> None:
        try:
            skill_installer().uninstall(name)
        except InstallError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        skills.reload()
        skills.disabled = skill_state.set_enabled(name, True)  # leave no switch behind

    @app.put("/api/skills/{name}", dependencies=auth, status_code=204)
    def set_skill_enabled(name: str, body: SkillEnabledIn) -> None:
        if not any(sk.manifest.name == name for sk in skills.list()):
            raise HTTPException(status_code=404, detail="unknown skill")
        skills.disabled = skill_state.set_enabled(name, body.enabled)

    def conversation_or_404(conversation_id: str):
        try:
            return store.get(conversation_id)
        except UnknownConversation:
            raise HTTPException(status_code=404, detail="unknown conversation") from None

    @app.get("/api/conversations", dependencies=auth)
    def list_conversations(limit: int = 50, offset: int = 0) -> list[ConversationOut]:
        page = store.list(min(max(limit, 1), 200), max(offset, 0))
        return [ConversationOut(**vars(c)) for c in page]

    @app.get("/api/conversations/{conversation_id}", dependencies=auth)
    def get_conversation(conversation_id: str) -> ConversationDetail:
        conv = conversation_or_404(conversation_id)
        return ConversationDetail(**vars(conv))

    @app.patch("/api/conversations/{conversation_id}", dependencies=auth)
    def rename_conversation(conversation_id: str, body: TitleIn) -> ConversationOut:
        conversation_or_404(conversation_id)
        store.rename(conversation_id, body.title)
        return ConversationOut(**{k: v for k, v in vars(store.get(conversation_id)).items()
                                  if k in ConversationOut.model_fields})

    @app.delete("/api/conversations/{conversation_id}", dependencies=auth, status_code=204)
    def delete_conversation(conversation_id: str) -> None:
        conversation_or_404(conversation_id)
        store.delete(conversation_id)

    @app.get("/api/runs", dependencies=auth)
    def list_runs(
        conversation_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[RunOut]:
        page = run_store.list(conversation_id, min(max(limit, 1), 500), max(offset, 0))
        return [RunOut(**vars(r)) for r in page]

    @app.get("/api/runs/{run_id}", dependencies=auth)
    def get_run(run_id: str) -> RunDetail:
        try:
            return RunDetail(**vars(run_store.get(run_id)))
        except UnknownRun:
            raise HTTPException(status_code=404, detail="unknown run") from None

    @app.get("/api/audit", dependencies=auth)
    def get_audit(limit: int = 200, offset: int = 0) -> AuditOut:
        report = audit.report(min(max(limit, 1), 1000), max(offset, 0))
        return AuditOut(
            entries=[AuditEntryOut(**vars(e)) for e in report.entries],
            verified=report.verified,
            problem=report.problem,
        )

    @app.websocket("/ws/chat")
    async def chat(ws: WebSocket) -> None:
        # Browsers can't set headers on WebSockets, so the token comes as a query param.
        if not secrets.compare_digest(ws.query_params.get("token", ""), token):
            await ws.close(code=4401)
            return
        await ws.accept()
        pending: dict[str, asyncio.Future[bool]] = {}  # approval id -> the user's answer
        run: asyncio.Task | None = None

        current_log: RunLog | None = None  # the run in progress, for the approval hook
        used_browser = False  # the run in progress called a browser tool: Stop then closes its pages

        async def approver(req: ApprovalRequest) -> bool:
            approved = await ask(req)
            if current_log:
                current_log.step(
                    "approval",
                    tool=req.tool,
                    arguments=req.arguments,
                    summary=req.summary,
                    why=req.why,
                    approved=approved,
                )
            audit.record(
                current_log.id if current_log else "",
                current_log.conversation_id if current_log else "",
                req.tool,
                req.summary,
                req.arguments,
                req.why,
                approved,
                current_log.secrets if current_log else (),
            )
            return approved

        async def ask(req: ApprovalRequest) -> bool:
            approval_id = secrets.token_hex(4)
            answer = asyncio.get_running_loop().create_future()
            pending[approval_id] = answer
            try:
                await ws.send_json(
                    {
                        "type": "approval_request",
                        "id": approval_id,
                        "tool": req.tool,
                        "arguments": req.arguments,
                        "summary": req.summary,
                        "why": req.why[:WHY_CHARS],
                    }
                )
                return await answer
            finally:
                pending.pop(approval_id, None)

        async def run_conversation(req: ChatRequest, provider: Provider) -> None:
            budget = run_settings.get()
            if budget.local_only and not provider.local:
                await ws.send_json(
                    {
                        "type": "error",
                        "message": (
                            f"Local-only mode is on, and {provider.name} is a cloud provider. "
                            "Pick a local model, or turn local-only off in Settings."
                        ),
                    }
                )
                return
            agent = Agent(
                provider,
                req.model,
                tools,
                skills,
                PermissionGate(approver),
                max_tokens=limits.resolve(
                    provider.id, req.model, reported.get((provider.id, req.model))
                ),
                turn_fn=turn_for(provider.id, req.model),
                context_tokens=context_limits.resolve(
                    provider.id, req.model, reported_context.get((provider.id, req.model))
                ),
                max_steps=budget.max_steps,
                max_total_tokens=budget.max_tokens,
                timeout_s=budget.timeout_s,
                model_caps=caps_for(provider.id, req.model),
                integration_issue=integration_issue,
            )
            if req.conversation_id:
                try:
                    conv = store.get(req.conversation_id)
                except UnknownConversation:
                    await ws.send_json(
                        {"type": "error", "message": "That conversation no longer exists."}
                    )
                    return
            else:
                conv = store.get(store.create().id)
                await ws.send_json({"type": "conversation", "id": conv.id})
            # An earlier run that died mid-tool-call must not break the next message.
            history = complete_tool_calls(conv.messages)
            messages = [*history, Message(role="user", content=req.message)]
            store.append(conv.id, [*history[len(conv.messages):], messages[-1]])
            saved_from = len(messages)
            saved = False
            nonlocal current_log, used_browser
            used_browser = False
            browser.begin_run()
            log = current_log = RunLog(
                conv.id,
                provider.id,
                req.model,
                req.message,
                price=price_for(provider, req.model),
                secrets=(provider.api_key() or "", search_mod.get_key() or "", *google.secret_values()),
            )
            agent.log = log
            logged = False

            def save_log(outcome: str, error: str | None = None) -> None:
                nonlocal logged
                if not logged:  # once per run, whichever path ends it first
                    logged = True
                    run_store.save(log, outcome, error)

            def save() -> None:
                nonlocal saved
                if saved:
                    return
                saved = True
                store.append(conv.id, complete_tool_calls(messages[saved_from:]))
                store.set_active_skills(conv.id, sorted(agent.active_skills))

            try:
                async for event in agent.run(messages, set(conv.active_skills)):
                    if isinstance(event, Text):
                        await ws.send_json({"type": "delta", "text": event.text})
                    elif isinstance(event, ToolStarted):
                        if event.name.startswith(("browser.", "browser__")):
                            used_browser = True
                            browser.set_working(True)
                        await ws.send_json(
                            {
                                "type": "tool_start",
                                "id": event.id,
                                "name": event.name,
                                "arguments": event.arguments,
                            }
                        )
                    elif isinstance(event, ToolFinished):
                        await ws.send_json(
                            {
                                "type": "tool_end",
                                "id": event.id,
                                "name": event.name,
                                "output": event.output[:UI_OUTPUT_CHARS],
                                "is_error": event.is_error,
                            }
                        )
                    elif isinstance(event, Finished):
                        save()  # before "done", so a list refresh right after it sees this run
                        save_log(event.reason)
                        await ws.send_json({"type": "done", "reason": event.reason})
            except (WebSocketDisconnect, asyncio.CancelledError):
                save_log("cancelled")
                raise
            except Exception as e:
                message = describe_error(provider, e)
                save_log("error", message)
                await ws.send_json({"type": "error", "message": message})
            finally:
                save()
                save_log("error", "The run ended unexpectedly.")
                current_log = None
                browser.set_working(False)

        async def run_agent(req: ChatRequest, provider: Provider) -> None:
            """Whatever goes wrong, the app must get an answer instead of waiting forever."""
            try:
                await run_conversation(req, provider)
            except (WebSocketDisconnect, asyncio.CancelledError):
                raise
            except Exception as e:
                await ws.send_json({"type": "error", "message": describe_error(provider, e)})

        try:
            while True:
                data = await ws.receive_json()
                kind = data.get("type", "chat") if isinstance(data, dict) else None
                if kind == "approval":
                    answer = pending.get(str(data.get("id")))
                    if answer and not answer.done():
                        answer.set_result(data.get("approve") is True)
                elif kind == "cancel":
                    if run and not run.done():
                        run.cancel()
                        await asyncio.gather(run, return_exceptions=True)
                        if used_browser:
                            await browser.stop()
                    # Always acknowledge, even when the run had just finished, so the app unlocks.
                    await ws.send_json({"type": "done", "reason": "cancelled"})
                elif kind == "chat":
                    if run and not run.done():
                        await ws.send_json({"type": "error", "message": "Piyo is still working."})
                        continue
                    try:
                        req = ChatRequest.model_validate(data)
                        provider = registry.get(req.provider)
                    except (ValidationError, KeyError, ValueError) as e:
                        await ws.send_json({"type": "error", "message": f"bad request: {e}"})
                        continue
                    run = asyncio.create_task(run_agent(req, provider))
                else:
                    await ws.send_json({"type": "error", "message": "bad request: unknown type"})
        except WebSocketDisconnect:
            pass
        finally:
            if run and not run.done():
                run.cancel()

    return app
