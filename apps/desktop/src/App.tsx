import { FormEvent, KeyboardEvent, useCallback, useEffect, useState } from "react";

import { api, inTauri, ModelInfo, onCoreExit, Provider, resetConnection, restartCore, SkillDraft, SkillInfo } from "./api";
import { BrowserBar } from "./BrowserBar";
import { ChatList } from "./ChatList";
import { ModelSettings } from "./ModelSettings";
import { Settings, Page } from "./Settings";
import { SetupWizard } from "./SetupWizard";
import { SkillEditor } from "./SkillEditor";
import { useModelWarnings } from "./useModelWarnings";
import { useScheduler } from "./useScheduler";
import { useSkillOffer } from "./useSkillOffer";
import { Onboarding } from "./Onboarding";
import { useRollback, useUpdate } from "./useUpdate";
import { describeUpdate, useCatalogCheck } from "./useCatalogCheck";
import { useSetupNeeded } from "./useSetupNeeded";
import { Tasks } from "./Tasks";
import { ApprovalCard, ToolChip } from "./Tools";
import { useChat } from "./useChat";
import { useStickToBottom } from "./useStickToBottom";

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

function describeModel(m: ModelInfo): string {
  const parts: string[] = [];
  if (m.free !== null) parts.push(m.free ? "free" : "paid");
  if (m.context_length) parts.push(`${Math.round(m.context_length / 1000)}k context`);
  if (m.tools !== null) parts.push(m.tools ? "tools" : "no tools");
  return parts.join(" · ");
}

