"""Local API for the desktop app. Bound to 127.0.0.1 and protected by a per-launch token."""

from __future__ import annotations

import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

from piyo import __version__
from piyo.models import (
    ChatMessage,
    MissingApiKey,
    Provider,
    ProviderRegistry,
    ProviderUpdate,
    list_models,
    stream_chat,
)

# Tauri webview origins (Windows/Linux, macOS) and the Vite dev server.
ALLOWED_ORIGINS = ["http://tauri.localhost", "tauri://localhost", "http://localhost:1420"]

SYSTEM_PROMPT = (
    "You are Piyo, a helpful personal assistant that helps the user with their daily tasks. "
    "Be concise and friendly."
)


class ProviderOut(Provider):
    has_key: bool


class KeyIn(BaseModel):
    key: str


class ChatRequest(BaseModel):
    type: str = "chat"
    provider: str
    model: str
    messages: list[ChatMessage]


def create_app(token: str, registry: ProviderRegistry | None = None) -> FastAPI:
    registry = registry or ProviderRegistry()
    app = FastAPI(title="Piyo Core", version=__version__)
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
    async def models(provider_id: str) -> list[str]:
        provider = get_provider(provider_id)
        try:
            return await list_models(provider)
        except MissingApiKey as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except Exception as e:  # network / auth errors from the provider
            raise HTTPException(status_code=502, detail=f"{provider.name}: {e}") from None

    @app.websocket("/ws/chat")
    async def chat(ws: WebSocket) -> None:
        # Browsers can't set headers on WebSockets, so the token comes as a query param.
        if not secrets.compare_digest(ws.query_params.get("token", ""), token):
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            while True:
                try:
                    req = ChatRequest.model_validate(await ws.receive_json())
                    provider = registry.get(req.provider)
                except (ValidationError, KeyError, ValueError) as e:
                    await ws.send_json({"type": "error", "message": f"bad request: {e}"})
                    continue
                try:
                    async for text in stream_chat(
                        provider, req.model, req.messages, system=SYSTEM_PROMPT
                    ):
                        await ws.send_json({"type": "delta", "text": text})
                    await ws.send_json({"type": "done"})
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    await ws.send_json({"type": "error", "message": str(e)})
        except WebSocketDisconnect:
            pass

    return app
