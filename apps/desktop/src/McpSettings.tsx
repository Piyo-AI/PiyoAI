import { FormEvent, useState } from "react";

import { api, McpServer, McpTool } from "./api";
import { lines, parseEnv, useMcpServers } from "./useMcpServers";

const STATE: Record<McpServer["state"], string> = {
  stopped: "Not running (starts when a tool is used)",
  starting: "Starting",
  running: "Running",
  failed: "Failed",
};

/** Settings > MCP servers: add the programs whose tools Piyo may use, review those tools, set secrets. */
export function McpSettings() {
  const { servers, error, busy, attempt } = useMcpServers();

  return (
    <section className="folders">
      <h3>MCP servers</h3>
      <p className="hint">
        An MCP server is a program that gives Piyo more tools. Piyo shows you every tool it offers before the model may
        use it, asks before each call unless you say otherwise, and switches a tool off if the server later changes it.
        A skill that needs a server lists it; the server's tools are only offered while such a skill is in use, unless
        you turn on "Always on".
      </p>
      <p className="hint warning-text">
        <strong>Unverified:</strong> a server runs on your computer as you and can do anything you can. Piyo cannot check
        it. Add only servers you trust.
      </p>
      {error && <p className="error">{error}</p>}
      {servers && servers.length === 0 && <p className="hint">No servers yet.</p>}
      <ul className="providers">
        {servers?.map((s) => (
          <ServerRow key={s.name} server={s} attempt={attempt} checking={busy === s.name} />
        ))}
      </ul>
      <AddServer attempt={attempt} />
    </section>
  );
}

type Attempt = (fn: () => Promise<unknown>, server?: string) => Promise<boolean>;

function ServerRow({ server, attempt, checking }: { server: McpServer; attempt: Attempt; checking: boolean }) {
  const pending = server.tools.filter((t) => t.status !== "approved");
  const approved = server.tools.filter((t) => t.status === "approved");
  const command = [server.command, ...server.args].join(" ");

  return (
    <li className="mcp-server">
      <div className="row-head">
        <strong>{server.name}</strong>
        <span className="hint skill-version">{STATE[server.state]}</span>
        <label className="check" style={{ marginLeft: "auto" }}>
          <input
            type="checkbox"
            checked={server.enabled}
            onChange={(e) => attempt(() => api.editMcpServer(server.name, { enabled: e.target.checked }))}
          />
          On
        </label>
        <label className="check" title="Offer its tools in every chat, not only through a skill (small servers only)">
          <input
            type="checkbox"
            checked={server.always_on}
            onChange={(e) => attempt(() => api.editMcpServer(server.name, { always_on: e.target.checked }))}
          />
          Always on
        </label>
      </div>
      <p className="hint">
        Runs: <code>{command}</code>
        {Object.keys(server.env).length > 0 && (
          <>
            {" "}
            with {Object.entries(server.env).map(([k, v]) => `${k}=${v}`).join(", ")}
          </>
        )}
      </p>
      {server.error && <p className="error">{server.error}</p>}

      {server.secrets.map((s) => (
        <SecretRow key={s.name} server={server.name} name={s.name} isSet={s.set} attempt={attempt} />
      ))}

      {pending.length > 0 && (
        <div className="mcp-review">
          <p>
            <strong>Review {pending.length === 1 ? "this tool" : `these ${pending.length} tools`}.</strong> The model
            sees each description; approve only what you expect from this server.
          </p>
          <ul>
            {pending.map((t) => (
              <ToolLine key={t.name} tool={t} />
            ))}
          </ul>
          <button
            type="button"
            onClick={() =>
              attempt(() => api.approveMcpTools(server.name, Object.fromEntries(pending.map((t) => [t.name, t.hash]))))
            }
          >
            Approve {pending.length === 1 ? "it" : "all"}
          </button>
        </div>
      )}

      {approved.length > 0 && (
        <details>
          <summary className="hint">{approved.length} approved tools</summary>
          <ul>
            {approved.map((t) => (
              <ToolLine key={t.name} tool={t}>
                <label
                  className="check"
                  title={t.destructive ? "The server says this tool destroys things, so it always asks" : undefined}
                >
                  <input
                    type="checkbox"
                    checked={t.auto}
                    disabled={t.destructive}
                    onChange={(e) => attempt(() => api.setMcpToolAuto(server.name, t.name, e.target.checked))}
                  />
                  Run without asking
                </label>
              </ToolLine>
            ))}
          </ul>
        </details>
      )}

      {server.log.length > 0 && (
        <details>
          <summary className="hint">Server output</summary>
          <pre className="mcp-log">{server.log.join("\n")}</pre>
        </details>
      )}

      <p className="install-actions">
        <button type="button" className="ghost" disabled={checking} onClick={() => attempt(() => api.testMcpServer(server.name), server.name)}>
          {checking ? "Checking..." : "Check its tools"}
        </button>
        <button
          type="button"
          className="ghost"
          onClick={() =>
            window.confirm(`Remove ${server.name}? Its secrets are deleted too.`) &&
            attempt(() => api.removeMcpServer(server.name))
          }
        >
          Remove
        </button>
      </p>
    </li>
  );
}

