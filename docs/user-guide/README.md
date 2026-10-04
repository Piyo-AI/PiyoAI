# Piyo AI user guide

Piyo is an assistant that runs on your computer. You chat with it, and it can use tools (your approved folders,
the web, a browser, Gmail and Calendar) and **skills**: saved procedures for jobs like tidying a folder or
tracking a parcel. It asks before it sends, buys, deletes or submits anything.

| Page | Read it to |
|---|---|
| [Install](install.md) | Download Piyo, get past the first-launch warnings, update it, remove it |
| [Models and providers](providers.md) | Connect a model: an online service or one running on your computer |
| [Skills](skills.md) | Use, install, write and remove skills |
| [Privacy](privacy.md) | See what stays on your computer and what leaves it |
| [Troubleshooting](troubleshooting.md) | Fix the problems people hit most often |

If you want to write a skill, see the [skill authoring guide](../skill-authoring.md). To work on Piyo itself, see
the [README](../../README.md) and [CONTRIBUTING](../../CONTRIBUTING.md).

## The first five minutes

1. [Install](install.md) Piyo and start it. A short setup opens the first time.
2. Choose where the model runs: an **online provider** (paste an API key) or **on this computer** (Ollama).
3. Read the "what leaves your computer" step. It is short and matters.
4. Pick one of the starter tasks, or just ask for something.

## Things worth knowing from the start

- **Folders.** Piyo can only read or change files in folders you approve (Settings > Folders). It keeps what it
  creates in its own folder, `Piyo`, in your home folder.
- **Approvals.** When Piyo wants to do something that changes or sends something, a card appears with what it
  will do. Nothing happens until you press Approve. Tasks > Approvals lists every decision.
- **Stop.** The Stop button ends a run at any time.
- **The task log.** Tasks shows what each run did, step by step, with its cost when the price is known.
- **Keys and passwords.** API keys are kept in your operating system's keychain. Piyo never types passwords, card
  numbers or ID numbers for you.
