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

**Licences.** `python scripts/licenses.py --check` (from `core/` with `uv run`, needs `uv sync`, `npm ci` and `cargo`) fails when a shipped
dependency has a licence outside `ALLOWED`; without `--check` it rewrites `THIRD-PARTY-NOTICES.txt` (all licence texts), which
`tauri.bundle.json` ships with `LICENSE`. Regenerate and commit it whenever `uv.lock`, `Cargo.lock`, `package-lock.json` or
`piyo/skills/helper_tools.json` change. CI job `licenses` runs the check.

Deno tests (`tests/test_script_runner.py`) run when `deno` is on PATH or `PIYO_DENO` points at it; otherwise they skip. Ruff 0.16.9 runs fine here: run it before pushing, CI fails on E501 and import order.

Lint: `uv run ruff check piyo tests` (line length 110, rules E F I B UP). Check `CLAUDE.local.md` if it fails to run.
Ruff 0.16.10 crashes on Windows (access violation); `pyproject.toml` excludes it. Do not remove that exclusion
until a newer release is confirmed to run.

## Layout (`core/piyo/`)

| Package | Responsibility |
|---|---|
| `config/` | Per-user data dirs (`PIYO_DATA_DIR` overrides), keychain secrets, approved folders, per-model output limits (`model_limits.py`) |
| `models/` | Providers as data (`providers.py`); model clients, model lists (`clients.py`); one tool-calling turn normalised across both wire formats (`turn.py`); tools as text for models without tool calling (`prompt_tools.py`) |
| `agent/` | The loop (`loop.py`): model turn, tool calls, results, repeat; system prompt (`prompts.py`) |
| `tools/` | `Tool`, `Risk`, `ToolRegistry`, `RunContext`; core tools `load_skill`, `current_time`; `google.py` (`ACCOUNT_PROP`, `who`, `google.accounts`), `gmail.py` (Gmail tools), `calendar.py` (Calendar tools), `weather.py` (Open-Meteo forecast, no key); `browser/` (`browser.open/read/click/type` over a `BrowserSession`; `rules.py` holds the hard stops and click risk, decided from the page element's role and name, never model text; refs come from `read` and are cleared after a click; `PlaywrightSession` runs Chromium in the persistent profile, every request must pass `web.check_url`; page text is fenced; `set_visible` restarts Chromium on the same profile to show/hide the window; Stop closes its pages when the run used a browser tool; API `/api/browser`, `/api/browser/stop`; site lists and the per-request page limit in `config/browser_rules.py` (`/api/browser/rules`, Settings > Browser), enforced for every request in `PlaywrightSession._guard`; app bar `BrowserBar.tsx`; the visible window shows a "Piyo is working" badge (`BADGE_SCRIPT`, driven by `set_working`: on at a browser tool call, off when the run ends); skill `browse-web`; Chromium is not bundled: `install.py` `BrowserInstaller` runs `playwright install chromium` only when the user presses Install browser in the app (`/api/browser/install`, progress parsed from the installer output); a missing browser makes the tools fail with a message that points at that button) |
| `scheduler/` | `rules.py` (structured schedules, `next_run`, local time + DST), `store.py` (`scheduler.db`: jobs, held actions, notifications), `service.py` `Scheduler` (20 s tick, catch-up, one run at a time; `RunResult`; the server supplies the runner `run_scheduled` that makes a conversation `Scheduled: <title>` and the executor that runs an approved held action). An unattended run's approver raises `ApprovalDeferred`, so a confirm call is added to the pending list and the model is told it did not happen. Tools `schedule.create` / `schedule.cancel` confirm, `schedule.list` auto (`tools/schedule.py`). API `/api/scheduler/jobs`, `/jobs/{id}/run`, `/pending`, `/pending/{id}/approve|decline`, `/events`; app: Settings > Scheduled (`SchedulePage.tsx`) and the banners from `useScheduler.ts` (polls every 15 s, also a Web Notification when allowed). `create_app(start_scheduler=False)` in tests |
| `safety/` | `PermissionGate`: every tool call is authorised here before it runs; `untrusted.py` fences outside text |
| `skills/` | `SKILL.md` parsing/validation (`manifest.py`) and discovery (`registry.py`); `runner.py` `ScriptRunner` (skill scripts: Python in a per-skill `uv` venv, `.js`/`.ts` in Deno with flags from the manifest; timeout, output cap, scrubbed env, declared secrets only; see its docstring for what each runtime can and cannot enforce) with the core tool `skill.run_script` in `tools/scripts.py` (confirm for installed skills, auto for built-in ones; result fenced); skill secrets: keychain `skill.<skill>.<NAME>`, API `PUT/DELETE /api/skills/{name}/secrets/{secret}` (values never returned), removed on uninstall; `editor.py` `SkillEditor` (write or edit `SKILL.md`/`SETUP.md` of skills in the user dir: `check` is the live validation the editor shows, `save`/`create` need any new permission approved like an update, every save/rollback/update keeps the old folder in `<data>/install/backups/<skill>/<timestamp>-<id>/` with `.piyo-backup.json`, 30 kept, `rollback` keeps the version it leaves; API `/api/skills/check`, `POST /api/skills`, `/api/skills/{name}/files|history|rollback`, `/api/skills-template`; app `SkillEditor.tsx`, and the skill list's Edit / Test it buttons: Test it opens a new chat prefilled for that skill, so the normal gate and task log apply); `learn.py` (skill learning: `draft_from_chat` / `refine_from_chat` make ONE plain model call over a fenced digest of the chat and ask for JSON name/description/steps/setup; the core assembles the frontmatter itself, so `requires.tools` is the tools the chat really used (successful calls, minus baseline/schedule/script tools) and integrations follow from them; `scrub` removes keys, emails, phone and long numbers, personal folder paths and link parameters; a refinement keeps the skill's own settings, bumps the patch version and returns a diff; API `POST /api/skills/draft`, `POST /api/skills/{name}/refine`; `POST /api/skills` with `learned: true` stores it as "learned from a chat" and switched off; app: `useSkillOffer.ts` shows the banner after a finished chat with at least two tool calls (or, if the chat loaded a skill the user owns, offers to improve it; the offer says why when a tool failed in the last turn or the user's last message looks like a correction, `looksLikeCorrection`, a regex heuristic whose false hits only show a dismissable banner; refine offers have a note field that goes to the API's `note`), the draft opens in `SkillEditor` with the scrub note and the diff); `git_hosts.py` (the hosts: `GitHub`, `GitLab` (gitlab.com, groups nest, `/-/tree/`), `Codeberg` (`/src/branch|tag|commit/`); each gives address parsing, the commit lookup URL, the archive URL and its token header; `GitClient` contacts only that host's `api_hosts`, never follows redirects, sends the token only there; GitLab and Codeberg need a ref's slash encoded, GitHub does not; a new host is a `GitHost` subclass in `HOSTS` plus a test, self-hosted servers are out of scope) and `git_source.py` (install from `https://github.com/<owner>/<repo>[/tree/<ref>[/<folder>]]` or the same on gitlab.com / codeberg.org: the ref is resolved to one commit, that commit's archive is downloaded from codeload.github.com, a repo with several skills answers with the folders to choose from and the commit to keep, then `stage_zip(subpath=...)`; no git binary; API `POST /api/skills/install/git {url, folder?, commit?}`; a ref may contain slashes: `GitAddress.candidates()` splits `.../tree/a/b/c` into ref/folder every way, shortest ref first, and `_resolve_address` takes the first one GitHub knows (`CatalogNotFound`, which is also GitHub's 422 for an unknown ref); a private repo works with the user's read-only token from `git_token.py` (keychain `git.<host>`, API `/api/git-tokens[/{host}]`, UI in `SkillInstall.tsx`), which only `GitClient` sends; `CatalogClient` never gets one); `revocation.py` (`check_catalog`: switches off an installed catalog skill whose version the catalog withdrew, once, records it in `skills_revoked.json`, reports updates and the permissions they add; API `POST /api/catalog/check`, `POST /api/catalog/withdrawn/{name}/acknowledge`, `SkillOut.withdrawn_reason`; app `useCatalogCheck.ts` banners in `App.tsx`); `validate.py` `validate_package` (the catalog's submission rules: licence, layout, tools, script and secret checks; errors vs reviewer warnings; `KNOWN_TOOLS` must match the registered tools, a test enforces it, so add a new tool there too); `install.py` `SkillInstaller`: `.piyoskill` zip -> staged `Preview` (permission labels `tool:`/`integration:`/`secret:`/`script:`/`runtime:`) -> `commit(token, approved)`; a fresh install needs every permission approved, an update only those not in the installed `.piyo-install.json` `approved` list; zip checks (traversal, links, size/file limits, forged metadata dropped); old versions go to `<data>/install/backups/`; API `POST /api/skills/install/preview` (raw zip body), `POST /api/skills/install`, `DELETE /api/skills/install/{token}`, `DELETE /api/skills/{name}`; `catalog.py` `CatalogClient`: the public catalog repo `Piyo-AI/piyo-skills` (index format in PLAN.md section 5): resolves `main` to one commit, reads `index.json` and downloads the zip at that commit (only api.github.com / raw.githubusercontent.com / codeload.github.com over https), `stage_zip(subpath=..., expected_sha256=...)` picks one skill folder and refuses a hash mismatch; API `GET /api/catalog` and `POST /api/catalog/install {name, commit}` (stages for review, then `POST /api/skills/install` finishes); the index must carry a valid signature (`signing.py`: Ed25519, `index.json.sig` read at the same commit, public keys in `TRUSTED_KEYS`; unsigned, malformed, unknown-key or mismatching means the catalog is refused before any entry is read), and an install through it is `verified` (`Preview.verified`, saved in `.piyo-install.json`, shown on the review card and by `SkillOut.verified`) and carries the index's `badge` (official / verified / community; `Preview.badge`, `SkillOut.badge`, app `Badge.tsx`; only kept together with `verified`, an unknown name becomes community); `stage_zip(verified=True)` only counts together with the expected hash; files and Git addresses are never verified; tests sign with a throw-away key (`tests/catalog_signing.py`, patched in by `conftest.py`) and the maintainer signs with `piyo-skills/scripts/sign_index.py` |
| `store/` | `memory.py` `MemoryStore` (`memory.db`: short facts by category preference/person/routine/account/outcome/note; secrets, card and ID numbers refused by `check_text`; health/finance/identity only after the user allows them, `MemorySettings`; keyword search; API `/api/memory`, `/api/memory-export`, `/api/memory-settings`; Settings > Memory in `MemoryPage.tsx`); tools in `tools/memory.py` (core tools `memory.recall` auto, `memory.remember` auto, `memory.forget` confirm; recalled text is fenced; `profile_prompt` puts a short fenced profile in the system prompt through `Agent(memory_prompt=...)`). `ConversationStore`: conversations, messages (tool calls included) and loaded skills in SQLite; `runs.py`: the task log (`RunLog`, `RunStore`, `redact`) |
| `integrations/` | `google/oauth.py`: `GoogleAuth` (BYO OAuth client, loopback + PKCE sign-in, token refresh, revoke; several accounts: `accounts()`, `resolve(account)`, per-account grants and tokens); tokens only in the keychain, one entry per account (`account_secret`). `google/client.py`: `GoogleClient` (authorised requests, 401 refresh-and-retry, `error_for` maps failures to `GoogleError(kind, message)`, `require` checks the granted scope before a tool calls out) |
| `telemetry.py` | The only place that decides what may be reported (opt-in, off until the user says yes): `EVENTS` is a table of event types whose fields accept only fixed shapes (`validate`), so a prompt, path, URL or error message cannot be sent; crash reports are `crash(exc)` = class name + `stack_frames` (`frame_label` turns file names into `piyo/...` or `pkg/...`, never a user folder); `Telemetry` keeps consent and a random install ID in `telemetry.json`, queues events in `telemetry-queue.jsonl` (200 max; opting out deletes both), `requests()` are the exact HTTPS requests `flush` sends (crashes to Sentry as envelopes, usage to PostHog `/batch/`, built by hand with no SDK, every queued event has an `id` used for dedupe and to drop only what was sent) and what Settings > Privacy shows; `SENTRY_DSN`, `POSTHOG_HOST`, `POSTHOG_KEY` are empty until the cloud projects exist so `flush` does nothing; the server calls `flush` every 15 minutes only when one is set. Hooks: run end (`finish_run` in `server/app.py`: tool names that are registered, skill ids that are built-in or catalog-verified), app start, `sys.excepthook`/`threading.excepthook` (`install_crash_hooks` in `server/__main__.py`), the server's 500 handler and unexpected run errors (`report_unexpected`; provider/network errors are not crashes), the app's window errors (`reportUiError` in `api.ts` -> `POST /api/telemetry/ui-error`), and the shell's panic note (`src-tauri/src/crash.rs` writes `file:line:col`, the core reads `PIYO_SHELL_CRASH_FILE` and deletes it). API `/api/telemetry`; app `PrivacyPage.tsx` and the `ShareStep` of onboarding. A new event or field goes in `EVENTS`, `PRIVACY.md` and a test in `tests/test_telemetry.py` together |
| `server/` | FastAPI + WebSocket API for the app (`app.py`), launcher (`__main__.py`; exits when stdin closes if `PIYO_EXIT_ON_STDIN_EOF` is set) |

