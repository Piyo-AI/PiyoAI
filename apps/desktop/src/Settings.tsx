import { FormEvent, useState } from "react";

import { api, ApiStyle, Provider } from "./api";

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
      </div>
    </div>
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
