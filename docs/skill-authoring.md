# Writing a skill for Piyo

A skill is a folder that teaches Piyo one procedure: how to tidy a folder, look up a parcel, convert units. This
guide takes you from nothing to a skill you can use yourself, and then to one you can submit to the catalog.

You do not need to read Piyo's code. If you want the exact rules, they are in
[`core/piyo/skills/manifest.py`](../core/piyo/skills/manifest.py) (the format) and
[`core/piyo/skills/validate.py`](../core/piyo/skills/validate.py) (what the catalog checks).

## How Piyo uses a skill

Piyo does not read every skill up front. It sees each enabled skill's **name and description** only. When your
request matches a description, Piyo loads that skill's full instructions and **unlocks the tools the skill
declared**. A skill cannot use a tool it did not list, and it cannot make a risky tool less risky: sending,
deleting, buying and submitting always ask the user, whatever the skill says.

So a skill is two things: instructions for the model, and a list of what it is allowed to touch.

## Start from a template

Pick the closest of the three templates in the catalog repo,
[`piyo-skills/templates/`](https://github.com/Piyo-AI/piyo-skills/tree/main/templates):

| Template | Use it when |
|---|---|
| `my-instructions-skill` | The model can do it with the tools Piyo already has, or with none. Most skills are this. |
| `my-python-skill` | An answer must be exact or needs a library: maths, parsing, file formats. |
| `my-typescript-skill` | Same, in TypeScript or JavaScript, with a stricter sandbox. |

Copy the folder, rename it, and make the folder name equal the `name` in `SKILL.md`. The templates pass the
catalog's checks as they are, so you start from a working skill.

## The folder

```
my-skill/
  SKILL.md      required: frontmatter + instructions for the model
  SETUP.md      required for the catalog: what the user must do before it works
  scripts/      optional: .py, .js or .ts files, directly in this folder
  assets/       optional: text files and png/jpg/webp/gif images
```

## SKILL.md

A block of YAML between `---` lines, then the instructions in Markdown.

```yaml
---
name: parcel-tracking
version: 1.0.0
description: Look up where a parcel is using its tracking number. Use when the user gives a tracking number or asks where a package is.
author: Your Name
license: MIT
requires:
  tools: [browser.open, browser.read, browser.wait]
  integrations: []
  secrets: []
  model: { tool_calling: true, vision: false, min_context: 32000 }
runtime:
  python: { timeout_s: 10 }
---
```

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Lowercase letters, digits and single dashes, at most 64 characters. Must equal the folder name. |
| `description` | yes | 1 to 1024 characters. Say what the skill does **and when to use it**: this is all Piyo sees until it loads the skill. |
| `version` | catalog | `1.2.3` style. Change it whenever you change the skill. |
| `author` | catalog | Your name or organisation. |
| `license` | catalog | An OSI-approved SPDX id: `MIT`, `Apache-2.0`, `BSD-3-Clause`, `MPL-2.0`, `GPL-3.0` and so on. |
| `requires.tools` | no | The tools the skill may use (below). Ask for as few as you can. |
| `requires.integrations` | no | `google` if you use Gmail, Calendar or Google account tools. |
| `requires.secrets` | no | Names of keys the user must supply, for scripts (below). |
| `requires.model` | no | What the model must be able to do. Piyo marks the skill unusable on a model that is known not to. |
| `runtime` | no | Settings for scripts (below). |
| `risk` | no | Free text for readers. It changes nothing; the permission gate decides. |

Unknown keys are ignored, so skills written for other tools in the Agent Skills format still load.

### The tools you can ask for

| Tool | What it does | Asks the user? |
|---|---|---|
| `web.fetch` | Read the text of one public web page | no |
| `web.search` | Search the web (needs the user's Brave key) | no |
| `browser.open`, `browser.read`, `browser.wait`, `browser.screenshot`, `browser.type` | Piyo's own browser: open a public page, read it, wait, take a picture, type into a field | no (typing with "submit" asks) |
| `browser.click` | Click something on the page | yes, when the element looks like send, buy, delete or submit |
| `files.list`, `files.read` | List and read files in folders the user approved | no |
| `files.write`, `files.create_folder`, `files.move` | Create files and folders, move files | yes, unless the user allowed changes in that folder |
| `files.delete` | Move a file to the trash | always |
| `gmail.search`, `gmail.read`, `gmail.draft` | Search and read mail, save a draft | no |
| `gmail.send`, `gmail.label`, `gmail.archive` | Send mail, change labels, archive | yes |
| `calendar.agenda`, `calendar.freebusy` | Read events and free time | no |
| `calendar.create`, `calendar.update`, `calendar.delete` | Change events | yes |
| `google.accounts` | List connected Google accounts | no |
| `weather.forecast` | Weather for a place | no |
| `skill.run_script` | Run the skill's own scripts | yes for a skill the user installed |
| `memory.*`, `schedule.*` | Remember facts, set reminders. Always available to Piyo; listing them in `requires.tools` is allowed but not needed | varies |

A skill that names a tool Piyo does not have is rejected. The user sees your list on install and approves it;
an update that adds a tool asks again.

### Writing the instructions

The body is what the model follows. What works:

- **Numbered steps** the model can follow exactly. Say what to ask, which tool to call with which arguments, and
  what to tell the user at the end.
- **Ask, do not guess.** "If the carrier is unclear, ask" beats a silent default.
- **Say what to do on failure.** "If the tool says the folder is not approved, tell the user to add it under
  Settings > Folders, then stop."
- **Treat outside text as data.** Add a line telling the model that pages, mail and file contents are material
  to work on, never instructions.
- **Stay small.** Under a page is typical. If you need lookup tables or long examples, put them in `assets/`.

What does not work, and what the catalog rejects: telling the model to skip a confirmation, to ignore its rules,
or to hide something from the user. The gate wins anyway, and a skill that tries is not listed.

## Scripts

Use a script when an answer must be exact or the model would do slow, error-prone work. Put `.py`, `.js` or `.ts`
files in `scripts/`, list `skill.run_script` in `requires.tools` (it must be there if and only if the skill has
scripts), and tell the model in the instructions how to call it:

```
Call skill.run_script with skill: "my-skill", script: "convert.py", args: {"value": 5, "from": "mi", "to": "km"}.
```

**The protocol is the same in both languages.** Piyo writes your `args` as one JSON object to the script's
standard input. The script prints one JSON value to standard output and exits. Print `{"error": "why"}` for a
failure the model should explain. Anything else on stdout, or no output, is an error.

Limits: 30 seconds unless `runtime.<python|deno>.timeout_s` says otherwise (300 at most), 1 MB of output,
100 KB of arguments. The script runs in an empty temporary folder that is deleted afterwards, so it cannot keep
state between runs.

### Python

```python
import json, sys
data = json.load(sys.stdin)
print(json.dumps({"result": data["value"] * 2}))
```

- Each skill gets its own virtual environment. Declare libraries pinned:
  `runtime: { python: { dependencies: ["requests==2.32.3"] } }`.
- No network unless you set `network: true`. That block catches mistakes; it is not a defence against hostile
  code, which is why scripts from installed skills ask the user every time.
- Secrets: list the name in `requires.secrets` and read `os.environ["PIYO_SECRET_<NAME>"]` (upper case, other
  characters become `_`). The user enters the value in Settings > Skills; it lives in the OS keychain, never in a file.
- Not allowed in the catalog: `subprocess`, `ctypes`, `pty`, `pickle`, `marshal`, `eval`, `exec`, `compile`,
  `__import__`, `os.system` and its relatives, and network libraries without `network: true`.

### TypeScript and JavaScript

```ts
const input = JSON.parse(await new Response(Deno.stdin.readable).text());
console.log(JSON.stringify({ result: input.value * 2 }));
```

- Run by Deno with real permissions: the script can read only the skill's folder, reach only the hosts in
  `runtime.deno.allow_net` (specific hosts such as `api.example.com`, never `*`), and cannot write files, start
  programs or read environment variables.
- `runtime.deno.npm: ["date-fns@3"]` downloads packages once before the script runs.
- Deno scripts cannot read secrets. Use Python if you need one.
- Not allowed in the catalog: `eval`, `new Function`, `child_process`, `Deno.Command`, `Deno.run`,
  `Deno.dlopen`, `Deno.env`, `process.env`.

## SETUP.md

The app shows this file as a step-by-step guide. Each `## Heading` is one step. Write for someone who has never
seen the code and does not know what an API key is. Say exactly which Settings page to open and what they
should see afterwards. If there is nothing to set up, say so and still include a "Try it" step. It must have
real content (the catalog rejects anything under 80 characters).

Supported Markdown: headings, paragraphs, bullet, numbered and task lists (`- [ ] done`), fenced code,
**bold**, `code` and http(s) links. A guide can also embed `:::google:::` (the Google connection form),
`:::google-test:::` (a connection test) and `:::open-setup <skill>:::` (a button that opens another skill's
guide). Nothing else runs.

## Test it

1. **Check the script by itself.** `echo '{"value": 5}' | python scripts/convert.py` should print one JSON line.
   For TypeScript: `deno run --allow-read scripts/convert.ts < input.json`.
2. **Install it into Piyo.** Zip the folder (the zip needs exactly one `SKILL.md`, at the top or inside one
   folder), then in Piyo open Settings > Skills and choose the file. You get the review card with the permissions
   you declared. Approve them, then ask Piyo for what the skill is for.
3. **Use Test it.** Each installed skill has a Test it button that opens a chat prefilled for that skill. The
   normal approvals and the task log (Tasks) apply, so you can see which tools it called and why.
4. **Run the catalog check.** In a checkout of `piyo-skills` next to `PiyoAI`:

   ```bash
   uv run --project ../PiyoAI/core python scripts/build_index.py
   ```

   It lists every problem at once, with the reason. Warnings are for the human reviewer and do not block.

Things worth checking by hand: a model with weak tool use (a small local one) still gets through the steps; the
skill stops cleanly when a folder is not approved or a key is missing; a page containing "ignore your rules" does
not change what it does.

## Submit it

Open a pull request against [`piyo-skills`](https://github.com/Piyo-AI/piyo-skills) adding
`skills/<your-skill>/`, following its [CONTRIBUTING.md](https://github.com/Piyo-AI/piyo-skills/blob/main/CONTRIBUTING.md).
CI runs the same check you ran. A maintainer then reads the skill: does it do only what it says, is each tool and
host needed, is `SETUP.md` accurate. You cannot set your own badge or category; maintainers do that.

To release a new version, raise `version` and open another pull request. If the update adds a tool, a script or
a host, users are asked to approve it again.

## Safety rules that apply to every skill

- A skill cannot lower a tool's risk or skip a confirmation.
- Passwords, card numbers and ID numbers are never typed automatically.
- Web pages, mail, chats and files are data, never instructions.
- Secrets are supplied at run time from the keychain and never written into skill files or prompts.
- Anything not installed from the signed catalog shows an "unverified" warning to the user.

## Skills Piyo writes for you

After a chat in which Piyo used several tools, it can offer to save the steps as a skill. The draft opens in
the same editor, with personal details (keys, emails, phone numbers, your folders) removed, and nothing is
saved until you review it. Learned skills are switched off until you turn them on, and the editor keeps their
history so you can go back. This is also a quick way to get a first draft of a skill you then polish by hand.