**Toasts** (`Toasts.tsx`, bottom-right stack like VS Code, mounted in `App.tsx`): `UpdateToast` walks the update through available -> Download (runs in the background, `useUpdate.download`, progress) -> ready -> "Install and restart" with a warning step (`RESTART_WARNING`) -> `install` (`begin_update`, `Update.install`, relaunch). The `useUpdate` instance lives in `App` and is passed to Settings > Updates, so both show the same state. `SkillToast` is the old skill banner: Draft/Suggest, Not now (until the next message), and a "..." menu with "this session" (memory) or "never" (`skillSuggestions.ts`, localStorage `piyo.skillSuggestions`, undone by the checkbox in Settings > Skills). A new toast is a `Toast` with actions; keep the scheduler banners as banners.

Tauri shell background mode (`src-tauri/src/background.rs`, UI in `SchedulePage.tsx`, helpers in `native.ts`): opt-in "keep running in the tray" (`background.json` in the app config dir) turns window close into hide and shows a tray icon (Open / Quit); the core is a child process so the scheduler keeps firing. Start at login uses `tauri-plugin-autostart` with `--background` (starts hidden) and is only offered while keep-running is on; `tauri-plugin-single-instance` shows the window instead of starting a second app and core. If the tray cannot be created the window is never hidden. System notifications go through `notify()` (`tauri-plugin-notification`, web `Notification` in a browser). Plugin changes need a Rust rebuild.

