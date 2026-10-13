# MCP servers

An MCP server is a program that gives Piyo more tools, for example to work with GitHub, a database or a note app.
Many are published with instructions like "run `npx -y some-package`". Piyo can use them, with the same approvals as
its own tools.

## Before you add one

A server runs on your computer **as you**. It can read and change anything you can, and Piyo cannot check what it
does. Add only servers from people you trust, the same as any program you install.

## Adding a server

Open **Settings > MCP servers** and fill in **Add a server** from the server's own instructions:

- **Name**: a short name you choose, like `github` (lowercase letters, digits and dashes).
- **Command** and **Arguments**: what starts it. `npx` needs Node.js installed (nodejs.org). For `uvx` or `uv`,
  Piyo uses its own copy of uv (downloaded the first time it is needed).
- **Settings**: plain `KEY=value` lines the server reads. Do not put keys or passwords here.
- **Secrets**: the names of keys or tokens it needs, like `GITHUB_TOKEN`. After adding the server, type each value
  in the box under it. Values go to your system keychain, are given only to that server, and are never shown again.

Piyo then starts the server once and lists its tools.

## Reviewing its tools

Every tool comes with a description that the model reads. Read them and press **Approve** only if they match what
you expect from that server. A description that tells the model to do something unrelated (send your mail
somewhere, ignore instructions) is a reason to remove the server.

Piyo remembers exactly what you approved. If the server later changes a tool's description or inputs, that tool is
switched off and shows **changed since you approved it** until you look at it again.

## When Piyo uses them

- A server's tools are offered to the model only while a skill that needs them is in use (its `SKILL.md` lists
  `mcp.<server>.*` or single tools). For a small server you use often, turn on **Always on** instead (up to 20 tools).
- Piyo asks before **every** call, showing the server, the tool and what it will be sent. For a tool you trust, tick
  **Run without asking**. A tool the server itself describes as destructive always asks.
- What a server sends back is treated as outside text: Piyo will not follow instructions in it, and afterwards it
  asks before sending anything to a new website or saving to memory.
- The server starts the first time a tool is used and keeps running until Piyo closes. **Server output** shows the
  end of what it printed, which helps when it does not start.

## Removing a server

Press **Remove**. Its tools disappear at once and its secrets are deleted from the keychain.

Servers on the internet (added by address, with a sign-in) are not supported yet.
