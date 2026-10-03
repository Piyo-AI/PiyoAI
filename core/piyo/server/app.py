"""Local API for the desktop app. Bound to 127.0.0.1 and protected by a per-launch token."""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

from piyo import __version__
from piyo.agent import Agent, Finished, Text, ToolFinished, ToolStarted
from piyo.config.folders import ApprovedFolders, FolderGrant
from piyo.models import (
    ChatMessage,
    MissingApiKey,
    ModelInfo,
    Provider,
    ProviderRegistry,
    ProviderUpdate,
    describe_error,
    list_models,
)
from piyo.models.turn import Message, stream_turn
from piyo.safety import ApprovalRequest, PermissionGate
from piyo.skills import SkillRegistry
from piyo.tools import ToolRegistry, core_tools
from piyo.tools.files import file_tools
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


class SkillOut(BaseModel):
    name: str
    description: str
    version: str
    source: str
    tools: list[str]
    has_setup: bool
    enabled: bool


class SkillsOut(BaseModel):
    skills: list[SkillOut]
    errors: dict[str, str]


class FolderEntry(BaseModel):
    path: str
    auto_changes: bool = False


class FoldersIn(BaseModel):
    folders: list[FolderEntry]


class ProviderOut(Provider):
    has_key: bool


class KeyIn(BaseModel):
    key: str


class ChatRequest(BaseModel):
    type: str = "chat"
    provider: str
    model: str
    messages: list[ChatMessage]


def grants_out(grants: list[FolderGrant]) -> FoldersIn:
    return FoldersIn(
        folders=[FolderEntry(path=str(g.path), auto_changes=g.auto_changes) for g in grants]
    )


def create_app(
    token: str,
    registry: ProviderRegistry | None = None,
    skills: SkillRegistry | None = None,
    turn_fn=stream_turn,
) -> FastAPI:
    registry = registry or ProviderRegistry()
    skills = skills or SkillRegistry()
    folders = ApprovedFolders()
    tools = ToolRegistry(core_tools() + file_tools(folders) + web_tools())
    app = FastAPI(title="Piyo Core", version=__version__)
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
            return await list_models(provider)
        except MissingApiKey as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except Exception as e:  # network / auth errors from the provider
            raise HTTPException(status_code=502, detail=describe_error(provider, e)) from None

    @app.get("/api/skills", dependencies=auth)
    def list_skills() -> SkillsOut:
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
                )
                for sk in skills.list()
            ],
            errors=skills.errors,
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

        async def approver(req: ApprovalRequest) -> bool:
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
                    }
                )
                return await answer
            finally:
                pending.pop(approval_id, None)

        async def run_agent(req: ChatRequest, provider: Provider) -> None:
            agent = Agent(
                provider, req.model, tools, skills, PermissionGate(approver), turn_fn=turn_fn
            )
            messages = [Message(role=m.role, content=m.content) for m in req.messages]
            try:
                async for event in agent.run(messages):
                    if isinstance(event, Text):
                        await ws.send_json({"type": "delta", "text": event.text})
                    elif isinstance(event, ToolStarted):
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
                        await ws.send_json({"type": "done", "reason": event.reason})
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
