import keyring
import pytest
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the real keychain, data dir, or developer API keys in tests."""
    monkeypatch.setenv("PIYO_DATA_DIR", str(tmp_path))
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
                "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(previous)
