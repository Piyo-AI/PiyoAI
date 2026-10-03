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
npm run tauri:dev      # full Tauri shell (needs Rust + platform deps); it starts the core itself
```

`python scripts/check.py` (repo root) runs everything CI runs: ruff, pytest, typecheck, build. CI is
`.github/workflows/ci.yml` (Windows, macOS, Linux).

Lint: `uv run ruff check piyo tests` (line length 110, rules E F I B UP). Check `CLAUDE.local.md` if it fails to run.
Ruff 0.16.10 crashes on Windows (access violation); `pyproject.toml` excludes it. Do not remove that exclusion
until a newer release is confirmed to run.

## Layout (`core/piyo/`)

| Package | Responsibility |
|---|---|
| `config/` | Per-user data dirs (`PIYO_DATA_DIR` overrides), keychain secrets, approved folders, per-model output limits (`model_limits.py`) |
| `models/` | Providers as data (`providers.py`); model clients, model lists (`clients.py`); one tool-calling turn normalised across both wire formats (`turn.py`); tools as text for models without tool calling (`prompt_tools.py`) |
| `agent/` | The loop (`loop.py`): model turn, tool calls, results, repeat; system prompt (`prompts.py`) |
| `tools/` | `Tool`, `Risk`, `ToolRegistry`, `RunContext`; core tools `load_skill`, `current_time`; `google.py` (`ACCOUNT_PROP`, `who`, `google.accounts`), `gmail.py` (Gmail tools), `calendar.py` (Calendar tools), `weather.py` (Open-Meteo forecast, no key) |
| `safety/` | `PermissionGate`: every tool call is authorised here before it runs; `untrusted.py` fences outside text |
| `skills/` | `SKILL.md` parsing/validation (`manifest.py`) and discovery (`registry.py`) |
| `store/` | `ConversationStore`: conversations, messages (tool calls included) and loaded skills in SQLite; `runs.py`: the task log (`RunLog`, `RunStore`, `redact`) |
| `integrations/` | `google/oauth.py`: `GoogleAuth` (BYO OAuth client, loopback + PKCE sign-in, token refresh, revoke; several accounts: `accounts()`, `resolve(account)`, per-account grants and tokens); tokens only in the keychain, one entry per account (`account_secret`). `google/client.py`: `GoogleClient` (authorised requests, 401 refresh-and-retry, `error_for` maps failures to `GoogleError(kind, message)`, `require` checks the granted scope before a tool calls out) |
| `server/` | FastAPI + WebSocket API for the app (`app.py`), launcher (`__main__.py`; exits when stdin closes if `PIYO_EXIT_ON_STDIN_EOF` is set) |

Tauri shell (`apps/desktop/src-tauri/src/core.rs`): runs the core as a child (`uv run piyo-core` in dev; release builds
report that packaging is not done), exposes `core_status` / `restart_core` and the `core-exited` event;
`api.ts` `resolveConnection` polls it. Debug builds only.

Desktop (`apps/desktop/src/`): `App.tsx` (chat shell), `useChat.ts` (chat state + socket events), `api.ts`
(HTTP/WS client), `Tools.tsx` (tool chips, approval card), `Settings.tsx` (settings window: left sidebar with Providers > Provider list / Add provider, Web search, Folders, Skills, Limits), `GoogleConnection.tsx` (client ID, access checkboxes, Connect/Disconnect), `SetupWizard.tsx` + `Markdown.tsx` (a skill's `SETUP.md` as a step-by-step wizard; `useSetupNeeded.ts` drives the chat banner).
`useModelWarnings.ts` (pre-send banner: unusable skills, local-only), `ModelSettings.tsx` (the "Model settings" popover: per-model max reply, context size and tool mode), `Tasks.tsx` (task log; its Approvals tab is `Audit.tsx`).
Built-in skills go in `skills/<name>/SKILL.md` at the repo root.

## How the pieces fit

- The app talks to the core over `127.0.0.1` only. HTTP uses `Authorization: Bearer <token>`; the WebSocket
  `/ws/chat?token=...` (browsers cannot set headers on WebSockets). The token is per launch.
- **WebSocket protocol.** Client to core: `chat {provider, model, message, conversation_id?}`, `approval {id, approve}`,
  `cancel`. Core to client: `conversation {id}` (when a new one was created), `delta`, `tool_start`, `tool_end`, `approval_request`, `done {reason}`, `error`.
  The core keeps reading while a run is in progress (that is how approve/cancel arrive). `cancel` is always
  answered with `done {reason: "cancelled"}`. Change both ends and `useChat.ts` together.
- **The core owns the history** (`store/`, SQLite `piyo.db` in the data dir): the app sends only the new message
  and a conversation id. Tool calls/results and loaded skills are saved with the conversation. A run is saved
  before `done` is sent, and an interrupted run is repaired (`complete_tool_calls`) so no tool call is left
  unanswered. Before each model turn `agent/context.py` (`fit_context`) sends a trimmed copy that fits the window
  (old tool output first, then whole old turns, never the current turn); the stored history is never altered.
  Window (`ContextLimits`, `/api/context-limit`, "Context" field in the app) = the user's setting, else the size
  the provider reported (cached from the model list), else 32k. Not yet: summarising dropped turns.
- **Models without tool calling** (`models/prompt_tools.py`): `prompt_turn(base, ...)` wraps a text-only turn function
  and gives it tools as `<tool_call>{json}</tool_call>` text (tolerant parser, retries, markup hidden from the stream,
  results fenced as data). The server picks per run (`turn_for` in `app.py`): the user's `ToolModes` setting
  (`/api/tool-mode`), else the provider's report, else native with `native_with_fallback`, which switches to text when
  the provider says the model has no tools. The agent loop is unaware, so the gate and checks apply unchanged.
- **Task log** (`store/runs.py`): the server gives each run a `RunLog`; the agent adds `model` and `tool` steps,
  the approval hook adds `approval` steps, and the run is saved once when it ends (done, cancelled or error).
  Everything goes through `redact` first. Read via `GET /api/runs` and `/api/runs/{id}`; the app shows it in
  `Tasks.tsx`. Tokens are provider-reported or estimated (`tokens_estimated`).
- **Approvals and audit.** `PermissionGate` builds an `ApprovalRequest` with `summary` (from `Tool.summarize`, the
  tool's own code, never model text) and `why` (the model's turn text). The server's approver sends both to the app,
  adds them to the task log, and records the decision in `store/audit.py` (hash chain; `GET /api/audit`). The agent
  refuses an identical call after 3 failures and never re-asks a declined one in the same run (`MAX_IDENTICAL_FAILURES`).
- **Outside text is fenced** with `wrap_untrusted` (web, search, `files.read`, `files.list`). Any new tool that returns
  text from outside must do the same; `tests/test_injection.py` holds the corpus. `shorten` keeps the fence closed.
- **Skill on/off** is stored in `skills_disabled.json` (`config/skill_state.py`) and applied to `SkillRegistry.disabled`
  at startup and on `PUT /api/skills/{name}`. A run logs a `skill` step when it loads one.
- **Skills use progressive loading.** The prompt carries each enabled skill's name and description only; the
  model calls `load_skill` for the full body. Loading a skill unlocks exactly the tools in its
  `requires.tools`. Everything else is hidden from the model and rejected if called.
- **Output limit** (`max_tokens` per model turn) is per model: the user's setting (`/api/output-limit`), else the
  maximum the provider reported (only OpenRouter does; cached from the model list the app loads, capped at
  16384), else 4096, which every provider accepts. A custom value is clamped to a known maximum.
- **Capabilities and budget.** `models/capabilities.py` compares a skill's `requires.model` (vision, min context)
  with `ModelCaps` (the server builds it per run: user/provider context window, provider-reported vision). Only a
  known mismatch blocks (vision: the user's per-model setting, `/api/vision`, beats the provider's report); the catalog marks the skill and `load_skill` refuses. `config/run_settings.py` holds the
  per-run budget (`max_steps`, `max_tokens`, `timeout_s`) and `local_only` (`/api/run-settings`); the agent ends a
  run with `token_limit` / `timeout` between model turns. Approval wait time does not count toward the timeout.
- **Cost** (`config/model_prices.py`): `RunLog.price` (USD per million tokens, input/output) is resolved per run:
  the user's setting (`/api/price`), else the provider-reported price (OpenRouter), local providers are free, else
  None. `RunLog.cost_usd` is saved in `runs.cost_usd` (schema v3, NULL = unknown).
- **Model capabilities in one place:** `GET /api/model-capabilities?provider&model` composes tool mode, vision, context and
  output limits, each with what the provider reported and what the user set. Reports are cached in memory when the app loads
  a model list. `tests/test_ollama_smoke.py` runs only with `PIYO_OLLAMA_MODEL` set (weekly CI job `ollama-smoke.yml`).
- **Paging.** `GET /api/conversations`, `/api/runs` and `/api/audit` take `limit` and `offset` (newest first) and return
  plain lists. The app's `usePaged` hook asks for one extra item to learn if there is a next page, so there is no total
  count. The audit check (`verified`) always covers the whole chain, whatever page is asked for.
- **Folder picker:** Settings > Folders uses Tauri's dialog plugin (`pickFolder` in `api.ts`, capability `dialog:allow-open`);
  in a plain browser only the typed path is offered.
- **Integrations.** A skill's `requires.integrations: [google]` is checked in `load_skill` through `RunContext.integration_issue`
  (the server's `integration_issue`): an unmet one refuses the load with a message for the user, and `/api/skills` lists it in
  `integration_issues`. Google API: `/api/integrations/google` (status, `PUT client`, `POST connect` returns the URL the app opens,
  `POST cancel`, `DELETE`). The sign-in runs as a background task in the server's event loop, so it needs one loop (tests use `with client:`).
- **Google accounts.** `GoogleAuth.resolve(account)` is the one place an account is chosen: no name works only while exactly one is
  connected, several need an exact (case-insensitive) match, anything else raises `GoogleError` (`account_needed` / `bad_account`).
  Every Google tool calls `GoogleClient.require(..., account=args.get("account"))` first and passes the returned id to every request, and its
  approval card uses `who(auth, args)`. A new Google tool must do both and take `ACCOUNT_PROP`. `tests/test_google_accounts.py` has the
  parametrised "refuses to guess" test: add the tool to its list.
- **Setup guides.** A skill's `SETUP.md` is served by `GET /api/skills/{name}/setup` and rendered by `Markdown.tsx` (headings, lists,
  task lists, code, bold, http(s) links; React elements, never HTML). `##` headings are the wizard's steps. A guide may embed only the
  widgets in `SetupWizard.tsx` (`:::google:::`, `:::google-test:::`, `:::open-setup <skill>:::`); `tests/test_google_setup.py` checks every
  built-in guide uses only those. Add a widget there and in that test's list together.
