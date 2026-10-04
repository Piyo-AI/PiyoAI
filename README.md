# Piyo AI 🐥

**A personal AI assistant that handles the daily tasks of an individual — and can learn new skills.**

Piyo AI is an open-source desktop assistant. It talks to the model you choose (local or cloud), uses your
browser and, later, your computer to get things done, and gets better over time by learning *skills* — reusable
procedures you can install, write yourself, or let Piyo learn from a task it just completed.

> **Status: early releases (0.x).** Installers for Windows, macOS (Apple Silicon) and Linux are on the
> [releases page](https://github.com/Piyo-AI/PiyoAI/releases). They are **not code-signed yet**, so your system warns
> you on first launch ([how to get past it](docs/user-guide/install.md)). Expect rough edges and breaking changes
> between versions. What is built and what is next: the [roadmap](PLAN.md#10-roadmap).

## Install

Download the installer for your system from the [releases page](https://github.com/Piyo-AI/PiyoAI/releases) and follow
the [install guide](docs/user-guide/install.md): a Windows `.exe`, a macOS `.dmg` (macOS 13.5 or newer, Apple
Silicon), or a Linux `.AppImage`, `.deb` or `.rpm`. The first start walks you through choosing a model. Piyo checks
for new versions itself and never installs one without you saying so. To run it from source instead, see
[Quick start](#quick-start-development) below.

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

- **Chat and models.** A desktop app (Tauri 2 + React) with streaming chat. Provider presets (Anthropic, OpenAI,
  OpenRouter, DeepSeek, Kimi, Gemini, Ollama) plus **any custom OpenAI- or Anthropic-compatible provider**, live model
  lists (OpenRouter has a **Free only** filter; each model shows its context size and tool support), and models
  without native tool calling are supported too. API keys live in the OS keychain (Windows Credential Manager, macOS
  Keychain, Secret Service).
- **An agent that asks first.** Tool calls go through a permission gate with an approval card, a task log and a
  tamper-evident audit of approvals. Sending, deleting, buying and submitting always need your confirmation; text from
  the web, mail, files and skills is treated as data, never as instructions.
- **Tools.** Files in folders you approve, web fetch and search, weather, and a **browser** (Playwright with a
  dedicated profile; Chromium is downloaded when you press *Install browser*) with site allow/block lists.
- **Google.** Gmail and Calendar through your own Google OAuth client, with several accounts. Reading and drafting are
  automatic; sending, labelling and creating or changing events ask first.
- **Skills.** Built in: `morning-brief`, `gmail-triage`, `calendar`, `browse-web`, `price-compare`, `parcel-tracking`,
  `plan-my-day`, `downloads-organizer`, `web-reader` and `web-research`. Install more from the signed catalog, from a
  file, or from a **Git address on GitHub, GitLab.com or Codeberg** (private repositories work with a read-only access
  token). Each install shows the permissions it asks for. You can write or edit skills in the app, test them, roll back
  a version, and let Piyo **draft a skill from a task it just finished**. Skills can ship Python or JS/TS helper scripts
  (Python in a per-skill environment, JS/TS in a Deno sandbox).
- **Memory.** Short facts about you (preferences, people, routines), viewable, editable and exportable; sensitive
  categories are opt-in, and saving a note after reading outside text asks first.
- **Scheduler.** Reminders and recurring routines ("every morning at 8, give me my brief"). Anything that would send or
  change something waits for your approval. Optionally keep Piyo in the tray so routines run with the window closed, and
  start it at login (Settings > Scheduled).
- **Updates.** A toast offers a new version; it downloads in the background and installs, after a restart warning,
  only when you choose. Every update is signature-checked, and a version that will not start offers to roll back.
- **Privacy.** Nothing leaves your computer except what the tools you use need. Crash and usage reporting is **off
  unless you opt in**, shows you exactly what would be sent, and is described in [PRIVACY.md](PRIVACY.md).
- **Under the hood.** The app starts the Python core itself (a packaged executable in the installers) and talks to it
  over a local, token-protected API on `127.0.0.1` only, with readable errors (for example "Couldn't connect to
  Ollama … Is it running?").

Not yet: code signing, an Intel Mac build, MCP servers (planned as Phase 7 in the roadmap), computer use beyond the
browser, and a WhatsApp skill (dropped for now).

## Repository layout

```
PiyoAI/
  apps/desktop/      Tauri app: React + TypeScript UI (src/) and the Rust shell (src-tauri/)
  core/              Python core "piyo": agent, providers, tools, skills, scheduler, memory, local API, tests
  skills/            Built-in skills, each with a SKILL.md and a SETUP.md
  scripts/           Checks, packaging, versioning and release scripts (check.py, build_core.py, bump_version.py, ...)
  docs/              User guide (docs/user-guide) and the skill authoring guide
  PLAN.md            Project plan, architecture, roadmap, decisions
  RELEASE.md         How versions are numbered, built, published and rolled back
  PRIVACY.md  SECURITY.md  CONTRIBUTING.md  THIRD-PARTY-NOTICES.txt  LICENSE
```

The skill catalog lives in a separate repository: [Piyo-AI/piyo-skills](https://github.com/Piyo-AI/piyo-skills).

## Documentation

- [User guide](docs/user-guide/README.md): install, models and providers, skills, privacy, troubleshooting.
- [Writing a skill](docs/skill-authoring.md), with starter templates in
  [piyo-skills/templates](https://github.com/Piyo-AI/piyo-skills/tree/main/templates).
- [Privacy policy](PRIVACY.md) (draft), [security policy](SECURITY.md) and
  [third-party notices](THIRD-PARTY-NOTICES.txt).
- [Contributing](CONTRIBUTING.md) and [Releasing](RELEASE.md) (versioning, the release workflow, updates and rollback).

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
(provider settings, skills, memory) in a folder of your choice instead of the per-user default.

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
python scripts/check.py   # ruff, pytest, typecheck and the UI build (CI also checks the packaged core and licences)

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
- **Changed the app icon, a plugin or a Tauri setting and nothing changed**: the Rust shell caches what it embeds; stop
  `tauri dev`, touch `apps/desktop/src-tauri/build.rs` and start it again.
- For problems with an installed copy, see the [user guide's troubleshooting](docs/user-guide/troubleshooting.md).

## Building, packaging and releasing

- `python scripts/build_core.py --smoke` builds the packaged core (PyInstaller) and checks that it runs; CI does this on
  all three systems. `uv` and Deno are not bundled: the core downloads the versions pinned in
  `core/piyo/skills/helper_tools.json`, checked against a SHA-256. Upgrade steps are in
  [CONTRIBUTING.md](CONTRIBUTING.md#building-the-packaged-core-and-upgrading-uv--deno).
- `npm run tauri:build` (in `apps/desktop`) builds an installer locally; it needs the updater signing key in
  `TAURI_SIGNING_PRIVATE_KEY` and a built core.
- **Releases** are made by pushing a `vX.Y.Z` tag: see [RELEASE.md](RELEASE.md) for the version scheme, the checklist,
  what the workflow builds, how to publish the draft, and what to do when something goes wrong.

## Contributing

Piyo AI is MIT-licensed and contributions are welcome. The project is young, so the best first step is to read
[CONTRIBUTING.md](CONTRIBUTING.md) and [PLAN.md](PLAN.md) and open an issue to discuss what you'd like to work on.
Skills for the catalog go in [Piyo-AI/piyo-skills](https://github.com/Piyo-AI/piyo-skills).

## Reporting security problems

Not here: see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © Piyo AI
