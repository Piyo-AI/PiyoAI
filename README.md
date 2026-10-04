# Piyo AI 🐥

**A personal AI assistant that handles the daily tasks of an individual — and can learn new skills.**

Piyo AI is an open-source desktop assistant. It talks to the model you choose (local or cloud), uses your
browser and, later, your computer to get things done, and gets better over time by learning *skills* — reusable
procedures you can install, write yourself, or let Piyo learn from a task it just completed.

> **Status: early development (pre-alpha).** The desktop chat app, provider/model management, the local API, the
> agent loop with a permission gate, skills, file and web tools work today. Gmail/Calendar/WhatsApp and browser
> automation are planned — see the
> [roadmap](PLAN.md#10-roadmap).

## Goals

- **Handle everyday tasks**: morning briefs, email and chat triage, calendar planning, web errands, file
  organizing, reminders.
- **Use the computer and the internet**: a dedicated, isolated browser profile first; screen control inside a
  sandbox later.
- **Learn skills**: a skill is a folder with instructions (`SKILL.md`), a setup guide (`SETUP.md`) and optional
  Python or JS/TS scripts. Skills can be built in, installed from a catalog / file / Git URL, written by the user,
  or learned from a completed task.
- **Work with any model**: local models (Ollama, LM Studio) and any OpenAI- or Anthropic-compatible API.
  Pre-added: Anthropic, OpenAI, OpenRouter, DeepSeek, Kimi, Google Gemini.
- **Run everywhere**: Windows, macOS and Linux, as a desktop app.
- **Keep you in control**: anything risky (sending messages, purchases, deleting data) needs your confirmation,
  passwords and card numbers are never entered automatically, and API keys live in your OS keychain.

The full design, architecture, skill system, security model, roadmap and decision log are in [PLAN.md](PLAN.md).

## What works today

- Desktop app (Tauri 2 + React) with streaming chat.
- Provider registry with the presets above, plus **add any custom provider** (OpenAI- or Anthropic-compatible).
- Live model lists from the provider. For **OpenRouter** there is a **"Free only"** filter, and each model shows
  its context size and whether it supports tools.
- API keys stored in the OS keychain (Windows Credential Manager, macOS Keychain, Secret Service).
- An agent that uses tools (files in folders you approve, web fetch and search) and **skills**, asking you before
  anything risky, with a task log and approval audit.
- The app starts the core itself and offers a Restart button if it stops (from source; installers come later).
- A local, token-protected API (`127.0.0.1` only) between the app and the Python core, with readable errors
  (e.g. "Couldn't connect to Ollama … Is it running?").

## Repository layout

```
PiyoAI/
  apps/desktop/      Tauri app: React + TypeScript UI (src/) and the Rust shell (src-tauri/)
  core/              Python core "piyo": providers, model clients, local API (FastAPI + WebSocket), tests
  skills/            Built-in skills, each with a SKILL.md and a SETUP.md
  docs/              User guide (docs/user-guide) and the skill authoring guide
  PLAN.md            Project plan, architecture, roadmap, decisions
```

The skill catalog lives in a separate repository: [Piyo-AI/piyo-skills](https://github.com/Piyo-AI/piyo-skills).

## Documentation

- [User guide](docs/user-guide/README.md): install, models and providers, skills, privacy, troubleshooting.
- [Writing a skill](docs/skill-authoring.md), with starter templates in
  [piyo-skills/templates](https://github.com/Piyo-AI/piyo-skills/tree/main/templates).
- [Privacy policy](PRIVACY.md) (draft).

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| [Python](https://www.python.org/downloads/) | 3.12+ | the core |
| [uv](https://docs.astral.sh/uv/) | recent | Python environments and dependencies |
| [Node.js](https://nodejs.org/) | 20.19+ or 22.12+ | the desktop UI |
| [Rust](https://rustup.rs/) | stable (1.77+) | the Tauri shell |

Plus the [Tauri system dependencies](https://v2.tauri.app/start/prerequisites/) for your OS:

- **Windows**: Microsoft C++ Build Tools (Visual Studio "Desktop development with C++") and WebView2
  (already included in Windows 10 22H2 / 11).
- **macOS**: Xcode Command Line Tools (`xcode-select --install`).
- **Linux**: `webkit2gtk-4.1`, `libgtk-3-dev`, `librsvg2-dev`, `libayatana-appindicator3-dev` and a C toolchain
  (package names vary by distro; see the Tauri link above).

You also need **one model source**: an API key (OpenRouter has free models) or a local server such as
[Ollama](https://ollama.com/download).

## Quick start (development)

`npm run tauri:dev` (step 2) starts the core for you, so for the full app you can skip step 1. Starting the core by
hand is only needed to work on the core alone or to use the UI in a browser (`npm run dev`).

### 1. Start the core (optional)

```bash
cd core
uv sync
PIYO_PORT=8765 PIYO_TOKEN=dev-token uv run piyo-core
```

On Windows PowerShell:

```powershell
cd core
uv sync
$env:PIYO_PORT = "8765"; $env:PIYO_TOKEN = "dev-token"
uv run piyo-core
```

`PIYO_PORT` and `PIYO_TOKEN` are fixed here only because the dev UI expects `8765` / `dev-token`. In the packaged
app a random port and token are generated on every launch. Optionally set `PIYO_DATA_DIR` to keep your dev data
(provider settings, future skills) in a folder of your choice instead of the per-user default.

### 2. Start the desktop app

```bash
cd apps/desktop
npm install
npm run tauri:dev
```

The first run compiles the Rust shell (a few minutes); later runs start in seconds. If you only want the UI in
a browser, `npm run dev` serves it at <http://127.0.0.1:1420>.

### 3. Connect a model

Open **Settings** in the app and either:

- **OpenRouter (free to try)** — create a key at <https://openrouter.ai/keys>, paste it, then in the model picker
  choose OpenRouter, tick **Free only** and pick a model labelled **tools**. Free models have strict
  request limits; "rate limiting" errors usually mean you've hit them.
- **A paid provider** — paste its API key (Anthropic, OpenAI, DeepSeek, Kimi, Gemini).
- **A local model** — install Ollama, run `ollama pull <model>`, then select *Ollama (local)* and enter the model
  name. No key is needed. A card with about 8 GB of VRAM comfortably runs 7–9B models.
- **Anything else** — *Add a provider* with a base URL for any OpenAI- or Anthropic-compatible service.

For development you can instead put keys in a `.env` file at the repo root (see [.env.example](.env.example)).
The app itself stores keys in the OS keychain, never in a file.

## Tests and checks

```bash
python scripts/check.py   # everything below, same as CI

cd core
uv run pytest           # core tests
uv run ruff check piyo tests   # lint

cd ../apps/desktop
npm run typecheck       # TypeScript
npm run build           # type-check + production UI build
```

## Troubleshooting

- **"Can't reach the Piyo core"** in the desktop app: the banner shows why the core stopped (often `uv` is not on
  your `PATH`); fix that and press *Restart*. In a browser (`npm run dev`) the core must be started by hand as in
  step 1, on the dev defaults (`8765` / `dev-token`); press *Retry*.
- **Port 1420 already in use**: another `npm run dev` / `tauri dev` is still running; stop it first.
- **`cargo` not found** right after installing Rust: open a new terminal so your `PATH` updates.
- **Low-end or unstable machine**: slow installs down so they use less CPU, disk and network at once, e.g.
  `UV_CONCURRENT_DOWNLOADS=1 UV_CONCURRENT_INSTALLS=1 uv sync` and `CARGO_BUILD_JOBS=2 cargo build`.

## Contributing

Piyo AI is MIT-licensed and contributions are welcome. The project is young, so the best first step is to read
[CONTRIBUTING.md](CONTRIBUTING.md) and [PLAN.md](PLAN.md) and open an issue to discuss what you'd like to work on. Security problems: [SECURITY.md](SECURITY.md). Skills for the catalog go in
[Piyo-AI/piyo-skills](https://github.com/Piyo-AI/piyo-skills).

## License

[MIT](LICENSE) © Piyo AI
