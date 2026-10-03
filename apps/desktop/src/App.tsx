import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import { api, Provider } from "./api";
import { Settings } from "./Settings";
import { useChat } from "./useChat";

const store = {
  get: (k: string) => {
    try {
      return localStorage.getItem(k) ?? "";
    } catch {
      return "";
    }
  },
  set: (k: string, v: string) => {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* storage unavailable: selection just isn't remembered */
    }
  },
};

export default function App() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerId, setProviderId] = useState(store.get("provider"));
  const [model, setModel] = useState(store.get("model"));
  const [models, setModels] = useState<string[]>([]);
  const [modelsNote, setModelsNote] = useState("");
  const [coreError, setCoreError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [input, setInput] = useState("");
  const { messages, busy, send, clear } = useChat();
  const bottom = useRef<HTMLDivElement>(null);

  const provider = providers.find((p) => p.id === providerId);

  const loadProviders = useCallback(async () => {
    try {
      const list = await api.providers();
      setProviders(list);
      setCoreError(null);
      setProviderId((current) => {
        if (list.some((p) => p.id === current)) return current;
        return (list.find((p) => p.has_key) ?? list[0])?.id ?? "";
      });
    } catch (e) {
      setCoreError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    loadProviders();
  }, [loadProviders]);

  useEffect(() => {
    store.set("provider", providerId);
  }, [providerId]);
  useEffect(() => {
    store.set("model", model);
  }, [model]);

  // Fetch the provider's live model list whenever the provider (or its key) changes.
  useEffect(() => {
    if (!provider) return;
    let cancelled = false;
    setModels([]);
    setModelsNote("");
    if (!provider.has_key) {
      setModelsNote("Add an API key in Settings to load models.");
      return;
    }
    api
      .models(provider.id)
      .then((list) => !cancelled && setModels(list))
      .catch((e: Error) => !cancelled && setModelsNote(`Couldn't load models: ${e.message}`));
    return () => {
      cancelled = true;
    };
  }, [provider?.id, provider?.has_key]); // eslint-disable-line react-hooks/exhaustive-deps

  // Pre-fill the provider's default model when there is nothing sensible selected.
  useEffect(() => {
    if (provider?.default_model && !model) setModel(provider.default_model);
  }, [provider?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const canSend = !!provider && !!model.trim() && !!input.trim() && !busy;

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!canSend) return;
    send(input.trim(), providerId, model.trim());
    setInput("");
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) submit(e);
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">🐥 Piyo AI</div>
        <div className="picker">
          <select value={providerId} onChange={(e) => setProviderId(e.target.value)} aria-label="Provider">
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {!p.has_key ? " (no key)" : ""}
              </option>
            ))}
          </select>
          <input
            list="models"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="Model"
            aria-label="Model"
            title={modelsNote}
          />
          <datalist id="models">
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </div>
        <div className="actions">
          <button className="ghost" onClick={clear} disabled={busy || messages.length === 0}>
            New chat
          </button>
          <button className="ghost" onClick={() => setShowSettings(true)}>
            Settings
          </button>
        </div>
      </header>

      <main className="chat">
        {coreError && (
          <div className="banner error">
            Can't reach the Piyo core: {coreError}{" "}
            <button className="ghost" onClick={loadProviders}>
              Retry
            </button>
          </div>
        )}
        {!coreError && messages.length === 0 && (
          <div className="empty">
            <div className="emoji">🐥</div>
            <h1>How can I help today?</h1>
            {provider && !provider.has_key && provider.requires_key ? (
              <p>
                <button onClick={() => setShowSettings(true)}>Add an API key</button> to get started, or pick a local
                provider.
              </p>
            ) : (
              <p className="muted">{modelsNote || "Ask me anything."}</p>
            )}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}${m.error ? " failed" : ""}`}>
            <div className="bubble">{m.content || (busy && i === messages.length - 1 ? <span className="dots" /> : "")}</div>
          </div>
        ))}
        <div ref={bottom} />
      </main>

      <form className="composer" onSubmit={submit}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder="Message Piyo…  (Enter to send, Shift+Enter for a new line)"
          rows={2}
        />
        <button type="submit" disabled={!canSend}>
          Send
        </button>
      </form>

      {showSettings && (
        <Settings providers={providers} onChanged={loadProviders} onClose={() => setShowSettings(false)} />
      )}
    </div>
  );
}
