# Piyo AI — Project Plan

> Living document. Last updated: 2026-10-03. Add new ideas under **Backlog** and decisions under **Decision Log**.

## 1. Vision

Piyo AI is a personal assistant that handles an individual's daily tasks. It can:

- Hold a conversation and remember the user's preferences, people, and routines.
- **Use the computer** (mouse, keyboard, screen) and **browse the internet** to get things done.
- **Learn skills**: turn a task it has done once into a reusable, named procedure it can run again (and improve over time).
- **Be extended by its users**: anyone can add skills — install pre-built ones (from a file, a Git URL, or later a skill catalog) or build their own (in-app editor, or by letting Piyo learn from a task). Skills are the main extension point of the whole product.
- Run tasks **on a schedule** or in response to triggers (e.g. "every morning, summarize my inbox").
- Work with **any model**: local models or any OpenAI- / Anthropic-compatible API.
- Run on **Windows, macOS, and Linux** as a desktop app.
- Always keep the user in control of anything risky (payments, sending messages, deleting data).

## 2. Example Daily Tasks (to drive design)

| Area | Example |
|---|---|
| Information | Morning brief: weather, calendar, news, unread email summary |
| Communication | Summarize Gmail / WhatsApp, draft replies, remind me to reply to X |
| Calendar | "Find a free slot Thursday and add dentist appointment" |
| Web errands | Compare prices, track a parcel, check bill status, fill a form |
| Files | Organize Downloads folder, rename/sort documents, extract data from PDFs |
| Planning | Plan a trip itinerary, build a weekly meal plan + grocery list |
| Reminders | "Remind me in 2 hours", recurring routines |

## 3. Core Concepts

- **Agent** — the LLM-driven loop that plans, calls tools, observes results, and repeats until the task is done.
- **Tool** — a primitive capability (read file, open URL, click, type, take screenshot, send email draft, search web).
- **Skill** — a reusable procedure built on tools; pre-built, user-written, or learned. Full design in §5.
- **Memory** — durable facts about the user (preferences, contacts, accounts in use, past outcomes).
- **Task** — a unit of work, user-requested or scheduled, with status, log, and outcome.
- **Model profile** — a configured model endpoint (local or cloud) plus its capabilities (tool calling, vision, context size).

## 4. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Desktop App (Tauri: Rust shell + React/TypeScript UI)        │
│  chat · task list · approvals · skills · settings · VM viewer │
└───────────────────────────┬──────────────────────────────────┘
                            │ localhost WebSocket / HTTP (auth token)
┌───────────────────────────▼──────────────────────────────────┐
│  Piyo Core (Python sidecar process)                           │
│                                                               │
│   Orchestrator / Agent loop  ──►  Model Router                │
│        │                          (openai + anthropic SDKs:  Ollama, LM Studio,│
│        │                           llama.cpp, OpenAI-compat,  │
│        │                           Anthropic-compat)          │
│        ├── Tools        ├── Skills library + learner          │
│        ├── Memory       ├── Scheduler + task log              │
│        └── Safety / permission gate (every tool call)         │
└───────┬──────────────┬───────────────┬───────────────────────┘
        ▼              ▼               ▼
  Integrations     Browser          Computer-use Sandbox (later)
  Gmail API        Playwright       Docker Linux desktop
  Calendar API     (isolated        (Xvfb + noVNC), optional
  WhatsApp*        profile)         full VM for Windows/mac apps
