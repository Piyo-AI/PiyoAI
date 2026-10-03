import { FormEvent, useEffect, useState } from "react";

import { api, ApiStyle, FolderEntry, Provider } from "./api";

interface Props {
  providers: Provider[];
  onChanged: () => void;
  onClose: () => void;
}

export function Settings({ providers, onChanged, onClose }: Props) {
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>Model providers</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <p className="hint">
          API keys are stored in your operating system's keychain, never in a file. Local providers need no key.
        </p>
        {error && <p className="error">{error}</p>}
        <ul className="providers">
          {providers.map((p) => (
            <ProviderRow key={p.id} provider={p} run={run} />
          ))}
        </ul>
        <AddProvider run={run} />
        <WebSearch />
        <Folders />
      </div>
    </div>
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

function ProviderRow({ provider: p, run }: { provider: Provider; run: (fn: () => Promise<unknown>) => void }) {
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

function AddProvider({ run }: { run: (fn: () => Promise<unknown>) => void }) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [style, setStyle] = useState<ApiStyle>("openai");
  const [requiresKey, setRequiresKey] = useState(true);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const id = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
    run(() =>
      api.addProvider({
        id,
        name: name.trim(),
        api_style: style,
        base_url: baseUrl.trim(),
        requires_key: requiresKey,
        local: /localhost|127\.0\.0\.1|\[::1\]/.test(baseUrl),
      }),
    );
    setName("");
    setBaseUrl("");
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
