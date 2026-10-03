import { FormEvent, useEffect, useState } from "react";

import { api, ApiStyle, FolderEntry, Provider, RunSettings, SkillInfo } from "./api";

interface Props {
  providers: Provider[];
  onChanged: () => void;
  onClose: () => void;
}

type Page = "providers" | "add-provider" | "web-search" | "folders" | "skills" | "limits";

export function Settings({ providers, onChanged, onClose }: Props) {
  const [page, setPage] = useState<Page>("providers");
  const [error, setError] = useState<string | null>(null);

  const go = (p: Page) => {
    setError(null);
    setPage(p);
  };

  /** Runs a change and reports whether it worked, so a page can move on after a success. */
  const run = async (fn: () => Promise<unknown>): Promise<boolean> => {
    setError(null);
    try {
      await fn();
      onChanged();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  };

  const inProviders = page === "providers" || page === "add-provider";

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog settings" role="dialog" aria-label="Settings" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>Settings</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="settings-body">
          <nav className="settings-nav" aria-label="Settings sections">
            <button className={`nav-item ${inProviders ? "active" : ""}`} onClick={() => go("providers")}>
              Providers
            </button>
            {inProviders && (
              <div className="nav-sub">
                <button className={`nav-item ${page === "providers" ? "active" : ""}`} onClick={() => go("providers")}>
                  Provider list
                </button>
                <button
                  className={`nav-item ${page === "add-provider" ? "active" : ""}`}
                  onClick={() => go("add-provider")}
                >
                  Add provider
                </button>
              </div>
            )}
            <button className={`nav-item ${page === "web-search" ? "active" : ""}`} onClick={() => go("web-search")}>
              Web search
            </button>
            <button className={`nav-item ${page === "folders" ? "active" : ""}`} onClick={() => go("folders")}>
              Folders
            </button>
            <button className={`nav-item ${page === "skills" ? "active" : ""}`} onClick={() => go("skills")}>
              Skills
            </button>
            <button className={`nav-item ${page === "limits" ? "active" : ""}`} onClick={() => go("limits")}>
              Limits
            </button>
          </nav>
          <div className="settings-content">
            {page === "providers" && (
              <>
                <h3>Model providers</h3>
                <p className="hint">
                  API keys are stored in your operating system's keychain, never in a file. Local providers need no
                  key.
                </p>
                {error && <p className="error">{error}</p>}
                <ul className="providers">
                  {providers.map((p) => (
                    <ProviderRow key={p.id} provider={p} run={run} />
                  ))}
                </ul>
              </>
            )}
            {page === "add-provider" && (
              <>
                {error && <p className="error">{error}</p>}
                <AddProvider run={run} onAdded={() => go("providers")} />
              </>
            )}
            {page === "web-search" && <WebSearch />}
            {page === "folders" && <Folders />}
            {page === "skills" && <Skills />}
            {page === "limits" && <Limits />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Skills() {
  const [skills, setSkills] = useState<SkillInfo[] | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    api
      .skills()
      .then((r) => {
        setSkills(r.skills);
        setErrors(r.errors);
      })
      .catch((e) => setError((e as Error).message));
  useEffect(() => {
    load();
  }, []);

  const toggle = async (name: string, enabled: boolean) => {
    setError(null);
    try {
      await api.setSkillEnabled(name, enabled);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const needs = (s: SkillInfo) => [
    s.model_needs.vision ? "a model that can read images" : "",
    s.model_needs.min_context ? `at least ${s.model_needs.min_context} tokens of context` : "",
  ].filter(Boolean);

  return (
    <section className="folders">
      <h3>Skills</h3>
      <p className="hint">
        Skills are procedures Piyo can follow. A skill can only use the tools listed here, and every risky action still
        asks you first. Switching one off hides it from Piyo; it is remembered after a restart.
      </p>
      {error && <p className="error">{error}</p>}
      {skills && skills.length === 0 && <p className="hint">No skills found.</p>}
      <ul className="providers">
        {skills?.map((s) => (
          <li key={s.name}>
            <div className="row-head">
              <strong>
                {s.name}
                <sup className={`pill ${s.source}`}>{s.source === "builtin" ? "Built in" : "Yours"}</sup>
              </strong>
              <span className="hint skill-version">v{s.version}{s.author ? ` · ${s.author}` : ""}</span>
              <label className="check" style={{ marginLeft: "auto" }}>
                <input type="checkbox" checked={s.enabled} onChange={(e) => toggle(s.name, e.target.checked)} />
                On
              </label>
            </div>
            <p className="hint">{s.description}</p>
            <p className="hint">Tools: {s.tools.length ? s.tools.join(", ") : "none"}</p>
            {s.integrations.length > 0 && <p className="hint">Integrations: {s.integrations.join(", ")}</p>}
            {s.secrets.length > 0 && <p className="hint">Needs secrets: {s.secrets.join(", ")}</p>}
            {needs(s).length > 0 && <p className="hint">Needs {needs(s).join(" and ")}.</p>}
            {s.has_setup && <p className="hint">Has setup steps.</p>}
          </li>
        ))}
      </ul>
      {Object.keys(errors).length > 0 && (
        <>
          <h3>Skills that did not load</h3>
          <ul className="providers">
            {Object.entries(errors).map(([folder, why]) => (
              <li key={folder}>
                <strong>{folder}</strong>
                <p className="error">{why}</p>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function Limits() {
  const [saved, setSaved] = useState<RunSettings | null>(null);
  const [steps, setSteps] = useState("");
  const [tokens, setTokens] = useState("");
  const [minutes, setMinutes] = useState("");
  const [error, setError] = useState<string | null>(null);

  const show = (s: RunSettings) => {
    setSaved(s);
    setSteps(String(s.max_steps));
    setTokens(String(s.max_tokens));
    setMinutes(String(+(s.timeout_s / 60).toFixed(2)));
  };
  useEffect(() => {
    api
      .runSettings()
      .then(show)
      .catch((e) => setError((e as Error).message));
  }, []);

  const save = async (changes: Partial<RunSettings>) => {
    setError(null);
    try {
      show(await api.setRunSettings(changes));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const dirty =
    saved != null &&
    (Number(steps) !== saved.max_steps ||
      Number(tokens) !== saved.max_tokens ||
      Number(minutes) * 60 !== saved.timeout_s);

  return (
    <section className="folders">
      <h3>Limits for one request</h3>
      <p className="hint">
        A request stops cleanly when it reaches any of these. Time spent waiting for your approval does not count.
      </p>
      {error && <p className="error">{error}</p>}
      {saved && (
        <>
          <form
            className="limits"
            onSubmit={(e) => {
              e.preventDefault();
              save({ max_steps: Number(steps), max_tokens: Number(tokens), timeout_s: Math.round(Number(minutes) * 60) });
            }}
          >
            <label>
              Steps (model turns)
              <input type="number" min={1} max={200} value={steps} onChange={(e) => setSteps(e.target.value)} />
            </label>
            <label>
              Tokens (input + output)
              <input type="number" min={1000} step={1000} value={tokens} onChange={(e) => setTokens(e.target.value)} />
            </label>
            <label>
              Time (minutes)
              <input type="number" min={1} value={minutes} onChange={(e) => setMinutes(e.target.value)} />
            </label>
            <button type="submit" disabled={!dirty}>
              Save
            </button>
          </form>
          <h3>Local only</h3>
          <label className="check">
            <input
              type="checkbox"
              checked={saved.local_only}
              onChange={(e) => save({ local_only: e.target.checked })}
            />
            Only use models that run on this computer
          </label>
          <p className="hint">
            With this on, cloud providers are refused, so nothing you type or any file Piyo reads is sent to one.
          </p>
        </>
      )}
    </section>
  );
}

function WebSearch() {
  const [status, setStatus] = useState<{ has_key: boolean; docs_url: string } | null>(null);
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    api
      .searchStatus()
      .then(setStatus)
      .catch((e) => setError((e as Error).message));
  useEffect(() => {
    load();
  }, []);

  const change = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      setKey("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section className="folders">
      <div className="row-head">
        <h3>Web search (Brave)</h3>
        <span className={status?.has_key ? "badge ok" : "badge"}>{status?.has_key ? "key saved" : "no key"}</span>
      </div>
      <p className="hint">
        Lets Piyo search the web. Your search words are sent to Brave. The key is stored in your operating system's
        keychain.
      </p>
      {error && <p className="error">{error}</p>}
      <form
        className="inline"
        onSubmit={(e) => {
          e.preventDefault();
          if (key.trim()) change(() => api.setSearchKey(key.trim()));
        }}
      >
        <input
          type="password"
          autoComplete="off"
          placeholder={status?.has_key ? "Replace Brave Search key" : "Paste Brave Search key"}
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button type="submit" disabled={!key.trim()}>
          Save
        </button>
        {status?.has_key && (
          <button type="button" className="ghost" onClick={() => change(() => api.deleteSearchKey())}>
            Remove key
          </button>
        )}
        {status && (
          <a href={status.docs_url} target="_blank" rel="noreferrer">
            Get a key
          </a>
        )}
      </form>
    </section>
  );
}

function Folders() {
  const [folders, setFolders] = useState<FolderEntry[]>([]);
  const [path, setPath] = useState("");
  const [autoNew, setAutoNew] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .folders()
      .then((r) => setFolders(r.folders))
      .catch((e) => setError((e as Error).message));
  }, []);

  const save = async (next: FolderEntry[]) => {
    setError(null);
    try {
      setFolders((await api.setFolders(next)).folders);
      setPath("");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section className="folders">
      <h3>Folders Piyo may use</h3>
      <p className="hint">
        Piyo can only read or change files inside these folders. Reading is always allowed. Deleting and
        replacing existing files always ask you first. For everything else, choose per folder whether Piyo may go
        ahead or must ask each time.
      </p>
      {error && <p className="error">{error}</p>}
      <ul className="providers">
        {folders.map((f) => (
          <li key={f.path}>
            <div className="row-head">
              <span>{f.path}</span>
              <button className="ghost danger" onClick={() => save(folders.filter((x) => x.path !== f.path))}>
                Remove
              </button>
            </div>
            <label className="check">
              <input
                type="checkbox"
                checked={f.auto_changes}
                onChange={(e) =>
                  save(folders.map((x) => (x.path === f.path ? { ...x, auto_changes: e.target.checked } : x)))
                }
              />
              Create folders and move or add files here without asking
            </label>
          </li>
        ))}
        {folders.length === 0 && <li className="muted">No folders yet.</li>}
      </ul>
      <form
        className="inline"
        onSubmit={(e) => {
          e.preventDefault();
          if (path.trim()) save([...folders, { path: path.trim(), auto_changes: autoNew }]);
        }}
      >
        <input
          placeholder="Full path, e.g. C:\Users\you\Downloads"
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <label className="check">
          <input type="checkbox" checked={autoNew} onChange={(e) => setAutoNew(e.target.checked)} />
          Don't ask for changes here
        </label>
        <button type="submit" disabled={!path.trim()}>
          Add folder
        </button>
      </form>
    </section>
  );
}

function ProviderRow({ provider: p, run }: { provider: Provider; run: (fn: () => Promise<unknown>) => unknown }) {
  const [key, setKey] = useState("");

  return (
    <li>
      <div className="row-head">
        <strong>{p.name}</strong>
        <span className={p.has_key ? "badge ok" : "badge"}>
          {p.local && !p.requires_key ? "local" : p.has_key ? "key saved" : "no key"}
        </span>
      </div>
      <div className="muted">{p.base_url}</div>
      {p.requires_key && (
        <form
          className="inline"
          onSubmit={(e) => {
            e.preventDefault();
            if (!key.trim()) return;
            run(() => api.setKey(p.id, key.trim()));
            setKey("");
          }}
        >
          <input
            type="password"
            autoComplete="off"
            placeholder={p.has_key ? "Replace API key" : "Paste API key"}
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
          <button type="submit" disabled={!key.trim()}>
            Save
          </button>
          {p.has_key && (
            <button type="button" className="ghost" onClick={() => run(() => api.deleteKey(p.id))}>
              Remove key
            </button>
          )}
          {p.docs_url && (
            <a href={p.docs_url} target="_blank" rel="noreferrer">
              Get a key
            </a>
          )}
        </form>
      )}
      {!p.preset && (
        <button className="ghost danger" onClick={() => run(() => api.removeProvider(p.id))}>
          Remove provider
        </button>
      )}
    </li>
  );
}

function AddProvider({ run, onAdded }: { run: (fn: () => Promise<unknown>) => Promise<boolean>; onAdded: () => void }) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [style, setStyle] = useState<ApiStyle>("openai");
  const [requiresKey, setRequiresKey] = useState(true);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const id = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
    void run(() =>
      api.addProvider({
        id,
        name: name.trim(),
        api_style: style,
        base_url: baseUrl.trim(),
        requires_key: requiresKey,
        local: /localhost|127\.0\.0\.1|\[::1\]/.test(baseUrl),
      }),
    ).then((ok) => ok && onAdded());
  };

  return (
    <form className="add" onSubmit={submit}>
      <h3>Add a provider</h3>
      <p className="hint">Any service that speaks the OpenAI or Anthropic API works.</p>
      <input placeholder="Name (e.g. Together)" value={name} onChange={(e) => setName(e.target.value)} required />
      <input
        placeholder="Base URL (e.g. https://api.together.xyz/v1)"
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
        required
      />
      <div className="inline">
        <select value={style} onChange={(e) => setStyle(e.target.value as ApiStyle)}>
          <option value="openai">OpenAI-compatible</option>
          <option value="anthropic">Anthropic-compatible</option>
        </select>
        <label className="check">
          <input type="checkbox" checked={requiresKey} onChange={(e) => setRequiresKey(e.target.checked)} />
          Needs an API key
        </label>
        <button type="submit">Add</button>
      </div>
    </form>
  );
}