```

### Components

1. **Desktop app (Tauri)** — cross-platform, small binaries, native tray icon + notifications. Bundles the Python core as a sidecar (packaged with PyInstaller per OS). Shows chat, live task progress, approval prompts, skill library, and a live view of the sandbox desktop.
2. **Piyo Core (Python)** — all agent logic. Runs as a local server only bound to `127.0.0.1` with a per-session auth token so other local processes can't drive it.
3. **Model Router** — provider-agnostic layer (official `openai` + `anthropic` SDKs; two wire formats cover every provider) so any of these work through one interface:
   - Local: Ollama, LM Studio, llama.cpp server, vLLM.
   - Cloud: OpenAI-compatible and Anthropic-compatible endpoints (custom base URL + key).
   - **Routing by capability**: each task/skill declares what it needs (tool calling, vision, long context). The router picks the configured model that satisfies it, e.g. local model for summaries & chat, a strong cloud vision model for computer use. Users can force "local only" mode.
   - **Providers are data, not code**: a provider = `{id, display name, api style (openai | anthropic | gemini | ollama), base URL, key (keyring), model list}`. Users can add **any** provider from Settings; any OpenAI- or Anthropic-compatible endpoint works with no code change.
   - **Pre-added presets** (user only pastes an API key): **Anthropic, OpenAI, OpenRouter, DeepSeek, Kimi (Moonshot), Google Gemini**, plus local **Ollama** and **LM Studio**. Each preset lists its models with capability flags (tool calling, vision, context size); a "refresh models" button pulls the live model list where the provider supports it.
   - **Development**: we develop against these cloud providers (keys via `.env` locally, keyring in the app). The local path stays supported and is covered by a smoke test so it doesn't rot.
   - **Local models on 8 GB VRAM** (target spec): 7–9B instruct models with tool calling at 4-bit quantization (Qwen / Llama / Gemma class), ~8–16k context; small 3–4B models for fast classification/routing; a small local embedding model for memory. Good for chat, summaries, triage, drafting. Multi-step browser automation and vision tasks are recommended on a cloud model — the router warns when a skill's `requires.model` exceeds what the local model can do. Exact default model chosen by a benchmark in Phase 1.
4. **Agent loop** — our own thin loop (not tied to a single vendor SDK) because we must support many providers: plan → tool call → observe → reflect → done/ask user. Supports sub-tasks and a max-steps / budget limit.
5. **Tools**
   - *Browser*: Playwright with a dedicated Piyo browser profile (DOM/accessibility tree first, screenshots only when needed).
   - *Computer use*: screenshot + mouse/keyboard, via a `ScreenController` interface; browser-only at first, sandbox backends later (see §7).
   - *Web search / fetch*.
   - *File system*: limited to user-approved folders.
   - *Integrations*: Gmail, Calendar, WhatsApp (see §6) — packaged as built-in skills. More can be added later by connecting MCP servers, which Piyo uses as a client (see the Decision Log, 2026-10-04, and `docs/phase-7-mcp.md`).
6. **Skills library + learner** — after a successful task, Piyo proposes "Save this as a skill?", drafts `SKILL.md` from the task log, user approves/edits. Skills are versioned; failures and corrections trigger a refinement proposal.
7. **Memory** — SQLite + markdown files; add embeddings (local model, e.g. via Ollama) for retrieval once the store grows.
8. **Scheduler** — APScheduler for recurring tasks & reminders; desktop notifications on completion or when approval is needed.
9. **Safety / permission gate** — every tool call is classified:
   - *Auto*: read, search, summarize, draft.
   - *Confirm*: send email/WhatsApp, create/modify calendar events, submit forms, purchases, deletes, installs.
   - *Never automated*: entering passwords, card numbers, or IDs; solving CAPTCHAs → handed to user.
   - Content from web pages, emails, and chats is **data, never instructions** (prompt-injection defense). Extra caution because Gmail/WhatsApp are untrusted input channels.
   - Secrets (API keys, OAuth tokens) in the OS keychain via `keyring` (Windows Credential Manager / macOS Keychain / Secret Service).
10. **Task log** — steps, tool calls, screenshots, outcome; viewable in the app; source material for skill learning.

## 5. Skill System

Skills are how Piyo grows. Three sources, one format:

| Source | How it gets in |
|---|---|
| **Built-in** | Shipped with the app (e.g. Morning brief, Gmail triage, WhatsApp summary) |
| **Pre-built / third-party** | Installed by the user from a `.piyoskill` (zip) file, a Git URL, or later a skill catalog |
| **Self-built** | Written in the in-app skill editor, or **learned**: after a successful task Piyo proposes "Save this as a skill?" and drafts it from the task log; user reviews and approves |

### Skill package format
```
<skill-name>/
  SKILL.md          # frontmatter + instructions for the agent (required)
  SETUP.md          # human setup guide shown in the app (accounts, API keys, OAuth steps)
  scripts/          # optional deterministic helpers (Python or JS/TS)
  assets/           # optional templates, examples
```

`SKILL.md` frontmatter (draft):
```yaml
name: gmail-triage
version: 1.0.0
description: Summarize unread Gmail and draft replies. Use when the user asks about email.
author: ...
requires:
  tools: [gmail.read, gmail.draft, gmail.send]   # capability permissions
  integrations: [google]                          # triggers SETUP flow if not connected
  secrets: []                                     # named secrets stored in keyring
  model: { tool_calling: true, vision: false, min_context: 32000 }
