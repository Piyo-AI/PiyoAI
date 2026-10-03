import { useEffect, useMemo, useState } from "react";

import { api, GoogleCheck } from "./api";
import { GoogleConnection } from "./GoogleConnection";
import { Block, Guide, parseGuide, renderInline } from "./Markdown";

interface Props {
  skill: string;
  onClose: () => void;
  onOpenSkill: (name: string) => void;
}

type Ticks = Record<string, boolean>;

const storageKey = (skill: string) => `piyo.setup.${skill}`;

// The ticked boxes are a convenience for this viewer only; the wizard works without storage.
function loadTicks(skill: string): Ticks {
  try {
    const raw = window.localStorage.getItem(storageKey(skill));
    const data = raw ? JSON.parse(raw) : {};
    return data && typeof data === "object" ? data : {};
  } catch {
    return {};
  }
}

function saveTicks(skill: string, ticks: Ticks) {
  try {
    window.localStorage.setItem(storageKey(skill), JSON.stringify(ticks));
  } catch {
    /* private window or blocked storage */
  }
}

export function GoogleTest() {
  const [result, setResult] = useState<GoogleCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.googleTest());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="widget">
      <button type="button" onClick={run} disabled={busy}>
        {busy ? "Testing…" : "Test connection"}
      </button>
      {error && <p className="error">{error}</p>}
      {result && (
        <ul className="test-results" aria-live="polite">
          {result.results.map((r) => (
            <li key={r.service} className={r.ok ? "ok" : "error"}>
              <strong>{r.service}:</strong> {r.ok ? "works. " : "problem. "}
              {r.detail}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function BlockView({
  block,
  stepIndex,
  blockIndex,
  ticks,
  onTick,
  onOpenSkill,
}: {
  block: Block;
  stepIndex: number;
  blockIndex: number;
  ticks: Ticks;
  onTick: (id: string, value: boolean) => void;
  onOpenSkill: (name: string) => void;
}) {
  switch (block.type) {
    case "p":
      return <p>{renderInline(block.text)}</p>;
    case "h3":
      return <h4>{block.text}</h4>;
    case "code":
      return <pre className="code">{block.text}</pre>;
    case "ol":
    case "ul": {
      const List = block.type;
      return (
        <List className={block.items.some((i) => i.task) ? "tasks" : undefined}>
          {block.items.map((item, i) => {
            const id = `${stepIndex}:${blockIndex}:${i}`;
            if (!item.task) return <li key={i}>{renderInline(item.text)}</li>;
            const checked = ticks[id] ?? item.checked;
            return (
              <li key={i}>
                <label className="check">
                  <input type="checkbox" checked={checked} onChange={(e) => onTick(id, e.target.checked)} />
                  <span>{renderInline(item.text)}</span>
                </label>
              </li>
            );
          })}
        </List>
      );
    }
    case "widget":
      if (block.name === "google") return <GoogleConnection onChange={() => {}} />;
      if (block.name === "google-test") return <GoogleTest />;
      if (block.name === "open-setup" && block.arg) {
        return (
          <p>
            <button type="button" className="ghost" onClick={() => onOpenSkill(block.arg)}>
              Open the full setup guide
            </button>
          </p>
        );
      }
      return null; // unknown widgets are ignored, never executed
  }
}

export function SetupWizard({ skill, onClose, onOpenSkill }: Props) {
  const [guide, setGuide] = useState<Guide | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [ticks, setTicks] = useState<Ticks>(() => loadTicks(skill));

  useEffect(() => {
    let cancelled = false;
    setGuide(null);
    setError(null);
    setStep(0);
    setTicks(loadTicks(skill));
    api
      .skillSetup(skill)
      .then((r) => !cancelled && setGuide(parseGuide(r.markdown)))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [skill]);

  const tick = (id: string, value: boolean) =>
    setTicks((cur) => {
      const next = { ...cur, [id]: value };
      saveTicks(skill, next);
      return next;
    });

  const current = guide?.steps[step];
  const progress = useMemo(() => {
    if (!current) return { done: 0, total: 0 };
    let done = 0;
    let total = 0;
    current.blocks.forEach((b, bi) => {
      if (b.type !== "ul" && b.type !== "ol") return;
      b.items.forEach((item, i) => {
        if (!item.task) return;
        total += 1;
        if (ticks[`${step}:${bi}:${i}`] ?? item.checked) done += 1;
      });
    });
    return { done, total };
  }, [current, step, ticks]);

  const last = !!guide && step === guide.steps.length - 1;

  return (
    <div className="overlay wizard-overlay" onClick={onClose}>
      <div
        className="dialog wizard"
        role="dialog"
        aria-label={guide?.title ?? "Setup"}
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <h2>{guide?.title ?? "Setup"}</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        {error && <p className="error">{error}</p>}
        {!guide && !error && <p className="hint">Loading…</p>}
        {guide && current && (
          <>
            <ol className="steps" aria-label="Steps">
              {guide.steps.map((s, i) => (
                <li key={i} className={i === step ? "current" : i < step ? "past" : ""}>
                  <button type="button" onClick={() => setStep(i)} aria-current={i === step ? "step" : undefined}>
                    <span className="n">{i + 1}</span>
                    <span className="t">{s.title}</span>
                  </button>
                </li>
              ))}
            </ol>
            <section className="step-body">
              <p className="hint">
                Step {step + 1} of {guide.steps.length}
                {progress.total > 0 ? ` · ${progress.done} of ${progress.total} checked` : ""}
              </p>
              <h3>{current.title}</h3>
              {current.blocks.map((b, bi) => (
                <BlockView
                  key={`${step}-${bi}`}
                  block={b}
                  stepIndex={step}
                  blockIndex={bi}
                  ticks={ticks}
                  onTick={tick}
                  onOpenSkill={onOpenSkill}
                />
              ))}
            </section>
            <footer className="wizard-nav">
              <button type="button" className="ghost" onClick={() => setStep(step - 1)} disabled={step === 0}>
                Back
              </button>
              {last ? (
                <button type="button" onClick={onClose}>
                  Done
                </button>
              ) : (
                <button type="button" onClick={() => setStep(step + 1)}>
                  Next
                </button>
              )}
            </footer>
          </>
        )}
      </div>
    </div>
  );
}