- **Links.** The Tauri webview does not follow links out of the app. `openLinksExternally()` (called in `main.tsx`; it listens in the capture phase because the dialogs call `stopPropagation`, which hides clicks from a bubbling `document` listener) sends http(s) clicks to the
  system browser through `tauri-plugin-opener` (capabilities `opener:allow-open-url` and `opener:allow-default-urls`; the first has no URL scope on its own, so without the second every open is denied); use `openExternal` for buttons. Adding the plugin needs a
  Rust rebuild: restart `tauri:dev`.
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
approval UI, stop/cancel, skill loading and per-skill tool grants, example skills `plan-my-day` and `downloads-organizer`. Gmail (`gmail.*` tools, skill `gmail-triage`; read/draft auto, send/label/archive confirm; mail is fenced as untrusted; needs the user's own Google client, see Settings > Skills). Calendar (`calendar.*` tools, skill `calendar`; agenda/free-busy auto, create/update/delete confirm; bare times are read in the calendar's zone). Weather (`weather.forecast`) and the read-only skill `morning-brief` (calendar + unread mail + weather; the user's city is asked each time until Phase 4 memory). The setup wizard is next. `tzdata` is a dependency (Windows has no zone database); a packaged core must include it.
File tools (`files.*`, approved folders only; API `/api/folders`, Settings section to manage them, with a per-folder "no prompts for changes" switch). `web.fetch` (public hosts only, output fenced as untrusted; skill `web-reader`). `web.search` (Brave; key in keychain, `/api/search`; skill `web-research`). Sidecar launch in dev, CI and contributor docs are in place. Not yet: packaged core for release builds,
integrations. See `../docs/README.md` for the ordered plan.
