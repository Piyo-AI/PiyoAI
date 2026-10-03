import pytest

from piyo.models import ApiStyle, Provider, ProviderRegistry, ProviderUpdate

EXPECTED_PRESETS = {"anthropic", "openai", "openrouter", "deepseek", "kimi", "gemini",
                    "ollama", "lmstudio"}


def test_presets_available():
    ids = {p.id for p in ProviderRegistry().list()}
    assert EXPECTED_PRESETS <= ids


def test_key_from_keyring_and_env(monkeypatch):
    reg = ProviderRegistry()
    assert not reg.get("openai").key_configured()
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    assert reg.get("openai").api_key() == "env-key"
    reg.set_key("openai", "keychain-key")
    assert reg.get("openai").api_key() == "keychain-key"
    reg.delete_key("openai")
    assert reg.get("openai").api_key() == "env-key"


def test_local_providers_need_no_key():
    assert ProviderRegistry().get("ollama").key_configured()


def test_add_update_remove_custom_provider_persists():
    reg = ProviderRegistry()
    reg.add(Provider(id="my-vllm", name="My vLLM", api_style=ApiStyle.OPENAI,
                     base_url="http://gpu-box:8000/v1", requires_key=False))
    reg.update("my-vllm", ProviderUpdate(default_model="qwen"))

    reloaded = ProviderRegistry()
    assert reloaded.get("my-vllm").default_model == "qwen"
    assert reloaded.get("my-vllm").preset is False

    reloaded.remove("my-vllm")
    with pytest.raises(KeyError):
        ProviderRegistry().get("my-vllm")


def test_preset_overrides_and_protection():
    reg = ProviderRegistry()
    reg.update("ollama", ProviderUpdate(base_url="http://other:11434/v1"))
    assert ProviderRegistry().get("ollama").base_url == "http://other:11434/v1"
    with pytest.raises(ValueError):
        reg.remove("anthropic")
    with pytest.raises(ValueError):
        reg.add(Provider(id="openai", name="dup", api_style=ApiStyle.OPENAI, base_url="x"))


def test_invalid_id_rejected():
    with pytest.raises(ValueError):
        Provider(id="Bad Id!", name="x", api_style=ApiStyle.OPENAI, base_url="x")