runtime:                                          # only if the skill has scripts
  python: { version: ">=3.11", dependencies: ["requests>=2.32"] }
  # or
  deno:   { npm: ["date-fns@3"], allow_net: ["api.example.com"] }
license: MIT                                      # required for catalog listing
risk: confirm-on-send                             # informational; the gate still enforces
```

### Skill scripts: Python and JS/TS
- **Python** scripts run with a bundled `uv`; each skill gets its own isolated virtual environment built from `runtime.python.dependencies` (no global installs, no conflicts between skills).
- **JS/TS** scripts run on a bundled **Deno** runtime: runs TypeScript directly, supports `npm:` packages, and has a built-in permission sandbox (`--allow-net=<hosts>`, `--allow-read=<skill dir>`) that maps directly onto the skill's declared permissions.
- Both are called the same way by the agent: `run_skill_script(skill, script, args)` → JSON in / JSON out over stdin/stdout, with timeout and output-size limits.
- Python has no equivalent built-in sandbox, so Python skill scripts run with OS-level restrictions (separate low-privilege process, restricted working dir, network blocked unless declared, via platform mechanisms) — and the Docker sandbox becomes an option for them in Phase 6.
- Format stays compatible with the open Agent Skills `SKILL.md` convention so existing skills can be adapted easily.
- **Progressive loading**: the agent only sees names + descriptions; full instructions load when relevant (scales to hundreds of skills).

### Lifecycle (Skills page in the app)
Install → review permissions & setup guide → configure (connect accounts, enter keys) → enable → run / schedule → update (shows permission diff) → disable / uninstall. Every skill is versioned; learned skills keep their edit history and can be rolled back.

### Trust & safety for user-added skills
Third-party skills are untrusted code + untrusted instructions, so:
- **Declared permissions only**: a skill can only call the tools it lists in `requires.tools`; anything else is blocked. User sees and approves the list at install time and on updates that add permissions.
- **Skill instructions cannot override the permission gate** — sending, purchasing, deleting, etc. still require confirmation regardless of what `SKILL.md` says.
- **Scripts run sandboxed** (Deno permissions for JS/TS, restricted process for Python — see above): no network unless declared, file access limited to the skill's folder + `PiyoExchange/`, timeout.
- Source shown on install (catalog / local file / Git URL + commit hash); anything not from the signed catalog gets an "unverified" warning.
- Secrets are injected at run time from keyring, never written into skill files or prompts.

### Skill Catalog (in the first public release)
- **Index repo**: a public GitHub repo `Piyo-AI/piyo-skills` holds a catalog index (`index.json`) pointing to skill packages (in-repo or external Git repos pinned to a commit/tag + content hash).
- **Catalog index** (`index.json`, generated by `piyo-skills/scripts/build_index.py`, never edited by hand; CI runs it with `--check`). Shape: `{"schema": 1, "skills": [entry, ...]}`, sorted by name. An entry: `name`, `version`, `description`, `author`, `license`, `path` (`skills/<name>`, the package folder in the repo), `sha256` (the package hash), `files`, `size` (bytes), `permissions` (the same `tool:` / `integration:` / `secret:` / `script:` / `runtime:` labels the install review shows), `integrations`, `secrets`, `model` (what the skill needs from the model). The **package hash** is `piyo.skills.install.package_hash`: sha256 over the sorted lines `<posix path> <file sha256>
` for every file in the folder (install metadata excluded), so it is the same on every OS (the repo is LF-only). The app recomputes it on what it downloaded and refuses a mismatch. Planned additions: `revoked`, `category`, signature of the whole index (Phase 5).
- **Submissions via pull request**; CI validates every submission: manifest schema, `SETUP.md` present, license declared (OSI-approved), permission list sane, static checks on scripts, size limits. Maintainer review before merge.
- **Signing**: catalog releases are signed (e.g. minisign/Sigstore); the app verifies the signature and each package hash before install.
- **Badges**: *Official* (Piyo team), *Verified publisher*, *Community*. Permissions are shown prominently on every listing.
- **In-app browsing**: search, categories, install count, required integrations, setup difficulty; one-click install → permission review → `SETUP.md` wizard.
- **Updates**: app checks the catalog for new versions; updates that add permissions require re-approval.
- **Takedown**: catalog can flag a version as revoked; the app disables revoked skills and notifies the user.
- Installing from a local file / arbitrary Git URL remains possible (with an "unverified" warning) so users can still self-host or share privately.

### Setup instructions per skill (for public release)
Each skill ships a `SETUP.md`, rendered in-app as a step-by-step wizard. For Google skills this lets users **bring their own Google Cloud OAuth client** if Piyo's shared client isn't verified yet — avoiding the restricted-scope verification blocker for early releases.

## 6. Integrations (first wave)

| Integration | Approach | Notes / risks |
|---|---|---|
| **Gmail** | Official Gmail API, OAuth 2.0 (desktop/loopback flow) | Read + draft by default; sending requires confirmation. Personal use works in Google Cloud "testing" mode; distributing publicly needs Google verification (Gmail scopes are *restricted* → security assessment). |
| **Google Calendar** | Official Calendar API, same OAuth client | Read free/busy, create/update events with confirmation. |
| **WhatsApp** | **Approach open** (2026-10-04: the WhatsApp Web automation was built, then withdrawn; see the Decision Log) | — |

**WhatsApp implementation notes (from the withdrawn WhatsApp Web approach; kept as background for the next attempt):**
- Read/summarize chats and draft replies automatically; **every send requires confirmation**.
- Conservative rate limits and human-like pacing; no bulk messaging, no contacting non-contacts.
- Automating WhatsApp Web is against WhatsApp's ToS and could lead to a ban — the skill's `SETUP.md` must state this clearly and the user opts in.
- WhatsApp Web's DOM changes often → keep selectors in one module, prefer accessibility roles/labels, add a vision-based fallback, and a quick health check that tells the user when the integration breaks.
- Packaged as a built-in skill (`whatsapp`) so it follows the same permission model as third-party skills.

## 7. Computer-Use Sandbox

Goal: Piyo controlling a screen must not be able to damage the user's real machine or accounts.

| Tier | What | Used for |
|---|---|---|
| 1. Browser only | Playwright, isolated profile on host | Most web tasks (default; fast, cheap, low risk) |
| 2. Sandbox desktop | **Docker container** with a Linux desktop (Xvfb + lightweight WM + noVNC), Chromium, LibreOffice, etc. | General computer use; works on Windows/macOS/Linux via Docker Desktop; disposable & resettable snapshot |
| 3. Full VM (optional) | VirtualBox / QEMU / Hyper-V / UTM with Windows or macOS guest | Only when a task needs a Windows/mac-only app |
| 4. Real desktop (opt-in) | User's own screen | Off by default; every action needs confirmation; visible "Piyo is controlling" overlay + kill switch hotkey |

- Shared folder between host and sandbox is limited to a single `PiyoExchange/` directory.
- Sandbox has no access to the user's host credentials; logins inside it are done by the user via the live noVNC view in the app.
- **Decided: start with Tier 1 only** — Playwright-controlled Chromium using its own dedicated Piyo profile (separate from the user's personal browser; cookies/logins for Gmail web, WhatsApp Web, etc. live only there). The app can show the Piyo browser window so the user can log in and watch. Tiers 2–4 are deferred to later phases.
- Design the computer-use tool behind an interface (`ScreenController`) so the sandbox backends can be added later without changing skills.

## 8. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Desktop shell | **Tauri 2** | Cross-platform, small, native tray/notifications, sidecar support |
| UI | React + TypeScript + Vite (+ Tailwind / shadcn) | Fast iteration, rich components |
| Core | **Python 3.12**, FastAPI + WebSocket | Best ecosystem for agents, Playwright, Google APIs |
| Model layer | **`openai` + `anthropic` SDKs** + provider presets | One interface for all providers (Anthropic, OpenAI, OpenRouter, DeepSeek, Kimi, Gemini, Ollama, LM Studio, any compatible endpoint) |
| Skill script runtimes | Bundled **uv** (Python) + bundled **Deno** (JS/TS) | Per-skill isolated envs; Deno's permission sandbox |
| Skill catalog | GitHub repo index + CI validation + signed releases | Free hosting, PR-based review, transparent |
| Browser | Playwright | Reliable, cross-platform |
| Sandbox | Docker (Linux desktop image) + noVNC | Cross-platform, disposable |
| Storage | SQLite (+ sqlite-vec later) + files | Local, private, zero setup |
| Scheduler | APScheduler | Simple, in-process |
| Secrets | `keyring` | OS-native secure storage on all 3 platforms |
| Packaging | PyInstaller (core) + Tauri bundler; GitHub Actions matrix (win/mac/linux) | One installer per OS |
| License | **MIT** | Open source; permissive for contributors and skill authors |

## 9. Repository Layout

### Local workspace
Two separate git repos side by side in one workspace folder:
```
D:\Projects\PiyoAI\          # workspace folder (not a repo)
  PiyoAI\                     # app repo → github.com/Piyo-AI/PiyoAI (everything below)
  piyo-skills\                # catalog repo → github.com/Piyo-AI/piyo-skills
