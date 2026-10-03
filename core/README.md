# Piyo Core

The Python process behind the Piyo AI desktop app: model providers, agent loop, skills, and a local API
(`127.0.0.1` only, token-protected) that the Tauri app talks to.

## Develop

```bash
cd core
uv sync
uv run pytest
uv run piyo-core          # starts the local API, prints {"port": ..., "token": ...} on the first line
```

API keys: the app stores them in the OS keychain. For development you can also put them in a `.env`
file at the repo root (see `.env.example`).
