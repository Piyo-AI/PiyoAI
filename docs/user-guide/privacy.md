# Privacy

The short version is in [PRIVACY.md](../../PRIVACY.md), which is the policy. This page is the practical side:
what to check and where to change it.

## What stays on your computer

Conversations, memory, the task log, skills, settings and Piyo's browser profile live in Piyo's data folder (see
[Install](install.md#where-your-data-is)). API keys live in your operating system's keychain. There is no Piyo
account and no Piyo server that holds your data.

## What leaves your computer

Only things you set up or ask for:

- **The model provider.** Your messages, and whatever Piyo reads to do a task (a file, an email, a page), go to the
  provider you chose. With a local model (Ollama) they do not leave the computer. To make sure of it, turn on
  Settings > Limits > **Only use models that run on this computer**.
- **Web searches** go to Brave with the words searched. **Pages Piyo opens** are requested from those sites.
- **Google** features talk to Google using the connection you created.
- **The catalog and updates** contact GitHub; they do not send your data.

## What Piyo refuses to do

- It reads only folders you approved, never the keys, `.env` files or browser profiles inside them, and never a
  whole drive or your home folder.
- After Piyo has read private data (a file, mail, calendar), it asks before sending an address or a search to a
  site you did not name, so a tricked model cannot leak it through a link.
- It asks before it sends, buys, deletes or submits anything. Deleting moves a file to the trash.
- It never types passwords, card numbers or ID numbers for you.

## Anonymous usage and crash reports

Off unless you say yes (setup asks once; Settings > **Privacy** changes it). If on, it can report Piyo's version,
your operating system, which tools and catalog skills ran, and the class name and code location of a crash, with a
random install ID. It never reports what you typed, your files, addresses, names, keys or error messages.

Settings > Privacy shows every event waiting to be sent, exactly as it would be sent, and lets you delete them.
Turning it off deletes them and the install ID. Crash reports go to Sentry and usage counts to PostHog, both cloud
services. If you turn it on, usage counts are sent to PostHog (US region) and crash reports to Sentry (US region). Both keep what is sent for 60 days, then delete it.

## Clearing things

- **A conversation:** delete it in the chat list.
- **What Piyo remembers about you:** Settings > Memory lists everything; you can edit, delete or clear it, and
  choose whether health, finance and identity facts may be remembered at all.
- **The task log:** Tasks lists every run; the log has keys and tokens masked before it is saved.
- **Everything:** uninstall Piyo and delete its data folder and keychain entries.
