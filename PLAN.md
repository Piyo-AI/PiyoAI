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
   - *Integrations*: Gmail, Calendar, WhatsApp (see §6) — packaged as built-in skills. Exposed as MCP-style tool servers so more can be added later.
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
| **WhatsApp** | **WhatsApp Web in Piyo's own browser profile** (decided) | User scans the QR code once; session persists in the Piyo profile. |

**WhatsApp implementation notes:**
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
- [ ] Monorepo setup (git, MIT `LICENSE`, `CONTRIBUTING.md`, Python env via `uv`, Node/pnpm, lint/test, CI matrix win/mac/linux)
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
- [ ] `whatsapp` skill via WhatsApp Web: read/summarize, draft, send-with-confirmation, rate limits, health check

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
| 2026-10-03 | Min OS: Windows 10 22H2+, macOS 12+, Ubuntu 22.04+/Debian 12+/Fedora 39+ | Tauri 2 / WebView requirements, broad reach |

## 13. Backlog / Ideas

- Voice interface (wake word "Hey Piyo")
- Mobile companion (approve actions from phone)
- More integrations: Outlook, Telegram, Slack, Notion, banking read-only
- Catalog extras: ratings, reviews, paid skills, verified-publisher program
- Proactive suggestions ("you usually pay this bill around now")
- Per-skill cost/latency tracking and auto model selection
- Skill composition (skills calling other skills)