```

### App repo (`PiyoAI/`)

```
PiyoAI/
  PLAN.md
  README.md
  apps/
    desktop/            # Tauri app (src-tauri/ + React UI in src/)
  core/                 # Python package "piyo"
    pyproject.toml
    piyo/
      server/           # FastAPI + WebSocket API for the desktop app
      agent/            # loop, prompts, planning
      models/           # router, model profiles, capability detection
      tools/            # browser, files, web, (later) screen controller
      integrations/     # google (oauth, gmail, calendar), whatsapp_web
      skills/           # package format, loader, registry, installer, permissions, learner
      memory/
      scheduler/
      safety/           # permission gate, policies, injection guards
    tests/
  skills/               # built-in skills (morning-brief, gmail-triage, whatsapp, ...)
  docs/                 # skill authoring guide, SETUP guides
  data/                 # local db, logs (git-ignored)
  .github/workflows/    # cross-platform CI/builds
```

User-installed and learned skills live in the per-user app data directory (e.g. `%APPDATA%/PiyoAI/skills`, `~/Library/Application Support/PiyoAI/skills`, `~/.local/share/PiyoAI/skills`), never inside the app bundle. The Piyo browser profile lives there too.

## 10. Roadmap

### Phase 0 — Foundations
- [ ] Monorepo setup (git, MIT `LICENSE`, `CONTRIBUTING.md`, Python env via `uv`, Node/npm, lint/test, CI matrix win/mac/linux)
- [ ] Piyo Core skeleton: FastAPI server, config, keyring secrets, per-OS app data dirs
- [ ] Model router + provider registry with presets (Anthropic, OpenAI, OpenRouter, DeepSeek, Kimi, Gemini, Ollama, LM Studio) and "add custom provider"; Settings page for keys/models
- [ ] Tauri app shell: chat window talking to core over WebSocket, streaming responses

### Phase 1 — Agent, safety, skill core
- [ ] Agent loop with tool calling (and fallback prompting for models without native tool calling)
- [ ] Permission gate + approval UI in the app
- [ ] **Skill system core**: `SKILL.md` format + validation, loader, registry, progressive loading, per-skill tool permissions
- [ ] File system tool, web search/fetch
- [ ] Task log + task view in the app
- [ ] Local model benchmark on 8 GB VRAM → pick default local chat + embedding models

### Phase 2 — Google skills
- [ ] OAuth flow (loopback) from desktop app; support bring-your-own OAuth client
- [ ] `gmail` skill: list/search/summarize, draft, send-with-confirmation
- [ ] `calendar` skill: agenda, free slots, create events with confirmation
- [ ] `morning-brief` skill (calendar + email + weather)
- [ ] `SETUP.md` rendering as an in-app setup wizard

### Phase 3 — Browser + WhatsApp
- [ ] Playwright browser tool with dedicated persistent Piyo profile; "show browser" for logins
- [ ] ~~`whatsapp` skill via WhatsApp Web~~ — dropped 2026-10-04; to be redone with a different approach (see Decision Log)

### Phase 4 — User-added skills, memory, scheduler
- [ ] Skills page: install from `.piyoskill` file / Git URL, permission review, enable/disable, update with permission diff, uninstall
- [ ] In-app skill editor + "test run"
- [ ] Skill learning from task logs ("Save this as a skill?")
- [ ] Script runners: uv per-skill venvs (Python), Deno with mapped permissions (JS/TS)
- [ ] User profile memory
- [ ] Recurring tasks, reminders, desktop notifications

### Phase 5 — Public release readiness
- [ ] Installers + auto-update for all 3 OSes, code signing (Windows Authenticode, Apple notarization)
- [ ] Google OAuth verification (or documented BYO-client path)
- [ ] Skill authoring guide + template repos (Python skill, TS skill, instructions-only skill)
- [ ] **Skill catalog**: `piyo-skills` index repo, submission CI, signing, in-app catalog browser, update/revocation checks
- [ ] Privacy policy; opt-in telemetry module + "View what is sent" page; crash reporting backend

### Phase 6 — Computer use beyond the browser (deferred)
- [ ] `ScreenController` backends: Docker Linux desktop + noVNC viewer, optional full VM, opt-in real desktop
- [ ] Voice input

## 11. Open Questions

_None blocking right now. Add new ones here._

### Platform support (decided)
| OS | Minimum | Reason |
|---|---|---|
| Windows | 10 (22H2) and 11, x64 (ARM64 later) | WebView2 available; Win 10 still widely used |
| macOS | 12 Monterey+, Apple Silicon + Intel | Covers ~all supported Macs; Tauri 2 & Playwright supported |
| Linux | Ubuntu 22.04+ / Debian 12+ / Fedora 39+, x64 | Tauri 2 needs webkit2gtk-4.1; AppImage + .deb + .rpm |

### Telemetry (decided)
- Anonymous usage stats and crash reports, **opt-in** (asked once at first run, changeable in Settings). Opt-in is the norm for open-source desktop apps and builds trust for an assistant that sees email/chats.
- Never collected: prompts, messages, email/chat content, file names, URLs visited, API keys, account identifiers. Only: app version, OS, feature/skill usage counts (catalog skill IDs only), error types, anonymized stack traces.
- Random install ID (not tied to any account); a "View what is sent" page in Settings; all telemetry code in one auditable module.
- Backend: self-hostable open-source tools (e.g. Sentry for crashes, PostHog/Plausible-style for usage) — pick in Phase 5.

## 12. Decision Log

| Date | Decision | Reason |
|---|---|---|
| 2026-10-03 | Project started; plan drafted | — |
| 2026-10-03 | Cross-platform from day one (Windows, macOS, Linux) | User requirement |
| 2026-10-03 | Desktop app is the primary interface; **Tauri** + Python core sidecar | Cross-platform, lightweight, Python ecosystem for agents |
| 2026-10-03 | Provider-agnostic models: local + OpenAI/Anthropic-compatible APIs → own thin router on the official openai + anthropic SDKs (replaced LiteLLM: fewer dependencies; presets are all OpenAI- or Anthropic-style) + own agent loop | Avoid vendor lock-in; support local/private use |
| 2026-10-03 | Develop against a cloud API provider; keep local models supported | Dev speed/quality without losing the local option |
| 2026-10-03 | First integrations: Gmail, Google Calendar, WhatsApp | User priority |
| 2026-10-03 | WhatsApp via WhatsApp Web in Piyo's own browser profile; sends need confirmation | Only way to use personal accounts; ToS risk disclosed to user |
| 2026-10-04 | WhatsApp via WhatsApp Web removed from the app (code, skills and UI deleted); WhatsApp will be redone with a different approach | Owner decision after the first live test; the ToS/ban risk and a fragile page were not worth keeping, and the approach is to be rethought. Lessons for the next attempt: the selectors and markup notes are in `docs/phase-3-browser-whatsapp.md`; carrier sites refuse a background browser, and WhatsApp Web was only ever tried in a visible window |
| 2026-10-03 | Start browser-only (Playwright, dedicated profile); sandbox/VM deferred | Simplest safe starting point |
| 2026-10-03 | Public release later; each skill ships a `SETUP.md` with user instructions | Users configure their own accounts/keys per skill |
| 2026-10-03 | Users can add any skill — pre-built (file/Git/catalog) or self-built (editor/learned); skills are permission-scoped | Skills are the product's main extension point |
| 2026-10-03 | Any provider can be added; presets for Anthropic, OpenAI, OpenRouter, DeepSeek, Kimi, Gemini (+ Ollama, LM Studio) | Flexibility; no lock-in |
| 2026-10-03 | Local model target: 8 GB VRAM (7–9B @ 4-bit); cloud recommended for heavy agentic/vision tasks | Realistic consumer hardware |
| 2026-10-03 | Skill scripts in Python (uv) and JS/TS (Deno) | Broad author base; Deno sandbox |
| 2026-10-03 | Skill catalog ships with the first public release | Easy discovery & safer installs |
| 2026-10-03 | Open source under MIT | User decision |
| 2026-10-03 | OpenRouter free models: a "Free only" filter in the model picker (per-provider, remembered), not a separate provider or a provider-level toggle. Free/paid, context size and tool support come from OpenRouter's public `/models` list, which also loads without a key | Free vs paid is per model on the same endpoint and key; keeps paid models available for later |
| 2026-10-03 | Repos owned by GitHub org **Piyo-AI** (https://github.com/Piyo-AI): `PiyoAI`, `piyo-skills`; MIT copyright holder "Piyo AI" | User decision |
| 2026-10-03 | Catalog submissions reviewed by the Piyo AI team | User decision |
| 2026-10-03 | Anonymous usage + crash reports, opt-in, no content ever collected | Privacy & trust |
| 2026-10-03 | File tools: each approved folder has an "allow changes without asking" switch (off by default for new folders). When on, creating folders, moving files and writing new files there need no per-call approval; **deleting and overwriting always ask**, as does anything outside such a folder. A tool may compute its risk per call (`Tool.risk_for`); skills cannot | Asking for every move made organising a folder unusable; the user decides trust per folder, destructive actions stay gated |
| 2026-10-03 | Web search: Brave Search API first (key in keychain, Settings section); `web.search` is `auto` and its results are fenced as untrusted. Other providers can be added behind the same tool | User chose Brave; simple JSON API with its own index |
| 2026-10-03 | Conversations live in the core (SQLite, `piyo.db`); the app sends only the new message plus a conversation id, and an interrupted run is repaired so no tool call is left without a result | Tool results and loaded skills must survive between messages and restarts; one source of truth |
| 2026-10-03 | Reply length cap is per model: user setting, else the provider-reported maximum (capped at 16384), else 4096. No built-in table of model limits | A value above a model's real maximum is rejected by some providers and model names change too often to hardcode; only OpenRouter reports limits |
| 2026-10-03 | UI package manager is **npm** (`package-lock.json`), not pnpm: one less tool to install, and nothing here needs a workspace. Ruff line length is 110 | The repo already used npm; the code was written to ~110 columns and 100 only produced noise |
| 2026-10-03 | The desktop shell starts the core as a child process (`src-tauri/src/core.rs`): it reads the `{port, token}` line, the webview polls `core_status`, a dead core shows a Restart banner, and the core exits when its stdin closes. In dev the child is `uv run piyo-core`; a release build shows an explanatory error until PyInstaller packaging (Phase 5) | Random port and token per launch with no manual steps; no orphaned core even if the app is killed |
| 2026-10-03 | Min OS: Windows 10 22H2+, macOS 12+, Ubuntu 22.04+/Debian 12+/Fedora 39+ | Tauri 2 / WebView requirements, broad reach |
| 2026-10-03 | Google OAuth: bring-your-own client for early releases (loopback redirect on 127.0.0.1 + PKCE); a shared Piyo client waits for Google verification | Restricted Gmail scopes need a security assessment; BYO avoids the blocker and keeps tokens on the user's machine |
| 2026-10-03 | Google tokens: refresh token + granted scopes in the keychain, access token in memory only; disconnect revokes at Google and deletes the keychain entries | Never store tokens in files |
| 2026-10-03 | Several Google accounts (supersedes "one account first"): one keychain entry per account (`google.account.<email>`) plus an index (`google.accounts`); scope grants are per account. With one account connected nothing changes for the model; with several, every Google tool needs an explicit `account` and refuses to guess, and approval cards name the account. No default account | A wrong-account send or event is worse than one extra question; an implicit default would be silent |
| 2026-10-04 | Scheduler is our own small asyncio loop with SQLite (`scheduler.db`), not APScheduler: structured rules (once, daily, weekdays, weekly, every N minutes) that the app, the model and the user all read the same way; local-clock times with DST; one run at a time. Jobs only fire while the core runs (no tray or start-at-login yet): on start a missed job runs once if it is less than 24 h late (1 h for every-N-minutes), otherwise it is recorded as missed. A scheduled run is unattended, so any call that needs approval is **held** (`ApprovalDeferred`), listed under Settings > Scheduled and only runs when the user approves it (expires after 7 days). The model can create schedules only through `schedule.create`, which asks the user | Persistent jobs with catch-up and a pending queue were simpler to get exactly right than to bend APScheduler to; an unattended run must never be able to send or delete on its own |
| 2026-10-04 | Skill scripts run through the `skill.run_script` core tool (`skills/runner.py`): JSON on stdin/stdout, 30 s default timeout (a skill may lower or raise it up to 300 s), 1 MB output cap, scrubbed environment plus only the secrets the skill declared (keychain entry `skill.<skill>.<NAME>`, passed as `PIYO_SECRET_<NAME>`), a scratch working folder. **Deno** runs with real permission flags from the manifest (read: the skill folder; net: `allow_net` hosts; nothing else). **Python** has no sandbox: it gets its own `uv` virtual environment, a separate process, and sockets blocked unless `runtime.python.network` is true; that block stops accidents, not hostile code. Therefore scripts of skills the user installed are confirmed on every call, built-in skills' scripts are not. Not done: OS-level isolation for Python (a restricted user, a job object or namespaces per platform) and bundling `uv` and Deno with the app (Phase 5; the runner finds them on PATH or through `PIYO_UV` / `PIYO_DENO`) | The honest line for users is that Python scripts are trusted code once approved; Docker (Phase 6) is the route to real isolation |
| 2026-10-03 | Google scopes come in groups (`read`, `gmail_draft`, `gmail_send`, `gmail_modify`, `calendar_write`); sign-in starts with `read` only and later requests add groups on top of what is granted | Least privilege: send/modify are asked only when the user enables those tools |
| 2026-10-04 | The core ships as a PyInstaller **one-folder** bundle (`core/piyo-core.spec`, `scripts/build_core.py`), bundled by Tauri as a resource and launched from the resource dir in release builds. The packaged core always uses a random port and token (it ignores `.env`, `PIYO_PORT` and `PIYO_TOKEN`), finds built-in skills in the bundle, and downloads Chromium into Playwright's normal per-user cache, not into the bundle (Playwright defaults to the bundle when frozen). `uv` and Deno are looked for in `<bundle>/bin` first, then on PATH | One-file builds unpack to a temp folder on every launch (slow, antivirus-hostile); a known token in a shipped build would defeat the local API's only protection; a browser inside the bundle would be re-downloaded at every update |
| 2026-10-04 | Release signing uses open-source signing programs instead of purchased certificates (Windows: SignPath Foundation's OSS programme is the candidate; macOS and Linux routes still open, see `docs/phase-5-public-release.md` A) | Fits an MIT, community project; avoids per-year certificate cost. Needs the repos public and CI-built releases before applying |
| 2026-10-04 | Pre-release security hardening: (1) once a run has read the user's private data, `web.fetch`, `browser.open` and `web.search` ask before sending an address or query to a host the user did not name (taint tracking, `safety/exfil.py`); (2) broad folders (drive, home, system) cannot be approved, and keys, `.env`, browser profiles and Piyo's own data are never touched by the file tools even inside approved folders; (3) `files.delete` moves to the OS trash instead of deleting; (4) the chat WebSocket also checks `Origin` | A model that has read mail or files can be tricked into putting it in a URL, and the model provider sees everything the file tools read; each fix keeps ordinary use prompt-free, and refuses rather than warns where the user cannot judge the risk |
| 2026-10-04 | Piyo has a default workspace folder, `~/Piyo` (`PIYO_WORKSPACE_DIR` overrides): always approved, created on first use, shown in Settings > Folders and not removable. Creating folders and adding new files there needs no approval; delete and overwrite still ask. A relative path in a file tool means "in the workspace", and the system prompt tells the model to save new files there unless the user names another approved folder | A task that creates files but names no folder had nowhere to go (no folder is approved by default); a fixed, visible place keeps Piyo's output together and out of the user's own folders |
| 2026-10-04 | MCP: Piyo is an MCP **client** only. Servers are added only by the user in Settings (a skill can name one in `SETUP.md`, never add it); stdio servers first, remote https servers (OAuth) second; the official `mcp` SDK; every MCP tool is `confirm` by default and server annotations can only add caution (risk stays a property of the tool); tool definitions are pinned by hash and a change disables the tool; results are fenced and taint the run; tools reach the model through skills (`requires.tools: mcp.<server>.*`) or a per-server "always on" switch; sampling, elicitation and roots are declined in v1 | An MCP server is third-party code (stdio) or a third-party service (remote) whose descriptions and results are untrusted text; keeping adding servers a user-only action and risk a tool property preserves the safety invariants. Stdio first because it needs no sign-in flow, so the safety pieces get built once, then reused by remote |

## 13. Backlog / Ideas

- Voice interface (wake word "Hey Piyo")
- Mobile companion (approve actions from phone)
- More integrations: Outlook, Telegram, Slack, Notion, banking read-only
- Catalog extras: ratings, reviews, paid skills, verified-publisher program
- Proactive suggestions ("you usually pay this bill around now")
- Per-skill cost/latency tracking and auto model selection
- Skill composition (skills calling other skills)
