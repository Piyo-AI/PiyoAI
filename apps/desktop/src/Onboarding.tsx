import { useCallback, useEffect, useState } from "react";

import { api, ModelInfo, OllamaStatus, openExternal, Provider } from "./api";

type Step = "welcome" | "choose" | "cloud" | "local" | "privacy" | "task";

const STARTERS = [
  "What can you do?",
  "Write a short welcome note and save it in my Piyo folder.",
  "What's the weather like where I live? Remember my city for next time.",
];

interface Props {
  providers: Provider[];
  /** The user picked a provider and model that work: the app switches to them. */
  onChoose: (providerId: string, model: string) => void;
  /** Setup is over (finished or skipped); `starter` is a first message to send, if one was picked. */
  onClose: (starter?: { text: string; providerId: string; model: string }) => void;
  /** Reload the provider list (a key was saved). */
  onChanged: () => void;
}

/** The first-run setup: pick how models run, connect, see what leaves the computer, try a first task. */
export function Onboarding({ providers, onChoose, onClose, onChanged }: Props) {
  const [step, setStep] = useState<Step>("welcome");
  const [provider, setProvider] = useState<Provider | null>(null);
  const [model, setModel] = useState("");

  const finish = async (starter?: string) => {
    await api.setOnboarding(true).catch(() => undefined);
    onClose(starter && provider ? { text: starter, providerId: provider.id, model } : undefined);
  };
  const connected = (p: Provider, m: string) => {
    setProvider(p);
    setModel(m);
    onChoose(p.id, m);
    setStep("privacy");
  };

  return (
    <div className="overlay">
      <div className="dialog onboarding" role="dialog" aria-label="Set up Piyo">
        <header>
          <h2>{step === "welcome" ? "Welcome to Piyo" : "Set up Piyo"}</h2>
          <button className="ghost" onClick={() => finish()}>
            Skip setup
          </button>
        </header>

        {step === "welcome" && (
          <>
            <div className="emoji">🐥</div>
            <p>
              Piyo is a personal assistant that runs on your computer: it can chat, use tools, browse the web and
              learn skills. Setup takes about a minute: choose a model, check what it can see, and try a first task.
            </p>
            <div className="inline">
              <button onClick={() => setStep("choose")}>Get started</button>
            </div>
          </>
        )}

        {step === "choose" && (
          <>
            <p className="hint">Piyo needs a language model. Where should it run?</p>
            <div className="choices">
              <button className="choice" onClick={() => setStep("cloud")}>
                <strong>Online provider</strong>
                <span>OpenAI, Anthropic, OpenRouter and others. You paste an API key.</span>
              </button>
              <button className="choice" onClick={() => setStep("local")}>
                <strong>On this computer</strong>
                <span>Ollama. Free and private, but needs a reasonably capable computer.</span>
              </button>
            </div>
            <button className="ghost" onClick={() => setStep("welcome")}>
              Back
            </button>
          </>
        )}

        {step === "cloud" && (
          <CloudStep
            providers={providers.filter((p) => !p.local)}
            onChanged={onChanged}
            onBack={() => setStep("choose")}
            onConnected={connected}
          />
        )}

        {step === "local" && (
          <LocalStep
            provider={providers.find((p) => p.id === "ollama")}
            onBack={() => setStep("choose")}
            onConnected={connected}
          />
        )}

        {step === "privacy" && provider && (
          <PrivacyStep provider={provider} onBack={() => setStep("choose")} onNext={() => setStep("task")} />
        )}

        {step === "task" && (
          <>
            <p>Try a first task. Piyo asks before anything that sends, changes or deletes.</p>
            <div className="choices">
              {STARTERS.map((text) => (
                <button key={text} className="choice" onClick={() => finish(text)}>
                  {text}
                </button>
              ))}
            </div>
            <div className="inline">
              <button className="ghost" onClick={() => finish()}>
                Finish without a task
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function CloudStep({
  providers,
  onChanged,
  onBack,
  onConnected,
}: {
  providers: Provider[];
  onChanged: () => void;
  onBack: () => void;
  onConnected: (p: Provider, model: string) => void;
}) {
  const [id, setId] = useState(providers[0]?.id ?? "");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [model, setModel] = useState("");
  const provider = providers.find((p) => p.id === id);

  const connect = async () => {
    if (!provider) return;
    setBusy(true);
    setError("");
    const hadKey = provider.has_key;
    try {
      if (key.trim()) await api.setKey(provider.id, key.trim());
      const list = await api.models(provider.id); // fails with a readable message if the key is wrong
      onChanged();
      setKey("");
      setModels(list);
      setModel(list.find((m) => m.id === provider.default_model)?.id ?? list[0]?.id ?? "");
    } catch (e) {
      // A key that did not work should not stay in the keychain (unless one was there before).
      if (key.trim() && !hadKey) await api.deleteKey(provider.id).catch(() => undefined);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <label className="field">
        Provider
        <select
          value={id}
          onChange={(e) => {
            setId(e.target.value);
            setModels(null);
            setError("");
          }}
        >
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      {provider?.docs_url && (
        <p className="hint">
          Need a key?{" "}
          <button className="link" onClick={() => openExternal(provider.docs_url as string)}>
            Open {provider.name}'s key page
          </button>
        </p>
      )}
      {!models && (
        <>
          <label className="field">
            API key {provider?.has_key && <span className="muted">(one is already saved)</span>}
            <input
              type="password"
              autoComplete="off"
              placeholder={provider?.has_key ? "Leave empty to use the saved key" : "Paste your key"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          </label>
          <p className="hint">The key is stored in your computer's keychain, never in a file.</p>
          {error && <p className="error">{error}</p>}
          <div className="inline">
            <button onClick={connect} disabled={busy || !provider || (!key.trim() && !provider.has_key)}>
              {busy ? "Checking…" : "Connect"}
            </button>
            <button className="ghost" onClick={onBack}>
              Back
            </button>
          </div>
        </>
      )}
      {models && provider && (
        <>
          <p className="ok">Connected. {models.length} models available.</p>
          <label className="field">
            Model
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id}
                </option>
              ))}
            </select>
          </label>
          <div className="inline">
            <button onClick={() => onConnected(provider, model)} disabled={!model}>
              Next
            </button>
          </div>
        </>
      )}
    </>
  );
}

function LocalStep({
  provider,
  onBack,
  onConnected,
}: {
  provider: Provider | undefined;
  onBack: () => void;
  onConnected: (p: Provider, model: string) => void;
}) {
  const [status, setStatus] = useState<OllamaStatus | null>(null);
  const [error, setError] = useState("");
  const [custom, setCustom] = useState("");
  const [model, setModel] = useState("");

  const refresh = useCallback(() => {
    api.ollama().then(setStatus, (e) => setError((e as Error).message));
  }, []);
  // Keeps looking while the user installs or starts Ollama, and while a model downloads.
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    if (status?.state === "ready" && !status.models.includes(model)) setModel(status.models[0] ?? "");
  }, [status, model]);

  const pull = async (name?: string) => {
    setError("");
    try {
      setStatus(await api.pullOllama(name));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      {error && <p className="error">{error}</p>}
      {!status && <p className="hint">Looking for Ollama…</p>}
      {status?.state === "missing" && (
        <>
          <p>Piyo did not find Ollama on this computer.</p>
          <p className="hint">Install it, start it, and this page notices by itself.</p>
          <div className="inline">
            <button onClick={() => openExternal(provider?.docs_url ?? "https://ollama.com/download")}>
              Download Ollama
            </button>
          </div>
        </>
      )}
      {status?.state === "no_models" && (
        <>
          <p>Ollama is running but has no models yet.</p>
          <p className="hint">
            The suggested model, {status.recommended}, is about 2 GB. It downloads once and then works offline.
          </p>
          <div className="inline">
            <button onClick={() => pull()}>Download {status.recommended}</button>
          </div>
          <div className="inline">
            <input
              placeholder="or another model name, e.g. qwen3:4b"
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
            />
            <button className="ghost" disabled={!custom.trim()} onClick={() => pull(custom.trim())}>
              Download
            </button>
          </div>
        </>
      )}
      {status?.state === "pulling" && (
        <>
          <p role="status">
            {status.message} {status.percent}%
          </p>
          <progress max={100} value={status.percent} />
        </>
      )}
      {status?.state === "failed" && (
        <>
          <p className="error">{status.message}</p>
          <div className="inline">
            <button onClick={() => pull()}>Try again</button>
          </div>
        </>
      )}
      {status?.state === "ready" && provider && (
        <>
          <p className="ok">Ollama is ready.</p>
          <label className="field">
            Model
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {status.models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <p className="hint">Small local models can be slow and may handle tools less reliably than online ones.</p>
          <div className="inline">
            <button onClick={() => onConnected(provider, model)} disabled={!model}>
              Next
            </button>
          </div>
        </>
      )}
      <button className="ghost" onClick={onBack}>
        Back
      </button>
    </>
  );
}

function PrivacyStep({ provider, onBack, onNext }: { provider: Provider; onBack: () => void; onNext: () => void }) {
  const [workspace, setWorkspace] = useState("");
  const [localOnly, setLocalOnly] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.folders().then((r) => setWorkspace(r.folders.find((f) => f.workspace)?.path ?? ""), () => undefined);
  }, []);

  const next = async () => {
    try {
      if (provider.local && localOnly) await api.setRunSettings({ local_only: true });
      onNext();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      <h3>What leaves your computer</h3>
      {provider.local ? (
        <p>
          Your messages and any files Piyo reads stay on this computer: the model runs here. Web searches and
          pages Piyo opens still go to the internet when you ask for them.
        </p>
      ) : (
        <p>
          Your messages, and the content of any file or page Piyo reads for a task, are sent to{" "}
          <strong>{provider.name}</strong> to produce answers. Keys, passwords and protected folders are never read.
          Choose a local model in Settings if that is not acceptable.
        </p>
      )}
      <p>
        Piyo can only read or change files in folders you approve. It keeps what it creates in its own folder
        {workspace ? (
          <>
            : <code>{workspace}</code>.
          </>
        ) : (
          "."
        )}{" "}
        Add more folders any time in Settings.
      </p>
      <p className="hint">Piyo asks before it sends, buys, deletes or submits anything.</p>
      {provider.local && (
        <label className="check">
          <input type="checkbox" checked={localOnly} onChange={(e) => setLocalOnly(e.target.checked)} />
          Only ever use models on this computer
        </label>
      )}
      {error && <p className="error">{error}</p>}
      <div className="inline">
        <button onClick={next}>Next</button>
        <button className="ghost" onClick={onBack}>
          Back
        </button>
      </div>
    </>
  );
}
