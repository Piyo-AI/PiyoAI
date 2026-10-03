# PiyoAI (app repo)

Personal AI assistant: Python **core** (agent, models, skills, local API) + **Tauri 2 / React** desktop app.
Design and decisions: `PLAN.md`. Implementation order: `../docs/` in the workspace folder (not in this repo).
Sibling repo `../piyo-skills` is the skill catalog.

## Commands

Core (from `core/`, uses `uv`, Python 3.12):

```bash
uv sync
uv run pytest -q                                          # all tests; no API keys or network needed
PIYO_PORT=8765 PIYO_TOKEN=dev-token uv run piyo-core      # dev server; prints {"port","token"} first
```

Desktop app (from `apps/desktop/`, npm):

```bash
npm run dev            # Vite dev server on :1420, talks to the core on 127.0.0.1:8765 with token "dev-token"
npm run typecheck      # tsc --noEmit
npm run build          # typecheck + vite build
npm run tauri:dev      # full Tauri shell (needs Rust + platform deps)
```

Lint: `uv run ruff check piyo tests` (line length 100, rules E F I B UP). Check `CLAUDE.local.md` if it fails to run.
Ruff 0.16.10 crashes on Windows (access violation); `pyproject.toml` excludes it. Do not remove that exclusion
until a newer release is confirmed to run.

## Layout (`core/piyo/`)

| Package | Responsibility |
|---|---|
| `config/` | Per-user data dirs (`PIYO_DATA_DIR` overrides), keychain secrets |
| `models/` | Providers as data (`providers.py`); model clients, model lists (`clients.py`); one tool-calling turn normalised across both wire formats (`turn.py`) |
| `agent/` | The loop (`loop.py`): model turn, tool calls, results, repeat; system prompt (`prompts.py`) |
| `tools/` | `Tool`, `Risk`, `ToolRegistry`, `RunContext`; core tools `load_skill`, `current_time` |
| `safety/` | `PermissionGate`: every tool call is authorised here before it runs; `untrusted.py` fences outside text |
| `skills/` | `SKILL.md` parsing/validation (`manifest.py`) and discovery (`registry.py`) |
| `server/` | FastAPI + WebSocket API for the app (`app.py`), launcher (`__main__.py`) |

Desktop (`apps/desktop/src/`): `App.tsx` (chat shell), `useChat.ts` (chat state + socket events), `api.ts`
(HTTP/WS client), `Tools.tsx` (tool chips, approval card), `Settings.tsx` (providers and keys).
Built-in skills go in `skills/<name>/SKILL.md` at the repo root.

## How the pieces fit

- The app talks to the core over `127.0.0.1` only. HTTP uses `Authorization: Bearer <token>`; the WebSocket
  `/ws/chat?token=...` (browsers cannot set headers on WebSockets). The token is per launch.
- **WebSocket protocol.** Client to core: `chat {provider, model, messages}`, `approval {id, approve}`,
  `cancel`. Core to client: `delta`, `tool_start`, `tool_end`, `approval_request`, `done {reason}`, `error`.
  The core keeps reading while a run is in progress (that is how approve/cancel arrive). `cancel` is always
  answered with `done {reason: "cancelled"}`. Change both ends and `useChat.ts` together.
- The app sends only plain text history each turn. Tool results and loaded skills are **not** yet remembered
  between messages (a conversation store is planned, see docs Phase 1).
- **Skills use progressive loading.** The prompt carries each enabled skill's name and description only; the
  model calls `load_skill` for the full body. Loading a skill unlocks exactly the tools in its
  `requires.tools`. Everything else is hidden from the model and rejected if called.
- Tool names use dots internally (`gmail.read`); the wire name is `gmail__read` (providers reject dots).

## Invariants (do not break; add a test when touching them)

1. Risk (`auto` / `confirm` / `never`) is a property of the **tool**, never of a skill. Skills cannot lower it.
   A tool's own code may relax it per call (`Tool.risk_for`, used by `files.*` for folders the user marked
   `auto_changes`); delete and overwrite never relax.
2. Every tool call goes through `Agent._execute`: granted-tools check, argument check, gate, then handler.
   New code paths that run tools must reuse it.
3. Tool failures become error results for the model; they never crash a run.
4. Built-in skills win name clashes with user skills; one broken skill never blocks loading the others.
5. API keys come from `keyring` (env fallback in dev). They are never returned by the API or logged.
6. Providers are data. Adding a provider must not need code changes unless it speaks a new wire format.

## Conventions

- Python: type hints, `from __future__ import annotations`, pydantic for data crossing a boundary. Match the
  surrounding comment density (short "why" comments only).
- Tests: `pytest` with `asyncio_mode=auto`. The autouse fixture in `tests/conftest.py` isolates the keychain, the
  data dir and API-key env vars. Test the agent with a fake `turn_fn` (see `tests/test_agent.py`), never a real
  provider. Server tests inject `turn_fn` and `skills` into `create_app`.
- TypeScript: strict; no new dependencies without a reason. Keep UI state logic in hooks, not components.
- Errors shown to users must be actionable (see `describe_error`).
- LF line endings. MIT licence; new files need no header.

## Current state

Working: chat with streaming, providers/keys/model lists, agent loop with tool calling, permission gate with
approval UI, stop/cancel, skill loading and per-skill tool grants, example skills `plan-my-day` and `downloads-organizer`.
File tools (`files.*`, approved folders only; API `/api/folders`, Settings section to manage them, with a per-folder "no prompts for changes" switch). `web.fetch` (public hosts only, output fenced as untrusted; skill `web-reader`). Not yet: web search, conversation store, fallback for models without tool calling, sidecar launch in
production builds, CI, task log, integrations. See `../docs/README.md` for the ordered plan.
