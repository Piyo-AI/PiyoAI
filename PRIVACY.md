# Privacy

*Draft, not yet reviewed by a lawyer. It describes what the code does; if the two ever differ, the code is the bug.*

Piyo AI is a desktop app. It runs on your computer, keeps its data there, and has no account system.

## What stays on your computer

Conversations, memory, the task log, skills, settings and the browser profile are stored in Piyo's data folder on
your computer. API keys and tokens are stored in your operating system's keychain, never in a file.

## What leaves your computer, because you asked for it

- **Your model provider.** Your messages, and the content of any file, email, calendar entry or web page Piyo reads
  while working on a task, are sent to the provider you picked (OpenAI, Anthropic and so on) so it can answer. That
  provider's own privacy policy applies. With a local model (Ollama) this stays on your computer, and Settings >
  Limits has an option to use local models only.
- **The tools you use.** Web searches go to the search provider you set up, pages Piyo opens go to those websites,
  and Google features talk to Google using your own OAuth client.
- **The skill catalog and updates.** Browsing the catalog and installing a skill downloads files from GitHub;
  checking for an update contacts GitHub. Neither sends your data. GitHub sees your IP address as any website does.
- **Installing from a Git address.** If you paste an address on GitHub, GitLab.com or Codeberg, Piyo contacts that one
  site to find the commit and download it. If you saved an access token for a private repository, it is kept in your
  keychain and sent only to that site, only for those downloads. Nothing else about you is sent.
- **Script runtimes.** The first time a skill script needs `uv` (Python) or Deno (JavaScript and TypeScript), Piyo downloads
  that program from its official GitHub release (about 20 and 45 MB), and only runs it after the file matches a fingerprint
  built into Piyo. The approval card for that script tells you the first run will download it. Nothing about you is sent.

Piyo asks before it sends, buys, deletes or submits anything, and never reads keys, passwords or protected folders.

## Anonymous usage and crash reports (optional, off by default)

Piyo asks once, at first run, whether you want to share anonymous usage counts and crash reports. The answer is
yes only if you say yes. You can change it any time in **Settings > Privacy**, which also shows exactly what would
be sent.

If you say yes, Piyo can report:

- Piyo's version, your operating system (Windows, macOS or Linux) and processor type (x64 or arm64);
- when a task finishes: how it ended (done, cancelled, error and so on), the names of Piyo's own tools that ran,
  and the ids of built-in or catalog skills that were loaded;
- when something crashes: the error's class name (for example `ValueError`) and a stack trace made of
  `file:line in function` entries for Piyo's own code and libraries;
- the day an event happened (not the time), and a random install ID that is not linked to you, your computer or any
  account.

Piyo never reports your messages, files, email or chat content, web addresses, file names or folder names, keys,
account names or email addresses, the names of skills you wrote yourself, or the text of error messages (which can
contain any of those). Stack traces contain no local variables, source code or paths from your computer.

How this is enforced: all of it is in one file, [`core/piyo/telemetry.py`](core/piyo/telemetry.py). Each kind of
event has a fixed list of fields and each field accepts only a short, fixed shape (a choice from a list, or a plain
identifier); an event that does not fit is discarded. There is no field that can hold free text.

Events wait in a file on your computer until they are sent. Turning reporting off deletes that file and the install
ID.

**Who receives it.** Crash reports go to **Sentry** (sentry.io, US region) and usage counts to **PostHog Cloud**
(posthog.com, US region). Both are services run by those companies, so what you opt in to share is processed and stored by
them under their own privacy policies, in addition to this one. Piyo sends only the fields above, built by hand
from the queued events with no Sentry or PostHog library running in the app, and asks both to keep no IP address
or location: events are anonymous, with no person profile. Their servers still see your IP address while the
request is made, which is true of any website; both projects are set to discard it rather than store it.
Settings > Privacy shows the exact requests that would be sent to each.

**Current status:** if you opt in, usage counts go to PostHog Cloud (US region) and crash reports go to Sentry (US
region). Both services keep what Piyo sends for
**60 days**, then delete it. If that period changes, it will be changed here and named in the release notes.

## Your choices

- Turn reporting on or off in Settings > Privacy at any time; off deletes the install ID and waiting events.
- Delete waiting events without turning reporting off, in the same place.
- Delete conversations, memory and the task log inside the app, or delete Piyo's data folder to remove everything.

## Questions

Open an issue at https://github.com/Piyo-AI/PiyoAI/issues.