function ToolLine({ tool, children }: { tool: McpTool; children?: React.ReactNode }) {
  return (
    <li className="mcp-tool">
      <div className="row-head">
        <code>{tool.name}</code>
        {tool.status === "new" && <span className="pill">new</span>}
        {tool.status === "changed" && <span className="pill unverified">changed since you approved it</span>}
        {tool.destructive && <span className="pill unverified">says it destroys things</span>}
        {tool.read_only && <span className="pill">says it only reads</span>}
        {children && <span style={{ marginLeft: "auto" }}>{children}</span>}
      </div>
      <p className="hint">{tool.description || "(no description)"}</p>
      <p className="hint">
        In a skill: <code>{tool.tool}</code>
      </p>
    </li>
  );
}

function SecretRow({ server, name, isSet, attempt }: { server: string; name: string; isSet: boolean; attempt: Attempt }) {
  const [value, setValue] = useState("");
  return (
    <form
      className="memory-add"
      onSubmit={async (e) => {
        e.preventDefault();
        if (await attempt(() => api.setMcpSecret(server, name, value))) setValue("");
      }}
    >
      <span className="hint">
        {name}: {isSet ? "set" : "not set"}
      </span>
      <input
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={isSet ? "Replace the value" : "Value (kept in your keychain)"}
        autoComplete="off"
      />
      <button type="submit" disabled={!value.trim()}>
        Save
      </button>
      {isSet && (
        <button type="button" className="ghost" onClick={() => attempt(() => api.deleteMcpSecret(server, name))}>
          Clear
        </button>
      )}
    </form>
  );
}

function AddServer({ attempt }: { attempt: Attempt }) {
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [env, setEnv] = useState("");
  const [secrets, setSecrets] = useState("");

  const add = async (e: FormEvent) => {
    e.preventDefault();
    const server = { name: name.trim(), command: command.trim(), args: lines(args), env: parseEnv(env), secrets: lines(secrets) };
    if (await attempt(() => api.addMcpServer(server))) {
      setName("");
      setCommand("");
      setArgs("");
      setEnv("");
      setSecrets("");
      await attempt(() => api.testMcpServer(server.name), server.name); // list its tools for review right away
    }
  };

  return (
    <form className="schedule-form" onSubmit={add}>
      <h4>Add a server</h4>
      <p className="hint">
        Copy these from the server's instructions. For example the command <code>npx</code> with the arguments{" "}
        <code>-y</code> and a package name (needs Node.js), or <code>uvx</code> and a package name (Piyo brings uv).
      </p>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. github" maxLength={32} />
      <input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="Command, e.g. npx" />
      <textarea value={args} onChange={(e) => setArgs(e.target.value)} rows={3} placeholder="Arguments, one per line" />
      <textarea
        value={env}
        onChange={(e) => setEnv(e.target.value)}
        rows={2}
        placeholder="Settings, one KEY=value per line (not for keys or passwords)"
      />
      <textarea
        value={secrets}
        onChange={(e) => setSecrets(e.target.value)}
        rows={2}
        placeholder="Names of secrets it needs, one per line, e.g. GITHUB_TOKEN (you enter the values after adding it)"
      />
      <button type="submit" disabled={!name.trim() || !command.trim()}>
        Add and check its tools
      </button>
    </form>
  );
}
