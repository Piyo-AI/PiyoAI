# MCP servers

An MCP server is a program that gives Piyo more tools, for example to work with GitHub, a database or a note app.
Many are published with instructions like "run `npx -y some-package`". Piyo can use them, with the same approvals as
its own tools.

## Before you add one

A server runs on your computer. One started with `npx` runs in Piyo's sandbox and reaches only what you allow;
any other (`uvx`, or a program you name) runs **as you** and can read and change anything you can. Piyo cannot check
what a server does. Add only servers from people you trust, the same as any program you install.

## Adding a server

Open **Settings > MCP servers** and fill in **Add a server** from the server's own instructions:

- **Name**: a short name you choose, like `github` (lowercase letters, digits and dashes).
- **Command** and **Arguments**: what starts it. For `npx`, Piyo runs the server in its own sandbox (Deno,
  downloaded the first time it is needed), so you do not need Node.js. For `uvx` or `uv`, Piyo uses its own copy of
  uv; those servers run without a sandbox.
- **Settings**: plain `KEY=value` lines the server reads. Do not put keys or passwords here.
- **Secrets**: the names of keys or tokens it needs, like `GITHUB_TOKEN`. After adding the server, type each value
  in the box under it. Values go to your system keychain, are given only to that server, and are never shown again.

Piyo then starts the server once and lists its tools.

## The sandbox for npx servers

An `npx` server can only use its own folder until you allow more. Under **Sites it may reach**, **Folders it may
read** and **Folders it may change** you add what it needs (the server's instructions usually say; a file server
needs the folders it serves). When the server tries something it may not, the page shows what it was refused with an
**Allow** button. It is never allowed to start other programs.

The first time it starts, Piyo notes the package's version and keeps using it, so the code you checked is the code
that runs. To move to a newer version, change its arguments (for example `some-package@2.1.0`) and review its tools
again.

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
