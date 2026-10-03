"""Model providers are data, not code.

Every provider speaks one of two wire formats: the OpenAI-compatible Chat Completions API
(OpenAI, OpenRouter, DeepSeek, Kimi, Gemini, Ollama, LM Studio and most others) or the
Anthropic Messages API. Users can add any endpoint that speaks either one.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from piyo.config import data_dir
from piyo.config.secrets import delete_secret, get_secret, set_secret


class ApiStyle(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Provider(BaseModel):
    id: str
    name: str
    api_style: ApiStyle
    base_url: str
    requires_key: bool = True
    local: bool = False
    preset: bool = False
    # Env var checked when the keychain has no key (development convenience).
    key_env: str | None = None
    default_model: str | None = None
    docs_url: str | None = None

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", v):
            raise ValueError("id must be lowercase letters, digits and dashes (max 40)")
        return v

    @property
    def secret_name(self) -> str:
        return f"provider:{self.id}:api_key"

    def api_key(self) -> str | None:
        return get_secret(self.secret_name, self.key_env)

    def key_configured(self) -> bool:
        return not self.requires_key or bool(self.api_key())


PRESETS: list[Provider] = [
    Provider(
        id="anthropic",
        name="Anthropic",
        api_style=ApiStyle.ANTHROPIC,
        base_url="https://api.anthropic.com",
        key_env="ANTHROPIC_API_KEY",
        default_model="claude-sonnet-5-5",
        docs_url="https://console.anthropic.com/settings/keys",
    ),
    Provider(
        id="openai",
        name="OpenAI",
        api_style=ApiStyle.OPENAI,
        base_url="https://api.openai.com/v1",
        key_env="OPENAI_API_KEY",
        docs_url="https://platform.openai.com/api-keys",
    ),
    Provider(
        id="openrouter",
        name="OpenRouter",
        api_style=ApiStyle.OPENAI,
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        docs_url="https://openrouter.ai/keys",
    ),
    Provider(
        id="deepseek",
        name="DeepSeek",
        api_style=ApiStyle.OPENAI,
        base_url="https://api.deepseek.com/v1",
        key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        docs_url="https://platform.deepseek.com/api_keys",
    ),
    Provider(
        id="kimi",
        name="Kimi (Moonshot AI)",
        api_style=ApiStyle.OPENAI,
        base_url="https://api.moonshot.ai/v1",
        key_env="MOONSHOT_API_KEY",
        docs_url="https://platform.moonshot.ai/console/api-keys",
    ),
    Provider(
        id="gemini",
        name="Google Gemini",
        api_style=ApiStyle.OPENAI,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        key_env="GEMINI_API_KEY",
        docs_url="https://aistudio.google.com/apikey",
    ),
    Provider(
        id="ollama",
        name="Ollama (local)",
        api_style=ApiStyle.OPENAI,
        base_url="http://localhost:11434/v1",
        requires_key=False,
        local=True,
        docs_url="https://ollama.com/download",
    ),
    Provider(
        id="lmstudio",
        name="LM Studio (local)",
        api_style=ApiStyle.OPENAI,
        base_url="http://localhost:1234/v1",
        requires_key=False,
        local=True,
        docs_url="https://lmstudio.ai",
    ),
]
for _p in PRESETS:
    _p.preset = True


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    default_model: str | None = None


class _Stored(BaseModel):
    custom: list[Provider] = Field(default_factory=list)
    overrides: dict[str, ProviderUpdate] = Field(default_factory=dict)


class ProviderRegistry:
    """Presets plus user-added providers, persisted to providers.json in the data dir."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "providers.json"
        self._stored = self._load()

    def _load(self) -> _Stored:
        if self.path.exists():
            return _Stored.model_validate(json.loads(self.path.read_text(encoding="utf-8")))
        return _Stored()

    def _save(self) -> None:
        self.path.write_text(self._stored.model_dump_json(indent=2), encoding="utf-8")

    def list(self) -> list[Provider]:
        result = []
        for preset in PRESETS:
            override = self._stored.overrides.get(preset.id)
            p = preset.model_copy()
            if override:
                p = p.model_copy(update=override.model_dump(exclude_none=True))
            result.append(p)
        return result + [p.model_copy() for p in self._stored.custom]

    def get(self, provider_id: str) -> Provider:
        for p in self.list():
            if p.id == provider_id:
                return p
        raise KeyError(provider_id)

    def add(self, provider: Provider) -> Provider:
        if any(p.id == provider.id for p in self.list()):
            raise ValueError(f"provider '{provider.id}' already exists")
        provider = provider.model_copy(update={"preset": False})
        self._stored.custom.append(provider)
        self._save()
        return provider

    def update(self, provider_id: str, changes: ProviderUpdate) -> Provider:
        current = self.get(provider_id)
        if current.preset:
            merged = self._stored.overrides.get(provider_id, ProviderUpdate())
            merged = merged.model_copy(update=changes.model_dump(exclude_none=True))
            self._stored.overrides[provider_id] = merged
        else:
            self._stored.custom = [
                p.model_copy(update=changes.model_dump(exclude_none=True))
                if p.id == provider_id
                else p
                for p in self._stored.custom
            ]
        self._save()
        return self.get(provider_id)

    def remove(self, provider_id: str) -> None:
        if self.get(provider_id).preset:
            raise ValueError("preset providers cannot be removed")
        self._stored.custom = [p for p in self._stored.custom if p.id != provider_id]
        delete_secret(f"provider:{provider_id}:api_key")
        self._save()

    def set_key(self, provider_id: str, key: str) -> None:
        set_secret(self.get(provider_id).secret_name, key)

    def delete_key(self, provider_id: str) -> None:
        delete_secret(self.get(provider_id).secret_name)