Tauri shell (`apps/desktop/src-tauri/src/core.rs`): runs the core as a child (`uv run piyo-core` in debug builds; release
builds run the packaged core from the app's resources, `core/piyo-core[.exe]`), exposes `core_status` / `restart_core` and
the `core-exited` event; `api.ts` `resolveConnection` polls it.

**Packaged core.** `python scripts/build_core.py [--smoke]` builds `core/dist/piyo-core/` with PyInstaller from
`core/piyo-core.spec` in its own venv (`core/.venv-build`, `uv sync --no-dev --group build`, so a running dev core that locks
`.venv` does not matter). `--smoke` starts the result like the app does and checks health, built-in skills, providers (keyring)
and the Playwright driver; CI runs it on all three OSes (job `package`). `npm run tauri:build` (in `apps/desktop`; needs `TAURI_SIGNING_PRIVATE_KEY` set, the updater key, see `docs/phase-5`) bundles that
folder through `src-tauri/tauri.bundle.json` (kept out of `tauri.conf.json` so `tauri dev` and `cargo check` do not need a
built core); build the core first. **uv and Deno are not in the bundle** (130 MB): `skills/helper_tools.py` `HelperTools` downloads the one a script needs the first time (`ScriptRunner._program` -> `ensure`), from the release URL and SHA-256 pinned in `skills/helper_tools.json` (shipped as data by `piyo-core.spec`); a hash mismatch deletes the download and nothing runs. Order of use: `PIYO_UV`/`PIYO_DENO`, our download in `tools_dir()` (`<local data>/tools/<tool>-<version>/`), then PATH (so a developer's own copy means no download). The approval card for `skill.run_script` says when the run will download (`ScriptRunner.download_note`). API `GET /api/helper-tools`, `POST /api/helper-tools/{uv|deno}/install`; `build_core.py --smoke` downloads both through the packaged core with PATH copies hidden. Upgrade steps in CONTRIBUTING.md.
`piyo/runtime.py` is the one place that knows about frozen runs: `frozen()`, `bundle_dir()`,
`use_shared_browser_cache()` (Playwright defaults
`PLAYWRIGHT_BROWSERS_PATH` to 0 = inside the bundle when frozen; we point it at the per-user cache). A frozen core ignores `.env`,
`PIYO_PORT` and `PIYO_TOKEN`. Anything that spawns `sys.executable` must handle the frozen case (the Chromium installer does:
`installer_command()` runs the bundled Node driver). A new dynamically imported package or data file needs an entry in the spec.

**Releases.** A `v*` tag runs `.github/workflows/release.yml`: the `notes` job writes the release text from the commit subjects since the previous tag (`scripts/release_notes.py`; a subject may start `feat:`, `fix:` or `security:` to be grouped, `- ` lines in a commit body are listed under it, "Version x.y.z" bumps are skipped, so write subjects for a reader), then the installers are built as a draft. `scripts/check_targets.py` fails a build whose native files need a newer glibc than 2.35 (Ubuntu 22.04) or macOS than 13.5 (Playwright's Node needs it); CI's `package` job and the release run it, both build Linux on `ubuntu-22.04` and set `MACOSX_DEPLOYMENT_TARGET=13.5`; `tauri.conf.json` has `minimumSystemVersion` 13.5. Changing the supported floor means changing `MAX_GLIBC`/`MAX_MACOS`, those two env settings, the runner, `docs/user-guide/install.md` and Phase 5 in `../docs/`.

Desktop (`apps/desktop/src/`): `App.tsx` (chat shell), `useChat.ts` (chat state + socket events), `api.ts`
(HTTP/WS client), `Tools.tsx` (tool chips, approval card), `Settings.tsx` (settings window: left sidebar with Providers > Provider list / Add provider, Web search, Folders, Skills, Limits), `GoogleConnection.tsx` (client ID, access checkboxes, Connect/Disconnect), `SetupWizard.tsx` + `Markdown.tsx` (a skill's `SETUP.md` as a step-by-step wizard; `useSetupNeeded.ts` drives the chat banner).
`Onboarding.tsx` (first-run setup overlay: online key or Ollama setup, privacy, first task; flag in `config/onboarding.py`, API `/api/onboarding`; Ollama status and model pull in `models/ollama.py`, API `/api/ollama` and `/api/ollama/pull`, loopback hosts only; shown unless done or an online provider already has a key; Settings > Run setup again), `SkillInstall.tsx` (Settings > Skills: pick a `.piyoskill`/zip, From GitHub, an updates notice for catalog skills with a newer version, review card with the permissions to approve and the Unverified notice, install; Browse skills opens `CatalogBrowser.tsx` (search, category chips, badge, what each skill asks for and needs set up, "from <repo> at <commit>" for external skills, withdrawn versions shown and not installable, a warning when you have one; helpers in `catalog.ts`; it also holds `describePermission`) with Install / Update; each installed skill has Uninstall), `useModelWarnings.ts` (pre-send banner: unusable skills, local-only), `ModelSettings.tsx` (the "Model settings" popover: per-model max reply, context size and tool mode), `Tasks.tsx` (task log; its Approvals tab is `Audit.tsx`).
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
- **Tool results can carry pictures** (browser screenshots): a handler calls `ctx.attach_image(...)`, the loop moves them onto the tool `Message.images` (dropped when the call failed). Anthropic gets them inside the `tool_result`; OpenAI-style gets a user message with `image_url` parts right after the group of tool results. `images` is `exclude=True`, so they are never saved with the conversation (history reloaded later has the text only); `fit_context` counts `IMAGE_TOKENS` per picture and drops old ones first. Text-only (prompt-tools) models never get them. `browser.screenshot` refuses only a model known to lack vision.
- **Private data and outbound strings** (`safety/exfil.py`). `RunContext.private_data` turns on when a tool that returns the user's
  data has run (`produces_private_data`: `files.*`, `gmail.*`, `calendar.*`, `google.*`, `memory.recall`, `skill.run_script`) or the
  chat history holds such a result. A `Tool` with a `guard` (`OutboundGuard`: `web.fetch` and `browser.open` by URL, `web.search` by
  query) then needs approval unless the host appears in `RunContext.user_text` (the user's own messages) or was approved this run;
  `PermissionGate.authorize(..., ctx)` adds the reason to the card. A guard only adds a confirmation. A new tool must be classified in
  `tests/test_exfil.py` (`PUBLIC`, or private by name), and a new tool that sends a model-written address or query out needs a guard.
- **Protected paths** (`config/protected.py`). `broad_reason` (drive, home folder, system folders, protected places) is checked when
  folders are approved (`ApprovedFolders.set`) and again for existing grants in `FileTools._resolve`; `protected_reason` (keys, `.env`,
  browser profiles, Piyo's data dir) is checked for every path any file tool resolves, and listings hide such entries. `files.delete`
  calls `send2trash` (a module attribute, replaced in `tests/conftest.py` so no test touches the real trash). The test fixture puts the
  data dir in `tmp_path/piyo-data` because approved test folders live under `tmp_path`.
- **Outside text and memory** (`safety/taint.py`). `RunContext.untrusted_text` turns on when a tool that returns text someone else
  wrote has run (`produces_untrusted`: `web.*`, `browser.*`, `gmail.*`, `calendar.*`, `files.read`, `files.list`, `skill.run_script`) or
  the chat history holds such a result. `memory.remember` carries a `MemoryGuard` and then needs approval. A new tool must be classified
  in `tests/test_taint.py` (`OWN_TEXT`, or untrusted by name); a new tool that stores anything persistent from model text needs a guard.
- **WebSocket origin.** `/ws/chat` refuses an `Origin` that is not in `allowed_origins()` (close code 4403) before checking the token; the Vite dev origins (`:1420`) are left out when the core is frozen.
- **Pinned fetches and path rechecks.** `web.fetch` connects to the IP `resolve_public` checked (`pinned_request`: `Host` and SNI stay the real host), so a second DNS answer cannot redirect it; the browser is not covered. File tools call `FileTools._recheck` right before touching a path and open with `O_NOFOLLOW`/`O_EXCL`; a new file tool that acts on a resolved path must do the same.
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
approval UI, stop/cancel, skill loading and per-skill tool grants, example skills `plan-my-day` and `downloads-organizer`. Browser (`browser.*`, skills `browse-web`, `price-compare`, `parcel-tracking`; see the `tools/` row). Gmail (`gmail.*` tools, skill `gmail-triage`; read/draft auto, send/label/archive confirm; mail is fenced as untrusted; needs the user's own Google client, see Settings > Skills). Calendar (`calendar.*` tools, skill `calendar`; agenda/free-busy auto, create/update/delete confirm; bare times are read in the calendar's zone). Weather (`weather.forecast`) and the read-only skill `morning-brief` (calendar + unread mail + weather; the user's city is remembered with `memory.remember`). The setup wizard is next. `tzdata` is a dependency (Windows has no zone database); a packaged core must include it.
File tools (`files.*`, approved folders only; API `/api/folders`, Settings section to manage them, with a per-folder "no prompts for changes" switch). `web.fetch` (public hosts only, output fenced as untrusted; skill `web-reader`). `web.search` (Brave; key in keychain, `/api/search`; skill `web-research`). Sidecar launch in dev, CI and contributor docs are in place. Not yet: packaged core for release builds,
integrations. See `../docs/README.md` for the ordered plan.
