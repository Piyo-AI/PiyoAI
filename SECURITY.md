# Security policy

Piyo AI can read your files and, later, your email and chats, so security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead: the **Security** tab of this
repository, then **Report a vulnerability**. Include what you found, how to reproduce it, and the version or commit.

We aim to acknowledge a report within a few days and to tell you what we plan to do about it. Piyo is pre-alpha, so
there is no bug bounty and no supported release yet, but we will credit you if you want.

## In scope

- Anything that lets text from outside (a web page, an email, a file, a skill) make Piyo act without the user's
  approval, or that bypasses the permission gate.
- Leaking API keys, tokens or personal data (to logs, the task log, the network, or another process).
- The local API (`127.0.0.1`, per-launch token) being reachable or usable by something it should not be.
- A skill gaining more access than it declares.

## Out of scope

- Problems that need an attacker who already controls your computer or your OS account.
- Behaviour of the models you connect (hallucinations, refusals) that does not bypass a safety control.

Known hardening work we are deferring is tracked publicly in the project plan.
