# Skills

A skill is a saved procedure. Piyo sees each skill's name and description, and when your request matches one it
loads the full instructions and gets exactly the tools that skill declared, nothing more. A skill never makes a
risky action safe: sending, deleting, buying and submitting always ask you.

## What comes with Piyo

| Skill | Does | Needs |
|---|---|---|
| Plan my day | Turns your tasks and appointments into a timed plan | Nothing |
| Web reader | Reads a public page you point to | Nothing |
| Web research | Searches the web and answers with sources | A Brave Search key (Settings > Web search) |
| Browse the web | Opens a page in Piyo's own browser, for sites that need one | A one-time browser download |
| Parcel tracking | Looks up a tracking number on the carrier's site | The browser download |
| Price compare | Compares a product across shops you name | The browser download |
| Downloads organizer | Sorts a folder into sub-folders by type | An approved folder |
| Gmail triage | Summarises unread mail, drafts replies | Your own Google connection |
| Calendar | Shows your agenda and free time, creates events | Your own Google connection |
| Morning brief | Calendar, unread mail and weather in one summary | Your own Google connection |

Open **Settings > Skills**. Each skill has a switch, shows the tools it uses, and has a **Setup** button when it
needs something from you. The setup guide is step by step and ends with something to try.

## Connecting Google

Gmail, Calendar and the morning brief use a Google connection that you create yourself in Google Cloud, so no
one else's account is involved. The guide under Gmail triage > Setup walks through it and tests the connection.
Piyo asks for only the access you tick: read, drafts, send, calendar.

## Installing a skill

Settings > Skills has three ways in (the buttons are **Browse skills**, **From GitHub** and **Install a skill from a file**):

- **Browse skills**: the catalog of reviewed skills. Each shows who made it, a badge (Official, Verified
  publisher or Community), what it can use as chips, and what it needs. The catalog is signed and each download is
  checked against its fingerprint, so a changed file is refused.
- **From GitHub**: paste a repository address. These skills are marked **unverified**.
- **Install a skill from a file**: a `.piyoskill` file or a zip. Also **unverified**.

Before anything is installed you see a review card: the tools the skill wants, any scripts it will run, any keys
it needs, and where it came from. Nothing is installed until you approve. An update that adds permissions asks
again, showing only what is new.

**Scripts.** Some skills include small programs. Python ones run in their own isolated environment with no
network unless declared; TypeScript ones run in a sandbox that allows only the folder and hosts declared. Python
has no real sandbox, so Piyo asks you before every run of a script from a skill you installed.

## Updates and withdrawn skills

When Piyo starts and when you close Settings, it checks the catalog. A skill with a newer version is listed with
any new permissions. If the catalog withdraws a version (a bug or a safety problem), Piyo switches that skill off
once and shows why; you can switch it back on or uninstall it. Skills you installed from a file or a GitHub address
are never changed by the catalog.

## Writing your own

- **Settings > Skills > Write a skill** opens an editor with live checks. You can edit a skill's text and its setup
  guide, and every save keeps the previous version so you can roll back.
- **Learned skills.** After a chat in which Piyo used several tools, a banner offers to save it as a skill. The
  draft has your keys, emails, phone numbers and folders removed, and opens in the editor for you to review. It
  stays off until you turn it on. If you correct a skill, Piyo can offer to improve it.
- **Test it** on any installed skill opens a chat for that skill, so you can see which tools it calls in Tasks.

To build something to share, see the [skill authoring guide](../skill-authoring.md).

## Removing a skill

Settings > Skills > **Uninstall**. The old version is kept in a backup folder inside Piyo's data folder (the most recent 30 backups are kept). Keys you saved for the skill are removed.