export default function App() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerId, setProviderId] = useState(store.get("provider"));
  const [model, setModel] = useState(store.get("model"));
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [freeOnly, setFreeOnly] = useState(false);
  const [modelsNote, setModelsNote] = useState("");
  const [coreError, setCoreError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [skillList, setSkillList] = useState<SkillInfo[]>([]);
  const [learning, setLearning] = useState<{ draft: SkillDraft; name?: string } | null>(null);
  const [learnBusy, setLearnBusy] = useState(false);
  const [learnError, setLearnError] = useState<string | null>(null);
  const [learnNote, setLearnNote] = useState("");
  const [settingsPage, setSettingsPage] = useState<Page | undefined>(undefined);
  const [wizardSkill, setWizardSkill] = useState<string | null>(null);
  const [showTasks, setShowTasks] = useState(false);
  const [input, setInput] = useState("");
  const [onboarding, setOnboarding] = useState(false);
  const {
    messages, busy, isStopping, approvals, conversations, hasMoreConversations, loadingConversations,
    loadMoreConversations, conversationId, send, respond, stop, newChat, open, remove,
  } = useChat();

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

  // First run: show the setup unless it was finished or skipped, or an online provider already has a key
  // (someone who set Piyo up before this screen existed).
  const [onboardingChecked, setOnboardingChecked] = useState(false);
  useEffect(() => {
    if (onboardingChecked || providers.length === 0) return;
    setOnboardingChecked(true);
    api
      .onboarding()
      .then(async ({ done }) => {
        if (done) return;
        if (providers.some((p) => p.requires_key && p.has_key)) {
          await api.setOnboarding(true);
          return;
        }
        setOnboarding(true);
      })
      .catch(() => undefined);
  }, [providers, onboardingChecked]);

  // The desktop shell reports a core that died on its own.
  useEffect(() => {
    let off: (() => void) | undefined;
    let gone = false;
    onCoreExit((message) => {
      resetConnection();
      setCoreError(message);
    }).then((unlisten) => (gone ? unlisten() : (off = unlisten)));
    return () => {
      gone = true;
      off?.();
    };
  }, []);

  const retryCore = async () => {
    if (inTauri()) {
      try {
        await restartCore();
      } catch (e) {
        setCoreError((e as Error).message);
        return;
      }
    }
    loadProviders();
  };

  useEffect(() => {
    store.set("provider", providerId);
  }, [providerId]);
  useEffect(() => {
    store.set("model", model);
  }, [model]);

  // "Free only" is remembered per provider.
  useEffect(() => {
    setFreeOnly(store.get(`freeOnly:${providerId}`) === "1");
  }, [providerId]);
  const toggleFreeOnly = (on: boolean) => {
    setFreeOnly(on);
    store.set(`freeOnly:${providerId}`, on ? "1" : "0");
  };

  // Fetch the provider's live model list whenever the provider (or its key) changes.
  useEffect(() => {
    if (!provider) return;
    let cancelled = false;
    setModels([]);
    setModelsNote("");
    if (!provider.has_key && !provider.public_models) {
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
  }, [provider?.id, provider?.has_key, provider?.public_models]); // eslint-disable-line react-hooks/exhaustive-deps

  // Pre-fill the provider's default model when there is nothing sensible selected.
  useEffect(() => {
    if (provider?.default_model && !model) setModel(provider.default_model);
  }, [provider?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Follow the newest content (streamed text, tool chips, approval cards) unless the reader scrolled up.
  const scroll = useStickToBottom<HTMLElement, HTMLDivElement>([messages, approvals]);
  const userMessages = messages.filter((m) => m.role === "user").length;
  useEffect(() => {
    scroll.stick(); // sending always brings you back to the bottom
  }, [userMessages]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    scroll.stick(); // a different conversation starts at its newest message
  }, [conversationId]); // eslint-disable-line react-hooks/exhaustive-deps

  const hasPricing = models.some((m) => m.free !== null);
  const shownModels = freeOnly ? models.filter((m) => m.free) : models;
  const current = models.find((m) => m.id === model);

  // Esc stops a running chat from anywhere in the window.
  useEffect(() => {
    if (!busy) return;
    const onEsc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && stop();
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [busy, stop]);

  const warnings = useModelWarnings(provider, model, [models, showSettings, busy]);
  const setup = useSetupNeeded([showSettings, wizardSkill]);
  const catalogCheck = useCatalogCheck([showSettings]);
  const scheduler = useScheduler(open);
  useEffect(() => {
    api.skills().then((r) => setSkillList(r.skills)).catch(() => {});
  }, [showSettings, learning, busy]);
  const update = useUpdate(true);
  const rollback = useRollback();
  // A rollback that starts by itself is shown on the Updates page, with its download progress.
  useEffect(() => {
    if (rollback.state === "idle") return;
    setSettingsPage("updates");
    setShowSettings(true);
  }, [rollback.state]);
  const skillOffer = useSkillOffer(messages, busy, conversationId, skillList);
  const startLearning = async () => {
    if (!conversationId || !skillOffer.offer) return;
    setLearnError(null);
    setLearnBusy(true);
    try {
      const { offer } = skillOffer;
      const draft =
        offer.kind === "refine" && offer.skill
          ? await api.refineSkill(offer.skill, conversationId, providerId, model.trim(), learnNote)
          : await api.draftSkill(conversationId, providerId, model.trim());
      setLearning({ draft, name: offer.kind === "refine" ? offer.skill : undefined });
      setLearnNote("");
    } catch (e) {
      setLearnError((e as Error).message);
    } finally {
      setLearnBusy(false);
    }
  };

  const canSend =!!provider && !!model.trim() && !!input.trim() && !busy;

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
            {shownModels.map((m) => (
              <option key={m.id} value={m.id} label={describeModel(m)} />
            ))}
          </datalist>
          {current?.free != null && (
            <span className={`tag ${current.free ? "free" : "paid"}`} title={describeModel(current)}>
              {current.free ? "free" : "paid"}
            </span>
          )}
          <ModelSettings providerId={providerId} model={model} models={models} busy={busy} />
          {hasPricing && (
            <label className="check" title="Only list models that cost nothing to use">
              <input type="checkbox" checked={freeOnly} onChange={(e) => toggleFreeOnly(e.target.checked)} />
              Free only
            </label>
          )}
        </div>
        <div className="actions">
          <button className="ghost" onClick={newChat} disabled={busy || messages.length === 0}>
            New chat
          </button>
          <button className="ghost" onClick={() => setShowTasks(true)}>
            Tasks
          </button>
          <button className="ghost" onClick={() => setShowSettings(true)}>
            Settings
          </button>
        </div>
      </header>

      <div className="body">
      <aside className="history">
        <ChatList
          conversations={conversations}
          currentId={conversationId}
          busy={busy}
          hasMore={hasMoreConversations}
          loading={loadingConversations}
          onOpen={open}
          onDelete={remove}
          onMore={loadMoreConversations}
        />
      </aside>
      <div className="pane">
      <main className="chat" ref={scroll.ref} onScroll={scroll.onScroll}>
        <div ref={scroll.contentRef}>
        {coreError && (
          <div className="banner error">
            Can't reach the Piyo core: {coreError}{" "}
            <button className="ghost" onClick={retryCore}>
              {inTauri() ? "Restart" : "Retry"}
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
            <div className="bubble">
              {m.tools?.map((t) => <ToolChip key={t.id} tool={t} />)}
              {m.content || (busy && i === messages.length - 1 && !m.tools?.length ? <span className="dots" /> : "")}
              {m.note && <div className="note">{m.note}</div>}
            </div>
          </div>
        ))}
        {approvals.map((a) => (
          <ApprovalCard key={a.id} approval={a} onAnswer={(ok) => respond(a.id, ok)} />
        ))}
        </div>
      </main>

      {setup.needed.map((n) => (
        <div key={n.integration} className="banner warn setup-banner" role="status">
          <p>
            {n.integration[0].toUpperCase() + n.integration.slice(1)} isn't connected yet, so {n.skills.join(", ")} can't
            run.{n.issue.includes("not connected") ? "" : ` ${n.issue}`}
          </p>
          <div className="inline">
            <button type="button" onClick={() => setWizardSkill(n.guide)}>
              Set up
            </button>
            <button type="button" className="ghost" onClick={() => setup.dismiss(n.integration)}>
              Not now
            </button>
          </div>
        </div>
      ))}
      {catalogCheck.withdrawn.map((w) => (
        <div key={w.name} className="banner warn" role="status">
          <p>
            Piyo switched off the skill {w.name}: the catalog withdrew v{w.version}. {w.reason}
          </p>
          <div className="inline">
            <button
              type="button"
              onClick={() => {
                setSettingsPage("skills");
                setShowSettings(true);
              }}
            >
              See skills
            </button>
            <button type="button" className="ghost" onClick={() => catalogCheck.acknowledge(w.name)}>
              OK
            </button>
          </div>
        </div>
      ))}
      {catalogCheck.updates.map((u) => (
        <div key={u.name} className="banner" role="status">
          <p>{describeUpdate(u)}</p>
          <div className="inline">
            <button
              type="button"
              onClick={() => {
                setSettingsPage("skills");
                setShowSettings(true);
              }}
            >
              Review
            </button>
            <button type="button" className="ghost" onClick={() => catalogCheck.dismissUpdate(u)}>
              Not now
            </button>
          </div>
        </div>
      ))}
      {update.status.state === "available" && !update.status.flagged && (
        <div className="banner" role="status">
          <p>Piyo {update.status.version} is available.</p>
          <div className="inline">
            <button
              onClick={() => {
                setSettingsPage("updates");
                setShowSettings(true);
              }}
            >
              See update
            </button>
          </div>
        </div>
      )}
      {skillOffer.offer && (
        <div className="banner" role="status">
          <p>
            {skillOffer.offer.kind !== "refine"
              ? "That took a few steps. Save this as a skill so Piyo can do it again?"
              : skillOffer.offer.reason === "failed"
                ? `Something went wrong while using the ${skillOffer.offer.skill} skill. Want to fix the skill from this chat?`
                : skillOffer.offer.reason === "corrected"
                  ? `It looks like you corrected Piyo. Want to teach the ${skillOffer.offer.skill} skill from it?`
                  : `Want to improve the ${skillOffer.offer.skill} skill from this chat?`}
          </p>
          {skillOffer.offer.kind === "refine" && (
            <input
              type="text"
              value={learnNote}
              maxLength={500}
              placeholder="What should be different next time? (optional)"
              aria-label="What should be different next time"
              onChange={(e) => setLearnNote(e.target.value)}
            />
          )}
          {learnError && <p className="error">{learnError}</p>}
          <div className="inline">
            <button type="button" disabled={learnBusy} onClick={startLearning}>
              {learnBusy ? "Drafting…" : skillOffer.offer.kind === "refine" ? "Suggest changes" : "Draft a skill"}
            </button>
            <button type="button" className="ghost" onClick={skillOffer.dismiss}>
              Not now
            </button>
          </div>
        </div>
      )}
      {scheduler.events.map((ev) => (
        <div key={ev.id} className={`banner ${ev.kind === "finished" ? "" : "warn"}`} role="status">
          <p>
            <strong>{ev.title}.</strong> {ev.body}
          </p>
          <div className="inline">
            {ev.kind === "approval" ? (
              <button
                type="button"
                onClick={() => {
                  setSettingsPage("scheduled");
                  setShowSettings(true);
                  scheduler.dismiss(ev.id);
                }}
              >
                Review
              </button>
            ) : (
              ev.conversation_id && (
                <button
                  type="button"
                  onClick={() => {
                    open(ev.conversation_id!);
                    scheduler.dismiss(ev.id);
                  }}
                >
                  Open
                </button>
              )
            )}
            <button type="button" className="ghost" onClick={() => scheduler.dismiss(ev.id)}>
              Dismiss
            </button>
          </div>
        </div>
      ))}
      {warnings.length > 0 && (
        <div className="banner warn" role="status">
          {warnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
        </div>
      )}
      <BrowserBar busy={busy} messages={messages} />
      <form className="composer" onSubmit={submit}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder="Message Piyo…  (Enter to send, Shift+Enter for a new line)"
          rows={2}
        />
        {busy ? (
          <button type="button" className="stop" onClick={stop} disabled={isStopping} title="Stop (Esc)">
            {isStopping ? "Stopping…" : "Stop"}
          </button>
        ) : (
          <button type="submit" disabled={!canSend}>
            Send
          </button>
        )}
      </form>
      </div>
      </div>

      {onboarding && (
        <Onboarding
          providers={providers}
          onChanged={loadProviders}
          onChoose={(id, m) => {
            setProviderId(id);
            setModel(m);
          }}
          onClose={(starter) => {
            setOnboarding(false);
            if (starter) send(starter.text, starter.providerId, starter.model);
          }}
        />
      )}
      {showTasks && <Tasks conversationId={conversationId} onClose={() => setShowTasks(false)} />}
      {showSettings && (
        <Settings
          providers={providers}
          providerId={providerId}
          model={model}
          onOpenConversation={open}
          onTestSkill={(skill) => {
            newChat();
            setInput(`Use the ${skill} skill. Show me what it can do with a small example, and tell me what it will access.`);
          }}
          initialPage={settingsPage}
          onChanged={loadProviders}
          onClose={() => {
            setShowSettings(false);
            setSettingsPage(undefined);
          }}
          onSetup={setWizardSkill}
          onRunFirstSetup={() => {
            setShowSettings(false);
            setOnboarding(true);
          }}
          refreshKey={wizardSkill}
        />
      )}
      {learning && (
        <div className="overlay" onClick={() => setLearning(null)}>
          <div className="dialog settings" role="dialog" aria-label="Review skill" onClick={(e) => e.stopPropagation()}>
            <div className="settings-content">
              <SkillEditor
                name={learning.name}
                draft={learning.draft}
                learned={!learning.name}
                onClose={() => setLearning(null)}
                onSaved={() => {}}
              />
            </div>
          </div>
        </div>
      )}
      {wizardSkill && (
        <SetupWizard skill={wizardSkill} onClose={() => setWizardSkill(null)} onOpenSkill={setWizardSkill} />
      )}
    </div>
  );
}
