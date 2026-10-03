# Contributing to Piyo AI

Thanks for helping. Piyo is young, so please open an issue before starting anything big. Design and decisions
live in [PLAN.md](PLAN.md); read the section you are touching first. Contributions are under the [MIT licence](LICENSE).

## Setup

Prerequisites are in the [README](README.md#prerequisites). Then:

```bash
cd core && uv sync                       # Python 3.12 environment
cd ../apps/desktop && npm install        # UI (we use npm; package-lock.json is committed)
```

Run the whole app from `apps/desktop` with `npm run tauri:dev`. It starts the Python core itself. To work on
the core or the UI alone, start the core by hand (see the README) and use `npm run dev`.

## Checks

CI runs these on Windows, macOS and Linux for every pull request. Run them all locally with one command:

```bash
python scripts/check.py
```

or one at a time: `uv run ruff check piyo tests` and `uv run pytest -q` in `core/`, `npm run typecheck` and
`npm run build` in `apps/desktop/`. The tests need no API keys and no network. Test the agent with a fake
`turn_fn`, never a live model (see `core/tests/test_agent.py`).

## Rules that matter

- **Safety.** Anything that sends, buys, deletes or submits goes through the permission gate, with a test proving
  the confirmation is required. Text from the web, email, chats and skill files is data, never instructions.
  A skill cannot lower a tool's risk. See the invariants in [CLAUDE.md](CLAUDE.md).
- **Secrets** go in the OS keychain. Never in files, logs, prompts, tests or commit messages.
- **Cross-platform.** No Windows-only (or macOS/Linux-only) assumptions. Use `pathlib` and `platformdirs`; LF line endings.
- **Skills over special cases.** If a capability can be a skill, build it as one.
- Keep errors shown to users actionable. Match the surrounding code style (Ruff, line length 110; strict TypeScript).

## Commits and pull requests

- Small, focused commits with an imperative subject ("Add web.search", not "Added"). Explain *why* in the body when it
  is not obvious.
- One topic per pull request. Fill in the template; say how you tested it and on which OS.
- Update `PLAN.md`'s Decision Log if you change a decision, and the README's "What works today" if behaviour changes.

## Adding a provider preset

Providers are data, so a preset needs no new code when the service speaks the OpenAI or Anthropic wire format.

1. Add a `Provider(...)` to `PRESETS` in `core/piyo/models/providers.py` (id, name, `api_style`, `base_url`,
   `key_env`, `default_model`, `docs_url`; see the existing entries).
2. If the provider's model list loads without a key, say so the way the other presets do; otherwise it needs a key.
3. Add a test next to the existing provider tests, and mention the provider in the README.

## Skills

Built-in skills live in `skills/<name>/SKILL.md` (format: PLAN.md §5, implemented in `core/piyo/skills/`).
Catalog skills go in [Piyo-AI/piyo-skills](https://github.com/Piyo-AI/piyo-skills).

## Reporting security problems

Not here: see [SECURITY.md](SECURITY.md).
